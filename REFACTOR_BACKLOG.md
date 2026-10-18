# 실행 비용·구조·기능 연결 리팩토링 백로그

기준: 2026-10-18, 제품 2.1.0, 로컬 `main`/HEAD `6cf3d4c` + 조사 시작 시 미커밋 변경.
이 문서가 이번 전수 감사와 후속 “다음”의 정본이다. 과거 종료 계획이나 R1/R2 완료 기록을 다시 실행하지 않는다.

## 현재 작업과 이어가기

- **R3-083 최종 차단 검증 보완:** 정밀 재감사 없이 구조/CI56건·성능 mutation4건·격리 CLI2건(19조건)을 검증했다. 기존 CI 단위검사가 무시하던6개 비활성화/실패무시 변이를 차단하도록 기존 테스트만 보강했다. 실제 push `2c3888f`의 가드레일 단계 성공을 확인했으며 조회 시 전체 CI는 진행 중이다. 새 보강 검사의 hosted 실행·실제 PR/의도적 위반의 hosted 실패는 미검증. 상세·부담·한계는083 말미에 기록하고 다음 제품002+023은 유지한다.

- **2026-10-18 구조 퇴행 방지 기반 적용(R3-083):** [기능별 책임 계약](docs/architecture/r3-ownership.md)과 source-site/계층·cycle delta 검사·기존 비용 회귀·CI 연결을 추가했다. R3 감사·개선은 계속하며 다음 제품 구현은 아래 **R3-002+023** 순서다. 기계 채택 목록은 기존 결함/타 작업의 승인이나 리팩토링 종료가 아니다. 최종 종료 체크리스트는083에 둔다.

- **최신: 잔여 코드 정밀 감사 종결.** 고정 `src/kiwoom_monitor` 394모듈 중 직전 완료25개를 근거·해시 대조로 승계하고, 부분175개와 정적만194개의 미검토 책임을 보완했다. 현재 **코드 정밀 완료394 / 부분0 / 정적만0 / 코드 미검토0**. 운영 분기 전수 실행이나 앱 전체 정상 연결을 뜻하지 않는다.
- **통합 결과:** 기존 R3-001~062와 구현·77개 세부 경로·모듈별 이전 판정을 보존했다. 신규 **R3-063~082(20개, 모두 대기)** 및 기존010/023/025/027/032 등의 추가 근거를 기록했다. 실제 호출·소비 근거로 F01~F25 매핑을 보완했으며 기능 운영 상태를 일괄 정상으로 승격하지 않았다.
- **이번 변경:** 백로그·기존 모듈 원장과 소유 진단 산출물만 작성했다. 제품 소스394개는 시작/종료 SHA256 동일. 코드 리팩토링·운영 DB/주문·NAS/배포·commit/push·전수 회귀는0이다. 기준 증거: `data/diagnostics/refactor-r3-final-audit-20261018/verification.json`.
- **이전 단계 현재:** 2026-10-18 **2차 정밀 코드 감사와 백로그 보완 완료**. 기존 R3-001~029와 구현은 보존했고, 기존 항목 보강 및 신규 **R3-030~050(21개)**를 등록했다. 아래 2차 장의 세부 경로77개는 호출 본문/상태 owner/저장·소비/실패·동시·종료를 나눠 기록한다. 모든 함수·환경의 실행 검증 완료라는 뜻은 아니다. 이번 제품 리팩토링은0이다.
- **다음:** 사용자 지시대로 **R3-002부터** 이어간다. 기존 연결/COMMIT3→1 구현을 다시 만들지 않고, 이번 재현으로 확인된 **실패 payload의 거래일 유실(R3-023)**을 작은 caller→writer→native 저장 경로에서 보완한다. 원일 고가·현재가 수신 timestamp·최신값 병합·종료 재시도의 계약을 구분하고 실제 writer/재조회 회귀 뒤 F06/F10/F24를 함께 갱신한다. 그 뒤 아래 의존/우선순위에 따라 진행하며 전체 재조사는 하지 않는다.
- **이전 검증 이력:** 기존 R3-002 transaction 관련140건/후속12건 부분 재실행, R3-004 native9000→200/고유58건 및 후속 부분 실행은 **그 당시 검증 범위**로 보존한다. 직전 **2차 진단21조건 + 관련 기존 회귀21건**과 그 뒤 깊이대조 진단14조건은 각각 당시 결과로 보존하며, 아래 최종 감사의 진단과 구분한다. 진단 통과는 현재 문제/비용의 재현 성공이며 해결 완료가 아니다. 모든 신규 ID는 대기다.
- **차단/미검증:** 운영 DB/NAS/실제 PG·wait/장중 TR·0B/실제 주문·외부 AI/설치 앱 전체 수락은 실행하지 않았다. 미확인 운영 영향은 추정으로 남긴다. 감사 문서에는 차단 없이 반영했으며 신규 구현은 이번 범위 밖이다.
- **정적 계약 잔여:** 기존 QueryStore source/audit1실패(병행 뉴스 `load_news_history(before_revision_id)`3서명)는 유지한다. R3-004 reader3서명 제한 갱신과 별개이며 이번에 baseline을 승인하거나 테스트 로딩 실패를 성공 처리하지 않았다.
- 기존 다른 작업의 미커밋 소스/테스트를 되돌리지 않았다. commit/push/CI 실행/배포/전수 회귀 없음. 최신 증거는 `data/diagnostics/refactor-r3-final-audit-20261018/`이며 이전 audit2/coverage 진단도 보존한다.
- 상태: **대기 / 진행 / 검증 완료 / 보류**, 분류: **제거 / 통합 / 유지 / 재설계**. `유지·검증 완료`는 기존의 명시된 계층 필요성 평가 범위다. 내부 책임 적절성·연결 회복·운영 수락은 별도 열/후속 ID에서 판단한다.
- 우선순위 P1=확인된 연결·정합성 오류 또는 장중 실행 경로 부하, P2=국소 비용/책임 개선, P3=부가·추가 측정 후보, P4=낮은 유지보수 비용. 실제 효과·회귀 위험·선행 계약을 함께 비교한다.

### 새 대화에서 “다음”

1. 이 문서의 현재/다음 항목, `git status`, 최신 HEAD, `CURRENT_STATUS`, guardrails와 해당 모듈 지도를 읽는다.
2. 아래 ID의 관련 파일/호출자/테스트만 현재 코드와 대조한다. 최신 감사 지문은 `data/diagnostics/refactor-r3-final-audit-20261018/source-end.json`과 같은 디렉터리의 `verification.json`, 모듈별 범위는 `docs/workstreams/refactor-module-coverage-20261018.json`이며 초기 비교용 `data/diagnostics/refactor-r3-20261018/source-before.json`/`scripts-before.json`도 보존한다. **파일 해시 차이는 재검토 범위를 고르는 신호이며, 전체 재감사나 작업 차단 조건이 아니다.** 관련 함수·조립/소비 경계가 달라졌는지 판단한다.
3. 관련 경로가 바뀌었을 때만 그 부분의 근거를 갱신한다. 새 발견은 기존 ID/O 항목과 중복을 대조하고 새 ID를 뒤에 붙인다. ID를 재사용/재정렬하지 않는다.
4. 한 작은 경로의 기준 → 변경 → 관련 통합/계약 회귀 → 비용/내용 대조 순으로 진행한다. 결과·실패·미검증·실제 commit 또는 `미커밋`을 해당 ID에 쓴다. **각 R3 항목 완료 시 영향받은 F01~F25 행도 같은 변경에서 반드시 갱신한다(2026-10-18 사용자 추가 지시).** 실제 호출/데이터 경로·검증 근거·미완성/미검증 범위를 기록하며, 상태가 그대로여도 확인 범위와 관련 R3 ID를 갱신한다. 코드 수정이나 단위 테스트만으로 전체 정상 연결로 승격하지 않는다.
5. 현재/다음/차단을 갱신한다. `all-local`은 별도 사용자 요청 때만 실행한다. 필요한 업무·안전·자료 조건을 새로 만들지 않는다.
6. 2026-10-18 사용자 승인: 기능뿐 아니라 상태·transaction·수명·공유 파일의 변경 소유권이 겹치지 않는 항목은 에이전트로 나눠 병렬 진행한다. 같은 reader/저장 계약의 변경은 의존을 먼저 확인한다. 에이전트별 범위/파일을 고정하고 공유 백로그와 F 원장의 최종 통합은 주 담당자가 맡는다. 독립 조사·fixture와 제품 코드 동시 변경을 구분한다.

## 감사 범위와 증거의 수준

- 실제 `src` **394개 Python 모듈**, 정의 **6,806개**의 AST/해시/호출 표현/SQL/이벤트 등록을 전수 목록화했다. 별도 `scripts` **174개**도 목록화했다. 문자열상 호출 수는 중첩 정의의 중복과 동적 이름을 포함하므로 실행 횟수/연결 증명으로 사용하지 않는다.
- 기존 감사 도구를 현재 코드에 실행했다. QueryStore **107개 메서드**, 검토된 store 접근380곳, 전달 연결61곳, 미확정 동명 후보39곳, 구문 오류0. 기존 승인 baseline 대비 소비/전달 delta0. PostgreSQL 직접 연결 승인60/60 일치. 이는 **정적 계약/목록 검사**이며 모든 함수 실행이나 PG 상태를 검증한 것이 아니다.
- 주요 앱·서버 조립, Qt signal/작업 수명, TOP20/0B/봉/writer, 판단/Shadow/계좌·주문, 뉴스/연구/동기화의 실제 호출 본문을 아래 연결 원장과 항목별로 추적했다. 단순 파일 크기나 클래스 수는 개선 판단 기준으로 사용하지 않았다.
- `source-before.json`, `scripts-before.json`, `query-store-consumers.json`, `postgres-access.json`, `storage-paths.json`, `git-status-before.txt`를 위 진단 디렉터리에 보존한다. `storage-paths` 최초 출력은 디렉터리 준비 순서로 실패했고 준비 뒤 재실행했다. 도구 실패를 제품 실패/통과로 섞지 않는다.
- `measure_costs.py`는 운영 연결 없이 임시 SQLite·고정 KST·20종목 fixture를 사용한다. `cost-before.json`: 분봉 연결1/SELECT40/COMMIT1, 가격·고가·시총 묶음 연결3/COMMIT3, 동일 시총 재적용 SQLite `total_changes=20`. `total_changes`는 SQL 반영 계수이며 물리 디스크/WAL 쓰기량이나 장중 지연 측정값이 아니다.
- 변경 후 관련 검사 총 **317건**: R3-001의23+5+30=58건, 서버 연결 계약258건(broker21/Collector33/CandidateMonitor185/0B·분봉 전환19), create_app·SQLite 조립 ASGI1건. 실패/오류/skip0, 각 worker 종료코드0·자식 종료·임시자료 정리 확인. 서버 계약에는 실제 app lifespan의 명시 RAM binding 조립/해제, 공유 입력 fan-out, checkpoint 실패/복구, 선택/인증 API가 포함된다. 실제 네트워크/브로커/PG/PC 화면 전체 수락은 아니다.
- 직전 순위표 읽기 정리는 이미 `ranking-cache-cleanup-20261018/verification.json`에 있다. R3-026으로 승계하고 다시 구현하지 않는다. 이전 테스트 결과의 소스 지문을 대조하며 변경 후 통과로 무조건 재사용하지 않는다.

## 기능별 실제 연결 원장

상태는 **정상 연결 / 부분 연결 / 미연결 / 확인 불가**다. 정적 조립이 보여도 현재 실행 검증이 없으면 `확인 불가`로 둔다. 아래 경로는 실제 코드에서 발견한 연결이며 설계 문서만의 화살표가 아니다. `미연결`은 호출자 검색 결과0만으로 확정하지 않는다. 신규 실계좌/운영 쓰기 없이 검증 가능한 경로를 우선한다.

| 경로 | 실제 진입·조립 → 실행·저장 → 소비 | 현재 연결 상태 / 검증과 남은 범위 | 연결 항목 |
|---|---|---|---|
| F01 PC 시작/실행모드 | `__main__ → bootstrap.main/build_api_runtime → MainWindow → AppController.configure_*` | 확인 불가(전체 기동): 본문 P01/P11/S23. bootstrap plain daemon 작업은 controller/restore lease 종료 대기에서 제외돼 있다(R3-032, 미재현). 초기 조립과 설정 sync cache 무효화 경합(R3-011)은 구분한다. 실제 앱 시작/복원 전체 미실행; **누락범위추가:** update helper 실패053와strictrestore body를추가했다;기동전체 수락은 별도이며 own-body 감사는 이번에 종결  **최종 코드감사 보완:** 전체 own-body 감사는 종결했고 기동/복원 운영 수락은 별도다. | R3-011/017/025/032/053 |
| F02 PC 순위 | MainWindow 설정/타이머 → AppController.request_ranking/handle_ranking_response → RankingWorkerController → loader → apply_ranking/후속 구독 | 확인 불가: 실제 연결 확인, 이번 턴 전체 순위 응답·30초 경계 동작 검증은 관련 fixture로만 구분  **최종 코드감사 보완:** 연결 모듈의 미독 책임 종결, 기존 실행/운영 한계는 그대로 유지. |R3-014/025/026; 신규 R3-070 |
| F03 NAS 순위/TR | `app.create_app → CentralRestBroker(...MarketDataIngestor.ingest) → AutonomousTop20Service.refresh_ranking_once → RAM membership → dataset 저장 → market API` | 부분 연결: S15~S18 본문 및 실제 index loop/native SQLite blocked fixture에서 membership RAM 공개 후 collector_lock 대기로 구독후속이 멈춤(R3-035). broker 송신/후처리 분리는 유지. 실제 NAS TR/PG 지연 미측정  **최종 코드감사 보완:** 연결 모듈의 미독 책임 종결, 기존 실행/운영 한계는 그대로 유지. | R3-006/007/035/036 |
| F04 0B 수신/PC 소비 | `app collector._receive/_publish_parsed → RealtimeHub → realtime_routes → CentralRealtimeTradeWorker → RealtimeWorkerController.trade_received → MainWindow._on_trade_tick` | 부분 연결: S01~S04/S21. actual route+Hub/fake socket에서 sender 예외 후 disconnect를 건너뛰고 구독1/code가 남음(R3-037). membership 공개 후 구독후속 대기(R3-035)도 재현했다. 상류 ACK/generation·실제 PC worker 조립은 확인했으나 실제 socket/failover/운영 재구독은 미실행; **누락범위추가:** condition진단저장await062의본문인과추가/실제recv지연미실측  **최종 코드감사 보완:** 연결 모듈의 미독 책임 종결, 기존 실행/운영 한계는 그대로 유지. | R3-007/014/025/035/037/062 |
| F05 로컬 0B 분봉 저장 | `_on_trade_tick → MinuteTradeValueAggregator.ingest → AppController.queue_minute_bar/flush_minute_cache → MarketCacheWriter → MinuteBarRepository._upsert_many → minute_bars + observation meta` | **정상 연결(격리 PC fixture)**: 공유 시계 변경의 naive/aware 비교 실패를 재현·비교2곳만 `as_kst`로 보완하고 legacy 분 키/값·이전분/현재분·metadata 유/무와 실제 Qt→writer→DB→종료·재조회 경로를 관련140건 중 분봉24/writer7/controller35로 재검증했다. 실제 Kiwoom/설치 앱/운영 DB는 미검증  **최종 코드감사 보완:** 연결 모듈의 미독 책임 종결, 기존 실행/운영 한계는 그대로 유지. | R3-001/002/023 |
| F06 PC 현재가/시총/고가 | `_on_trade_tick → queue_price_cache → 기존1초 timer → MarketCacheWriter._save_prices → StockRepository.update_price_cache → 단일 transaction → load_ranking_cache/표`; 실제 저장소를 쓰는 writer 없는 호환 경로도 같은 메서드 사용 | 부분 연결: 기존 단일 transaction/최신 같은 날 재시도 검증은 보존. 이번 actual controller fallback→native SQLite에서 실패 payload 원일 고가가 다음 거래일로 저장됨(R3-002/023). 원거래일 실패/실제 writer 자정 경계는 후속이며 실제 장중/설치 앱 미검증  **최종 코드감사 보완:** 연결 모듈의 미독 책임 종결, 기존 실행/운영 한계는 그대로 유지. |R3-002/003/023/026; 신규 R3-070 |
| F07 NAS 0B RAM/봉 저장 | Collector._observe_ram_trade/_minute_bars → `_save_snapshots/_flush_snapshot_cycle → store.save_minute_bars/finalize_minute_bars → metadata/revisions` | 부분 연결: S05~S11. native snapshot/op-ID/authority/owned drain은 유지. actual0g130 저장중140→실패복원130→retry DB130/RAM140 재현(R3-034). account-entry 유사대입은 본문만 확인, FILL 원장 손실 증거는 아님. 실제 PG/원본 운영 검증 미실행; **누락범위추가:** conditioncohort/native ACK와수신062의분기추가/원본보존운영미검증  **최종 코드감사 보완:** 연결 모듈의 미독 책임 종결, 기존 실행/운영 한계는 그대로 유지. | R3-006/007/034/062 |
| F08 봉 화면/복구 | NAS `market_read_routes.load_display_minute_bars → collector.load_live_minute_bars → store(DB+RAM delta)`; PC history worker → writer → ACK → aggregator/UI | 확인 불가(전체 운영): S11~S14. 기존 R3-004 range/overlay 계약은 보존. 이번 native SQLite에서1행page10회 metadata1200/SOR1200행(R3-038), 빈이력 최근2일 API31native reads(R3-039)를 확인했다. 운영/실제PG·장후coverage·실제PC복구 미검증; **누락범위추가:** 숨김분리chart의read6/aggregate12를055로추가/실제SQL·paint미측정  **최종 코드감사 보완:** 숨김 live page075와 paint081은 비용 미실측 후보로 분리. R3-083: 기존 Runtime→Collector→SQLite 검사 보강으로 native400→200 반환/무쓰기·close 관련16건 확인. 전체PG/GUI수락은 별도. 최종R3-083: full-day변이400행을기존200행계약이거절하고복원재통과(실제PG/GUI별도). |R3-002/004/016/025/038/039/055; 신규 R3-075/081; R3-083 |
| F09 TOP20 지수/장후 | AutonomousTop20 `_advance → _queue_index/JsonRecordOutbox → _flush_pending_indexes/_write_index → dataset → API/PC 그래프`; 프로그램수급은 별도 `ProgramSnapshotWriter` | 부분 연결: S17~S19. actual index 저장 대기 중 구독후속 차단(R3-035), 두 flush가 동일 payload native2회/final1행(R3-036)을 재현. 숨김PC 그래프 동일cut120행×4 재집계도 확인(R3-027). 실제 장후/PG 및 kernel 전체 수치 수락은 미검증  **최종 코드감사 보완:** outbox 읽기실패 원본교체065 재현, 장후 coverage SELECT078 실측. |R3-006/007/027/035/036; 신규 R3-065/078 |
| F10 기본정보/NXT/신고가 | ranking 후속 → 전용 controllers/workers → NAS DB 또는 직접 모드 loader → MainWindow 수신 → writer/StockRepository/DailyBarRepository → RAM/가격 표시 | 부분 연결: 기존 PC batch의 고가MAX/유통시총/기본정보시각 보존 근거는 유지. reference retry 구값 영속화(R3-034)와 실패고가 날짜 유실(R3-023)은 이번 재현. 실제 daily/NXT/장후 기본정보 수락은 미실행; **누락범위추가:** catalog busy일때OCR후속callback0의054및조건검색F10.1추가  **최종 코드감사 보완:** cached include_nxt 누락068 actual service 재현; 첫 이미지 OCR080 호출수 확인. |R3-002/003/015/023/026/034/054/062; 신규 R3-068/076/080 |
| F11 완료봉 후보 Shadow | `app operational settings → CandidateMonitor.from_json/start → run_once → observation cursor → family_for_config(config).evaluate_bar → save_shadow_evaluation/checkpoint → research_read_routes → CandidatePollWorker/UI` | 확인 불가(전체 운영): R6/T03/T09에서 app설정→monitor/cursor→공유Family→Shadow저장/게시 근거와 Runner소비 본문을 대조했다. 이번 모든Family/실제minute→Risk→intent 통합은 미실행. 새0B정책과 기존봉정책을 혼용하지 않음  **최종 코드감사 보완:** PG Shadow immutable silent ACK는010 추가 범위, 실제 PG 미재현. |R3-018/024/047/048; R3-010 보강 |
| F12 주도주 0B 후보 | app `collector.set_breakout_trade_listener → operational_state.candidate_monitor.observe_trade → RAM baseline/queue → run_once → save_shadow_evaluation → UI` | 부분 연결: S10/R6/T10에서0B source/baseline→queue→공유판단·보호/저장 소비 본문을 확인했다. 정책필수조건 미정은 유지한다. 실제kernel+queue/drop+주문상태 통합/장중운영은 이번 미실행  **최종 코드감사 보완:** PG Shadow 동일 ID 충돌은010 추가 범위이며 실제0B 정상 수락으로 확대하지 않음. |R3-005/018/024/047; R3-010 보강 |
| F13 시장·테마 RAM Shadow | `app RAM 선택 복원/게시 → MarketThemeShadowRuntime.start → hub event → _snapshot/_dispatch → shared evaluator → result/선별 기록 → API/관찰창` | 확인 불가(전체 운영): R3-004 실제 Runtime→Collector→SQLite reader에 기존10분 range를 적용했고 최종200행 값/metadata 동일·native9000→200을 확인했다. 고유58건은 실제 app binding·선택 저장/재시작/종료·공유 read/TTL·core 비차단/close·자정/UTC/2초 fence를 포함한다. PG는 SQL/transaction 계약 fixture만 확인, 실제 PG/PC 관찰창/장중은 미검증  **최종 코드감사 보완:** 연결 모듈의 미독 책임 종결, 기존 실행/운영 한계는 그대로 유지. R3-083: 20종목 native10행씩=200, 무제한 양성대조400,8소비자/100회조회/close 추가read0을 기존검사로 확인. 실제장중/PG수락은 별도. 최종R3-083: 격리native full-day변이400행거절·복원200행과구독/close동일검사재통과. | R3-004/018/022; R3-083 |
| F14 현재 비중/포지션 | `position_account_source → owner ACK RAM → runtime._borrow_position_account → comparison/allocation → 선택 결과/API/UI` | 부분 연결: T16/T21 및 S10의 ACK된 RAM관측/실제price소비·read-only계좌owner 분리 본문을 확인하고 선택된accountRAM/real-read회귀 각2건을 실행했다. 모든모델의양의지원/운용승인·실제계좌자료/위험전체문서는 미검증  **최종 코드감사 보완:** 연결 모듈의 미독 책임 종결, 기존 실행/운영 한계는 그대로 유지. | R3-009/024/049 |
| F15 연구→판단 | `research_process.execute_process_request → research_families registry/evaluator → repository/후보 게시 → MockAutomationSpecification/선택 → Runner` | 부분 연결: R6/R7/T02/T03에서 실제Family공유·immutable계보/Shadow근거→게시→Runner 소비 본문을 확인했다. 일반연구규칙을자동live/주문승인으로승격하는범용연결은 없으며 모든Family·계좌activation통합은 미검증; **누락범위추가:** feedback/context/thesis builder의producer연결미확정과stored-input평가consumer를024로구분  **최종 코드감사 보완:** MDD ppm067 실제 helper 재현; 연구→범용 live 적용 미정024 유지. |R3-018/024/041/047; 신규 R3-067 |
| F16 모의 주문/취소 | `mock_order_routes → ManualMockOrderGateway.refresh/owned command → ExecutionRuntime → OrderLifecycle → ExecutionRepository → execution store → transport` | 확인 불가(실제 주문): T12~T15. Gateway/Runtime/Lifecycle/nativeclaim은 실제 안전 책임. actual gateway 동일요청 load2/두 번째 event-loop 동기 read 재현(R3-040), 선택된 claim/UNKNOWN/drain 관련 회귀 통과. 실제 주문 송신/PG·모든동시조합은 미검증  **최종 코드감사 보완:** 연결 모듈의 미독 책임 종결, 기존 실행/운영 한계는 그대로 유지. | R3-009/010/040/050 |
| F17 자동 모의/보호 | `mock_publication/control API → Supervisor → Runner/monitor recovery → latest Risk/control → Runtime/native claim → Lifecycle; Gateway는 manual/자동취소 명령 경계` | 부분 연결: T01~T12/T18~T20. 실제 owner/bundle/runtime/supervisor/runner 조립에서 설정 교체 후 구runner map이 새MANUAL bundle과 불일치해 restore skip/재개 UI불가 재현(R3-042). runner task/실제승인·송신은 미실행, stale 주문권한 생존으로 단정하지 않음. status의 Shadow전량조회/ACK후오류 R3-041, 내부 책임 R3-047~050 남음  **최종 코드감사 보완:** MDD067은 candidate eligibility 입력에 연결됨; 실제 주문 수락은 미실행. |R3-008/009/024/041/042/047/048/049/050; 신규 R3-067 |
| F18 체결·잔고→상태 | 계좌00/04 → MockAccountMonitor.handle_realtime/recovery → native ledger/reconciliation → Risk snapshot ACK → listener → Runner FILL/position RAM | 부분 연결: T07/T16/T17/T21 및 T20의 종료 보존. 상세FILL→원장→Runner만position 갱신, monitor RAM/real-read 분리 본문과 선택 회귀 확인. owner 교체가 supervisor에 조정되지 않는 R3-042 및 journal backfill 저장예외 R3-031 재현. 실제00/04·전체FILL/Risk ACK/PG 경합은 미검증; **누락범위추가:** 일지 readside051/복기종료052/분리chart055/팩출처057/사후입력058/draft재조회059추가;FILL원장과별개  **최종 코드감사 보완:** snapshot 재전달064·real 종료미저장066·summary/statistics074·hidden live075·paint081 추가. FILL 원장 전체 손실로 일반화하지 않음. |R3-008/009/025/031/042/049/051/052/055/057/058/059; 신규 R3-064/066/071/074/075/081 |
| F19 뉴스 수집/분석 | `app → CentralNewsService.refresh_once → TOP20 저장 membership → _collect → revision/job → BODY/RULE/AI → stored-page/history → news_process.StockNewsWindow` | 부분 연결: N1~N7. 현재 수집→source/body revision→BODY/RULE/AI→저장/PC 근거 소비 본문 추적. actual SQLite stale claim A→B완료→A retry가 새 COMPLETED를 PENDING으로 되돌림(R3-043). 실제 공급자/AI/과금·PC 전체GUI·PG claim은 미실행; F19.1 일지 뉴스 read-side051은 별도소비 경로로 추가  **최종 코드감사 보완:** legacy schema069·NAS 수동 AI 버튼072·동일기사 쓰기073 추가, 모든 NAS AI 미연결로 일반화하지 않음. |R3-019/020/043/051; 신규 R3-069/072/073 |
| F20 장전 연구 입력 | 설정/예약 → `MorningMarketBrief → AI research → raw/result 보존 → latest/context → PC dialog` | 부분 연결: P13의 실제UI설정→HTTP→예약/claim→AI→원문·결과보존→latest/context→UI/close 본문을 확인했다. context는명시consumer_binding=not_connected/advisory_only다. 같은run무단재요청없음/재표시timer복원본문은 확인했으나 실제07:55·AI·GUI종료/재시작은 이번미실행; **누락범위추가:** F20.1 외부지연시세→월물→native/API060/061별도추가;장전AI자동입력으로확정안함  **최종 코드감사 보완:** 연결 모듈의 미독 책임 종결, 기존 실행/운영 한계는 그대로 유지. | R3-024/025/060/061 |
| F21 연구 실행/재시작 | PC 연구 dialog → research child request → research_process → data source/registry → research repository/worker claim → 결과/복구 snapshot/UI | 부분 연결: R1~R7. child/lease/registry/immutable 결과→UI의 본문 연결과 generic CLI request=result 오류출력 원본교체 재현(R3-046). 일반 UI는 별도경로를 만들어 해당trigger 증거 없음. 모든campaign/storage/family·실제child중단복구는 미실행  **최종 코드감사 보완:** 일반 run terminal race063와 MDD067, legacy schema069 추가. owned final/search 전체 fence와 구분. |R3-017/018/024/046; 신규 R3-063/067/069 |
| F22 일지/뉴스·테마 동기화 | journal/news process→CentralContentSyncService identity/hash delta→content API/document store→pull/merge/UI; 테마는 CentralThemeSyncDispatcher→CentralContentSyncService→ThemeBackupService expected_revision import | 부분 연결: N8/P06/P07. 실제 sync scheduling 본문이 전송 뒤 생긴 로컬 edit도 clean ACK(R3-044), 동일 theme created_at만으로2차전송1행(R3-045), actual writer backfill 예외 뒤 queue1/failed0 상태에서도 controller ready(R3-031)를 재현. 기존 delta/테마revision R3-029 유지. 실제 Qt/network/다중PC/PG 미검증; **누락범위추가:** snapshot뉴스051/복기052/catalog054/DriveACK056/팩출처·복기입력057~059추가;실제다중PC미검증  **최종 코드감사 보완:** journal fullsync071·native 기사073·일지074·catalog076·미사용 profile077·OCR080 추가. |R3-003/021/029/030/031/044/045/051/052/054/056/057/058/059; 신규 R3-069/070/071/073/074/076/077/080 |
| F23 API/설정 교체 | `MainWindow → AppController API reload → producer drain → build_api_runtime → factory 교체/신호 → 후속 재구독` | 부분 연결: P02/P03/T04/T22. actual controller API swap 중 old writer loader 결과가 cache 재삽입돼 다음요청 구provider cache 사용(R3-030), 실제 SettingsRepository clear 후 old read 재삽입(R3-011). advertised 재시작 필요 모드는 즉시전환 누락으로 단정하지 않음. 실제 credential/전체 재구독은 미실행; **누락범위추가:** Drive ACK056/catalog054/vault·operationalsettings 추가본문범위명시;보안실행미검증  **최종 코드감사 보완:** 제외종목찾기070 인자 연결 단절과 NAS 수동AI072, real 종료066 추가. 도달불가 옛 layout082는 실행비용0. |R3-002/011/014/017/025/030/042/054/056/060; 신규 R3-066/070/072/076/082 |
| F24 종료/재시작 | PC AppController producer stop→flush→writer drain→신호 대기; 서버 lifespan close_services→각 owned close/drain→store close | 부분 연결: P07/P11/S21/S23/T14/T20. 기존 owned native/transaction/drain 검증은 보존. journal queue 잔류인데 ready(R3-031), socket sender오류 뒤구독잔류(R3-037), owner↔runner불일치(R3-042) 재현. bootstrap plain daemon/lease R3-032는 정적 후보다. outer첫close실패시dependency보존은 명시계약. 실제NAS/강제중단 복구 미실행; **누락범위추가:** 복기dirty052/업데이트mixed053/DriveACK056/외부poll owner060추가;전종료검증완료아님; catalog 후속054/분리chart055의 실제 show·close 미검증  **최종 코드감사 보완:** 일반 run063/snapshot064/outbox065/realclose066의 잔여를 구분; 기존 수명 owner 유지 판단은 전체 안전 수락이 아님. R3-083:004의 close 추가read0 및 기존 도구/비용 관련회귀 확인. 다른종료미결은그대로이며 전수종료수락아님. 최종R3-083: scope후함수identity·정상검사복원/worker종료확인. 전기능수명수락아님. |R3-002/008/014/025/030/031/032/037/042/044/048/050/052/053/054/055/056/060; 신규 R3-063/064/065/066/071/075/077/080/081; R3-083 |
| F25 진단/replay | 기존 writer/read 계측 → registry/trace → offline replay dedicated store/clock; 실제 order transport와 격리 | 확인 불가(운영/전체 재생): D1~D4의 진단run→capture→manifest/window→owned replay/restore 본문 추적. wrappers의 outcome분리·실제executor/lease소유 역할 유지. 기존 range/capture호환 근거 보존. OFF/ON비용·파일손상/용량·dedicatedPG복구·뉴스3서명 audit실패는 미해결  **최종 코드감사 보완:** 진단 모듈 코드 책임 완료. v4 clock 삭제 범위079는 조건부 가설/실제 PG 재현 필요. |R3-004/022; 신규 R3-079 |


### 최종 감사의 세부 연결 상태

부모 F01~F25의 상태를 유지하면서 이번에 원인이 드러난 분기를 세분화한다. 아래 상태는 실제 코드 경로와 명시한 격리 검증에 한정한다.

| 세부 경로 | 실제 호출·소비 | 연결 상태·검증 한계 | R3 |
|---|---|---|---|
| F09.1 index outbox 복구 | queue_index→outbox.load/put→native 저장→remove→재시작 pending | 부분 연결: unreadable→빈 dict→원본 교체2조건 재현. 실제 NAS·전원장애 미검증 |065|
| F18.1 real event 종료 ACK | real00/04→pending/writer→native row; closing→drain→bundle 퇴역 | 부분 연결: 실패+stop+closing의 잔여검사 공백은 본문 근거. 실제 계좌·저장실패 종료 재현 미실행 |066|
| F18.2 snapshot 중복·보강 | order_execution→writer→same-key upsert; news enrichment→native→일지 | 부분 연결: 보강news1→0 native 재현. 후속 재보강의 복구·실제 중복 빈도 미확인; aware enqueue 차단023도 남음 |023/064|
| F19.2 NAS 수동 AI 버튼 | 뉴스선택→manual enable→central AI worker; 자동·doubleclick·완료후 경로 별도 | 부분 연결: NAS-only predicate manualFalse/autoTrue. 전체 AI 미연결 아님; 실제 GUI/AI 미실행 |072|
| F21.1 일반 연구 terminal | start→append/report→finish/fail/cancel→결과표/비교 | 부분 연결: 지연 fail이 completed를 덮는 native 반례. owned final/search fence와 구분 |063|
| F22.1 journal sync | timer/runner→load_all→merge→SELECT*→전체 upload→다른 PC/일지 | 부분 연결: actual service 3행 동일자료도 두번째 업로드3. remote는 대역, 실제 다중PC 충돌 미실행 |071|
| F23.1 제외종목 찾기 | 기본설정→종목찾기→SimilarStockDialog→제외목록 저장→테마합계 | 부분 연결: 실제 caller 인자 binding과 join TypeError 확인. QWidget/GUI 클릭 미실행 |070|


## 우선순위 목록

| ID | 대상 | 분류 | 우선순위 / 상태 | 선행·비고 |
|---|---|---|---|---|
| R3-001 | 로컬 분봉 메타데이터 동일 행 중복 SELECT | 제거 | P1 / 검증 완료 | SELECT40→20, 내용 동일, 관련58건 |
| R3-002 | 가격·시총·고가 한 queue job의 3개 transaction | 통합 | P1 / 진행 | 연결/COMMIT3→1, 최종 관련140건, 영향 F7행 갱신 |
| R3-003 | 동일 시총·가격 UPDATE와 확인시각 의미 | 제거 | P1 / 대기 | 조사/진단 측정 완료·제품 대기, 가격 확인시각 유지·cap 조건부 UPDATE 후보 |
| R3-004 | RAM Shadow의 종목별 하루 전체 분봉 직렬 읽기 | 재설계 | P1 / 검증 완료 | native9000→200·최종값동일/기능58통과, 전체 정적 감사는 별도 뉴스1실패 |
| R3-005 | 0B 후보 기준선의 종목별 일전체 분봉 조회 | 재설계 | P1 / 대기 | R3-004와 reader 공통점 비교 |
| R3-006 | PG 호출별 연결 및 flush 비용 | 재설계 | P1 / 보류 | 실제 wait/연결 비용 미측정 |
| R3-007 | 중앙 저장 직렬화·REST ingest 분리·retry | 유지 | P1 / 검증 완료 | 정적 책임 평가. 운영 보존은 미검증 |
| R3-008 | 게시 binding 중 runner 없는 계좌의 자동복원 조회 | 재설계 | P2 / 보류 | 외부 control/기동 후 binding 발견에 현재 polling 필요 |
| R3-009 | control/Risk/계좌 재검증과 실행 계층 | 유지 | P1 / 검증 완료 | 안전 경계의 재조회는 제거하지 않음 |
| R3-010 | 불변 승인 문서 조회→UPSERT→재조회 | 재설계 | P2 / 대기 | CAS/충돌·ACK·동시 writer 검증 |
| R3-011 | 같은 설정의 UPSERT/version 갱신 | 재설계 | P3 / 대기 | 동기화 revision 의미 먼저 확인 |
| R3-012 | 열 설정 전체 UPDATE/version 저장 | 통합 | P3 / 대기 | 실제 변경 열과 sync 소비 확인 |
| R3-013 | MainWindow의 controller 별칭/전달 접근 | 제거 | P3 / 대기 | caller별 역할/호환 확인 후 선택 제거 |
| R3-014 | AppController 수명·순위·종료 owner | 유지 | P2 / 검증 완료 | 크기나 전달 signal 수로 제거하지 않음 |
| R3-015 | 개별 worker controller의 실제 수명 책임 | 유지 | P3 / 검증 완료 | 공통 추상 controller 추가하지 않음 |
| R3-016 | QueryStore aggregate와 DB mixin/좁은 계약 | 유지 | P2 / 검증 완료 | aggregate 삭제보다 실제 wide 소비를 검토 |
| R3-017 | 조립 dict/다중 책임의 국소화 | 재설계 | P2 / 대기 | F01/F21/F23, 계약별 실제 변경 범위 |
| R3-018 | 연구/Shadow/Runner evaluator 공유 | 유지 | P2 / 검증 완료 | 단위/시점 adapter는 계산 중복과 구분 |
| R3-019 | 뉴스 _collect 반환 뒤 캐시 재조회/오류 은폐 | 재설계 | P2 / 대기 | 수집/저장/confirmed 합성 계약 보존 |
| R3-020 | 뉴스 BODY/RULE/AI의 서버 프로세스 결합 | 재설계 | P2 / 보류 | 기존 OPEN_ITEMS O19 승계, 중복 신규 과제 아님 |
| R3-021 | 콘텐츠 delta/no-op 저장·동기화 | 유지 | P2 / 검증 완료 | 이미 해결된 전량 반복 업로드를 재등록하지 않음 |
| R3-022 | 진단/owned thread·capture wrapper | 유지 | P2 / 검증 완료 | 실익 없는 wrapper와 구분, 오버헤드 미측정 |
| R3-023 | 남아 있는 host clock/fallback 시계 경계 | 재설계 | P2 / 대기 | 기존 time-basis-kst 원장에 연결 |
| R3-024 | 연구 결과/새 주도주 전략의 부분 연결 | 재설계 | P2 / 보류 | 기존 A03/주도주 미정 정책. 임의 매매 조건 생성 금지 |
| R3-025 | 앱/서버 시작·교체·종료 순서 | 유지 | P1 / 검증 완료 | 정적 수명 평가. 실제 재시작은 별도 |
| R3-026 | 직전 순위표 다섯 저장값 조회 통합 | 통합 | P1 / 검증 완료 | 기존 작업 승계. 연결5→1/SELECT5→2 |
| R3-027 | 순위표/테마 합계의 같은 cut 반복 계산 | 재설계 | P2 / 대기 | 계산 횟수·설정/분 경계 invalidation 측정 먼저 |
| R3-028 | 사용하지 않는 시간 import | 제거 | P4 / 대기 | 런타임 효과 없음. 독립 저우선 정리 |
| R3-029 | 테마 import revision과 실시간 stocks 전체 열의 결합 | 재설계 | P2 / 대기 | R3-003 조사에서 실제 export 동일/충돌 재현, 테마 필드·동시 편집 보호 검토 |

| R3-030 | API 교체 중 일지 수급 enrichment cache의 구 provider 결과 재삽입 | 재설계 | P1 / 대기 | R3-014/017/025; R3-031과 같은 writer 직렬 변경 |
| R3-031 | 일지 backfill 저장 예외와 잔여 queue의 종료 판정 누락 | 재설계 | P1 / 대기 | R3-014/025; R3-030과 같은 writer |
| R3-032 | bootstrap plain daemon 작업의 종료·restore lease 수명 누락 | 재설계 | P2 / 대기 | R3-011/021/025; 실제기동 추가검증 필요 |
| R3-033 | 첫 틱/거래일 변경의 GUI 동기 retention DELETE | 재설계 | P2 / 대기 | R3-025/027; 기존 retention/writer 경계 재사용 |
| R3-034 | Collector 실패 복원이 최신 reference pending을 덮음 | 재설계 | P1 / 대기 | R3-007; 계좌 entry 확장 별도재현 |
| R3-035 | TOP20 collector lock 안의 outbox/native 저장 await | 재설계 | P1 / 대기 | R3-036 기준 먼저; R3-006/007 |
| R3-036 | TOP20 두 flush caller의 같은 payload 중복 native 저장 | 통합 | P1 / 대기 | R3-007; R3-035와 동일 파일 직렬 |
| R3-037 | WebSocket sender 예외 뒤 Hub 구독 해제 누락 | 재설계 | P1 / 대기 | R3-025; Collector와 독립 수정 가능 |
| R3-038 | 분봉 작은 page ingest의 하루 전체 metadata/SOR read | 제거 | P2 / 대기 | R3-004/016; R3-039와 native 경계 조정 |
| R3-039 | 최근 거래일 API의 빈이력31회 직렬 read | 통합 | P2 / 대기 | R3-016/038; RAM-only day 계약 |
| R3-040 | manual 동일 요청의 중복 load와 event-loop DB read | 제거 | P2 / 대기 | R3-009; scope/멱등 검증 유지 |
| R3-041 | 운용 UI refresh의 Shadow 전량조회와 명령ACK/후속오류 결합 | 재설계 | P2 / 대기 | R3-017/024; 게시 authority 유지 |
| R3-042 | 계좌 owner 교체 후 Supervisor의 구 Runner 연결 잔류 | 재설계 | P1 / 대기 | R3-008/009/025; 자동재승인 금지 |
| R3-043 | 뉴스 stale claim의 옛 retry/finish가 새 실행 상태 갱신 | 재설계 | P2 / 대기 | R3-019/020; claim ownership/CAS |
| R3-044 | 뉴스 sync 중 로컬 편집의 거짓 clean ACK | 재설계 | P1 / 대기 | R3-021; R3-045와 sync 계약 직렬 |
| R3-045 | 테마 created_at만 달라 생기는 불필요 delta | 제거 | P2 / 대기 | R3-021/029/044; 완료표식·backup 호환 |
| R3-046 | generic research CLI 오류출력의 원요청 파일 덮어쓰기 | 통합 | P2 / 대기 | R3-017; 기존 모드 경로보호 재사용 |
| R3-047 | Runner 입력·체결·미체결·보호·시계·checkpoint 책임 결합 | 재설계 | P2 / 대기 | R3-009/017/018/025; R3-042 먼저 |
| R3-048 | 변경 prefix마다 전체 checkpoint 복사·직렬화 | 재설계 | P2 / 대기 | R3-009/025/047; per-prefix ACK 유지 |
| R3-049 | Risk 갱신에서 누적 history 반복 filter/sort/재계산 | 재설계 | P3 / 대기 | R3-009/018; 비용 추가계측 먼저 |
| R3-050 | async Supervisor의 동기 Runner 조립·status read | 재설계 | P2 / 대기 | R3-009/017/025/042; thread owner 주의 |

| R3-051 | 일지 snapshot별 같은 뉴스 재조회와 optional 저장 실패의 표시 전파 | 재설계 | P2 / 대기 | R3-031은 writer, 이번은 read-side; R3-059와 prepare 상태 직렬 |
| R3-052 | 복기 debounce 편집을 종료 경계에서 저장하지 않음 | 재설계 | P1 / 대기 | R3-025; R3-031과 별개 편집 dirty owner |
| R3-053 | 업데이트 순차 파일 교체 실패 뒤 혼합 버전 잔류 | 재설계 | P2 / 대기 | 기존 update planner/hash/manifest 보존 |
| R3-054 | catalog 실행 중 OCR 요청의 후속 callback 유실 | 재설계 | P2 / 대기 | R3-014/015의 single-worker는 유지 |
| R3-055 | 숨김 분리차트의 조회와 같은 source/interval 반복 집계 | 제거 | P2 / 대기 | R3-027은 별도 TOP20 caller; 052와 journal 소유 조정 |
| R3-056 | 이전 Drive 업로드 ACK가 이후 편집 dirty를 지움 | 재설계 | P1 / 대기 | R3-044와 다른 MainWindow/Drive owner; snapshot/ACK 검증 |
| R3-057 | 전략팩 이름 prefix로 출처·규칙 수를 오복원 | 통합 | P2 / 대기 | R3-059와 prepare 전달 계약 직렬 |
| R3-058 | 진입분 전체 거래대금을 매수 직전 충족 근거로 표시 | 재설계 | P2 / 대기 | 사후 복기와 당시 가용 입력 구분; 새 전략조건 금지 |
| R3-059 | 복기 prepare의 전체·회차·표시별 동일 draft 재조회 | 제거 | P3 / 대기 | R3-057 및051과 prepare 상태 조정 |
| R3-060 | 외부 시세 metadata 오류가 poll owner를 종료 | 재설계 | P2 / 대기 | R3-061과 같은 collector 직렬 |
| R3-061 | active 월물 수집 실패를 거래량0으로 롤 판단 | 재설계 | P2 / 대기 | R3-060; 입력결측과 실제0 구분 |
| R3-062 | condition 진단 저장 ACK가 단일 수신의 다음0B를 지연 | 재설계 | P2 / 대기 | 미실측: blocked-store fixture 먼저; R3-007/034와 소유 조정 |

| R3-063 | 연구 실행의 지연 실패·취소가 완료 상태를 덮어쓸 수 있음 | 재설계 | P1 / 대기 | 미해결·제품변경0; 상세9필드/기능 연결은 최종 감사 절 |
| R3-064 | 체결 스냅샷 재전달이 이미 보강한 뉴스를 비움 | 재설계 | P2 / 대기 | 미해결·제품변경0; 상세9필드/기능 연결은 최종 감사 절 |
| R3-065 | outbox 읽기 실패를 빈 상태로 삼아 기존 pending 파일 교체 | 재설계 | P1 / 대기 | 미해결·제품변경0; 상세9필드/기능 연결은 최종 감사 절 |
| R3-066 | real account event 종료 실패가 정상 close로 반환 | 재설계 | P1 / 대기 | 미해결·제품변경0; 상세9필드/기능 연결은 최종 감사 절 |
| R3-067 | 최대 낙폭 ppm을 낙폭 시점의 peak가 아닌 이후 최고자산으로 나눔 | 재설계 | P2 / 대기 | 미해결·제품변경0; 상세9필드/기능 연결은 최종 감사 절 |
| R3-068 | NXT 포함 사상고가의 캐시 증분 경로가 KRX만 조회 | 재설계 | P2 / 대기 | 미해결·제품변경0; 상세9필드/기능 연결은 최종 감사 절 |
| R3-069 | 기존 과거뉴스 스키마의 후행 열을 먼저 참조하는 index | 재설계 | P2 / 대기 | 미해결·제품변경0; 상세9필드/기능 연결은 최종 감사 절 |
| R3-070 | 제외종목 찾기의 부모 인자가 themes로 전달됨 | 재설계 | P2 / 대기 | 미해결·제품변경0; 상세9필드/기능 연결은 최종 감사 절 |
| R3-071 | JournalSync의 동일 자료 전량 왕복 | 제거 | P2 / 대기 | 미해결·제품변경0; 상세9필드/기능 연결은 최종 감사 절 |
| R3-072 | NAS-only AI의 수동 버튼과 실행 경로 가용성 불일치 | 통합 | P2 / 대기 | 미해결·제품변경0; 상세9필드/기능 연결은 최종 감사 절 |
| R3-073 | PC 뉴스의 동일 기사 재수신도 전체 article 행 UPDATE | 제거 | P2 / 대기 | 미해결·제품변경0; 상세9필드/기능 연결은 최종 감사 절 |
| R3-074 | 일지 summary·statistics가 같은 복기와 비용 분모를 반복 조회·계산 | 통합 | P2 / 대기 | 미해결·제품변경0; 상세9필드/기능 연결은 최종 감사 절 |
| R3-075 | 표시하지 않는 오늘 일지 페이지가 초마다 하루 봉을 복사·저장·재표시 | 재설계 | P2 / 대기 | 미해결·제품변경0; 상세9필드/기능 연결은 최종 감사 절 |
| R3-076 | 테마와 종목 저장소의 catalog 판단 중복 및 import 행별 조회 | 통합 | P3 / 대기 | 미해결·제품변경0; 상세9필드/기능 연결은 최종 감사 절 |
| R3-077 | 사용되지 않는 개별 테마 프로필 백업 경로의 종료 오류·중복 구현 | 제거 | P3 / 대기 | 미해결·제품변경0; 상세9필드/기능 연결은 최종 감사 절 |
| R3-078 | TOP20 장후 reconciliation coverage 2N SELECT | 통합 | P2 / 대기 | 미해결·제품변경0; 상세9필드/기능 연결은 최종 감사 절 |
| R3-079 | account-context/v4 clock projection의 삭제 범위 불일치 | 재설계 | P2 / 대기 | 미해결·제품변경0; 상세9필드/기능 연결은 최종 감사 절 |
| R3-080 | OCR 첫 이미지의 준비와 본처리에서 동일 predict 반복 | 제거 | P3 / 대기 | 미해결·제품변경0; 상세9필드/기능 연결은 최종 감사 절 |
| R3-081 | 차트 paint의 전체 원본 MA/좌표 재계산 | 통합 | P3 / 대기 | 미해결·제품변경0; 상세9필드/기능 연결은 최종 감사 절 |
| R3-082 | 기본설정의 unreachable 구형 layout | 제거 | P4 / 대기 | 미해결·제품변경0; 상세9필드/기능 연결은 최종 감사 절 |
| R3-083 | R3 이후 책임·비용·기능 연결 퇴행 방지 | 통합 | P1 / 진행 | 기반 구현·관련 로컬 검증, 개선별 계약 축소/확장·hosted CI 확인은 계속. 다음 제품002+023 유지 |

## 항목별 근거·변경·검증 계약

### R3-001 — 분봉 권한/품질의 중복 메타데이터 읽기

- 경로: F05. `presentation/main_window.py::_on_trade_tick` → `AppController.flush_minute_cache` → `MarketCacheWriter._save_minute` → `infrastructure/persistence/minute_bar_repository.py::_upsert_many`.
- 원인/근거: 같은 `BEGIN IMMEDIATE` 안에서 `_has_complete_minute_authority`가 `(kind, code, minute)`의 completeness/origin/source를 SELECT한 직후, `_upsert_many`가 같은 행의 completeness/origin을 다시 SELECT한다. 두 읽기 사이 DB 변경은 없다. QUERY 저장이나 gap/empty coverage의 별도 권한 검사와 혼동하지 않는다.
- 실측/가설: 20종목 열린0B 봉의 **SELECT40**, 연결/BEGIN/COMMIT 각1, 반영40(값20+metadata20). 장중 실행빈도·wall time·lock wait 개선치는 미측정이다.
- 방법/범위: 이 경로에서는 기존 행3필드를 한 번 읽고 권한/품질 판단에 공유한다. 권한 predicate는 기존 helper와 공유한다. RAM cache·별도 계층·새 transaction·writer·schema를 추가하지 않는다.
- 효과/위험: 해당 비권한0B 봉당 SELECT2→1. complete query/backfill/NAS realtime 보호, partial/missing 유지, 동일 key 중복 입력의 순차 상태를 깨뜨리면 데이터 손상 위험이 있다.
- 의존/완료: 독립. 수정 전/후 fixed clock의 값·metadata 전체 동일, SQL40→20, 기존 BEGIN IMMEDIATE/COMMIT1·rollback/재시도 유지, Qt signal→writer→DB→재조회 통합과 직접 관련 회귀. 종료/구독·0B 집계 기준은 유지한다.
- 변경: `_upsert_many`의 한 SELECT 결과를 authority/quality 양쪽에 재사용한다. 기존 gap/empty coverage의 `_has_complete_minute_authority`도 같은 순수 predicate를 호출한다. 같은 key가 한 batch에 여러 번 나오면 매회 현재 transaction의 metadata를 읽어 앞선 갱신을 반영한다. 호출 경로/핵심 파일 수·writer/시계/transaction은 그대로이며 predicate는 같은 파일 안에 있다.
- 결과: `cost-before.json`/`cost-after.json`의 **SELECT40→20**, 연결/BEGIN/COMMIT 각1·반영40 유지, **분봉 모든 열/metadata 모든 열이 동일**. 기존 세 모듈 분리 실행55건 → 새3건 포함58건 통과. `test_minute_bar_repository`23 / `test_market_cache_writer`5 / `test_app_controller`30. 네이티브 종료코드0·worker/자식 전체 종료 확인·소유 임시 폴더 정리 완료.
- 통합 근거: AppController의 `test_close_waits_for_late_trade_to_reach_app_buffers_and_real_database`, `test_main_window_realtime_signals_feed_app_buffers_and_saved_signal_keeps_repair_receiver`, `test_final_minute_failure_blocks_close_and_retries_without_duplicate_commit`는 실제 Qt 수신/후속·실제 writer·DB 값/실패 재시도/종료를 확인한다. 외부 Kiwoom·실제 NAS/PG·실제 앱/장중·전수 회귀는 미검증이다.
- 최초 실패: 세 모듈 합동 baseline worker가 AppController 첫 GUI 검사에서 native exit `3221226505`로 종료돼 결과 JSON을 만들지 못했다. `r3-001-before-tests/execution.json`/로그/임시 자료를 보존했다. 같은 소스의 모듈별 독립 worker는 모두 통과했다. QCoreApplication을 만드는 writer 검사와 QApplication이 필요한 UI 검사의 합동 구성을 확인했으나 native 실패의 내부 원인은 확정하지 않았고 제품 결함으로 수정하지 않았다.
- 상태/commit: **검증 완료 / 미커밋**. `before-test_*`, `after-test_*`의 result/execution/log에 최초·재실행을 구분했다. 실제 운영 성능 개선치나 전체 R3 완료로 확대하지 않는다.

### R3-002 — 한 가격 캐시 job의 다중 transaction

- 경로/근거: F06, `MarketCacheWriter._save_prices`가 `StockRepository.update_last_prices/update_last_market_caps/update_intraday_highs`를 순서대로 호출하고 각 메서드가 독립 connect/commit한다. fallback `AppController.flush_price_cache`도 같은 순서다.
- 실측: 20종목 세 값이 있는 job에서 연결3/BEGIN3/COMMIT3·반영60. 운영 빈도·디스크 대기 미측정. 값 중 하나가 비면 해당 메서드는 저장을 생략한다.
- 권장: 기존 저장소가 같은 cache job의 SQL을 한 업무 transaction으로 처리하게 통합한다. 새 service/writer를 만들지 않는다. 기존 timestamp/동기화 의미를 먼저 확인한다.
- 효과/위험: 묶음당 연결/commit 감소 예상. 중간 성공 뒤 전체 실패 재전달이라는 현재 의미가 원자 rollback으로 바뀔 수 있으므로 최신 실패 병합·부분 입력·종료 drain·재시작을 검증한다.
- 의존/완료: R3-001 뒤 첫 대상. writer/caller/조회·재시도 전후 결과와 connection/COMMIT, 첫/중간/마지막 SQL 실패 및 종료 시 보존을 비교한다. 새 기록 경로가 생기면 기존 registry도 동반 확인한다.
- 변경: `StockRepository.update_price_cache`가 기존 세 SQL과 기존 고가35일 정리를 `sqlite_transaction` 한 번으로 처리한다. 원래 개별 메서드는 같은 구현을 사용하는 standalone/legacy 호환 진입점이다. 실제 writer와 실제 StockRepository를 가진 writer 없는 호환 경로가 batch를 사용하며, 일부 메서드만 제공하는 외부 lookup은 기존 분기를 보존한다. 새 writer/파일/DB/schema·반복 주기·동일 값 차단은 없다. 저장 책임과 기존 실패 신호/최신값 병합·drain은 같은 owner에 유지한다.
- 구조 비교: controller→writer→repository의 업무 owner3개와 비동기 전달 깊이는 유지한다. 저장 본문 이해에는 기존 `sqlite_connections.py`의 transaction/close helper1파일 참조가 추가되지만 새 service/클래스/파일은0이며 세 개별 저장 호출을1개로 줄인다. helper는 rollback/close를 소유하는 기존 구현을 재사용한 것으로 단순 전달 계층을 새로 넣은 것이 아니다.
- 시계: AppController가 기존 `ranking_now`를 writer에 주입하고, batch는 caller의 시각을 `as_kst` ISO(+09:00)로 세 timestamp 필드에 명시 저장한다. `stocks.last_price_updated_at`, 시총의 `stocks.updated_at`, `intraday_highs.updated_at` 대상이며 기본정보 확인시각은 바꾸지 않는다. 별도 TR/HTTP/polling 없음. standalone/legacy 메서드·clock 없는 격리 writer만 공통 KST fallback을 사용하며 키움 동기화 완료로 보고하지 않는다. 기존 저장 자료를 일괄 변환하지 않는다.
- 실측: 현재 소스 재측정과 `price-comparison.json`에서 20종목 **연결3→1/BEGIN3→1/COMMIT3→1/SELECT0 유지/반영60 유지**. 이전 세 함수(고정 commit6cf3d4c)와 새 함수를 같은 fixture·같은 순간의 통제된 UTC/KST 시계로 비교해 숫자·기본정보 확인시각·유통시총·고가 MAX/정리·timestamp 실제 순간이 일치했다. 실패 시 앞선 필드가 부분 commit되는 동작은 전체 rollback으로 바뀌며 의도한 원자성 변경이다.
- 검증: 변경 전15+5+30=50건, 변경 후 저장소19/writer7/controller32=58건과 순위 화면52/기본정보 서비스2=54건, 총 **112건 통과**(실패/오류/skip0). 실제 SQL trigger로 각 단계/정리 실패를 재현했고, 실제 child 프로세스의 commit 전후 종료·재시작 재적용, orphan 고가/비어 있는 부분 배치, float_ratio null, GUI 비차단 enqueue, 최종 실패 복구와 최신 peer 보존, 교체 loader 시계와 실제 저장/readback을 검증했다. worker/자식 종료·임시 자료 정리 확인. 근거는 `data/diagnostics/refactor-r3-20261018/r3-002/`.
- 이후 공유 소스 변경: 위112건 뒤 다른 시계 보완이 같은 작업 트리에 반영됐다. `current-linked`의 writer7건 중2실패/controller33건 중5실패를 보존했다. 실제 분봉 writer가 aware `available_at`과 naive KST `bar.minute`를 비교해 저장 실패/종료 재시도를 일으키는 원인을 로그와 두 비교문으로 확인했다. 비교 시 `as_kst(bar.minute)`만 적용해 기존 거래일/분 key·metadata 권한·값·transaction은 유지하고, injected UTC clock+legacy naive 봉의 이전/현재분과 metadata 유/무 회귀1건을 추가했다. 이 보완을 R3-023 전체 완료로 확대하지 않는다.
- 재검증 기록: `final-linked`는 stock19/writer7/controller33/MainWindow53/기본정보2 통과, minute24 중1실패다. 이1건은 새 TOP20 repair fixture가 2035년 clock·2026년 자료/전체 cache count1을 혼용한 기대값 불일치다. 실행 중 MainWindow/controller 검사 등 공유 소스도 바뀌어 해당 결과를 최종 동일 소스 통과로 쓰지 않는다. 다른 작업에서 fixture clock/대상 날짜 조회가 바로잡힌 현재 소스의 재검증은 `stable-linked`에 별도 보존한다.
- 최종: `stable-linked`의stock19/minute24/writer7/MainWindow53/기본정보2 통과. controller는실행 중 공유 교체 로직 보완 뒤 불완전 Mock cache에서 `Mock.keys() returned a non-iterable`이 발생해 오류창/180초 timeout으로 끝났고 프로세스 전체 종료·자료 보존을 확인했다. `runtime-fixture-diagnosis`가 실제 오류 메시지를 확인했다. 해당 검사만 실제 `EntrySnapshotWriter`+메서드 spy와 예상치 않은 warning 부재 검사로 바꿨다. `controller-final` **35건 통과/실패·오류·skip0/소스 변경0**이다. 최종 고유 관련 합계 **140건**이며 각 통과 worker/자식 종료·임시 자료 정리를 확인했다. MainWindow 변경은 해당 worker 시작 전임과 최종 hash 동일을 대조했고 PC 핵심6파일의 현재 hash를 `verification.json`에 고정했다. 실행 중 변경/timeout을 통과로 덮지 않는다.
- 이후 공통 시계 연결: 다른 작업이 `MarketCacheWriter.run`의 minute/daily repository에도 기존 `self._now`를 주입한2줄을 추가했다. 이전140건은 해당 수정 전 근거로 보존하고, 변경 후 실제 writer7건+Qt 연결/분봉 이력/가격·종료5건을 `post-clock-review/user`에서 재검증했다. **12건 전부 통과/소스8파일 변경0/자식 종료·임시 자료 정리**다. 기존140건의 부분 재실행이며152고유검사로 합치지 않는다. 최초 sandbox TEMP 접근 실패는 별도 보존했다. 독립 읽기 전용 검토에서도 R3-002의 새 회귀·데이터 유실·불필요 계층 findings는 없었다. 가격 batch/SQL/주기는 동일하다.
- 연결/한계: F01/F05/F06/F08/F10/F23/F24를 같은 변경에서 갱신했다. 전체 순위 지연·물리 I/O/실제 운영 lock wait·장중/설치 앱·NAS/PG는 미측정이다. RAM 대기열의 crash 내구성은 이전과 같다. 운영 자료/계좌/주문·전수 회귀/새CI/배포/commit/push 없음.
- 상태/commit: **검증 완료 / 미커밋**. 다음은 R3-003. 동일 시총 재적용20건의 UPDATE는 그대로이며 R3-003의 효과로 중복 보고하지 않는다.

- **2차 정밀 감사 보완(2026-10-18):** 단일 transaction 구현·기존140건/후속12건 부분 재실행 근거는 보존한다. 다만 `on_price_cache_failed`가 payload의 `_trade_date`를 버리고 code별 고가만 pending에 합치며 `flush_price_cache`는 현재 거래일을 다시 붙인다. 실제 controller→실제 StockRepository→임시 SQLite의 날짜 경계 재현에서 10/16 고가120이 10/17에 저장됐다(`date-retry-measurements.json`). R3-023의 미검증 가설이 재현된 것이다. **현재 상태는 진행(후속 날짜 계약)**으로 재개한다. batch SQL을 재구현하지 않고 원거래일 고가·현재가 수신시각을 구분한 실패 재전달과 실제 writer/종료 경계를 다음에 검증한다. 아래 과거 ‘다음 R3-003/검증 완료’는 그 당시 transaction 범위의 기록이다.

### R3-003 — 변하지 않은 시총/가격 쓰기

- 경로/근거: F06, MainWindow는 유효 시총·현재가를 매0B마다 pending에 넣고 StockRepository는 조건 없는 UPDATE를 한다. 고가는 이미 `previous_high`보다 클 때만 enqueue하므로 같은 문제로 재등록하지 않는다.
- 실측: 같은 시총20개 재적용도 UPDATE 반영20/COMMIT1. `last_price_updated_at`과 `stocks.updated_at`은 실제 수신/변경 중 무엇을 뜻하는지 별도 확인이 필요하다. 물리 쓰기량/디스크 원인은 아직 모른다.
- 권장/효과: freshness 역할이 없는 값만 SQL no-op으로 줄인다. ACK 전 RAM 캐시에 저장 완료로 표시하거나 재조회 SELECT를 추가하지 않는다.
- 위험/의존/완료: R3-002의 transaction 의미와 별개로 timestamp/기본정보 갱신·시총소비를 추적한다. 동일 값·변경 값·float_ratio 후행 변경·시총결측·실패재시도·재시작을 검증하고 값/확인시각/반영행을 대조한다.
- 조사/측정: `data/diagnostics/refactor-r3-20261018/r3-003-parallel/REPORT.md`, `measurement.json`.진단 SQL 후보는 cap 또는 현재 DB의 float_ratio로 계산한 유통시총이 다른 행만 null-safe 조건으로 UPDATE한다. 동일cap20의 SQLite `total_changes20→0`, UPDATE시도20/연결1/SELECT0/COMMIT1은 그대로다. 가격은 같은 값의 후속 수신도 `last_price_updated_at`을 유지 갱신하고, `fundamentals_updated_at`은 기본정보 당일 gate이므로 유지한다. 실제 재오픈 reader `load_last_prices/load_fundamentals/load_ranking_cache/fundamentals_to_refresh`의 값·가격 확인/기본정보 시각은 동일했다. 물리 WAL/장중 비용은 미측정이다.
- 연결/계약: `stocks.updated_at`을 명시 이름으로 읽는 freshness 소비자는 찾지 못했으나 ThemeBackupService의 `SELECT *` revision hash가 간접 소비함을 확인해 '소비자 없음' 가설을 반증했다(R3-029). 후보는 같은 cap의 일반 `updated_at`과 테마 revision/충돌 결과를 바꾸므로 관측 가능한 변경이다. 해당 필드가 수신 확인시각이라는 명시 계약은 아직 확인되지 않았다. 이 사실을 자동 차단 조건이나 새 TTL로 만들지 않고 최소 구현 때 의도/회귀로 명시한다.
- 검증/위험: SQL fixture5조건(동일cap/동일가격 후속시각/NULL·비율 후행·부분배치/NULL·0비율/rollback·retry·새연결)과 실제 테마 dispatcher→service→import2조건, 관련 기존3건 통과. 최종11파일 hash 안정이다. 최초 fixture/TEMP 실패를 보존했으며 진단 후보만 검증한 것으로 제품 no-op 구현 완료가 아니다.
- 다음 최소안/완료: 가격 확인시각은 유지하고 cap 조건부 UPDATE만 기존 batch 안에 적용하는 안을 우선한다. cap값만 비교해서는 후행 float_ratio/유통시총 보정이 누락되므로 파생값도 비교한다. 새 SELECT/RAM ACK 캐시/계층은 필요 없다. 실제 writer의 실패·최신값 병합/종료와 R3-029의 테마 충돌/편집 보호를 관련 회귀로 검증한다. R3-029 전면 해결을 필수 선행 차단으로 두지 않는다.
- 상태/commit: **대기 / 없음**. 독립 조사/진단 측정 완료·제품 미변경. F06/F10/F22/F24의 확인 범위/미검증을 갱신했다.

### R3-004 — RAM Shadow 분봉의 하루 전체 범위/직렬 read

- 경로/근거: F08/F13, `MarketThemeShadowRuntime._market_minute_key/_market_minutes` → `collector.load_live_minute_bars` → store는 종목/거래일 전체를 읽고 runtime이 최근10분만 선별한다. wanted 최대200개를 순차 await한다. 분/commit token cache는 이미 있으므로 “매틱 전체 조회”로 과장하지 않는다.
- 실측/가설: 운영 row/연결/대기 미측정. 실제 Runtime→Collector→SQLite reader의20종목·450분 KRX/NXT fixture에서 native main 반환9000/overlay후9001/최종채택200행. 같은 key 재호출 추가 연결0. 진단 SQL range prototype은 native/반환200행·모든 최종 값/metadata 동일, 연결20/SELECT22 유지다. 단회127.122→59.389ms는 운영 효과가 아니다.
- 권장/효과: 필요한 시간 범위·여러 종목 read를 기존 reader 경계에서 좁히는 안부터 비교한다. 무제한 gather나 새 pool을 먼저 넣지 않는다.
- 위험/의존/완료: late commit, inflight/retry/RAM delta overlay, venue/당일·최근10분/결측 상태 보존. R3-005와 공유 가능성, PG 전용 fixture에서 결과/행수/연결/대기·후행 봉 반영을 비교한다.
- 병렬 조사: `data/diagnostics/refactor-r3-20261018/r3-004-parallel/FINDINGS.md`, `cost-results.json`과 fixture1건/기존 연결10건 통과 기록. 기존 `load_minute_bars`의 optional half-open 범위를 backend/Protocol/Collector에 전달하고 Runtime만 opt-in하는 최소안을 제안했다. retry/inflight/pending 동결·lost ACK/op dedup·query authority·단일 snapshot, F08 full-day 기본값과 midnight/venue/date·capture signature 보존이 필수다. 새 pool/무제한 gather/reader 계층을 추가하지 않는다.
- 구현/구조: 기존 SQLite/PG reader와 두 Protocol에 `start_minute` 포함/`end_minute` 제외 optional 인자를 추가했다. Collector가 기존 동결 delta에도 같은 범위를 적용해 같은 native snapshot/overlay로 전달하고 Runtime만 기존10분 window와 KST 거래일의 교집합을 opt-in한다. 무범위 caller는 새 kwargs 없이 기존 당일 전체를 읽는다. Runtime→Collector→native 세 실행 owner/파일·비동기 호출 깊이·cache/commit token/owned close는 그대로며 신규 service/클래스/파일/pool/index/task는0이다.
- 제품 실측: `cost-after-product.json`은 proxy 없이 실제 product Runtime→Collector→SQLite reader에서 native SELECT9000→200/overlay후9001→200/최종채택200값·metadata hash 동일을 확인했다. 연결20/SELECT22·같은 key 추가 연결0 유지. 기존 index의 실제 EXPLAIN은 code/day 탐색에서 code/day/minute 범위 탐색으로 좁아졌다. 단회151.798→93.727ms는 호스트 fixture 관찰이며 운영 성능 개선율로 쓰지 않는다. PG 실제 planner/connect/wait는 미측정이다.
- 기능 검증: **고유58건 통과**. Collector34+제품비용1, 관련16개 중15통과/선택복원1실패 후 해당 helper9+실제appbinding1을 재검증했다(중복 제외). query authority·op dedup/lost ACK·부분/빈 범위·metadata, PG SQL/REPEATABLE READ 선언, 자정/UTC/2초 fence·8소비자 공유/TTL·보호 판단 비차단/close·full-day API/RAM/COMBINED와 capture 호환을 포함한다. source4/test4 검사 전후 해시 동일, 성공 worker/자식 종료·TEMP 제거 확인. `IMPLEMENTATION.md`, `product-core-v1-*`, `product-related-v1-*`, `selection-recheck-v1-*`, `final-source-check.json`에 기록했다.
- 최초 실패/보완: 선택 PUT422는 새 reader 호출0회/고정cut시점 snapshot0개였다. 병행 시계 변경 후 기존 helper의 time patch가 writer의 주입 시계에 적용되지 않아 자료가 고정cut 이후로 저장된 원인을 `selection-failure-evidence.json`으로 확인했다. helper에 기존 `_now_provider`로 같은 고정 시계를 주입하고 실패/직접 연결만 재검증했다. 별도 probe의 TestCase import 노출로 추가 실행된 실패도 보존하며 성공으로 섞지 않았다. 제품 시계 우회/정책은 추가하지 않았다.
- 정적 계약: 기존 baseline의 reader3서명·Collector의 동일 callable 중복2→1만 제한 갱신했다. 이후 site/candidate/forward delta0·parse/stale0이다. 기존 source/audit9건은 **8통과/1실패**로, 남은 차이는 병행 `load_news_history(before_revision_id)`3서명뿐이다. 뉴스 서명은 승인/흡수하지 않았고 전체 정적 gate 통과로 보고하지 않는다. `query-store-contract-tests-*`, `query-store-after-r3-004-baseline.json`, `query-store-baseline-r3-004-delta.json`을 보존한다.
- 상태/commit: **검증 완료(로컬 기능) / 미커밋**. F08/F13/F25를 동반 갱신했다. 실제 PG/장중/NAS/PC 관찰창·전수 회귀/CI/배포는 미검증이다. R3-005의 다른 cut/window나 R3-006 pool/batch는 적용하지 않았다. 전체 정적 감사의 뉴스-only 잔여는 R3-016/019에 연결한다.

- 후속 공유 fixture 변경: 위58건 검증 후 candidate 테스트 파일이 다른 작업에서 다시 바뀌었다. 제품4파일은 동일하며 관련 선택복원 helper9+실제 app binding1+자정/fence1만 `post-shared-fixture`에서 다시 실행했다. **11건 통과/제품4+관련test1 해시 변경0/worker·자식 종료·TEMP 정리**다. 기존58건의 부분 재실행이며 변경 후58건 전체를 재실행했다고 보고하지 않는다. 첫 `final-source-check.json`과 후속 `post-shared-fixture/source-check.json`을 구분해 보존했다.

- **R3-083 성능 퇴행 방지 보강:** 기존 native reader 통합검사1개에 무제한 조회 양성 대조400행(`[20]*20`) 뒤 실제 Runtime→Collector→SQLite 반환200행(`[10]*20`) assertion을 추가했다. 8소비자·100회 조회·close의 추가 read0/무쓰기도 유지했다. 이번 관련16건 통과이며 과거9000→200 전체 fixture를 재실행했다는 뜻은 아니다. `data/diagnostics/structure-regression-20261018/r3-costs-user-v1/`에 현재 실행을 보존하며 F08/F13/F24를 동반 갱신했다. 전체 운영·PG 수락은 그대로 미검증이다.

### R3-005 — 주도주 후보 기준선의 일전체 read

- 경로/근거: F12, `CandidateMonitor._refresh_document_observations` → 종목별 `store.load_minute_bars` → `document_breakout_reference`. 날짜/분/commit token별 cache가 있고 0B callback 자체에는 DB read가 없다.
- 실측/가설: 일전체 read 후 계산 범위를 좁히는 구조 확인, 실제 부하 미측정. R3-004와 같은 reader를 쓰지만 cut/2초 fence/정책 window는 다르다.
- 권장/효과: 정확히 필요한 구간을 읽게 개선하되 입력 cut이 바뀌거나 source가 교체된 뒤 결과를 적용하지 않는 현재 검사를 보존한다.
- 위험/의존/완료: R3-004 reader 검토 후 별도 fixture. 다른 종목·정책 window·시간 이동·commit 갱신·source 교체/종료 동안 동일 baseline과 read 비용을 비교한다.
- 상태/commit: 대기 / 없음.

### R3-006 — PG 연결/flush 지연의 원인 분리

- 경로/근거: 모든 backend mixin → `database.PostgresQueryStore._connect → psycopg.connect`. Collector flush, TOP20 dataset, 뉴스/Shadow는 독립 transaction을 소유한다. 공통 관측 wrapper가 있고 pool은 없다.
- 실측/가설: 현재 연결 site 승인60/60만 확인. 실제 connect/query/commit/wait owner·순위 지연의 비율은 미측정이다. per-call 연결 자체를 장애 원인으로 확정하지 않는다.
- 권장/효과: 기존 단계시간/wait 계측으로 active 경로를 먼저 재현하고, 필요할 때만 pool/업무batch를 비교한다.
- 위험/의존/완료: 격리 PG에서 독립 commit/rollback·advisory lock/해제·동시 reader/프로세스 종료·자격증명·리플레이 격리를 검증해야 한다. 실제 wait 증거 전 구조 변경 보류.
- 상태/commit: 보류 / 없음. 기존 COMMON_DB_ACCESS_OBSERVABILITY_REVIEW/KIWOOM_STORAGE_WRITE_AUDIT 승계.

### R3-007 — 중앙 직렬화/후처리의 유지 판단

- 경로/근거: broker `_run`은 TR 우선 queue, `_run_persistence`는 후처리 queue다. Collector `_flush_serialized`는 owned task+flush lock, 종류별 pending/retry 복원과 commit 뒤 완료 상태를 소유한다.
- 사실/가설: 이미 TR 송신과 DB 후처리를 분리했다. “DB 때문에 broker worker 전체가 항상 직렬 대기”는 현재 구현에 맞지 않는다. 원호출 future가 저장 ACK를 기다리는 경로와 전송 queue를 구분해야 한다.
- 방법/효과: 유지. 운영 병목의 원인 비율은 R3-006에서 계측한다. 이미 고정 fixture로 원인을 확인한 R3-034~036은 각 완료 기준에 따라 별도로 진행할 수 있다. 잠금/transaction을 통째로 합치거나 제거하지 않는다.
- 위험/완료: retry에서 최신값/원본/분봉 finalization 순서·close drain이 핵심이다. 정적 수명 책임 확인 완료, 이번 PG/장중 보존 검증 없음.
- 상태/commit: 검증 완료(유지 판단) / 없음.

- **2차 정밀 감사 보완(2026-10-18):** broker 송신/후처리 task 분리·Collector flush_lock/op ID·ProgramSnapshotWriter owned save는 유지할 실제 수명이 있다. 그 안의 reference 최신값 복원(R3-034), TOP20 잠금 범위/중복 flush(R3-035/036)는 각각 재현됐다. 유지 판정은 이 내부 경로의 완료를 뜻하지 않는다.

### R3-008 — 자동복원 스캔의 주기적 조회

- 경로/근거: app가 같은 store/mock_owner/server_now로 Supervisor를 조립한다. `_restore_loop/_restore_once`는 시작 즉시/회차 후 최대2초 대기하며, owner의 RAM `_bundles` 중 binding/account_queries가 게시되고 runner가 없는 계좌만 영속 control을 조회한다. 모든 휴면/과거 DB 계좌 스캔이 아니다. retry_after/이미 실행 중 runner 제외는 존재한다.
- 실측/가설: 실제 supervisor+fake owner/repository/runner11조건에서 binding 없음30scan=0control calls, 미설정/STOPPED3binding×30scan=90calls, 기존 runner30scan=0calls. 외부 None→RUNNING/새 supervisor/기동 후 binding 게시의 다음scan은3calls+settings1(스캔/activate 최신확인/status 각각). PG SQL/latency·운영 binding 수는 미측정이다.
- 판단/방법: **현재 polling 유지**. owner on_change는 app.state/position listener 갱신이며 supervisor 알림은 없다. SQLite/PG control CAS에도 외부 change channel이 없다. negative cache나 event만으로 치환하면 외부 process 변경과 기동 후 게시 binding 발견을 잃는다. 임의 간격/TTL·승인 조건을 추가하지 않는다. 효과가 입증되면 외부 변경 보존 또는 같은 경계 batch 가능성을 검토한다.
- 위험/의존/완료: R3-009 보호 경계 보존. cold start·다른 프로세스 control 변경·owner교체·실패재시도·종료를 검증하고 idle/active 조회량을 비교한다. 임의 polling 간격 변경으로 숨기지 않는다.
- 검증: `data/diagnostics/refactor-r3-20261018/r3-008-parallel/REPORT.md`, `measurement.json`. 11조건과 기존 supervisor3건 통과. 기존30초 retry 이전0재조회/기한 후 재시도·orders false, double start/close·owned drain을 확인했다. admission/recovery/runner는 fake, 실제 FILL/Risk/주문·PG/NAS 재시작은 미검증이다. sandbox asyncio 초기화 대기와 중단 기록은 보존하고 사용자 문맥의 exit0/잔여 프로세스0만 성공으로 기록했다.
- 상태/commit: **보류 / 없음**. R3-009 최신 승인 경계는 유지하며 외부 변경 보존과 운영 비용 증거 확보 후 재개한다. F17/F18/F24를 검증 범위에 맞춰 갱신했다. 제품 변경 없음.

- **2차 정밀 감사 보완(2026-10-18):** 외부 control·늦은 binding 발견 역할과 polling 보류는 유지한다. 현재 runner가 있다는 이유만으로 새 bundle과 identity가 일치하는지는 확인되지 않았고 실제 owner 교체에서 불일치가 재현됐다(R3-042). polling 제거와 owner 조정 복구는 다른 작업이다.

### R3-009 — 계좌/Risk/control 재검증·주문 계층 유지

- 경로/근거: 자동 신규 주문은 Runner/monitor.refresh_recovery → stored control/Risk·dispatch_from_risk → Runtime.submit_automation_intent/lock·lease → OrderLifecycle/native intent claim으로 간다. Gateway는 manual 명령과 자동 취소의 refresh/owned command에 재사용한다. FILL만 position/cooldown 상태를 소유한다.
- 사실/가설: 반복처럼 보이는 조회 중 일부는 STOP/control revision·동시 계좌 변경·취소 직전 체결 fence다. RAM 관측을 영속 승인과 동등하게 취급할 수 없다. 기존 status/preflight no-op 개선도 이미 있다.
- 방법/효과: 유지. 실제로 같은 보호 시점의 중복임이 입증되는 부분만 R3-010 등에서 좁힌다.
- 위험/완료: stale 승인/중복 주문/UNKNOWN 자동 재송신 위험. 정적 책임 검토 완료, 실제 주문·모의 계좌 수락은 하지 않았다.
- 상태/commit: 검증 완료(유지 판단) / 없음.

- **2차 정밀 감사 보완(2026-10-18):** Gateway의 인증 명령/drain, Runtime의 불변 계좌/run·lease/operation lock, Lifecycle의 UNKNOWN/멱등/FILL, native 같은 transaction의 control/claim fence는 유지한다. 내부 중복 load R3-040, owner 연결 R3-042, Runner 결합 R3-047, checkpoint 구성 R3-048, Risk 재계산 R3-049, async 조립 R3-050은 별도 미완료다. latest Risk/control 재검증은 같은 안전시점의 중복으로 입증되지 않았으므로 삭제하지 않는다.

### R3-010 — 불변 문서 저장 경계

- 경로/근거: `ForwardEvaluationRepository._save_immutable`은 load_document → upsert_documents → load_document를 하고 본문 충돌과 ACK를 확인한다. `save_stage_revision`은 선행 existing 확인도 한다.
- 사실/가설: 신규 일반 저장에 read2/write1 호출, stage 경로에는 추가 read가 있다. 실제 SQL/PG 시간·동시 충돌 안전성은 이번 미측정. 이전 R2의 좁은 lineage read 최적화와 다른 쓰기 경계다.
- 권장/효과: 불변 insert/동일값 no-op/충돌 결과와 ACK를 native transaction에서 반환하는 계약을 비교한다. 마지막 확인 read를 삭제하는 정도로 끝내지 않는다.
- 위험/의존/완료: 두 backend의 동시 다른 문서·같은 문서·ACK실패·commit 전후 종료, stage CAS/기존 gate/승인 lineage를 대조한다. schema/ownership 변경이 필요하면 이 ID 안에서 범위를 다시 정한다.
- 상태/commit: 대기 / 없음.

- **2차 정밀 감사 보완(2026-10-18):** 이미 저장된 immutable 문서의 early-return/no-op은 존재한다. `ForwardEvaluationRepository.save_mock_automation_spec`는 profile 확인 및 binding 전체 history 읽기→profile/env 필터→최대 revision 후 immutable 저장을 한다. `_validated_risk_metrics→_load_context`의 admission/recovery 재조회도 본문에서 확인했으나 중간 STOP/Risk writer가 있어 동일 안전시점 중복인지는 미검증이다. 현재 binding 범위 축소와 immutable context 재사용을 기존 항목의 세부 조사로 남기며 마지막 ACK/control read는 기계적으로 제거하지 않는다. 완료 시 scope 변경/동시 immutable/ACK 유실/STOP·Risk 갱신/native claim 및 backend parity를 대조한다.

- **최종 잔여 감사 보완:** 실제 native SQLite의 read→upsert 사이 두 writer를 겹치면 둘 다 성공하고 B 내용이 A로 덮인다(`root/observations.json:native_forward_immutable_interleave`). 기존 atomic immutable 계약의 실측 보강이며 새 ID 없음. PG Shadow native table은 선행SELECT→INSERT DO NOTHING에서 첫 값 보존/다른 내용 loser silent ACK가 가능한 별도 SQL 경로다(`server/REPORT.md:S-F01`, PG 미재현). 각 backend/table의 conflict/ACK를 따로 검증한다. 근거 디렉터리: `data/diagnostics/refactor-r3-final-audit-20261018/`. 제품 변경·해결 검증·새 commit 없음.

### R3-011 — 설정값과 revision의 no-op

- 경로/근거: `SettingsRepository.set_many`는 모든 입력을 settings와 central_setting_versions에 쓰고 cache를 commit 후 갱신한다. get은 RAM cache가 있으므로 매호출 DB 재조회 문제는 없다.
- 사실/가설: 동일 값도 두 UPSERT·version 변경. 실제 hot 호출/쓰기량은 미측정이고 “값 동일”과 “사용자의 새 동기화 의사”가 같은지는 미확정이다.
- 권장/효과: 소비자인 중앙 동기화/복원 계약을 확인하고 의미 없는 같은 값 갱신만 줄인다. 실패 전 cache 갱신 금지.
- 위험/완료: 다른 프로세스 변경·cache invalidation·mixed-offset revision/사용자 편집 보호·실패 rollback을 검증한다. R3-012와 timestamp/변경셋 의미 비교.
- 상태/commit: 대기 / 없음.

- **2차 정밀 감사 보완(2026-10-18):** `SettingsRepository.get`의 DB fetch→finally close→cache 삽입과 `clear_cache` 사이 경합을 실제 SQLite로 재현했다. 다른 connection이 new를 commit하고 clear_cache한 뒤 지연된 old read가 cache를 다시 채워 다음 get도 old다(`settings-measurements.json`). 동기화의 write→clear 경로와 연결되나 전체 remote sync는 미실행이다. 같은 설정 UPSERT/revision 의미와 별개로 기존 cache의 무효화 세대/삽입 경계를 바로잡아야 한다. 실제 account native/owner의 same-settings no-op은 이미 존재해 재구현하지 않는다. 첫 진단 v1의 WinError32는 진단 fixture connection 미종료였고 명시 close한 v2만 성공 근거다.

- **최종 잔여 감사 보완:** MainWindow._save_window_geometry5138~5141의4개 settings.set와 AlertSettingsDialog의4개 set을 추가 확인했다. 값/version no-op·기존 geometry debounce·프로세스 간 cache invalidation과 별개로 batch 가능성을 기존 항목에서 평가한다. 이번 실제 UI 저장 빈도/물리 I/O는 미측정. 근거 디렉터리: `data/diagnostics/refactor-r3-final-audit-20261018/`. 제품 변경·해결 검증·새 commit 없음.

### R3-012 — 열 설정 저장의 변경셋

- 경로/근거: MainWindow `_save_columns` → `ColumnSettingsRepository.save`는 모든 열을 UPDATE하고 각 version도 기록한다. reset은 별도 명시 동작이다.
- 사실/가설: 전체 열 쓰기 구조 확인, 실제 resize/move signal 빈도·COMMIT 비용 미측정.
- 권장/효과: 실제 변경 열 또는 기존 debounce의 적합성을 확인하고 불필요 UPDATE/version만 줄인다. reset/동기화 소유권은 유지한다.
- 위험/의존/완료: R3-011의 revision 의미 확인. 위치·폭·표시·재시작 복원·서버 pull과 사용자의 변경 보존, SQL 반영행/COMMIT 대조.
- 상태/commit: 대기 / 없음.

### R3-013 — MainWindow controller 별칭 정리 후보

- 경로/근거: MainWindow `_ranking_execution/_ranking_timer/_*_worker_controller/_market_cache_writer` 등은 AppController 필드를 반환한다. 반면 실제 slot/rendering/설정 접근은 별도 역할이다.
- 사실/가설: 전달-only 접근자는 확인했지만 속성별 소비/호환/test monkeypatch 의미를 전부 검증하지 않았다. 비용 문제로 주장하지 않는다.
- 권장/효과: 이미 controller 소유로 확정된 직접 접근은 caller 단위로 별칭 제거를 검토한다. 새 facade나 대체 Manager를 추가하지 않는다.
- 위험/완료: Qt parent/신호/동적 worker교체·shutdown 테스트를 보존하고 한 기능 이해 파일수/호출깊이가 줄었는지 확인한다.
- 상태/commit: 대기 / 없음.

### R3-014 — AppController 유지 판단

- 경로/근거: MainWindow configure_ranking/runtime/shutdown → AppController는 ranking revision/후속 1회 실행, producer 중단, API 교체, writer drain/신호 대기와 cache 실패 합류를 실제로 소유한다.
- 사실/가설: 단순 이벤트 relay 클래스가 아니다. `App*Actions` callback들은 UI 동작을 controller 수명에서 호출하는 경계다. 크기만으로 재분할하지 않는다.
- 방법/효과: 이 수명 owner는 유지한다. 구체적으로 혼합된 책임은 R3-017/R3-013의 한 경로에서 판단한다.
- 위험/완료: 정상/실패/종료 중 늦은 signal, 새 factory, 재시도/최신 값 보존이 핵심. 정적 책임 평가 완료, 현재 관련 fixture 실행 결과는 R3-001에 기록한다.
- 상태/commit: 검증 완료(유지 판단) / 없음.

- **2차 정밀 감사 보완(2026-10-18):** AppController의 producer 정지→flush→writer drain→종료 조정은 필요한 책임이다. 그러나 EntrySnapshotWriter의 private loader/cache를 직접 교체하고(R3-030) 잔여 queue를 drain 완료 조건으로 확인하지 않는(R3-031) 내부 결합은 미해결이다. 외부 owner 유지와 내부 책임 적절성을 분리한다.

### R3-015 — worker controller 유지 판단

- 경로/근거: Fundamentals/DailyHigh/Realtime 등의 controller.start는 실행 중 중복 방지, worker type/factory, Qt parent, 신호 연결, finished 후 참조 해제/deleteLater를 소유한다. Realtime은 REG ACK·failover와 동적 구독도 다룬다.
- 사실/가설: 일부 형식 중복은 있으나 결과/완료 신호와 수명 계약이 다르다. 공통 base 도입이 실행 비용을 낮춘다는 근거는 없다.
- 방법/효과: 유지. 진짜 동일한 순수 변환만 별도 비교하며 수명 로직을 MainWindow로 되돌리거나 generic controller를 추가하지 않는다.
- 위험/완료: thread affinity·late signal/종료·반복 start 및 객체 파괴. 정적 역할 검토 완료, 전체 worker UI/NAS 실행은 미검증.
- 상태/commit: 검증 완료(유지 판단) / 없음.

- **2차 정밀 감사 보완(2026-10-18):** worker controller는 한 live worker/parent/signal 등록·typed 확인/release·deleteLater를 소유하므로 유지한다. 반면 상태 없이7개 callable을 전달하는 SecondaryDataFollowupCoordinator와 AppSecondaryActions/MainWindow 왕복은 R3-017의 통합 후보다. 공통 base controller를 새로 추가할 근거는 없다.

### R3-016 — QueryStore 및 저장소 인터페이스 재평가

- 경로/근거: `app.create_app → create_query_store → SQLiteQueryStore/PostgresQueryStore` 조립; backend mixin은 실제 SQL/연결/transaction을 소유하고 QueryStore는 aggregate다. `QueryCacheStore`는 두 메서드, Collector/MarketIngest/라우터별 좁은 Protocol이 실제로 쓰인다.
- 사실/가설: 107 메서드라는 폭과 61 전달 연결은 조사 단서다. Protocol은 새 런타임 서비스 호출 계층이 아니며 파일 분리만으로 I/O가 줄지도 않는다. 최초 감사의 source baseline delta0 뒤 R3-004의 optional reader3서명/중복 호출1개 제거를 제한 갱신했다. 현재 site/candidate/forward delta0·parse/stale0이나 병행 뉴스의 `load_news_history`3서명 차이가 남아 전체 source/audit1건은 실패다. 기존 유지 판단과 현재 전체 계약 gate 통과 여부를 구분한다.
- 방법/효과: 조립 aggregate와 native DB owner를 유지한다. 넓은 `_store: Any` 소비는 실제 호출 subset·상태소유 변경이 필요할 때 좁힌다. 구조상의 전역 registry나 새 repository 계층을 만들지 않는다.
- 위험/완료: SQLite/PG parity, transaction·compat import와 API/stored document 계약. 정적 계약 검토 완료, 실제 PG 전체 검증은 미수행.
- 상태/commit: 검증 완료(유지 판단) / 없음.

- **2차 정밀 감사 보완(2026-10-18):** Protocol은 정적 계약이고 aggregate/mixin은 추가 runtime relay가 아니다. 내부 leaf는 `_lock/_connect/_connection/_now_provider/_observation_history_enabled` 및 revision/metadata helper를 공유한다. 따라서 aggregate 유지≠모든 leaf 책임 격리·107개 메서드 실행/PG parity 검증이다. 새 repository 수를 늘리는 대신 R3-038/039의 실제 native read 범위/권한/ACK 계약부터 좁힌다. 병행 뉴스3서명으로 남은 기존 source audit1실패는 승인하지 않는다.

- **394모듈 깊이 대조 보완:** 394모듈 대조에서 native leaf 전체와central_schema1~18은여전히미정밀이다. helper/aggregate유지를개별method완료로보지않는다. local metadata의 upsert helper는실제writer가소비하지만별도 MarketDataMetadataRepository class의production조립은현재확정못했다. 이름검색만으로삭제/미연결확정하지않고잔여G01/G09로남긴다.

- **잔여 감사 종결 보완:** 바로 위 문단은 직전 깊이 대조 당시의 판정으로 보존한다. 이번에는 native leaf·central_schema1~18·로컬 metadata 모듈의 미검토 본문과 책임을 보완해 G01/G09의 코드 미독을 종결했다. 개별 모듈의 실제 caller·소비·실패 경계는 현재 모듈 원장에 기록한다. 별도 class의 생산 조립이나 실제 PG/운영 실행을 확인하지 못한 범위는 그 원장의 운영 미검증으로 남으며, 코드감사 완료를 사용 중·정상 연결의 근거로 삼지 않는다.

### R3-017 — 광범위한 조립/state 책임의 실제 경계

- 경로/근거: bootstrap `build_api_runtime`의 dict를 MainWindow/AppController가 factory별로 적용한다. app.create_app은 credential/계좌/Collector/Shadow/서비스/라우터와 교체 closure를 조립한다. research_process는 여러 실행 모드/복구/게시를 공유한다.
- 사실/가설: 여러 기능의 변경이 같은 조립자에 닿는 결합은 확인. 파일길이만으로 결함이나 추가 service 필요성을 판단하지 않는다. 구체적인 새 기능 변경 범위의 전후 실측은 아직 없다.
- 권장/효과: API runtime 교체처럼 수명/입출력이 정해진 한 계약부터 필드 소유와 stale 참조를 좁힌다. 필요 없는 조립 단계 제거를 먼저 검토한다.
- 위험/의존/완료: F01/F21/F23, R3-014/016/025 유지. 같은 실행 source/설정 복원/교체 후 실제 factory와 종료를 통합 검증하고 이해에 필요한 파일/호출깊이 및 무관 변경수를 비교한다.
- 상태/commit: 대기 / 없음.

- **2차 정밀 감사 보완(2026-10-18):** 실제 후속은 minute history 완료→AppController→SecondaryDataFollowupCoordinator→AppController.start_daily_high_phase→AppSecondaryActions lambda→MainWindow→worker controller, daily high 완료도 다시 coordinator→controller→MainWindow로 간다. stateless callback relay를 기존 owner로 통합할 후보이며 길이만으로 분리하지 않는다. readonly MainWindow compatibility property는 별도 mutable 상태가 아니다. API 설정 UI는 일부 모드의 전체 적용에 재시작 필요를 명시하므로 bootstrap에서 한 번 조립된 content/candidate client를 새 hot-reload 결함으로 단정하지 않는다. 실제 부분 교체 범위는 F23에 남긴다. Runner 내부·UI status 책임은 R3-041/047/050으로 별도 관리한다.

### R3-018 — 판단/변환 중복과 기존 공유 계산 구분

- 경로/근거: `research_families.FAMILY_REGISTRY/get_research_family`의 evaluator를 연구·CandidateMonitor·Runner가 소비한다. RAM Shadow는 한 snapshot/계좌 cut과 market 계산을 여러 monitor에 공유한다. market metadata의 저장/조회 codec도 이미 공통이다.
- 사실/가설: 기존 Family evaluator 재구현/동일 market 계산의 매monitor 중복은 현재 경로에서 이미 줄어 있다. 분봉 연구와 0B live의 관측 시점 adapter는 무조건 동일 함수로 합칠 대상이 아니다.
- 방법/효과: 유지. 새 중복은 같은 입력/단위/상태/실패 의미인지 먼저 증명한다. 구형 명시 비교 Family/ID를 새 정책으로 자동 대체하지 않는다.
- 위험/완료: 같은 input의 같은 decision/FILL 소유·정책 scope 유지. 정적 공유 연결 확인, 모든 Family의 실제 연구/운용 수락은 미검증.
- 상태/commit: 검증 완료(유지 판단) / 없음.

- **2차 정밀 감사 보완(2026-10-18):** `scripts/run_research:304`, `candidate_monitor:509`, `mock_automation_runner:2431`은 같은 Family evaluator를 소비한다. 연구 비용/FILL·Shadow cursor·Runner 계좌/Risk owner와 단위/가용시점 adapter는 유지한다. 이 공유가 모든 Family 운영 연결/내부 orchestration 검증은 아니다. Collector/MarketEvents가 같은0g를 각각 parse하는 본문은 확인했으나 공유 가능성·비용은 P3 미측정 가설로 남긴다. Risk 누적 재계산은 R3-049로 관리한다.

### R3-019 — 뉴스 수집 결과와 재조회/조용한 오류

- 경로/근거: `CentralNewsService.refresh_once`는 `_collect`의 목록 반환을 쓰지 않고 `_load_cached(...since)`를 다시 읽는다. `_collect` 자체도 저장 뒤 cached/fresh/confirmed를 병합해 반환한다. `_auto_analyze`는 별도 저장 AI 이력을 읽는다. 종목 loop는 광범위 `except Exception: continue`다.
- 사실/가설: 성공 경로의 재조회와 오류 원인 미노출은 코드로 확인. _collect 반환과 owner cache가 완전히 같은 집합/순서라는 보장은 아직 검증하지 않았다. 운영 오류율/비용 미측정.
- 권장/효과: 동일 반환 계약을 확인한 뒤 기존 반환을 사용하거나 필요한 저장 ACK 결과를 전달한다. confirmed/fresh/source revision을 지우지 않는다. 필요한 기존 로그/상태로 원인을 드러낸다.
- 위험/완료: 일부 공급자 실패·저장 실패·confirmed만 존재·자동 분석 설정/본문 revision·날짜 필터·중복 기사와 actual store call 비용을 비교한다.
- 공유 변경 관찰: R3-004 정적 감사 시 현재 뉴스 작업의 `load_news_history(before_revision_id)` native2/Protocol1 서명이 기존 승인 baseline과 달라 `test_repository_connections_match_reviewed_baseline`1건이 실패했다. R3-004 reader 계약과 별개로 보존했으며 뉴스 변경 경계에서 실제 소비자/기존 호환과 승인 baseline을 대조해야 한다. 이를 R3-004 기능 실패로 숨기거나 무관한 뉴스 서명을 자동 승인하지 않는다.
- 상태/commit: 대기 / 없음.

### R3-020 — 뉴스 서버 프로세스 결합

- 경로/근거: app lifespan에서 NewsService/job runner/AIService를 API/Collector와 함께 조립한다. BODY/RULE lane·claim/wakeup은 기존 DB가 소유한다.
- 사실/가설: 동일 프로세스 결합 확인. 과거 처리량·기아 수치는 OPEN_ITEMS O19의 과거 관측이며 현재 수치로 재사용하지 않는다.
- 권장/효과: O19의 실제 지연/CPU/DB wait를 먼저 갱신하고 독립 worker의 credential 전달/설정/lease 소유권을 정한다. 새 프로세스만 추가해 해결로 보고하지 않는다.
- 위험/의존/완료: credential owner·PG claim/중복 작업·업무 예약/종료를 보호해야 한다. O19를 같은 항목의 외부 원장으로 연결하며 별도 이중 구현하지 않는다.
- 상태/commit: 보류 / 없음.

### R3-021 — 이미 구현된 콘텐츠 delta 유지

- 경로/근거: news_process background → CentralContentSyncService `_upload_changed` identity/content hash manifest → API upsert의 동일 JSON WHERE → pull cursor/merge. 정상 NAS 모드는 전체 뉴스 catalog 복제를 사용하지 않는다.
- 사실/가설: no-op·pending·echo 억제 코드 확인. `_read_news/_read_themes`의 로컬 자료 준비 비용은 여전히 별도 측정 후보이나 전량 업로드가 반복된다는 결론은 아니다.
- 방법/효과: 기존 delta 경계의 역할은 유지한다. R3-044/045의 ACK·false delta를 후속으로 해결하고 source staging 비용은 별도로 측정한다.
- 위험/완료: pull/no-op manifest/cursor, 사용자 편집/삭제·journal scope 보호. 계층 필요성의 정적 유지 판단이며 내부 R3-044/045는 대기다. 운영 자료를 이번에 동기화하지 않았다.
- 상태/commit: 검증 완료(유지 판단) / 없음.

- **2차 정밀 감사 보완(2026-10-18):** manifest/cursor/no-op WHERE/echo 억제·두 DB 적용 후 cursor는 실제 역할이다. 그런데 그 바깥 news_process가 전송하지 않은 동시 편집도 clean으로 ACK하는 R3-044, export created_at만 달라 false delta를 만드는 R3-045가 재현됐다. 전량 업로드 해결을 중복 등록하지 않고 해당 내부 후속을 분리한다. staging 전체 read 비용은 미측정이다.

### R3-022 — 진단/capture/owned 경계 유지

- 경로/근거: broker/Collector/DB wrapper는 context/행수/단계시간/trace parent, replay native 작업의 cancel/close drain을 연결한다. 단순 이름만 다른 전달 Service와 역할이 다르다.
- 사실/가설: 역할/호출 연결 확인, 현재 실행 오버헤드 미측정. 모든 wrapper가 불필요하다는 근거는 없다.
- 방법/효과: 유지. master OFF일 때 실제 비용을 먼저 계측하고 필요하면 그 경로에서만 빠른 반환을 개선한다. replay/원본 또는 주문 격리를 삭제하지 않는다.
- 위험/완료: 취소 중 native 종료, trace 실패/용량/TTL/진단 pause 복구. 정적 역할 평가 완료, 실제 NAS I/O 진단은 미수행.
- 상태/commit: 검증 완료(유지 판단) / 없음.

- **2차 정밀 감사 보완(2026-10-18):** capture wrapper는 원 DB outcome과 관측 실패를 분리하고 replay scope는 취소된 waiter 대신 실제 executor 종료/restore lease를 소유한다. 따라서 단순 forwarding으로 제거하지 않는다. OFF/ON input_token·freeze/thaw·큰 payload/window/manifest 비용, 실제 PG restore/동시성은 미측정이며 내부 전체가 적절하다는 완료 판정이 아니다.

### R3-023 — 시계 연결의 남은 host fallback

- 경로/근거: app는 server_now를 주입하지만 `database_realtime_snapshot.load_realtime_snapshots`의 stale cutoff는 `time()`이고 `market_observations._bar_available_at`/`market_ingest.fundamentals_document_is_current`에는 host now fallback이 남아 있다. PC AppController는 loader.server_now 또는 공통 KST fallback을 쓴다.
- 사실/가설: 정적 fallback 존재와 호출 경계 확인. 해당 fallback이 현재 운영에서 실제 실행되는지/clock skew 영향은 미측정. 이미 수행된 DB KST 변환을 다시 수행할 근거가 아니다.
- 권장/효과: 기존 `now_provider`를 각 실제 소비 경계까지 전달하고 fallback이 합당한 offline/bootstrap인지 구분한다. epoch에9시간을 가감하지 않는다.
- 위험/의존/완료: 기존 `docs/workstreams/time-basis-kst.md`에 잔여 연결을 합친다. UTC/KST·expired/신선/동기화 전·과거 봉 시각·replay 고정시계 대조. 별도 HTTP/TR/polling 추가 금지.
- R3-002에서 발견한 별도 경계: 기존 `AppController.on_price_cache_failed`는 `_trade_date`를 소비하지 않고 code별 pending high에 합친다. 날짜를 넘겨 재시도하면 원래 거래일 대신 새 flush 날짜를 쓸 가능성이 있다. 이번 batch는 받은 거래일을 보존하며 이 기존 실패 재병합 정책은 바꾸지 않았다. 실제 날짜 경계 재현/완료 기준은 이 ID에서 후속 확인한다. 현재 미재현 가설이며 R3-002의 개선 효과나 새 운영 차단 조건으로 쓰지 않는다.
- R3-002 연결 재검증에서 공유 시계 변경의 naive/aware 분봉 비교 오류를 재현하고 해당2곳만 `as_kst`로 보완했다. fixed clock·현재분/이전분·metadata 유/무·실제 writer/controller 저장/종료 회귀로 구분하며 이 작은 보완으로 다른 host fallback이나 시계 경계 전체가 해결됐다고 하지 않는다.
- 상태/commit: 대기 / 없음.

- **2차 정밀 감사 보완(2026-10-18):** R3-002의 거래일 유실 가설을 actual controller→native SQLite로 재현했다: 원일 고가 없음/다음일 high120. 우선 원거래일 고가와 실제 수신 timestamp를 구분하고 실패/종료 재전달을 검증한다. snapshot read cutoff는 현재 주입 시계로 바뀌어 옛 host-clock 근거를 그대로 쓰지 않는다. `database_datasets.save_dataset_snapshots:58`의 `time()` saved_at은 본문에 남아 time-basis 원장과 대조할 범위다. 기존 날짜 key/원본 시각 일괄 변환은 하지 않는다.

- **394모듈 깊이 대조 보완:** 추가read에서local_bar_observations의명시available_at없을때hostfallback,SettingsBackup legacy테마SQL/일지legacyledger의CURRENT_TIMESTAMP를확인했다. 기존time-basis범위로보강하며새R3로중복등록하지않는다. 실제일괄자료보정/새시계조회는안했다.

- **최종 잔여 감사 보완:** actual aware ranking_now→finalization_retry_allowed의 빈 retry map/default naive datetime.min TypeError를 격리 재현했다(`root/observations.json:R3_023_aware_finalization`). MainWindow._execution_pressure_at_entry3863도 같은 기본값 비교를 하고 _on_order_execution3749 enqueue 전에 호출된다(후자는 본문 확인, GUI 미실행). 원거래일 payload 복구와 이 비교 경계를 같은 시계 항목의 별도 작은 범위로 관리한다. 근거 디렉터리: `data/diagnostics/refactor-r3-final-audit-20261018/`. 제품 변경·해결 검증·새 commit 없음.

### R3-024 — 실제 부분 연결과 전략 미완성

- 경로/근거: F12/F14/F15/F17/F20. Registry는 live/research 입력을 명시하고 현재0B 가격 관측과 Runner/Shadow 연결은 존재한다. 임의의 연구 결과를 live 전략/모델로 승인하는 범용 adapter는 없다. 장전 context 제공과 자동 campaign/주문 연결도 별개다.
- 사실/가설: 코드에 미정 정책·지원 범위 차단과 기존 A03 잔여가 있다. 이를 “리팩토링 때문에 끊긴 기능”이나 무조건 미사용 코드로 판단하지 않는다. 실제 운영 설정의 활성/지원 상태는 미확인이다.
- 권장/효과: OPEN_ITEMS의 주도주 연구/Shadow 방향·기존 A03과 연결하여 정의된 규칙의 consumer만 복구한다. 보존된 구형 비교 경로는 명시 이력으로 둔다.
- 위험/의존/완료: 사용자 전략/미정 조건을 만들거나 fallback Family로 자동 대체하지 않는다. 필요한 정의가 정해졌을 때 UI 선택→저장/재시작→자료 공급→판단→위험/기록의 같은 기준을 통합 검증한다.
- 상태/commit: 보류 / 없음.

- **394모듈 깊이 대조 보완:** 추가body/caller대조에서feedback revalidation/context뉴스가설build·advance/thesis생성helper와evaluate_realtime_breakout은src/scripts의명시적앱producer연결을확정못했다. 단위fixture/capability존재만으로자동적용완료라고하지않는다. thesis평가consumer는실제RAMShadow에연결돼생성과소비를분리한다. learning/review의CLI·GUI artifact는retrospective경로이며generic연구→live/LLM학습으로승격하지않는다.

### R3-025 — 시작/중지/교체·재시작 수명

- 경로/근거: PC AppController `_advance_shutdown/_advance_api_reload`는 producer→pending flush→writer drain→queued signal/실패 대기; 서버 lifespan은 부분 시작 실패/반복 cancel에도 owned close_services를 기다린다. Collector·각 서비스의 close는 자체 native 작업을 소유한다.
- 사실/가설: 명확한 안전/격리 역할이 있어 유지한다. 종료가 길다는 증상만으로 wait를 삭제하지 않는다. 실제 NAS 장시간/다른 PC 수락은 OPEN_ITEMS O12와 별도다.
- 방법/완료: 관련 fixture에서 정상/실패/늦은 signal/중단 재요청과 마지막 데이터 보존을 검증한다. 실제 restart/구독 해제는 별도 미검증으로 남긴다.
- 상태/commit: 검증 완료(유지 판단) / 없음. 신규 shutdown 계층을 만들지 않는다.

- **2차 정밀 감사 보완(2026-10-18):** outer lifespan의 첫 close 실패 뒤 dependency/store close 중단은 명시 테스트 계약으로 확인돼 결함 후보에서 제외했다. 모든 dependency를 무조건 닫는 수정은 하지 않는다. 반면 entry queue 종료 판정 R3-031, bootstrap plain daemon 수명 R3-032, route local sender 정리 R3-037, owner/runner 교체 R3-042는 내부 미완료다. actual native join/lease는 유지하며 실제 강제 종료/NAS 복구를 검증했다고 하지 않는다.

- **394모듈 깊이 대조 보완:** 누락추가감사에서일지review dirty종료052,helper설치부분교체053,Drive ACK056,external owner종료060을분리했다. MainWindow수동일지복원은child정지/wait뒤DB대체지만 main EntrySnapshotWriter까지같은복원경계에서drain되는지는미확인으로G02에남겼다. actual유실로단정하거나운영DB복원실험하지않았다.

- **최종 잔여 감사 보완:** 전체 수명 코드 감사는 종결했으나 운영 수락은 별도다. validation_client의 중지 없는 daemon, CentralRealtimeWorker의 stop 실패 뒤 참조 해제, journal sync daemon/ThemeDispatcher timer-only close를 정적 보강했다. StrategyPackManager 추출 중 row 삭제/재추출/닫기와 captured-row callback은 통제된 worker barrier로 추가 검증할 후보(D-F07)이며 실제 파괴/유실은 미재현. real account close의 구체 잔여검사 공백은 신규066으로 분리한다. 근거 디렉터리: `data/diagnostics/refactor-r3-final-audit-20261018/`. 제품 변경·해결 검증·새 commit 없음.

### R3-026 — 이미 완료한 순위표 저장값 읽기 통합 승계

- 경로/근거: bootstrap는 실제 StockRepository를 주입하고 `_apply_ranking_view → load_ranking_cache → RAM/표`로 연결된다. 기존 개별 read는 다른 소비자와 부분 lookup 호환에 남는다.
- 실측: 직전 fixture 연결5→1/SELECT5→2, 쓰기0. 관련94건·SQL 결과/Qt 신호·NXT 미확인/Y/N 표시를 별도 검증했다. 해당 지문/검증은 `data/diagnostics/ranking-cache-cleanup-20261018/verification.json`.
- 방법/효과: 기존 구현 유지. 일봉/역사적 신고가를 이 batch로 무조건 합치거나 모든 앱 DB read가2회라고 주장하지 않는다.
- 위험/완료: 시장-only 고정 구성·현재 표시 종목·동일 날짜/누락값과 다시 읽은 변경값 보존. source가 변경되면 이 ID 범위만 재확인한다.
- 상태/commit: 검증 완료(직전 로컬 변경) / 미커밋. 이번 신규 개선 개수로 중복 집계하지 않는다.

### R3-027 — 표/테마 합계의 반복 계산 후보

- 경로/근거: MainWindow `_on_trade_tick`는 원본 RAM/분봉을 즉시 갱신하고 `_flush_trade_tick_updates`로120ms 표시를 합친다. `_render_trade_values`는 1/5/60분·당일 값을 읽고 테마 합계 경로도 aggregator를 소비한다.
- 사실/가설: 표시 합치기는 이미 있다. 같은 cut의 합계/강도 계산을 얼마나 중복하는지·Qt 렌더링 vs 계산 비용은 미측정이다.
- 권장/효과: 실제 한 flush/테마 갱신의 계산횟수를 계측하고 같은 cut에서만 값 공유를 검토한다. 신호 관측·알림/품질·분 경계/시장 변동을 별도로 보존한다.
- 위험/의존/완료: 잘못된 cache가0B 최신/분 전환·설정/테마 변경을 가릴 수 있다. 고정 tick replay에서 값/알림/표·계산횟수를 대조하며 UI 주기를 임의로 늦추지 않는다.
- 상태/commit: 대기 / 없음.

- **2차 정밀 감사 보완(2026-10-18):** MainWindow250ms timer→`_update_top20_trade_value_index`→`_aggregate_top20_rows`→chart.set_data/set_series 본문을 확인했다. 실제 MainWindow/chart 메서드와 고정 collector120행을 이용한 숨김 창 동일 cut4회에서 aggregate4/처리480행/set_data4회다(`pc-measurements.json.hidden_chart`). 실제 timer를4회 자동 기다린 것이 아닌 handler 직접 호출이며 CPU/paint/장중 latency는 미측정이다. collector advance/봉 저장·구독은 유지하고 기존 변화/표시 경계에서 불필요 render·집계만 줄이는 안을 먼저 검증한다.

- **최종 잔여 감사 보완:** MainWindow._render_trade_values:4454~4464가 live_only 표시에서도 다른 기간을 포함한 집계/quality를 수행하는 본문을 확인했다. 기존 same-cut 재계산 항목의 추가 소비자 범위이며 이 branch 자체의 CPU/운영 호출빈도는 미측정. 차트 paint081과 owner/trigger가 다르다. 근거 디렉터리: `data/diagnostics/refactor-r3-final-audit-20261018/`. 제품 변경·해결 검증·새 commit 없음.

### R3-028 — 불필요한 import의 낮은 우선순위 제거

- 경로/근거: `settings_repository.py`, `column_settings_repository.py`의 `datetime.UTC/datetime` import는 현재 본문에서 참조하지 않고 실제 시계는 kst_now/as_kst 경로다.
- 사실/가설: AST/본문상 비참조 import. 실행/DB 비용 개선 근거는 없으며 장중 최적화로 보고하지 않는다.
- 권장/효과: 관련 파일을 실제 수정할 때 함께 최소 제거하거나 별도 저우선 정리. 파일/클래스 분할 불필요.
- 위험/완료: annotation/동적 소비·import 호환 여부와 관련 import/설정 검사를 확인한다. 시간 동작 변경을 끼워 넣지 않는다.
- 상태/commit: 대기 / 없음.

### R3-029 — 테마 revision의 시장 자료/저장 시각 결합

- 대상/경로: F06/F22. `StockRepository.update_price_cache`→`stocks`의 시장값/시각; `CentralThemeSyncDispatcher._run`→`ThemeBackupService.revision/_revision`→원격 snapshot 취득→`CentralContentSyncService.apply_theme_snapshot`→`ThemeBackupService._import_all_profiles(expected_revision=...)`.
- 원인/코드 근거: `infrastructure/persistence/theme_backup.py::_revision`은 stocks 포함7테이블을 `SELECT * ... ORDER BY rowid`로 해시한다. 반면 같은 service의 export `stock_catalog`는 code/name/market만 담는다. 따라서 테마/종목 catalog 내용과 관계없는 현재가·시총·확인시각까지 테마 import의 편집 충돌 fence에 포함된다. `_import_all_profiles`는 적용 전 같은 revision을 대조하고 dispatcher는 충돌 시 pending을 보존/재시도한다. 이름 검색만으로 `updated_at` 소비가 없다고 판단하면 놓치는 간접 소비다.
- 실측/미확정: R3-003의 실제 DB→저장소→export/revision→실제 dispatcher/service/import 격리 fixture에서 동일 cap의 timestamp만 갱신해 export(생성시각 제외)는 동일/revision은 변경/flush False·충돌 재시도1·pending 유지가 재현됐다. 진단 cap no-op 후보는 export 동일/revision 불변/flush True·재시도0·pending 삭제다. 외부 client.load_all과 재시도 timer만 fake다. 운영 충돌 빈도·테마 동기화 지연/PG 비용은 미측정이다.
- 권장/변경 범위: 먼저 테마 import/export가 실제 보존하는 종목 catalog/사용자 편집 필드를 대조해 revision의 책임을 정한다. 실시간 저장의 수신 확인시각을 임의 제거하거나 충돌 검사를 통째로 끄지 않는다. schema/새 revision 저장소·서비스를 먼저 늘리지 않는다. 현재 제품 수정 없음.
- 효과/회귀 위험: 테마 적용과 무관한 시장 갱신 때문에 생기는 넓은 hash 재계산/불필요 충돌을 줄일 수 있다. 필드를 지나치게 제외하면 이름/시장/alias/프로필·할당/사용자 편집을 덮어쓸 수 있다. export 복원 의미·트랜잭션 snapshot/잠금·pending/generation 재시도는 보존해야 한다.
- 의존/우선순위: P2 / R3-003의 관측 가능한 영향으로 연결한다. R3-021 뉴스/콘텐츠 delta의 이미 해결된 문제와 다른 테마 import fence다. R3-011/012의 설정 revision과도 별도다. R3-003 no-op은 이 결합을 관련 회귀로 검증하며 이 항목의 전면 완료를 자동 선행 조건으로 삼지 않는다.
- 완료 기준/검증: 실제 local 편집·catalog 이름/시장/alias/프로필 변경·원격 적용 도중 변경은 충돌/보존하고, 시장 값/확인시각만 변할 때 테마 내용의 계약에 맞는 동일 결과를 보인다. 실제 dispatcher→service→import/end-to-end 격리 회귀, 동일 fixture hash/SQL/충돌·재시도 횟수, 원자 rollback/재시작 pending을 전후 비교한다. 관련 F06/F22와 백로그를 동반 갱신한다.
- 상태/commit: **대기 / 없음**. `data/diagnostics/refactor-r3-20261018/r3-003-parallel/theme-revision-measurement.json`의 조사 근거 승계. 운영 쓰기/배포 없음.

- **2차 정밀 감사 보완(2026-10-18):** 기존 stocks 전체열 revision 결합 발견/진단은 보존한다. R3-045는 export의 volatile created_at만 달라진 별도 false delta 원인이므로 중복 ID가 아니다. 두 항목은 같은 theme export/동시 편집·remote_completed_at 계약을 공유해 구현 소유권을 조정한다.

## 이번 실행 기록

1. 구조/연결 전수 선별 및 현재 주요 owner 호출 본문 대조, 백로그 작성.
2. 임시 SQLite 비용 기준 확보: `cost-before.json`.
3. 전체 후보 목록 작성 뒤 R3-001을 한 제품 파일에서 수정했다. 관련 테스트3건을 기존 모듈에 추가하고, 모든 관련 모듈을 독립 worker로 검증했다. 변경 후58건/실패0/오류0/skip0, fixed fixture의 값·metadata 전체 동일과 SELECT40→20을 확인했다.
4. 실제 create_app→SQLite QueryStore→인증된 coverage API의 직접 ASGI 검사1건과 서버 연결 계약258건이 통과했다. `connection-contracts/{result,execution}.json`, `composed-asgi/{result,execution}.json`에 기록했다. 후자1건은 lifespan을 실행하지 않고, 전자258건 안의 실제 app lifespan/binding/시작 실패 검사가 그 수명을 따로 검증한다. 외부 Kiwoom/실제 PG 수락은 아니다.
5. 다음은 R3-002. 이번 요청의 첫 작은 경로는 완료했으며, 이후 “다음”은 해당 항목부터 범위를 확인해 이어간다. commit/push/CI/NAS 배포/운영 DB 변경은 없다.
6. 후속 “다음”으로 R3-002를 완료했다. 기존 범위만 재확인하고 제품3파일과 직접 관련 검사를 수정했다. 관련112건과 연결/COMMIT3→1·timestamp 순간 대조, native rollback/commit 전후 자식 종료·재시작을 검증했다. 영향 F01/F06/F10/F23/F24를 갱신했고 다음은 R3-003이다.
7. 사용자 추가 지시를 반영했다. 모든 항목 완료 시 관련 F 행 동반 갱신을 이어가기 규칙에 명시했고, 기능·상태/transaction 소유권이 분리된 R3-004와 R3-008의 조사/fixture를 에이전트에 병렬 배정했다. 제품 코드·공유 문서는 주 담당자가 결과를 대조해 통합한다.
8. 병렬 조사 결과를 통합했다. R3-004는 진단 fixture1건/연결10건으로 범위 축소 효과를 확인했으나 제품 구현은 대기, R3-008은11조건/기존3건으로 외부 변경/기동 복원 역할을 확인해 현재 polling 유지·보류다. F08/F13/F17/F18/F24를 동반 갱신했다. 공유 시계 변경 후 실패와 범위 한정 보완/최신 재검증은 R3-002 본문과 `current-linked`, `final-linked`, `stable-linked`에 구분한다.
9. 사용자 지속 병렬 요청으로 R3-004 최소 제품 구현과 R3-003 소비자/SQL·테마 통합 조사를 병렬 진행하고, R3-002를 독립 읽기 전용 검토했다. R3-002는 최종140건 후 공유 writer 시계 연결 보완에 대해12건을 부분 재실행해 통과했다. 원인 확정한 fixture의 실패/timeout·재실행을 모두 별도 보존했다.
10. R3-004 실제 product native 반환9000→200/최종값·metadata 동일/기능58건을 검증했다. full-day API/RAM overlay/권한·수명·capture와 source4/test4 해시를 확인했고 F08/F13/F25를 동반 갱신했다. 기존 source/audit9건은8통과·병행 뉴스-only1실패다. R3-004의 제한 baseline 갱신 후에도 남은 뉴스3서명 차이는 R3-016/019에 명시했다.
11. R3-003은 SQL5조건·실제 테마 dispatcher/service/import2조건·관련 기존3건으로 조사 완료/제품 대기다. 같은 시총 timestamp 갱신이 테마 revision/충돌 재시도를 바꾸는 결합을 R3-029로 추가했고 F06/F10/F22/F24를 갱신했다. 다음 작은 제품 작업은 가격 확인시각을 유지하는 cap 조건부 UPDATE다. R3-029 전면 완료를 새 차단 조건으로 만들지 않는다.

## 2차 정밀 감사 결과 — 2026-10-18

### 기준·방법·완료 범위

초기394모듈/174스크립트 목록을 다시 감사 완료의 근거로 삼지 않았다. 주 담당+독립 담당3명이 실제 앱/서버 조립, 호출 본문, 상태 갱신/ACK, native 저장·재조회, UI 소비, 실패·재시도·동시 실행·종료를 추적했다. 아래 **PC13 + 실시간23 + 매매22 + 뉴스·연구·진단19 = 77개 세부 경로**는 이번에 확인한 구간과 미확인 구간을 함께 남긴 증거 지도다. 모든 분기·함수·전략의 전수 실행 증명은 아니다. F01~F25 부모 상태는 세부 경로 중 알려진 단절/미검증을 포함한 상태이며 격리 진단 성공만으로 정상 연결로 승격하지 않았다.

- **본문 사실:** caller→실행 본문→상태 소유자→writer/reader→소비자 및 finally/close를 대조했다. 코드 행은 이 감사 시점 참고이며 다음 작업은 함수/관련 해시로 대조한다.
- **격리 실측/재현:** 실제 제품 메서드와 임시 native SQLite/고정 KST를 사용하고 외부 broker/socket/AI/transport·일부 upstream은 대역으로 제한했다. 각 ID에 대역/실제 경계를 명시한다. 진단21조건 통과는 **현재 비용 또는 결함이 존재한다는 재현**이다. 실제 서비스 장애빈도·ms·물리WAL·투자성과를 뜻하지 않는다.
- **가설:** 본문 패턴에서 추정되지만 운영 빈도/영향 또는 제거 안전성이 검증되지 않은 사항은 그대로 표시한다. R3-032/047/049/050은 특히 추가 계측/실행검증이 남아 있다.
- **기존 유지 재평가:** 계층이 필요한 이유와 내부 책임이 적절한지는 아래 표에서 별도로 판단한다. 기존 완료 이력은 삭제하지 않으며 미완료 내부 문제는 후속 ID를 연결했다.
- **경계:** 제품·공유 테스트·실행 script·DB/schema·승인 baseline 변경0. 운영 DB/NAS/외부API/실제 주문·AI/전수회귀·commit/push/배포 없음. 기존 다른 작업의 수정과 R3-001/002/004 구현은 보존했다. 신규 ID는 전부 **대기**다.
- **중복 관리:** 요청의 시작 번호029는 이미 테마 revision 결합 항목에 배정돼 있어 재사용하지 않았다. 신규는030~050, 기존011(설정 cache경합)/023(고가 거래일)/027(동일cut그래프)/017(후속callback)/010(binding read)로 같은 원인을 보강했다.

### 실제 상태 소유와 계층 재평가

| 기존 ID/구조 | 계층의 실제 역할·분류 | 내부 책임 적절성의 별도 평가 | 남은 검증/후속 |
|---|---|---|---|
| R3-007 Broker/Collector/Program writer | **유지**: 송신·후처리·원본/delta·dirty ACK·owned native 수명은 다름 | Collector reference 실패복원과 index flush/lock 정책이 불일치. live최신/RAM dirty dict를 무조건 통합하면 안 됨 | R3-034~036; 실제PG wait는006 |
| R3-009 Gateway/Runtime/Lifecycle/native | **유지**: command drain/lease/UNKNOWN·FILL/native fence | 필요성≠내부 적절성. 같은 intent load2, Runner다중상태, asyncDB·context재조립 존재 | R3-040/042/047~050,010; 안전 read 임의삭제 금지 |
| R3-014 AppController/MainWindow | **유지**: 비동기 producer/writer·종료 owner, UI와 상태별 소비 | controller의 writer private상태 교체/queue 실패소비 누락. MainWindow 첫틱retention/숨김차트계산과 statelesscallback왕복은 개선 후보 | R3-030~033/027/017 |
| R3-015 worker controllers | **유지**: 단일 worker·parent/signal/release | typed readonly alias는 별도 mutable state 아님. SecondaryDataFollowupCoordinator의 callable relay는 **통합 검토** |017/013; 공통 base추가 불필요 |
| R3-016 QueryStore/Protocol/mixins | **유지**: native계약/조립은 실행 relay가 아님 | private connection/lock/clock/revision 공유 범위는 넓음. aggregate유지가 각leaf 독립 책임을 증명하지 않음 |038/039 실제reader범위;107메서드/PG전체parity미검증 |
| R3-018 연구/Shadow/Runner evaluator | **유지**: 순수 Family 계산 공유와 서로 다른 FILL/cursor/권한 owner | 모든 orchestration/adapter가 독립·효율적이라는 뜻은 아님. Risk누적계산/Runner상태 결합은 별도 |047~049/024; 모든Family실행미검증 |
| R3-021 delta sync | **유지**: manifest·cursor·no-op·pull echo/다중DB ACK | news child clean ACK와 volatile theme staging identity는 불완전 |044/045;029 revision과 구분 |
| R3-022 capture/owned replay | **유지**: DB outcome격리/실제 executor종료/restore lease | 내부 freeze/thaw/window/manifest 비용 적정성은 확인불가 | OFF/ON계측/대용량/실제PG restore 남음 |
| R3-025 시작/교체/close | **유지**: 소비자→producer→native→dependency 순서 | outer정합성이 client-local 정리·rawthread·owner map까지 보장하지 않음 |031/032/037/042;첫close실패dependency보존은 명시계약 |
| R3-002/004 기존 완료 구현 | **유지**: transaction·범위read 비용감소/계약근거 보존 |002 caller의 날짜실패는 이번추가확인으로 진행 재개.004 다른caller의 daywide read를 해결한 것은 아님 |002+023 우선,038/039 분리 |

### 이어갈 순서와 병렬 소유권

1. **R3-002+023**의 원거래일 실패 재전달부터 이어간다. 기존 batch SQL/범위read를 다시 구현하지 않는다. 원일 고가와 receipt timestamp를 구분해 실제 writer→DB→readback→종료를 관련 검사로 확인한다.
2. 그 뒤 확인된 연결/정합성 오류 **034/031/042/044/037**, 설정cache **011**, runtimecache **030**를 작은 실행 경로로 처리한다. 서로 다른 파일·상태 owner면 병렬 가능하다.031↔030(entry writer),034↔Collector수명수정은 같은 owner라 직렬 통합한다.042는 새 자동재승인 정책을 만들지 않고 기존 수명/명시제어 계약을 먼저 고정한다.
3. 장중 경합/중복실행 **036→035**는 같은 TOP20 파일에서 동일payload flush 기준을 먼저 확보한 뒤 critical section을 줄인다. outbox/lateACK/close를 함께 보호한다. 이 순서는 lock을 밖으로 옮겨 중복flush 노출을 늘리지 않기 위한 것이다.
4. 비용이 확인된 **003/038/039/040/041/045/027**을 비교해 작은 경로로 진행한다.003의 cap no-op·수신시각 계약은 조사결과를 재사용한다.038/039는 native reader를 공유하므로 같은 인터페이스 동시변경 금지,044/045/029는 sync identity·완료표식을 공유하므로 직렬 합의한다.040 gateway와041 UI는 독립 범위일 때 병렬 가능하다.
5. **043** claim owner/046 CLI 원본보존은 격리재현 확보된 별도 P2다. **032/033/047~050/010**은 보존계약과 추가측정을 먼저 확인한다. 큰클래스 분리/PG pool/새Manager를 선행조건으로 만들지 않는다. 새 회귀나 코드변경이 없는 범위의 전체 재조사는 하지 않는다.

### PC 세부 연결 — P01~P13

| 세부 경로 / F | 실제 진입→소유→저장·소비 | 확인한 정상/실패/동시/종료 | 상태·미확인 |
|---|---|---|---|
| P01 F01/F24 | `__main__→bootstrap.main restore gate→_run_desktop→MainWindow/AppController→app.exec/finally` | 시작모드/lease/factory/Qt종료/daemon등록 본문 | 확인 불가(전체기동). rawthreadjoin누락032 정적후보 |
| P02 F23 | API settings dialog→controller producer drain→runtime factory→_apply_api_runtime→후속재구독 | 기존loader시계전달/rollback/drain본문; actualswap과oldload경합030재현 | 부분 연결. 실제credential/TR·전체모드교체 미실행; 재시작 명시모드 보존 |
| P03 F01/F23 | bootstrap settings sync write→SettingsRepository.clear_cache→UI get | 실제SQLite fetch/동시commit/clear/old재삽입011재현 | 부분 연결. 원격settings승인/실제thread스케줄 미실행 |
| P04 F02/F10 | ranking worker→controller response→MainWindow apply→history/daily/fundamental controllers | mutableowner/typedalias/statelesscallable왕복본문, callbackchain017 | 확인 불가(전체순위). 기존관련회귀 이력과 이번코드감사 구분 |
| P05 F04/F05/F06 | Qt0B→MainWindow aggregator/RAM/pending→controller timers→cache writer→native readback | 실제실패고가 원일→다음일023 재현; 기존분봉authority/transaction구현 보존 | 부분 연결(날짜실패). native QThread 자정/restart 후속 |
| P06 F18/F22/F23 | 일지enqueue→EntrySnapshotWriter enrichment loader/cache→repository | 실제oldloader/block→swap→oldcache030재현 | 부분 연결. Pythonthread barrier이며 nativeQThread/외부조회 미실행 |
| P07 F18/F22/F24 | program/investor backfill→writer.run→repo save→controller drain/ready | 실제run저장fault→exception/queue1/failed0/ready031재현 | 부분 연결. nativeQThread 종료형태/운영유실 미측정 |
| P08 F04/F05/F24 | 첫틱/날짜변경→_ensure_today_minute_bar_storage→native purge/commit | 실제메서드첫/같은/다음날→purge2/callingUIthread033확인 | 확인 불가(비용영향). nativeDELETE ms/대용량/오류재시도 미측정 |
| P09 F02/F09 | 250ms timer→indexupdate→aggregate120행→chart.set_data/set_series | 숨김·동일cut handler4호출→aggregate4/480행/set_data4(027) | 확인 불가(최적화전후). 실제자동timer/paint/CPU 미측정 |
| P10 F10 | minute완료→SecondaryFollowup→controller→AppSecondaryActions→MainWindow→worker | 각callback본문/signal연결/workerowner상태 대조017 | 확인 불가(전체후속). statelessrelay통합 후보; 신호제거 미실행 |
| P11 F01/F22/F24 | bootstrap timer→content/settings/retention daemon→native/file→finally/restorelease close | timer중단/dispatcher/serverclose에 rawthreadjoin없음032본문 | 확인 불가. 느린작업/종료/restore경합 추가fixture 필요 |
| P12 F24 | AppController stopproducers→flush→writerwait→_complete_shutdown→UIclose | cachewriter failedstate와 entryqueue 미소비 차이031재현 | 부분 연결. 모든producer/native/OS강제종료 조합 미검증 |
| P13 F20/F23/F24 | `MainWindow._show_morning_market_brief:1883 → MorningMarketBriefDialog.showEvent/load/save/run_now/refresh_report → CentralOperationalSettingsClient → morning_market_brief router → runtime request/_generate/latest → native documents/context/UI`; app:433/723/771 조립/start/close | 본문: 공통server_now주입,설정revision저장후RAM게시,request lock·claim-before-AI·same-run진행중병합/재시작불명자동재송신방지,raw/usage·완료/실패영속,read시보존응답복원·원기록불변,close shieldedjoin. AI는credential snapshot/acceptedtask·executionlock 사용. dialog는worker중닫기거부/close timer중단/showEvent재시작 | 부분 연결(명시advisory). `context_from_run:729`은 availability/expiry와consumer_binding=not_connected/auto_trade_eligible=False를분리한다. 실제scheduled/AI/DBfault·다중instanceclaim·취소/종료GUI통합은 미실행.5초예약확인/10초표시read비용은 미측정이며안전역할확인없이새cache/주기변경을권고하지않음 |


### 중앙 실시간 세부 연결 — S01~S23

아래 S행의 F 매핑: S01~04/S21=F04·F24(계좌 이벤트 S03은 F18 포함), S05~10=F07·F10·F13·F18, S11~14=F08·F07, S15~17=F03(S17 구독후속은 F04 포함), S18~19=F09, S20=F10, S22=F01·F08·F25, S23=F24. ‘본문’은 확인 불가(실제환경), 재현된 실패branch는 부분 연결이다. 전체 기능 정상 검증으로 해석하지 않는다. 코드파일은 `src/kiwoom_monitor/central_server` 기준이다.

표의 **본문**은 현재 caller와 구현을 직접 대조했다는 뜻이고 실행 통과가 아니다. **재현**은 이번6개 fixture, **기존 검사**는 해당 테스트 본문/이전 결과의 제한된 경계만 확인했으며 이번 전체 재실행하지 않았다. `미확인`을 정상 연결로 승격하지 않는다.

| 세부 경로 / 실제 caller→구현→소비 | 정상·데이터 계약 | 실패/재시도 | 동시/교체 | 종료/남은 검증 |
| --- | --- | --- | --- | --- |
| S01 상류 LOGIN/REG: `app Collector.start → _run:614 → _receive:649 → RegistrationProgress → _approve_incremental_subscription:829 → Hub READY` | 본문: token generation 확인, ACK 받은 sources만 승인; 요청접수와 READY 구분 | 본문: timeout/실패→gap·READY false→재연결, 등록 도중 target 변경은 superseded | 본문: recv/send owned task + generation fence; 실제 소켓/키움 ACK 타이밍 미확인 | `_receive finally:820`에서 recv/send cancel+gather; 정상/실패 구독 실제 NAS 미확인 |
| S02 source 교체: `_receive → _apply_trade_source_approval:552` | 유지 source의 연속성은 보존하고 신규/제거 source의 RAM reference·누적 baseline을 reset | gap 시 baseline/observations 제거, 알려지지 않은 값은 실패 사실 유지 | 본문: 승인 set 갱신과 세대 증가; venue별 RAM key 사용 | credential drain은 수신 정지 후 flush; 실제 다계좌/credential 전환 미확인 |
| S03 계좌 이벤트 전달: `_publish_parsed:1130 → resolver → _deliver_account_event/publish_account_event:1239` | 시장과 분리된 scope resolver, FILL parser 결과 전달, 매수종목은 수집용 별도문서 | callback 예외는 경고/로그, 수신 owner 전체를 쓰러뜨리지 않음 | account-entry pending은 같은(day,code)의 최신값; 실패 restore 대입의 R3-034 확장 가능성은 본문 근거만 있음 | 실제 FILL/잔고·Risk 반영은 이 영역에서 실행하지 않음; 주문 안전 경계를 합치지 않음 |
| S04 0B 팬아웃: `_receive → _publish_parsed:1130 → Hub.publish → Top20/Shadow/route` | 실제 파서 DTO→payload→한 event fan-out, 미승인 trade source 거름 | 불완전 REAL data는 parser의 기존 빈 batch 의미; callback 오류 로그 | 동일 mutable event를 여러 queue가 참조하지만 현재 확인한 Top20 소비는 재구성/읽기만 함; mutation 결함 근거 없음 | 실제 PC worker/화면은 별도 담당. source별 drop/혼합 venue 전체 운영 미확인 |
| S05 최신 snapshot: `_publish_parsed:1180 → pending/latest → save_realtime_snapshots → initial_realtime_snapshots:1275` | 현재 RAM은 즉시, durable checkpoint는 별도; native read는300초 cutoff | snapshot 저장 실패는 `setdefault` 복원으로 await 중 최신 pending을 보존 | live RAM overlay와 DB read 사이 갱신은 RAM으로 보정. 다른 buffer가 같은 복원 정책은 아님 | periodic에는 pending 유지, 최종 close에는 저장. 기존 Collector cadence/close 테스트 경계; 현재 PG crash 미확인 |
| S06 0B 분봉 delta: `_publish_parsed:1205 → minute_bars.add → flush:1395 → save_minute_bars` | op ID/원본 누적 의미, complete query 권한 보호, 현재분은 periodic2초 fence 이후 drain | `_merge_minute_retry:1491`가 op payload 동일성 확인; lost ACK는 ledger로 중복 차단 | `_inflight_minute_bars` reader overlay, flush_lock 직렬. R3-001 로컬 저장소 SELECT 중복은 이미 완료되어 재등록하지 않음 | close 최종 flush/owned native join; 실제 PG 동시 commit/fault 원본은 미확인 |
| S07 분봉 finalization: `_flush_snapshot_cycle:1452 → drain_closed → finalize_minute_bars(SQLite875/PG1230)` | delta 저장 성공 이후만 close 처리, closure 원래 관측시각/품질 보존 | 실패 closure key별 복원; 저장 실패 때문에 다음 분을 먼저 확정하지 않는 기존 검사 본문 확인 | native query authority/processed op가 같은 transaction 안에 있음 | 강제 프로세스 종료/PG rollback 실제검증은 이번 미실행 |
| S08 0g reference: `_publish_parsed:1220 → RAM tuple key/pending code key → flush:1438 → documents` | parser/Hub/RAM/DB의 실연결 존재 | **R3-034 재현:** 저장실패 복원에서 새 pending을 구값으로 덮고 다음 retry도 구값 저장 | await 중 새0g140은 RAM/대기에 도착했으나 실패 뒤 pending130. venue별 RAM과 code별 문서 identity 차이는 정책 영향 추가 확인 필요 | close flush도 같은 복원 규칙 사용. 실제 장중 장애 빈도 미확인 |
| S09 시장지수 원본: `_publish_parsed market_state → _pending_market_history[market] → save_dataset_snapshots` | 같은 시장 최신 history 대기, snapshot과 별도로 시각별 dataset 저장 | `setdefault`로 최신 pending을 보존; reference restore와 다른 점 확인 | await 중 새시장값 유지, 현재 수집 빈도와 DB 비용 미측정 | close 마지막 pending 저장, 별도 통합 fixture 이번 미실행 |
| S10 RAM cut: `_observe_ram_trade:890 → ram_observation_snapshot:970 → Shadow/automation price` | 시간·중복 signature·원누적·source/venue·retention 보존,1회 cut 복사; DB read 없음 | 관측 역행/누적역행/retention cap은 gap을 남김 | source reset/같은 event-loop cut; code tuple membership·매행 datetime 재해석 비용은 정적 확인만 | lifetime owner는 Runtime에 단일 부여. 기존 RAM 관련 검사 본문, 이번 전체계산/운영 처리량 미측정 |
| S11 live 분봉 read: `market_read_routes → Collector.load_live_minute_bars:1308 → native load_minute_bars(SQLite802/PG1148)` | full-day 기본값; Shadow만 선택10분 range; date/venue/metadata 보존 | 일관된 native read snapshot으로 pending/retry/inflight op의 중복/권한 검사 | bound 밖 delta도 제외, DB+op ledger+authority 같은 transaction. R3-004 기존 구현/진단과 대조 | 현재 source에서 새로운 회귀를 반복하지 않음. PG 실제 snapshot/lock/계획 미검증 |
| S12 최근일 read: `/recent-minute-bars → 31일 loop:99 → live reader` | 빈 날은 건너뛰고 실제 bars 있는 날짜 수로2~5일 채움; 가짜봉/TR 보완 없음 | **R3-039 재현:** 완전 빈 history도 native empty SELECT31회; 사용자 오류를0봉으로 추정하지 않음 | 연속일 read 동안 commit 도착 시 날짜별 결과는 서로 다른 snapshot; 기존 계약 유지 필요 | API 요청 취소/Collector change 동시 boundary 미확인 |
| S13 분봉 조회 ingest: `Broker persistence → MarketDataIngestor._ingest_minutes:143 → metadata range → replace_minute_bars` | actual/in-progress 보호와 query complete 교체를 구분; 원래 응답 추정 거래대금 유지 | 저장 실패는 broker recording_succeeded=false; 진단 비교 실패는 저장 성공을 되돌리지 않음 | native 권한/metadata/revisions transaction 유지. 외부 metadata 선행조회와 writer 내부 권한 검사는 무조건 중복 제거 불가 | **R3-038 재현:** 작은 page도 하루 meta 전량 read; 실제 PG writer 경합은 미확인 |
| S14 SOR 비교: `_ingest_minutes → _record_sor_trade_value_comparisons:217 → SOR/day read+document read → comparison documents → read API` | KRX/NXT components·SOR 실제값·비교시각·누락 유지; 소비는 `/trade-value-comparisons` | 비교 오류는 로그 후 기본 분봉 성공 유지 | **R3-038 재현:**1행 page10회에 SOR1200행 전달. page overlap/후속 NXT가 기존 components를 보존해야 함 | 원본 writer와 비교 writer를 통합 transaction으로 바꿀 필요는 없음. 비교 실패 재수행 정책은 별도 확인 |
| S15 broker 요청/캐시: `_request:370 → guard/cache/inflight → _resolve_cache_or_queue:431` | 같은 fingerprint 공유, deepcopy 반환; ka10001 시점/recording pending 특수계약 있음 | cache native lookup 실패는 future 예외; ingest 실패는 payload 전송 성공과 recording flag로 구분 | credential generation/ownership fence, 요청 cancel은 shared future abandon 안함 | `_close:333` lookup 합류→queue sentinel→persistence sentinel; 실제 지속요청 close 경합 미측정 |
| S16 broker 송신/저장: `_run:497 → transport_call → persist_queue → _run_persistence:564` | 송신worker와 DB 후처리worker는 분리되어 있음; ranking reservation requeue 후 yield | handler/cache 오류 별도 로그·recording flag; 오류만으로 성공 payload 철회 안함 | 송신은 다음 TR 진행, 원요청 ACK는 ingest/cache 후. '모든DB가TRworker를막음' 주장은 반증 | 원자 저장/계좌 계층을 제거할 근거 없음. 실제 queue/PG wait는 R3-006 |
| S17 TOP20 순위: `_schedule_loop:722 → _select_ranking_response:257(request_unrecorded) → refresh_ranking_once:301` | fresh20칸 검증→RAM membership pending 공개→collector prepare→subscription schedule→DB ACK persisted | 오래된/빈칸은 제한 재조회·미공개; 회원 저장 실패는 fresh projection을 철회 안함 | **R3-035 재현:** RAM 공개 다음의 collector_lock이 index DB 대기를 상속해 구독후속이 지연 | 실제 NAS TR 회차/복구 수락 미검증; known static code와 fixture까지만 |
| S18 TOP20 지수: `_index_loop:904 → _advance:920 → _save_index:937 → _queue_index/_flush_pending_indexes` | collection_open+upstream_ready/queue gap을 completeness에 반영 | index 오류는 pending/outbox 보존하고 loop 계속 | **R3-035/036 재현:** collector_lock 안에 native await; flush 두 진입이 같은payload2회write 가능 | outbox configured app는 restart load, 삭제/단순화가 아니라 lock/owned flush 책임을 먼저 좁혀야 함 |
| S19 프로그램수급: `_event_loop:868 → ProgramSnapshotWriter.enqueue → _index_loop.flush → save_dataset_snapshots` | subject별 최신 pending + 별도 detached batch | 실패 `setdefault` 복원, caller cancel은 native task abandon 안함 | 단일 owned save task가 중복flush 차단. Top20 index가 현재 이것과 다른 수명이라는 실질 대비 근거 | `Top20._close:254 → drain`은 진행batch합류 후 최신분 저장. 이 owner는 유지 |
| S20 일봉/기본정보/백필: ranking후 `_schedule_fundamentals → stage/day tokens → broker → ingest/native → marker` | KRX/NXT 단계·daily version 분리, 실제빈칸만 보완, source 불명0값으로 채우지 않음 | 코드/단계 재시도·부분 성공 보존, 마지막 marker read가 장마감 넘으면 조회생략 | 별도task가 main ranking에서 분리됨; `_daily_input_versions`는 thread lock 보유 | 이번 fixture는 이 경로를 실행하지 않음. 이전 test본문의분리/실패범위 확인, 실제 장후/캘린더자료 미확인 |
| S21 WebSocket 소비: `realtime_routes.realtime:33 → subscribe → native snapshot/RAM overlay → send_events` | 인증·ACK·snapshot·upstream readiness 순서, native Hub drop accounting | **R3-037 재현:** sender.send 예외 후 finally의 await가 hub.disconnect를 건너뜀 | dead subscriber/codes가 union에 남음. sender만 죽고 receive가 대기하는 더긴 유지시간은 가설 | 정상 disconnect는 기존 ASGI fixture가 확인하나 sender 실패 branch는 빠져 있음. 실제 socket 에러빈도 미측정 |
| S22 store aggregate: `create_query_store → SQLiteQueryStore/PostgresQueryStore → native mixins` | Protocol은 static typing/aggregate는 조립; leaf가 각 SQL/transaction 소유 | native exception 전파·caller retry ownership. 무차별 fallback backend 없음 | SQLite RLock은 같은 store 전기능 공유; PG per-call connect. 실제 쟁점은 숨은 private state와 대기 원인 | R3-016 유지와 내부 응집 미완료는 동시에 참. 모든107메서드의 실행/PG parity를 증명하지 않음 |
| S23 서버 close: `app.close_services:730 → services → collector → broker → store` | 소비자부터정지·owned drain 후 dependency release, 반복 cancel에도 shutdown 합류 | 첫 close 오류 뒤 후속 dependency close중단은 기존 **명시 계약**; 신규결함으로 등록하지 않음 | credential drain은 generation증가→수신중단→native합류→gap/flush, failedpending보존 | 실제 syscall/process강제종료·NAS 장시간은 별도. R3-037는 이 outerlifespan이 아닌 client route local cleanup 문제 |

### 매매·계좌 세부 연결 — T01~T22

상태의 ‘검증’은 아래 격리 범위에 한정한다. 실제 broker/운영 DB/설치 앱/NAS 확인을 의미하지 않는다. 코드 위치는 `function-lines.json`, 검사명/로그는 `checks.json`에 대응한다.

| 세부 경로 | 실제 파일:함수:행 / 상태 소유자 | 확인한 정상·실패·동시·종료 | 남은 확인 / 관련 F |
| --- | --- | --- | --- |
| T01 UI account/spec 선택 | `mock_automation_dialog.py:_request:410`, `_load_context:134`, `operation_availability:70` / dialog RAM+server revisions | policy7건: READY/정확active spec, 상태read실패, 복원오류, account permission/범위결측 차단 | 실제 Qt 시작→실제NAS response 미실행. F17/F23 |
| T02 candidate 준비/게시 | `mock_automation_dialog.py:_publish_selected_candidate:467`, `mock_publication_routes.py:publish_mock_automation_candidate:52`, `publish_mock_automation_spec:161` / repository 불변계보 | 본문상 현재binding·실제 Shadow event·구현hash 검증/게시와 시작 분리 확인 | 이번 실제파일 게시→재시작→Runner package 연결 fixture 미실행. F15/F17 |
| T03 Shadow→READY | `mock_automation_specification.py:publish_ready_mock_automation_spec:221` / forward/stage/spec | 실제 stored event를 다시 찾고 candidate/family/session/binding 조건 대조하는 본문 확인 | 모든Family/구형버전별 수락·중간단계저장실패 후재게시 미실행. F11/F12/F15/F17 |
| T04 계좌설정 UI→owner | `nas_credentials_dialog.py:428`, `central_credentials_client.py:update_account:285`, `credential_runtime.py:update_account_settings:592`, `mock_runtime.py:_update_settings:542` / owner+nativeCAS | R3-042 actual owner fixture에서 same settings rev2/bundle 유지, 변경rev3→다시허용rev4 검증 | 실제GUI→ASGI→현재owner까지 하나의 fixture는 아님. F17/F23/F24 |
| T05 계좌 settings no-op | `database_account_settings.py:_save_account_settings:229`, `mock_runtime.py:_update_settings:584`, `real_runtime.py:_update_settings:641` / native revision와applied revision | 기존 native no-op과 current credential 검증 후 owner no-op 확인, R3-042의 same settings에 실제close 없음 | 불필요한 vault/load 비용은 미측정. 권한검증 생략 안함. F23 |
| T06 AUTO 입장 | `mock_automation_supervisor.py:activate:131`, `mock_runtime.py:switch_execution_mode:634` / account lock+불변bundle | 이전R3-008 fake admission/runner3건을 승계하되 이번 actual bundle AUTO 전환과 new gate false 확인 | 새fixture의 Risk계산/control은fake, 실제 로컬SQLite lease/Runtime.start는 사용했다. 실제 admission/gate/transport 승인·운영 lease는 미검증. F17 |
| T07 owner 교체→runner 조정 | 같은 `_update_settings:589-612`, `supervisor._restore_once:431`, `activate:143-147` / 두 owner map | **R3-042 재현:** retired old runtime, newMANUAL, stale runner key skip, activate구runner반환, stop실패, 재허용후UI제어모두False | 영속control/실제task실행/실제주문안전은미검증. F17/F18/F24 |
| T08 restore idle/외부control | `supervisor._restore_loop:418`, `_restore_once:431` / supervisor task+retry map | 기존R3-00811조건: idle0/active0, STOPPED published3×30=90, 외부change/cold/latebinding/retry/close | 외부change channel 없음. 이번 비용 재측정 안함. owner교체조정이 새 보강. F17/F24 |
| T09 분봉→판단 | `runner.run_once:2018`, `_consume:2386`, registry.evaluate_bar / runner bars/universe/state | 실제run_once에 비거래consume seam으로20prefix/20checkpoint·idle0 확인 | 실제minute→evaluator→Risk→intent 통합은이번미실행. F11/F15/F17 |
| T10 0B leader→판단 | `runner.observe_leader_trade:502`, `_run_leader_page:577`, `_observe_leader_stop:540` / queue·source·pinnedproof | source/context/discontinuity·openposition proof·nativeRisk/claim 연결 본문 확인 | 이번 actualleader kernel+queue/drop/보호와order state통합fixture 없음. F12/F17 |
| T11 최신Risk→dispatch | `mock_automation_execution.py:_validated_risk_metrics:1054`, `dispatch...:647/734` / storedRisk/recovery | 현재storedRisk 다르면 dispatch거절 기존fixture1건 통과 | 모든동시 Risk/control갱신 시점·PG snapshot경합 미측정. F16/F17 |
| T12 nativeclaim | `execution_runtime.py:submit_automation_intent:181`, `database_execution.py:_require_execution_ownership:17` / native writer transaction | persistedSTOP이 oldcontrol claim거절하는 기존fixture1건 통과 | 실제PG lock/wait/프로세스중단 별도. F16/F17 |
| T13 manual same-request | `execution_runtime.py:_submit_limit:374`, `load:452` / gateway command lock | R3-040 실제 gateway2load, firstworker/secondloop, refresh0/transport0. 기존멱등/취소전refresh1건 통과 | immutable scope재사용후patch는미구현. F16 |
| T14 runtime/lease/drain | `execution_runtime.py:automation_decision_guard:170`, `_require_active:285`, `_command:347`, `begin_credential_change:324` | waiter취소뒤실제명령drain, transport대기중 heartbeat갱신/stop대기2건 통과 | livelease/postgres·모든동시cancel/reconcile 미실행. F16/F24 |
| T15 송신ACK/UNKNOWN | `order_lifecycle.py:submit:758/cancel:784`, `mock_execution.py:submit/cancel` / durableintent/event | UNKNOWN재시작자동재송신안함1건. fake transport만 사용 | broker실제ACK/지연/계좌수락미실행. F16 |
| T16 00/04→RAM/Risk | `mock_account_monitor.py:handle_realtime:498`, `_recover_once:563`, `_publish_risk:658` / monitorqueue+recoverylock | opt-in fullrecovery2sharedreads/RAMgetter0, 실행중query/ledger실패가empty승격안함2건 통과 | 실제WebSocket페이지/누락/비정상order번호전파미실행. F18 |
| T17 FILL→전략position | `order_lifecycle.py:reconcile:805`, `runner._apply_new_fills_core:2509` / 원장→fillcursor/dedup | close가진행중fillread join후checkpoint하는1건 통과, 상세fill만position변경 본문확인 | 이번full 상세/aggregate중복·잔고변화실계좌대조안함. F18/F24 |
| T18 미체결등록/후속관리 | `runner._dispatch_original_and_register:1302`, `_register_native_unfilled:1201`, `_produce_native_unfilled_inputs:1311` / rootanchor·checkpoint | dispatch뒤등록/checkpoint, 종료미해결oldanchor 보존·recovery discovery 본문확인 | dispatch성공→receipt/registration save실패→재시작의전체chain 새fixture 미실행. F16/F17/F24 |
| T19 clock/보호 | `runner._poll_session_clock:2199`, `_poll_first_protection_exit:756`, `_cancel_unfilled:1812` / clockdocument·protection/preflightcue | STOP/control/recovery/claim 재검증·ACK를flatproof로대체하지않는 본문확인 | 실제0B/시간동시·취소ACKloss·각plan재시작 조합은이번미실행. F17/F24 |
| T20 checkpoint ACK/restart | `runner._save_checkpoint:2736`, `_restore_or_bootstrap:2568`, `close:1950` / prefix·seenfills·lastACK | noop/forcedclose·commitACKloss재평가금지·pendingACK후속입력차단·closejoin 4건 통과 | fakeconsume20prefix측정은전략동등성아님. 운영process crash미실행. F17/F24 |
| T21 real read-only계좌 | `real_runtime.py:read_account:232`, `_read_account:250`, `_monitor_account_cycles:413`, `mock_account.py:_KiwoomAccountReader.read:107` / realcontext·readslots·binding | 실제owner/fakebroker 환경별route/scopes분리, close완료까지drain/새read거절2건 통과 | 실제계좌/TR/socket미실행. 이 경로에 mock gateway/runtime을 붙이지 않음. F14/F18/F24 |
| T22 global설정DB/cache | root `settings_probe.py` / SettingsRepository.cache+syncthread | 루트 별도fixture의 get-fetch→nativewrite/commit→clear_cache→old재삽입재현은 R3-011 보강으로 승계 | 이 감사 담당의재현/수정아님. 전체remote설정수락은검증하지않음. F23 |

### 뉴스·연구·진단 세부 연결 — N1~N8/R1~R7/D1~D4

F 매핑: N1~N7=F19, N8=F22·F19·F24, R1~R5=F21, R6=F11·F12·F15·F17, R7=F15·F17, D1~D4=F25·F24. 본문만 확인한 세부경로는 실제 실행의 확인 불가로 유지하며, 신규 재현된 실패branch는 부분 연결이다.

`본문`은 아래 owner/caller/store/consumer의 함수 본문을 읽고 실행 연결을 따라갔다는 뜻이며 운영 수락이나 관련 전체 테스트 통과를 뜻하지 않는다. `부분`은 경로 일부만 읽어 미확인 지점을 명시했다.

| Subroute | 진입 → 상태/이벤트 owner → 저장/조회/표시·후속 | 실패·복구·종료 확인 | 이번 수준/남은 범위 |
|---|---|---|---|
| N1 종목 뉴스 자동수집 | app.create_app → CentralNewsService.start/refresh_once/_collect → news_article/news_sync → cached+confirmed → auto_analyze | refresh 종목별 except/외부 공급자 오류 fallback, close의 owned collection join | 본문. R3-019 반환 버림/재조회/오류 continue를 재확인. 실제 비용·동일 반환 집합 미측정 |
| N2 query set | CentralNewsService → QuerySetNewsCollector.start/run_once/_collect_query → save_news_source_page → article/BODY/cursor | shielded _collecting/close, page별 cursor·pending/last_success, 오류 재기록 | 본문. SQLite/PG source-page native writer 진입 확인. 실제 공급자 pagination/PG 중간 장애는 미검증 |
| N3 시황/common/flash/world | service → MarketNewsCollector.run_once/_collect → date-scoped source page → GLOBAL/target/BODY | 429/5xx bounded retry, repeat/old-day/cap 상태, cursor/error 저장, close/loop | 본문. 02시 전날 보완 등의 실제 운영 시간은 미검증. host datetime/time 잔여는 R3-023로 귀속 |
| N4 BODY/RULE | NewsJobRunner.run_once/_run_body/_run_rule → immutable body+RULE enqueue/native event writer → news_assessment/event/membership | 요약 fallback/3회 실패, body와 다음 RULE enqueue 같은 store transaction·commit 후 wake | 본문+R3-043 SQLite 재현. claim 세대·실제 PG는 별도 |
| N5 자동 AI | service._auto_analyze → enqueue_news_ai_jobs → runner._run_ai → CentralAIService.analyze/_prepare_and_run/_run → news_ai+revision+usage | accepted/shield·동일입력 task 병합, execution lock/캐시, credential snapshot, native save_news_ai_results transaction | 본문. 실제 AI/provider·과금·계정 회전은 미실행. prompt/body adapter를 무조건 하나로 합치지 않음 |
| N6 PC 목록/본문 근거 | NewsWindowCoordinator child command → StockNewsWindow → NewsSearchWorker → CentralNewsClient.stored_page → service cache/confirmed → local repository → NewsPrepareWorker → table/group | Stored evidence worker cancellation/request ID, article/title/description·body_revision·membership/event ID 일치 후 표시, 실패 notice·shutdown | 본문. live Qt/HTTP 통합은 미실행. 병행 뉴스 history cursor 서명 변경의 reviewed baseline 승인은 이번에 하지 않음 |
| N7 수동 PC AI | AINewsWorker.run → central analyze 또는 local article fetch/분석 → 기존 AI repository/UI 결과 | 중앙 경로와 local grouped-article 입력 역할 분리, plan cancel event/worker 종료 대기 | 부분. worker본문 확인, 모든 UI queue/매뉴얼 재분석 소비자는 미전수 |
| N8 콘텐츠 push/pull | news_process timer → sync.push/read/manifest/upsert → sync.pull/cursors → news/theme writer → 완료signature | push 성공 batch별 manifest, 두 DB 적용 뒤 cursor, pull echo hash | 본문+R3-044/045 재현. UI 실제 동시 편집·다중프로세스/PG는 미검증 |
| R1 연구 child 진입 | ResearchDialog._start/_launch_campaign_worker → AuxiliaryProcessManager.start → research_process.main/CLI → load_request/input | StrictRestoreCoordinator child scope, fixed result/cancel, manager stop/exit wait, atomic result replacement | 본문+R3-046. 실제 새 child spawn/restore 잠금 실험은 미실행 |
| R2 limited search | execute_process_request → frozen input/identity → repository.enqueue/start_search_job → evaluate/record → commit_trial_result → cards/finish → UI poll/_apply_result | owner_token/generation renewal, interrupted attempt, slice/cancel, 기존 완료 job 재사용 | 본문. 모든 trial/장시간 lease·kill 복구는 미실행. 반복 load 비용은 이번에 계측하지 않음 |
| R3 campaign | UI claim → run_campaign_worker → discover inputs/hypothesis schedule/execute_campaign_cycle → search job → atomic result → UI retry | repository claim/renew/finish의 owner+generation/lease 검사, 부모·child의 idempotent 종료, loop lease 체크 | 본문. discover_campaign_inputs의 staging/NAS 전체 하위분기는 부분만 확인, 실제 NAS·disk cap·orphan cleanup은 미검증 |
| R4 independent/final | validation/final dialog child → execute_development_validation/execute_final_holdout_evaluation → locked dataset/code hash/run claim → research report → atomic progress/results | completed cache 검증·cancel·explicit final recovery 조건·immutable candidate set | 본문. 모든 fold/bucket 조합과 crash recovery native DB 동시성은 미실행 |
| R5 연구 관측 export | research_read_routes.research_observations → create_observation_export → fixed membership/watermark → native page → research_data_source FrozenResearchDataset | request parameters/watermark 대조, immutable revision member 목록, partition point-in-time | 본문. API route+SQLite/PG 구현 확인, 실제 ASGI/PG 동시 변경 fixture 미실행 |
| R6 evaluator 공유·Shadow/Runner | scripts/run_research.run_research의 family.evaluate_bar → paper engine; CandidateMonitor._consume → shared family → shadow save; MockAutomationRunner bar path → same family → fill/Risk/order owner | shared frame cache는 validated native page 한 round, Shadow state checkpoint/bootstrap, Runner fill-owned state 보존 | 본문. 모든 Family 동등성/실제 주문 수락 미검증. R3-018 유지 필요성은 해당 ID의 별도 평가 |
| R7 결과 게시/사용 | prepare_mock_automation_candidate CLI → readonly research evidence → package/eligibility → immutable output; mock_publication_routes → repository publish → 저장 spec/후속 Runner | current binding·scientific hash·size·immutable contract, 저장 응답 orders_started=False | 부분. 준비·route 본문/Runner evaluator 소비를 확인. CLI publish HTTP·repository 모든 CAS·supervisor 기동 승인 전체는 미전수, R3-024 범용 자동 활용으로 확대하지 않음 |
| D1 진단 API/run | app.create_app → diagnostic routers → DiagnosticRuns.start/_run → sampler/compare/replay → report/manifest/status | request_id/session/revision, run lock, quota/TTL, stop flag, report 저장 실패 상태·child 최종 join | 본문. 실제 OS sampling/PG latency/trace capture는 미실행 |
| D2 store capture | SQLiteQueryStore/PostgresQueryStore 생성 → install_store_capture → input_token/owner→freeze/payload → native call → receipt | OFF이면 original 직접호출; capture 실패는 원래 write 결과와 분리, native finally context 복원 | 본문. OFF 비용·대형 payload peak/프로덕션 capture 없음. wrapper 필요성과 내부 코드 비용은 별도 |
| D3 manifest/replay read | trace.stop/recover_interrupted → atomic manifest → recorded_events/window reader → plan | incomplete/old schema/drop/censored 거절, chunk/sequence/hash 검증, restart unknown_tail_loss | 본문. 소유 capture 파일 생성/손상 재생 실험은 미실행. checksum만으로 replay 준비 완료 판단 금지 |
| D4 owned replay/restore | offline run_owned_recorded_experiment → baseline lease → native operation/collector → runtime drain → exact restore → lease close | actual submitted executor thread를 finally에 retire, cancel waiter와 실제 종료 분리, advisory/process lock·outsider session·owned connections·generation | 본문. dedicated PG/actual restore/concurrency는 미실행. 운영 writer/주문 transport 대체 경로를 만들지 않음 |

### 새 개선 항목 — R3-030~050

아래 상대 코드파일명은 각 경로의 패키지(`presentation`, `infrastructure/persistence`, `infrastructure`, `central_server`)를 뜻한다. 실제 caller/owner/SQL/소비 본문과 대조했으며 상세 행 지도는 진단 report의 function-lines/source 해시에 보존한다. 신규 ID 모두 대기이고 제품 수정/완료 commit은 없다. 분류 ‘제거’는 명시한 중복 실행/데이터만, ‘통합’은 기존 owner 안의 중복 책임을 뜻한다. 안전 경계 전체 삭제를 뜻하지 않는다.

### R3-030 — API 교체 중 일지 수급 enrichment cache의 구 provider 결과 재삽입

- 분류/우선순위/상태: **재설계 / P1 / 대기**. 변경 범위는 아래 owner의 해당 경로와 관련 검사다.
- 현재 실행/소유 경로: `AppController._api_runtime_workers:548 → _apply_api_runtime:606(619~650) → EntrySnapshotWriter.set_investor_loader:45 → _investor_at_execution:145 → cache → 일지 저장`. F18/F22/F23/F24.
- 원인·코드 근거: API producer drain 목록에 entry writer가 없고 controller가 writer private loader/cache를 읽어 교체한다. cache clear 전 시작한 old loader가 나중에 code-only60초 cache를 다시 채운다.
- 측정/가설 구분: `pc_probe.py`의 실제 controller swap/실제 writer 메서드·Python thread barrier: new loader 설치됐지만 다음 요청 source=old-runtime/cached=true, old1/new0. 실제 QThread/HTTP·계좌정보 유출/주문 오류는 검증하거나 주장하지 않는다.
- 권장 방법/예상 효과: 기존 runtime 교체 경계에서 loader generation 또는 job별 provider 소유를 고정하고 old 결과가 새 cache를 채우지 못하게 한다. 기존 job 결과/provenance는 보존한다. 새 서비스/폴링은 불필요하다.
- 부작용·회귀 위험: origin_scope·old job 정상완료·교체 실패 rollback·cache hit/만료·backfill/close를 보존해야 한다.
- 의존 관계: R3-014/017/025, R3-031 같은 writer이므로 직렬.
- 완료 기준/검증: 실제 QThread에서 old loader 대기→성공/실패 교체→새 job/rollback/종료를 대조하고 새provider 사용/불필요TR0·cache출처를 확인한다.
- 연결: F18/F22/F23/F24. 변경 내용/해결 후 측정/관련 테스트/commit: **제품 변경 없음 / 미해결 / 없음**. 이번 진단은 재현 근거다.


### R3-031 — 일지 backfill 저장 예외와 잔여 queue의 종료 판정 누락

- 분류/우선순위/상태: **재설계 / P1 / 대기**. 변경 범위는 아래 owner의 해당 경로와 관련 검사다.
- 현재 실행/소유 경로: `MainWindow` snapshot enqueue/장후 program·investor backfill → `EntrySnapshotWriter.run:52 → _backfill_program:98/_backfill_investor:119 → repository.save_*`; `AppController._drain_writers:955 → _complete_shutdown:1006`.
- 원인·코드 근거: backfill loader 오류는 처리하지만 repository save 오류는 run 밖으로 나가며 다음 job/실패 payload ACK가 없다. failed는 MainWindow에서 로그만 받고 controller는 실행중 여부 외에 잔여 일지 queue를 확인하지 않는다.
- 측정/가설 구분: actual run 직접 호출+저장fault: 예외 escape/queue1/failed signal0. 실제 controller completion은 이 잔여 queue인데 ready를 게시한다(`pc-measurements.json`). native QThread crash/운영 자료 손실은 미실행이다. base save 최종실패를 로그 후버리는 경계도 본문 확인만 했다.
- 권장 방법/예상 효과: 기존 writer 안에서 job 단위 실패·미저장 payload/잔여 작업을 소유하고 controller가 그 결과를 종료 판정에 소비하도록 연결한다. 새 durablequeue/Manager를 임의 도입하지 않는다.
- 부작용·회귀 위험: 정상 snapshot과 enrichment/backfill의 실패 의미·기존 retry 한도·중복삽입/정지/다음job 처리·DB close 전 native완료를 보존한다.
- 의존 관계: R3-014/025, R3-030과 파일·상태 공유.
- 완료 기준/검증: 실제 writer/QThread에서 initial/backfill save 오류·뒤따르는job·retry·close를 주입해 무음잔류0/원문보존/ready거짓게시0·readback/중복0을 확인한다.
- 연결: F18/F22/F24. 변경 내용/해결 후 측정/관련 테스트/commit: **제품 변경 없음 / 미해결 / 없음**. 이번 진단은 재현 근거다.


### R3-032 — bootstrap plain daemon 작업의 종료·restore lease 수명 누락

- 분류/우선순위/상태: **재설계 / P2 / 대기**. 변경 범위는 아래 owner의 해당 경로와 관련 검사다.
- 현재 실행/소유 경로: `bootstrap._run_desktop:128`의 content sync daemon:200, retention daemon:395/30초 callback:398, settings daemon:419/60초timer → native/file IO; `app.exec` 뒤 finally:430/outer restore gate:108~125.
- 원인·코드 근거: plain daemon Thread 참조/join이 없고 AppController producer 목록/findChildren(QThread)에 포함되지 않는다. 종료는 timer/dispatcher/server를 닫고 restore lease를 해제하지만 시작된 rawthread는 그 경계에 합류하지 않는다.
- 측정/가설 구분: 본문으로 소유 누락 확인. slow in-flight 실제 GUI/restore fixture·운영 중단/DB 손상은 **미재현 가설**이다. 동기화/retention이 반드시 종료 시 실행중이라는 주장은 하지 않는다.
- 권장 방법/예상 효과: 현재 bootstrap이 만든 작업의 참조/완료를 기존 shutdown/restore 경계에서 소유해 timer중지 후 해당 native 작업을 join/cancel 가능한 지점까지 기다린다.
- 부작용·회귀 위험: UI중지 deadlock/무한대기·HTTPtimeout·dependency닫힘·restore중외부쓰기. 새로운 전역 lifecycle manager는 필요하지 않다.
- 의존 관계: R3-011/021/025, 실제 기동격리검증 필요.
- 완료 기준/검증: 느린 fakeHTTP/실제임시DB 작업 중 앱종료/재시작/staged restore를 수행해 작업종료 전 dependency/lease해제0, 새 작업 시작0을 확인한다.
- 연결: F01/F22/F24. 변경 내용/해결 후 측정/관련 테스트/commit: **제품 변경 없음 / 미해결 / 없음**. 이번 진단은 재현 근거다.


- **최종 잔여 감사 보완:** bootstrap 외에도 CentralJournalSyncRunner.schedule의 plain daemon과 ThemeDispatcher.close의 timer cancel/진행작업 미join 경계를 확인했다(실제 종료 경합 미재현). 새 관리 계층 없이 기존 owner의 작업·restore lease 수명 계약으로 검증한다. journal의 동일자료 왕복 비용은 별도071이다. 근거 디렉터리: `data/diagnostics/refactor-r3-final-audit-20261018/`. 제품 변경·해결 검증·새 commit 없음.

### R3-033 — 첫 틱/거래일 변경의 GUI 동기 retention DELETE

- 분류/우선순위/상태: **재설계 / P2 / 대기**. 변경 범위는 아래 owner의 해당 경로와 관련 검사다.
- 현재 실행/소유 경로: `MainWindow._on_trade_tick:3046 → _ensure_today_minute_bar_storage:3028 → flush/discard → MinuteBarRepository.purge_before:627 → commit`.
- 원인·코드 근거: 기동 초기 `_minute_bar_storage_date=None` 또는 날짜 변경 시 GUI callback 안에서 minute_bars/meta/history_sync/index 테이블 DELETE를 직접 수행한다. background bootstrap retention도 있으나 이 별도 경로가 남는다. 오류여도 storage_date를 새날로 설정한다.
- 측정/가설 구분: 실제 MainWindow 메서드/fake repository에서 첫날/동일날/다음날3호출에 purge2·호출 GUI thread 확인. nativeDELETE시간/DB규모/실제0B 지연·오류후재시도 영향은 미측정이다.
- 권장 방법/예상 효과: 날짜별 RAM reset과 저장소 retention 소유를 구분하고 기존 background retention/writer 경계로 불필요 GUI I/O를 옮길 수 있는지 작은 범위를 검토한다.
- 부작용·회귀 위험: 날짜 partition/dirty분봉 ACK·집계discard·gap/meta/source·retention cutoff/실패재시도 의미 보존.
- 의존 관계: R3-025/027; 새 주기/폴링 추가 없이 기존 owner 재사용.
- 완료 기준/검증: 첫틱·동일날·자정·purge실패·close 중 실제 임시DB readback/누락0, GUI callback nativeDELETE0 및 같은 retention결과를 검증한다.
- 연결: F04/F05/F24. 변경 내용/해결 후 측정/관련 테스트/commit: **제품 변경 없음 / 미해결 / 없음**. 이번 진단은 재현 근거다.


### R3-034 — Collector reference/entry 실패 복원에서 최신 pending 손실

- 연결/위치: `realtime_collector.py:_publish_parsed:1220~1238 → _flush_snapshot_cycle:1438~1450`; `database_documents.py:upsert_documents`; 조회/소비는 `content_routes`의 `stock_price_references`, `market_events.py:_ensure_symbol:748~757`의 상한가 복원. F07/F10/F24 및 현재 RAM/영속 reference 연결에 영향.
- trigger/원인: old reference를 detach→native write await 중 새 같은 code 도착→native 실패→`self._pending_stock_references[owner] = old`가 최신 pending을 덮음. 정상 RAM은 새 값이며 재시도는 구값을 저장한다. 계층이 필요한지와 별개인 **상태 복원 책임의 불일치**다.
- 확정/실측: `reference-retry.json`: actual0g130→저장중140→during pending140/failed pending130/latest RAM140/retry SQLite130. 새140이 DB까지 전달되지 않았다. 같은 buffer의 실제 동시 실패 재현이며 운영 장애율은 미측정.
- 연관 가설: `:1434` account-entry restore도 같은 대입 패턴. 동일(day,code) 새 문서의 `last_executed_at`이 덮일 가능성은 본문상 확정 경로지만 실제00 parser→복원은 이번 미재현. 계좌 거래/FILL 레저 손실로 확대하지 않는다. RAM tuple(code,venue)/영속 code identity차이도 확인됐으나 올바른 venue정책은 이번 미확정.
- 분류/우선: **재설계(기존 복원 몇 줄의 최신값 병합) / P1**. R3-007의 outer owner 유지 결론을 보강하되 독립 후속 ID로 관리할 수 있다. R3-003/가격 writer retry와 코드/owner가 다르다.
- 최소 방법/효과: snapshot/market_history/ProgramSnapshotWriter가 이미 사용하는 `setdefault` 방식과 대조해 old restore가 새 pending을 덮지 않게 한다. 공통 Manager/새queue/새clock/추상화는 불필요하다. op-ID minute delta에는 같은 key 정책을 무조건 적용하지 않는다.
- 범위/의존/위험: Collector 두 복원구간과 해당 기존 테스트만. 최신보존 기준은 이미 arrival-last dict이므로 새 timestamp정책 금지. 새 값이 없다면 old retry는 남겨야 하며 source/day/venue와 close/credential drain 중 실패도 보존해야 한다. 계좌승인/원주문 경계는 건드리지 않는다.
- 완료/검증: 이 fixture를 본래 요구 assertion(latest140 persisted)으로 전환, 새값없음/여러실패/동시새값/기동후read/close실패·재시도, account-entry 별도고정fixture, 정상원문/시각 동일. 이번은 **대기: 원인·재현 확보, 제품 미수정**.

- 확정 분류/우선순위/상태: **재설계 / P1 / 대기**. 변경 내용/해결 후 측정/관련 commit: **제품 변경 없음 / 미해결 / 없음**. 진단 통과를 수정 완료로 해석하지 않는다.


### R3-035 — TOP20 collector_lock이 index DB/파일 await까지 보호

- 연결/위치: `autonomous_top20.py:_index_loop:904~918`의 `async with _collector_lock` 안 `_save_index`; `:937 → _queue_index:941(outbox.put) → _flush_pending_indexes:951 → _write_index:960 → database_datasets.save_dataset_snapshot`. 경쟁 caller는 `refresh_ranking_once:340~350`의 같은 lock과 그다음 `_schedule_subscription_update`다. F03/F09/F04.
- trigger/인과: 분 경계 완료record 저장/파일put이 늦을 때 lock을 계속 보유해 새 순위의 collector prepare/구독 갱신이 대기한다. RAM fresh membership은 lock 전에 공개되어 그 부분까지 차단됐다고 말하지 않는다.
- 실측: `index-lock.json`: native index writer blocked 상태에서 `lock_held=true`, fresh membership published=true, refresh_done=false, subscription_scheduled=false, advance1회. 실제 indexloop→owned native writer와 competing refresh를 호출했다. `_advance`의 완료 record와 상류 순위 응답은 고정했다. 실제 loop/lock/outbox/native write의 경합을 검증했으며 순수 index kernel·실제 TR 수락의 새 검증은 아니다. 특정PG가 실제로몇ms늦는지·매일발생빈도는 미측정이다.
- 분류/우선: **재설계(critical section 축소) / P1**, R3-006 wait원인 조사 및 R3-007 내부책임에 연결. 막연한 pool도입과 다른 구체적인 lock await 경로다.
- 최소 방법/효과: collector state의 advance/완료record 추출만 lock 안에 두고 이미 만들어진 record의 outbox/native await는 밖에서 수행하는 안을 우선 비교한다. 새 reader/writer layer보다 현재 실행 경계 이동을 먼저 검토한다.
- 위험/의존: 분경계 record 유실·중복·membership실패 전 outbox 등록 순서·반복close/cancel을 보호한다. R3-036가 같은 method를 소비하므로 동시 flush도 같이 관찰하되 한 번에 광범위한 owner 재구성을 하지 않는다. DB transaction들을 하나로 합치지 않는다.
- 완료/검증: 동일 blockedwriter에서 collector lock 획득/구독후속이 가능한지, 기존 index값/품질/구간clock 동일, outbox put→DB→remove/restart/partial membership 실패·늦은 native commit·close를 확인. **대기: 원인·재현 확보**.

- 확정 분류/우선순위/상태: **재설계 / P1 / 대기**. 변경 내용/해결 후 측정/관련 commit: **제품 변경 없음 / 미해결 / 없음**. 진단 통과를 수정 완료로 해석하지 않는다.


### R3-036 — index pending 두 caller가 같은 payload를 동시에 native 저장

- 위치/경로: `refresh_ranking_once:393 → _flush_pending_indexes:951`와 `_index_loop:907/_save_index:937 → 같은 flush`. pending items snapshot을 잡고 `_write_index`하는 부분에는 save task/flush lock이 없으며 `_index_outbox_lock`은 put/remove만 보호한다.
- trigger/원인: 첫 flush가 write await중이면 pending entry가 남아 두번째 flush가 같은 entry를 읽어 저장한다. 마지막 identity 확인은 **RAM/outbox에서 최신payload를 지우지 않기 위한 것**이며 DB 중복 실행을 막지 않는다.
- 실측: `index-duplicate.json`: 같은(kind,subject,key)의 native writer 진입2회/실제SQLite canonical1행/pending0·outbox정리. 두 실제 flush 메서드 진입을 직접 겹친 재현이다. 실제 loop가 얼마나 자주 겹치는지·PG WAL/CPU/추가revision 비용은 미측정. DB의UPSERT final1행을 비용0으로 취급하지 않는다.
- 분류/우선: **통합(기존 동일 flush 소유/직렬화) / P1~P2**, R3-007 보강 또는 별도후속. R3-035보다 먼저/함께 핵심동시성 기준을 확보한다. ProgramSnapshotWriter는 동일문제가 없으므로 다시구현하지 않는다.
- 방법/범위: 기존 service 안의 owned flush 또는 좁은flush serialization으로 동일batch를 한 번만 native 처리하는 안을 비교한다. 새 service/file/pool이 선행필수는 아니다. pending/outbox lock을 native 전체에 무작정 넓혀 R3-035를 악화시키지 않는다.
- 위험/완료: old/new same-minute payload 동시갱신, 쓰기실패·lostACK·retry·cancel·outbox remove도중신규값·restart·close가 보호돼야 한다. 최신payload가 비동기로 들어온 경우 후속flush까지 보존. 기준fixture2native→1과 최종동일/순서확인. **대기: 재현 확보**.

- 확정 분류/우선순위/상태: **통합 / P1 / 대기**. 변경 내용/해결 후 측정/관련 commit: **제품 변경 없음 / 미해결 / 없음**. 진단 통과를 수정 완료로 해석하지 않는다.


### R3-037 — client sender 예외가 Hub disconnect를 건너뛰어 구독이 남음

- 위치/연결: `realtime_routes.py:realtime:33`, `send_events:45`, `finally:104~108`의 `await sender` 뒤 `realtime_hub.disconnect`; `RealtimeHub.requested_codes:151 → Collector._receive:697`이 남은code를 상류대상으로 쓴다. F04/F24.
- trigger/원인: sender task의 `websocket.send_json`이 OSError/RuntimeError로 끝난 뒤 수신route가 종료되면 finally가 이미실패한 sender를 await한다. suppress는 CancelledError뿐이라 기존send예외가 다시나와 다음줄disconnect가 실행되지 않는다.
- 실측: `websocket-disconnect.json`: actual route endpoint+Hub/fakeWebSocket에서 sender실패 후 정상receive disconnect를 줬지만 client_count1/requested_codes005930이 남았다. fake socket의 오류주입은 실제transport 빈도 증거가 아니다.
- 분류/우선: **재설계(기존 local task cleanup) / P1**, R3-025 outer수명유지와 별개 내부미완료. Hub계층삭제로 해결되지 않음.
- 최소 방법: sender합류에서 예외가 발생해도 hub.disconnect가 수행되도록 route의 nested finally 경계를 보장한다. 원래송신오류를조용히성공처리하지 않는다. sender만죽고receive가대기하는경우 양방향task감시 필요성은 별도가설로 표시하고 이재현범위먼저고친다.
- 범위/의존/위험: realtime_routes와기존ASGI/socketfixture,구독union·REGtarget변화 소비확인. 정상disconnect·authfail·snapshot실패·ping/ready실패·queue gap전송실패·cancel/repeatedclose에서구독누수0. 계좌/Collector원본수신구독을지우면안됨.
- 완료: 같은failurefixture에서client0/codes빈값, 다른정상subscriber는보존되고별도오류가원래대로관측됨. **대기: 재현 확보**.

- 확정 분류/우선순위/상태: **재설계 / P1 / 대기**. 변경 내용/해결 후 측정/관련 commit: **제품 변경 없음 / 미해결 / 없음**. 진단 통과를 수정 완료로 해석하지 않는다.


### R3-038 — 작은 분봉 page도 전체일 metadata/SOR/비교문서를 다시 읽음

- 위치/소비: `market_ingest.py:_ingest_minutes:169~194`가 first_day~last_day 하루범위metadata를 읽음; `:217~279`에서각day의SOR분봉전체와비교문서최대2000개를읽고이번page분만사용. broker._run_persistence의 실제handler이며 비교API `market_read_routes.py:142`가소비한다.
- 실측: `ingest-day-reads.json`: 실제120분×KRX/SOR fixture에서1행page10회→metadata조회10/1200행, SOR조회10/1200행, 비교문서조회10회. 값/row이운영상동일빈도로중복수신된다는주장은아니다. 이 fixture는 native querypage이후 재계산/진단비용의 고정기준이다.
- 원인/분류/우선: input page 키보다read범위가날짜전체로확대됨. **제거(불필요한행전달)/P2**. Shadow R3-004나Candidate R3-005와다른 ingestorcaller이므로 완료항목을재등록하지않는다. R3-016 leafreader 재사용평가에연결.
- 권장: 기존range reader로 SOR범위를 실제page에필요한분으로제한하는 작은안부터비교. metadata는 actual/in-progress보호용필요키만보는안/기존range범위를좁히는안검토. comparisons는 components후속KRX/NXT병합을보존해야하므로비교문서read를단순삭제하면안된다.
- 위험/의존: 중첩/불연속page·날짜교차·completequery권한·emptySOR·늦은SOR·원본저장성공/진단실패독립·available_at/value_kind·replay를보호한다. `_replace_bars`의native권한검사는transaction시점보호라선행metadata와기계적중복이아니다. 새batch/pool/schema금지.
- 완료: 고정page같은최종분봉/meta/비교문서(비교시각정책포함), 실제반환행/쿼리수/연결을전후대조;PGSQL계약과가능시격리PG확인. **대기: 비용재현확보,개선안미구현**.

- 확정 분류/우선순위/상태: **제거 / P2 / 대기**. 변경 내용/해결 후 측정/관련 commit: **제품 변경 없음 / 미해결 / 없음**. 진단 통과를 수정 완료로 해석하지 않는다.


### R3-039 — 최근2거래일 API가 빈이력에서31개날짜를 직렬native조회

- 연결/위치: `market_read_routes.py:recent_minute_bars:95~124 → load_display_minute_bars → Collector/native load_minute_bars`; PC의최근분봉복구/차트소비. F08.
- 실측: `recent-minute-reads.json`: actualrouterendpoint와빈임시SQLite에서요청2거래일·bars0에native31회read.31은현재기존scan상한이며새차단조건이아니다. 사용자실제빈symbol호출빈도와PG대기·총latency는미측정.
- 원인/분류/우선: 날짜별단건reader만조립해read가능한거래일을찾는다. **통합(기존native조회범위)/P2~P3**,R3-004분범위개선과다른경로. R3-038와reader변경owner를겹치게동시구현하지않는다.
- 권장: native기존읽기경계에서실제저장된최근날짜/여러날을한번에선별할수있는지검토한다. current-dayRAM에만봉이있는경우·market공백/COMBINED·limit2~5날짜선정은별도보존해야한다. 단순캘린더로거래일을추정하거나없는날0봉을생성하지않는다.
- 위험/완료: 빈DB/주말·긴휴장/신규종목/당일RAM만있음/2개venue/SOR·최신commit/read중취소·정렬/기존HTTP계약동일. nativecall31→적은묶음조회와최종날짜/행동일성. **대기: 기준확보,정책·제품변경없음**.

- 확정 분류/우선순위/상태: **통합 / P2 / 대기**. 변경 내용/해결 후 측정/관련 commit: **제품 변경 없음 / 미해결 / 없음**. 진단 통과를 수정 완료로 해석하지 않는다.


### R3-040 — manual 동일 request 경로의 중복 load와 event-loop 동기 I/O

- 확정 분류/우선순위/상태: **제거 / P2 / 대기**.
* `execution_runtime.py:ManualMockOrderGateway._submit_limit:382-387`: command lock 안에서 `await to_thread(repository.load,intent_id)`가existing을반환하면 곧바로 `self.load(intent_id)`를다시호출하고, 요청일치는첫existing으로검증하여반환한다. 두번째load의목적은scope확인이지새mutableorderstate소비가아니다. `self.load:452`는repository.load동기호출이다.
* `measure_trading_paths.py`가실제publicgateway.submit_limit을fakeimmutableexistingrecord로실행했다. 같은명령1회에load2, 첫asyncio_0/둘째MainThread의실행중eventloop, brokerrefresh0/transport0을확인했다. SQL/PG시간·실제요청빈도미측정.0B마다실행되는비용이아니다.
* **제거 후보 / P2 / R3-009 보강**, risk중간: immutableintent범위검증을이미읽은record에적용할수있다면중복SELECT1회와eventloopDB호출1회를없앨수있다. 다만외부row교체/기존scope거절과요청충돌동작을대조해야한다. 취소전accountrefresh와Runtimenativeclaim은이개선대상이아니다.
* 완료: same-request동일/충돌,다른account/run/environment,UNKNOWN/취소중/종료/동시재요청,credentialdrain을기존gateway/lifecycle/nativefixture에서검증하고load2→1/transport불변을측정. F16/F24.

- 변경/해결 검증/commit: **제품 변경 없음 / 미해결 / 없음**. 관련 세부 coverage T표 및 진단 원장 참조.


### R3-041 — 운용 상태 refresh에 publication용 Shadow 전량 조회 결합

- 확정 분류/우선순위/상태: **재설계 / P2 / 대기**.
* `mock_automation_dialog.py:MockAutomationWorker._load_context:134-184`는credentials→accountsettings→candidatepublication→**Shadowhistory전체**→specs→status를순서대로호출한다. `_load_shadow_events:187`는cursor0에서최대100페이지×1000을읽는다. 실제events소비는 `_publish_selected_candidate:476`의matching_shadow_events와publicationdialog에있고start/stop/resume가이근거를다시승인하는것은아니다.
* 실제workercontextmethod/fakecontent로5000events×refresh2회에서Shadow10pages/10000dicts, candidates/specs/status각2,credentials/accountsettings각2를확인했다. Shadow페이지를재사용하는cache는없다. 실제HTTP·서버SELECT/latency·GUIthreadblocking을측정한것은아니다. worker는QThread이므로메인GUI동기정지라고보고하지않는다.
* `worker.run:119-131`은start/stopAPI를먼저실행한뒤전체context를읽고, 후속실패도operationfailed하나로전달한다. 실제runmethod/fakeAPIfixture에서stopACK기록후context실패→failed만emit을확인했다. `_failed:519`는안전하게상태미확인으로만들어버튼을닫지만문구는‘요청실패’다. 실제STOP실패로오인하면안된다.
* 서버게시도 `mock_publication_routes.py:find_shadow_candidate:145`에서eventID를찾기위해cursor0부터최대100000events를검색한다. UI전량조회와서버검증은서로다른authority이므로서버검증을삭제하지않는다. 이검색의실측은이번없다.
* **재설계/제거 후보 / P2 / R3-017·024 보강**, 위험중간. 최소방향은운용status읽기와게시근거읽기를기존worker/UI조작안에서분리하고, 실제게시시현재account/binding/event를검증하는것이다. 새cacheTTL/새전략조건/게시자동선택을만들지않는다. demandload또는기존저장key기반조회가능성을확인한뒤좁힌다.
* 완료: 시작/STOP/재개ACK후publicationhistory실패를독립표시,동일현재status/버튼,게시에서실제선택candidate/event/binding재검증,100페이지경계/커서오류/프로필교체/닫기drain,normalrefresh의Shadow조회0을측정. F15/F17/F23/F24.

- 변경/해결 검증/commit: **제품 변경 없음 / 미해결 / 없음**. 관련 세부 coverage T표 및 진단 원장 참조.


### R3-042 — 계좌설정 교체 뒤 Supervisor/Runner 소유 참조 조정 누락

- 확정 분류/우선순위/상태: **재설계 / P1 / 대기**.
* 연결/trigger: 자동 모의운용 계좌에서 계좌 감시/주문허용 설정을 변경. `mock_runtime.py:_update_settings:584-612`의 no-op이 아니면 기존 AUTO bundle을 map에서 제거/on_change(None)→old.close→settings CAS→`MockAccountBundle(... runtime_mode/context 미전달)`→new.start→map재게시/on_change(new). bundle 기본은 MANUAL. `app.py:publish_mock_bundle:307`은 app.state/verifiedbindings/position listener만 바꾸고 supervisor map은 조정하지 않는다. Supervisor._restore_once:434는 account key가 있으면 즉시 skip, activate:143은 spec ID만 같으면 구runner status를 반환한다.
* 원인/경계: credential/settings owner는 bundle 수명을 소유하고 supervisor는 runner map을 별도로 소유한다. map membership이 **현재 bundle과 일치하는지** 확인하는 연결이 없다. 기존 R3-008의 ‘runner 있으면 비용0’은 이 identity 불일치까지 건강하다는 증거가 아니었다.
* 실측: `measure_owner_replacement.py`/`owner-replacement.json`. 실제 owner/bundle/runtime/supervisor/runner를 사용했고 fake client/Risk계산/controlrepository로 네트워크·주문을 차단했다. AUTO→설정orders off에서 old runtime retired=True/active=False, newMANUAL/contextNone, restore control reads0, runner는oldbundle. activate는구runner반환, stop은 `MOCK_AUTOMATION_RUNTIME_NOT_ACTIVE`. orders를다시켜revision4가되어도newMANUAL+oldrunner, 실제 UI operation_availability의start/stop/resume 모두False였다. 같은설정rev2는owner/bundle교체없이보존됐다.
* 한계: Runner의task는시작하지않았다. 영속 RUNNING control은fake이며 실제admission/주문/계좌/타iming safety를검증하지않았다. **계좌교체후 stale주문권한이살아있다는안전결함으로단정하지않는다.** 오히려oldruntime.retired와nativelease가실행을막는기존보호가있다. 입증한것은참조/복원/제어연결불일치다.
* 분류/우선/의존: **재설계 또는 연결복구 / P1 / R3-008·009·025 보강**. nativeowner/claim은유지. 설정변경시AUTO전략을자동재승인하거나MANUAL을임의AUTO로전환하는정책을만들지않는다.
* 최소개선방향: 기존owner change callback와Supervisor의runner수명경계를대조해, 교체된bundle에묶인runner를기존drain/close로조정하고현재control/명시재개정책을따르게한다. 새Manager나새polling추가보다현재등록/해제연결을우선한다. 명시STOP/재개와설정변경의기존의미가불명확한부분은분리기록한다.
* 완료검증: 실제app조립의on_change→Supervisor, active runner중settings off/on·credential rotation·실패rollback·partialpublish·close/restart, openposition/UNKNOWN경우auto승격없음, oldrunnernativeworkdrain, 최신binding/control/Risk유지, UI정확상태를검증한다. F17/F18/F24 갱신.

- 변경/해결 검증/commit: **제품 변경 없음 / 미해결 / 없음**. 관련 세부 coverage T표 및 진단 원장 참조.


### R3-043 — stale 뉴스 작업의 옛 실행이 새 완료 상태를 되돌린다

- 판정: **재설계 / P2**, 기존 R3-020의 프로세스 결합과 구분되는 job 소유권 결함. 같은 O19를 새 worker 분리 과제로 중복 등록할 필요는 없지만 이 완료 검증은 추가해야 한다.
- 실제 진입/owner: `central_server/app.py:create_app:369`가 `jobs_parallelism=3`으로 서비스를 조립한다. `news_jobs.py:NewsJobRunner.start:54`의 BODY/BODY/RULE lane은 hard stage 제한이 아닌 preferred 정렬로 `run_once:98`에서 같은 native claim을 사용한다. `run_once`가 body/rule/AI를 실행하고 성공 시 finish, 실패 시 retry를 호출한다.
- 정확한 저장 경계: `central_server/database_news_jobs.py:SQLiteNewsJobStoreMixin.claim_news_jobs:67`는 RUNNING의 `updated_at < now-120`을 PENDING으로 복구한 뒤 attempts를 증가시킨다. `finish_news_job:103`, `retry_news_job:110`은 `WHERE job_key=?`만 사용한다. 반환된 attempts/실행 세대를 finish/retry 조건으로 소비하지 않는다. PostgreSQL도 `claim_news_jobs:173`의 recover_stale와 `finish_news_job:347`, `retry_news_job:365`가 같은 소유권 의미다.
- 재현: 첫 claim → 121초 뒤 같은 key 재claim → 두 번째 실행 finish → 첫 실행의 늦은 retry. 실제 임시 SQLite는 `COMPLETED, attempts=2, output_ref=new-owner-output`에서 **`PENDING, attempts=2, output_ref=''`**로 되돌아갔다. `observations.json:news_stale_attempt`와 진단 `test_old_news_attempt_retries_completed_reclaimed_attempt`.
- 원인/영향: 오래된 실행의 상태 쓰기를 현재 실행과 구분할 수 없다. 완료 job이 다시 claim되거나 옛 실패가 새 attempts를 사용해 terminal FAILED로 바꿀 수 있는 경계다. 본문/사건의 불변·멱등 revision 보호는 따로 있으며 이 fixture에서 revision 삭제나 실제 AI 중복 과금을 입증한 것은 아니다.
- 실제 trigger의 한계: 기본 BODY timeout은 각 URL 15초이고 AI 기본 timeout은90초이지만 claim 이후 DB 대기/RULE 처리/AI 실행 lock 대기는 별도다. `ai_service.py:CentralAIService.analyze:128`은 accepted 요청을 shield하고, `_prepare_and_run:256`은 프로세스 내부 동일입력 중복을 합친다. 따라서 모든 지연이 곧 중복 외부 POST라는 주장은 틀리다. **현재 운영에서120초를 넘기는 빈도·다중 서버 동시 실행은 미측정**이다. 재현은 stale 처리 이후의 무조건 상태 쓰기 자체를 확인했다.
- 최소 범위: 기존 claim 반환의 실행 식별/attempt와 finish/retry CAS를 같은 owner에서 연결하는 범위부터 검토한다. 새 service·별도 queue·무조건 timeout 증가는 근거가 없다. immutable 결과 저장 뒤 ACK 유실도 별도로 보존해야 한다.
- 의존/부작용: SQLite/PG 동일 계약, NewsJobRunner 모든 terminal 호출, 재시작 stale 복구와3회 실패 정책. 오래된 ACK를 거절하되 정상 복구 작업을 영구 RUNNING으로 남기면 안 된다.
- 완료 검증: 두 실행의 성공/실패 순서 양방향, 오래된 retry 후 새 COMPLETED 유지, stale finish가 새 RUNNING을 완료하지 못함, commit ACK 유실/재시작/동일입력 멱등 revision, native PG 동시 claim을 격리 DB로 검증한다.

- 확정 분류/우선순위/상태: **재설계 / P2 / 대기**. 변경 내용/해결 후 측정/관련 commit: **제품 변경 없음 / 미해결 / 없음**. 진단 통과를 수정 완료로 해석하지 않는다.


### R3-044 — 동기화 중 생긴 로컬 편집을 업로드 완료로 ACK한다

- 판정: **재설계 / P1**, R3-021의 delta 유지 판단에 추가할 실제 누락 경계다. 콘텐츠 delta 자체를 제거할 이유가 아니다.
- 실제 연결: `presentation/news_window_coordinator.py:NewsWindowCoordinator.ensure_started:90` → `news_process.py:_run_news_process:201` → 같은 함수 내부 `schedule_content_sync:398`. local_server/personal_server 모드에서60초 timer와 기동1초 callback이 실행한다. personal_server는 catalog 복제를 끄지만 journal news link 등 허용 컬렉션의 push/pull은 유지한다.
- 정확한 위치: `news_process.py:395`가 기동 시 현재 파일signature를 이미 처리한 값으로 둔다. `schedule_content_sync:400-403`에서 outbound 전 signature로 local_changed를 판단한 뒤, 내부 `run:409-418`은 push → pull → **그 시점의 최신 파일signature**를 last_content_signature에 저장한다. 이것은 push가 읽은 snapshot의 ACK가 아니다.
- 재현: 실제 nested scheduling 함수의 AST를 현재 소스에서 가져와 함수 본문을 그대로 compile했다. 외부 UI·thread launch·network client만 대역이고 `_news_content_signature`는 실제 함수다. 첫 push는 `local-before-push`를 읽고, pull 중 별도 로컬 편집을 모사해 `local-unuploaded-edit-after-push`를 기록했다. 두 번째 tick에서는 push가 호출되지 않는다. 기동 초기값과 같은 현재signature로 재bind한 뒤에도 push가 호출되지 않는다. 서버에 보낸 snapshot은 첫 값 하나뿐이다. `observations.json:news_sync_concurrent_edit`.
- 사실/한계: 실제 scheduling 본문에서 전송하지 않은 edit을 clean으로 판정하는 제어 흐름을 실행했다. 실제 Qt 이벤트루프·SQLite 사용자 편집과 네트워크의 병렬 timing은 미실행이다. 로컬 DB가 삭제되는 결함은 아니다. 이후 다른 쓰기가 있으면 재발견될 수 있으나 **추가 쓰기나 명시 push가 없으면 중앙 전파가 누락**되며 재시작만으로 복구되지 않는다.
- 최소 범위: 기존 push manifest/전송snapshot을 완료 기준으로 삼고, sync 진행 중 로컬 변경은 다음 outbound 작업으로 남기는 범위다. pull로 발생한 echo 억제는 유지한다. 단순히 signature를 무조건 이전 값으로 두면 pull 자체가 다시 push되는 역효과가 있으므로 원인별 소유를 구분해야 한다. 새 polling task·전량 catalog 복제는 필요하지 않다.
- 의존/부작용: 같은 `CentralContentSyncService`의 push manifest·pull cursor·서버 echo 억제, 뉴스 프로세스 재시작·종료, local_server/personal_server별 scope. 기존 R3-021 완료 설명은 이 경계를 포함한 전체 성공으로 쓰면 안 된다.
- 완료 검증: push snapshot 이후/pull 도중/최종 ACK 직전 사용자·AI·journal link 편집, pull-only 변화, push/pull 실패, 재시작 후 미전송 자료 발견을 실제 임시 DB와 대역 HTTP에서 검증한다. 전송내용/manifest/DB·cursor를 함께 비교한다.

- 확정 분류/우선순위/상태: **재설계 / P1 / 대기**. 변경 내용/해결 후 측정/관련 commit: **제품 변경 없음 / 미해결 / 없음**. 진단 통과를 수정 완료로 해석하지 않는다.


### R3-045 — unchanged theme_metadata의 created_at이 매번 delta를 만든다

- 판정: **제거 / P2**, 기존 R3-021의 구체적 no-op 잔여. R3-029의 `ThemeBackupService.revision()`이 stocks의 실시간 전체열에 결합된 문제와는 다른 원인이다. 둘은 theme export 책임에서 만나므로 같은 변경에서 중복 정책을 만들지 않아야 한다.
- 정확한 위치: `infrastructure/central_content_sync.py:CentralContentSyncService.push:68`는 뉴스 변경을 계기로도 `_read_themes:394`를 호출한다. `_read_themes:424-429`는 `ThemeBackupService.export_document()`로 full metadata를 만든다. `infrastructure/persistence/theme_backup.py:ThemeBackupService.export_document:70`의 `created_at`은 호출 때마다 새로 생성된다. `central_content_sync.py:_manifest_hash:677`는 document 전체를 hash하고 `_upload_changed:251`는 이 hash가 다르면 upsert한다.
- 재현: 동일한 초기화 DB, 동일 테마/종목/alias, 고정 KST clock만60초 전진해 두 번 실제 push를 호출했다. 두 번째 전송은 **theme_metadata 1행**, document의 차이는 **created_at 1개 필드뿐**이다. 실제 client transport는 대역이며 네트워크를 사용하지 않았다. `observations.json:theme_metadata_noop`.
- 저장/후속 확인: `central_server/database_documents.py:SQLiteDocumentStoreMixin.upsert_documents:34`와 PG counterpart의 JSON 비교에는 이 시간값이 포함된다. `content_routes.py:persist_content:206`의 theme 변경 callback도 연결된다. `CentralContentSyncService.load_theme_snapshot:123`는 metadata의 서버 updated_at을 완료표식으로 사용한다. **테마 historical snapshot은 `_append_sqlite_theme_snapshot:399`/PG:425의 content_hash 동일 비교로 추가 방어한다.** 따라서 snapshot 이력이 반드시 중복된다는 결론은 내리지 않는다. 불필요 metadata 전송·현재문서 갱신 후보를 확인했으며 실제 NAS write/lock 비용과 충돌 영향은 미측정이다.
- 최소 범위: backup 파일의 생성시각과 동기화 내용의 식별/완료표식을 구분하여 기존 delta 경계에서 no-op을 처리한다. created_at 전체 삭제나 실제 변경의 effective_at 덮어쓰기는 요구 범위가 아니다.
- 의존/부작용: R3-029의 revision 범위 검토, full/프로필 backup 호환, theme_metadata pull echo, CentralThemeSyncDispatcher의 remote_completed_at 비교·동시 편집 보존.
- 완료 검증: 동일 DB를 다른 clock으로 export/push해 두 번째 업로드/서버 UPDATE0, 실제 테마 변경은1회, backup 생성시각·effective_at·history 의미 유지, 재시작 manifest/pull/복수 PC 동시 편집을 대조한다.

- 확정 분류/우선순위/상태: **제거 / P2 / 대기**. 변경 내용/해결 후 측정/관련 commit: **제품 변경 없음 / 미해결 / 없음**. 진단 통과를 수정 완료로 해석하지 않는다.


### R3-046 — 일반 research CLI가 오류 결과로 자신의 요청 파일을 덮어쓴다

- 판정: **통합 / P2**, R3-017의 프로세스 조립/모드별 경계에서 별도 확인할 데이터 보존 결함. R3-024의 미정 전략/연구 실시간 adapter와 다른 문제다.
- 정확한 위치: `research_process.py:_run_research_process:2191`. final exposure/evaluation, development validation, compare에는 각각 request/database/dataset 등 보호경로 검사가 있다(`2214` 이후). 일반 `--request`는 `2263-2277`에서 mode가 leader_breakout일 때만 output 충돌을 검사한다. 일반 모드는 `2299`에서 load/실행하고, 잡힌 오류도 `2314-2317`에서 같은 `_write_result(args.result, result)`로 게시한다. `_write_result:2382`는 임시파일을 지정 target으로 원자 교체한다.
- 재현: 임시 `request.json`에 `{"mode":"limited_search","audit_source_marker":"preserve-this-input"}`를 저장하고 `--request request.json --result request.json`으로 실제 `_run_research_process`를 호출했다. 유효하지 않은 요청이므로 정상적으로 exit1이지만, 그 오류 envelope `{"status":"failed","reason":"ValueError: research request strategy must be an object"}`가 **원래 요청을 교체**했다. `observations.json:research_request_overwrite`.
- 한계: 실제 CLI 함수와 파일 교체를 실행했으나 별도 child 실행/정상 연구 완료를 이 fixture에서 돌리지는 않았다. ResearchDialog는 결과경로를 별도 state 파일로 구성하므로 보통의 UI 클릭이 이 충돌을 만든다는 증거는 없다. 사용자가 명시한 CLI 경로 충돌이 trigger이며 유효하지 않은 요청에서조차 원본이 보존되지 않는 점을 확인했다. 실제 운영 DB를 덮어쓰는 실험은 하지 않았다.
- 최소 범위: 이미 다른 실행 모드에서 갖고 있는 request/result/cancel 보호 계약을 일반 모드에도 일관되게 적용한다. 새 실행 엔진/범용 경로 계층을 만드는 제안이 아니다. 잘못된 입력을 파싱하다 실패했어도 최소한 원래 request 자체를 error sink로 사용하지 않아야 한다.
- 의존/부작용: CLI 모드 전체, error envelope의 정상 별도 출력, ResearchDialog fixed 경로, request/database/runs/dataset 보호의 기존 차이. legitimate 결과파일 교체 계약은 보존한다.
- 완료 검증: malformed/valid 일반 요청과 result/cancel=request/database/frozen input 충돌을 임시 파일에서 검증하고 원본 bytes 불변을 확인한다. 모드별 기존 안전검사와 정상 UI child 게시·취소·재시작을 관련 검사로 대조한다.

- 확정 분류/우선순위/상태: **통합 / P2 / 대기**. 변경 내용/해결 후 측정/관련 commit: **제품 변경 없음 / 미해결 / 없음**. 진단 통과를 수정 완료로 해석하지 않는다.


### R3-047 — Runner 내부의 여러 상태 수명 결합

- 확정 분류/우선순위/상태: **재설계 / P2 / 대기**.
* 본문상 Runner는 (a)minute/frame/bootstrap입력cursor·bars/universe (b)leader0Bqueue/source/pinnedproof (c)FILL-ownedposition/dedup (d)Risk/protectionreview/firstexit (e)nativeunfilledregistration/preflight/reprice/cancelrecovery (f)sessionclock (g)checkpoint/status/latency를동시에소유한다. 생성자80-238, run_once2018, close1950, `_save_checkpoint2736`에서같은bundle/status/checkpoint에합류한다.
* `_unfilled_preflight_lock`은공개preview만이아니라run_once전체를감싸고DB·brokerawait동안유지한다. 보호·미체결·일반평가·종료native작업이같은checkpoint와gate순서를공유하기때문이다. 이lock이필요하다는것과모든정책state가한객체에퍼져있어야한다는것은다르다. 단순라인수/메서드수는결함근거로쓰지않았다.
* **재설계 후보 / P2 / R3-009·017·018·025 보강**. 실제기능추가시수정파급/실행지연은미측정이다. 기존application의sessionclock/unfilled/protection순수정책을재사용하면서, 먼저상태소유·mutable공유필드·ACK전후경계를명시하고불필요한status/state복제나같은context조립이있는한경로부터줄인다. 새Service/Handler묶음을반사적으로추가하지않는다.
* 완료: 선택한한episode에서필요파일/호출깊이·수정필드수·복제/직렬화횟수를전후비교하고,원래FILL만position소유·anchor/예산·protection우선·UNKNOWN/ACK회복·clock독립·cancelrace·closejoin을통합검증한다. 유지판정이었던R3-009를‘내부구조검증완료’로확대하지않는다.

- 변경/해결 검증/commit: **제품 변경 없음 / 미해결 / 없음**. 관련 세부 coverage T표 및 진단 원장 참조.
- 측정 전제: 비용 존재와 제거 가능성은 다르다. 아래 보존 계약을 만족하는 작은 경로를 고른 뒤 실행시간/복제·읽기 수를 추가 측정한다. 책임 분할 자체를 완료 기준으로 삼지 않는다.


### R3-048 — 입력마다 전체 checkpoint 직렬화/저장

- 확정 분류/우선순위/상태: **재설계 / P2 / 대기**.
* `runner.run_once:2109-2115`는각consume뒤cursor전진→`drain_thread_call(_save_checkpoint)`를기다린다. `_save_checkpoint:2747-2815`는bars/universe/dedupIDs와여러episode문서를매번dict/deepcopy/asdict/sort하고sameACKcontent와비교한다. cursor가바뀌면전체content도달라no-op아니다.
* 실제run_once+nativeSQLiteQueryStore에**비거래consume seam**을써20rows→20checkpointupsert,serializedJSON합19183bytes,그다음idle추가0을측정했다.값/전략평가동등성·물리I/O/PG연결/장중손실측정은아니다. 현재noop와forcedclose는이미구현돼있으므로‘idle매poll중복저장’으로재등록하지않는다.
* **재설계/통합 조사 / P2 / R3-009·025 보강**. per-prefixACK는실패후재평가/중복주문을막는다. batch마다1저장으로바꾸거나close만저장하는수정은권고하지않는다. 먼저같은ACK문서구성중중복복사·복원불필요필드·immutableframe표현을살피고성능을재측정한다. DBcheckpoint내용을줄이려면복구계약을별도로확정해야한다.
* 완료: commitACKloss의consumedprefix재평가0,후속입력ACK차단,상태/보호cue/seenfill복원,동시FILL·closejoin·강제최종저장,같은자료의bytes/calls/CPU비교. 이번관련4건은이보호의현재통과근거다.

- 변경/해결 검증/commit: **제품 변경 없음 / 미해결 / 없음**. 관련 세부 coverage T표 및 진단 원장 참조.
- 측정 전제: 비용 존재와 제거 가능성은 다르다. 아래 보존 계약을 만족하는 작은 경로를 고른 뒤 실행시간/복제·읽기 수를 추가 측정한다. 책임 분할 자체를 완료 기준으로 삼지 않는다.


### R3-049 — 계좌 Risk 복구의 누적 history 재계산

- 확정 분류/우선순위/상태: **재설계 / P3 / 대기**.
* `MockAccountMonitor._publish_risk:658-736`은deltaevents를`_risk_events`에누적하지만매recovery전체tuple·FILL날짜리스트를만들고`build_mock_automation_risk_snapshot`에전체events/costs를전달한다. `mock_automation_risk.py:build_mock_automation_risk_snapshot:420` 및 helpers573-615는여러filter/sort/ownedbasis/position계산을한다. cost는당일자료를매복구다시조회하고처음/날짜변경시fullperiod다.
* **반복계산 조사 / P3 / R3-009·018 보강**, 위험높음. 이는0B마다무조건실행되는경로가아니라계좌복구/행동판단의갱신경계다. 현재data량/실행비용/회복빈도미측정. 최신brokercost와Risk재계산을중복이라는이유로끄지않는다.
* 최소방향은기존riskstate/비용source와당일/누적ownership회계의입력을계측하여같은cut계산을재사용할수있는범위를먼저확인하는것이다.새상시cache/잘못된zero/임의재조회주기추가는없다. 원본ledger와UNKNOWN을보존한다.
* 완료: delta없음/체결추가/과거정정·비용지연·날짜경계·부분read/lease교체에서기존Risk전체문서와동일, latestACK/RAMpublication순서동일, 누적event수별CPU/읽기횟수측정.

- 변경/해결 검증/commit: **제품 변경 없음 / 미해결 / 없음**. 관련 세부 coverage T표 및 진단 원장 참조.
- 측정 전제: 비용 존재와 제거 가능성은 다르다. 아래 보존 계약을 만족하는 작은 경로를 고른 뒤 실행시간/복제·읽기 수를 추가 측정한다. 책임 분할 자체를 완료 기준으로 삼지 않는다.


### R3-050 — async supervisor의 동기 조립/상태 조회

- 확정 분류/우선순위/상태: **재설계 / P2 / 대기**.
* `Supervisor.activate:224`의Runner생성자는package/read/checkpointrestore/bootstrap등동기DB작업을실행한다. `activate:236/stop:266/resume:362`의`return self.status(...)`도`status:364`에서control을동기read한다. HTTP GET status(`mock_automation_routes.mock_automation_status:55~62`)는 async route가 명시적으로 `await asyncio.to_thread(supervisor.status, ...)`를 사용한다. 반면 activate/stop/resume의 이 return들은 async method 내부에서 동기 실행된다.
* **실행경계 조사 / P2 / R3-009·017·025 보강**. 문서화된동기I/O사실이며eventloop지연시간은미측정이다. eventloop밖으로생성자전체를옮기면monitorlistener등록/asyncioowner접근·checkpoint복원이threadboundary를넘으므로일괄to_thread를먼저추가하지않는다. 이미읽은control을안전한responseprojection으로쓸수있는지도lateststatus계약과대조해야한다.
* 완료: 실제생성/복원에서각DB단계와eventloop진행을격리계측하고partialconstructorfailure의listener해제·runner등록순서·close/drain·현행response필드/freshness를검증한다.

- 변경/해결 검증/commit: **제품 변경 없음 / 미해결 / 없음**. 관련 세부 coverage T표 및 진단 원장 참조.
- 측정 전제: 비용 존재와 제거 가능성은 다르다. 아래 보존 계약을 만족하는 작은 경로를 고른 뒤 실행시간/복제·읽기 수를 추가 측정한다. 책임 분할 자체를 완료 기준으로 삼지 않는다.


### 검증 결과·실패 이력·검증되지 않은 범위

| 실행 묶음 | 현재 소스에서 확인한 결과 | 실제/대역 경계·한계 |
|---|---|---|
| PC counterexample4 + settings1 + date1 | **6조건 통과**. `pc-probe-v1`, `settings-probe-v2`, `date-retry-v1`의 result/execution/worker.log | actual메서드/임시SQLite, thread interleave. nativeQThread/실제HTTP미실행. bootstrap032는 이6조건에 포함되지 않음 |
| realtime fixtures-v3 | **6조건 통과**. reference최신손실/lock/중복write/sender해제누락/ingestread/recentdaysread | 실제Collector/loop/route/nativeSQLite. socket/상류순위/advance는 명시대역. v1/v2 반복을 합산하지 않음 |
| trading measurements + owner replacement | **5조건 통과**. gateway2load/UI전량read/ACK후후속실패/prefix20checkpoint/owner교체 | actualgateway/worker/runner.run_once/owner/bundle/runtime/supervisor. consume·broker·Risk/control대역, ownerfixture runner task시작 안함/transport0 |
| news-research user-final | **4조건 통과**. stale claim/cleanACK/themefalseDelta/requestoverwrite | 실제SQLite/CLI/sync. sync scheduler는현재nested AST본문compile/실제signature, UI/threadlaunch/network대역 |
| trading bounded related checks | **21건/7개 독립process 통과**: UI7/Runner4/Runtime2/nativegate2/Lifecycle2/accountRAM2/realreads2 | 선택된기존회귀만. 전체주문/실제계좌/전수회귀아님. `trading/checks.json`의정확선택/로그 |

각 성공 worker의 nativeexit0/소유 자식종료/임시자료 정리를 확인했다. 진단 **21조건**과 기존회귀 **21건**은 성격이 달라 합쳐 ‘42기능 정상’이라고 하지 않는다. 첫 settings v1은 진단 자체 sqlite connection close 누락의 WinError32 정리실패를 보존하고 v2 명시close 뒤 성공했다. trading owner 최초 fixture는 고정spec시각이 계좌검증보다 이른 입력오류로 실패했고 fixed KST를 맞춘 별도재실행만 성공근거다. 제품검사를 바꾸거나 실패를 숨기지 않았다.

**미검증 원장:** 실제 설치PC/Qt 전체 이벤트루프·KiwoomLOGIN/REG/0B/00/04·모든Family/전략파라미터·generic연구→live적용의 미정정책·뉴스모든parser/실제AI/과금·discover_campaign_inputs 모든storage/NAS분기·역사뉴스 준비/봉인/전송hold·실제PG/planner/lock wait/WAL·장후캘린더/장시간backfill·capture대용량/파일손상/ownedPGrestore·OS강제종료와operational crash 내구성은 검증하지 않았다. 해당 기능을 전부 미연결이라 단정하거나 가짜tick/zero자료/새전략조건으로 채우지 않는다. 기존 NAS transfer hold와 원본/주문 authority는 보존한다.

**제외한 결함 가설:** server첫close실패 뒤 dependency보존은 명시계약; Protocol은 runtime계층이 아님; live최신/dirty ACK/RAM observation은 다른수명; 설정UI의 재시작명시는 hotreload범위; stale 뉴스job상태오류가 실제중복AI과금을 증명하지 않음; CLIpath충돌은 일반UI기본값 발생증거가 아님. 유지할 이유와 내부개선 필요를 함께 기록했다.

**지속 관리:** 다음은002의날짜실패계약 하나다. 신규문제 구현에 앞서 해당 ID의 완료검증을 actual caller/consumer/close까지 수행하고 영향F부모/세부행을 같은변경에서 갱신한다. 이미완료된SELECT/transaction/range개선은 재구현하지 않는다. 신규코드변경이 이감사근거를 무효화한부분만 다시 읽는다. source전후·문서검증은 같은진단root의`verification.json`에 보존하며 진단파일이없어도 이문서의 원인/방법/완료기준으로 이어갈 수 있다.


### 최종 교차 검토와 공유 작업 트리 변화

독립 담당3명이 통합 정본을 각 담당 REPORT/실제 경로와 다시 대조했다. 자동 신규주문과 Gateway 수동/취소 분기, GET status의 명시 to_thread 경계, fixture의 고정 upstream/advance 조건, F04/F18 매핑, 과거 ‘유지’ 설명과 신규 미완료 ID의 충돌을 바로잡았다. 문서 생성 진단의 첫 실행 두 번은 누락된 ID/표 행 assertion으로 저장 전에 중단됐고 식별자 추출/공백 행 인식만 고친 뒤 통합했다. 제품/검사 오류로 처리하지 않는다.

감사 시작→최종 비교에서 **제품 src의 Python은 변경0**이다. 한편 다른 작업의 `scripts/run_postgres_access_integration.py`, `scripts/run_regression.py`, `tests/unit/test_candidate_monitor.py`, `tests/unit/test_run_regression.py` 해시가 바뀌고 `scripts/check_test_changes.py`, `tests/unit/test_test_change_review.py`가 추가됐다. 이 감사 담당은 해당 파일을 수정하지 않았다. 현재 diff의 변경은 검사 실행/개별ID 증거와 fixture 보강이며 본문 감사의 제품 caller/owner에는 변화가 없다. 제품 사실 재감사는 필요하지 않다. 다만 이번 격리 실행/21건 관련회귀는 **각 실행 시점의 runner·test 해시/로그에 대한 결과**로 유지하며 이후 바뀐 검사 도구의 현재 통과를 주장하지 않는다. 다른 작업의 검사 보강을 이 감사 성과나 제품 변경으로 합치지 않는다. 전체 tree 안정 assertion 최초 실패도 이 외부변화 탐지로 기록하며 무조건 ‘전체 소스 동일’로 덮지 않는다.


장전 연구 경로를 P13/F20으로 추가 대조했다. schedule/claim/AI request/usage·raw보존/최신context·UI설정/worker/재표시/close의 본문을 확인했고 이를 새 연결 완료로 승격하지 않았다. ‘닫았다 다시 열면 timer가 복구되지 않는가’라는 후보는 `showEvent:96~100`의 `_timer.start()`로 반증돼 신규 ID를 부여하지 않았다. 입력가용성과 명시 consumer_binding=not_connected를 구분해 기존 R3-024의 미정 엔진활용을 임의로 구현하지 않는다. 추가 실행 fixture/유료호출은0이다.

## 394개 모듈 감사 깊이 대조 — 2026-10-18

### 판정 기준과 정정

이 절은 1·2차의394개 AST 목록과77개 주요 경로를 **모듈 전체 정밀 감사 완료로 읽지 않도록** 실제 지속 근거에 대조한 결과다. 파일 열기·해시·함수명 검색·테스트 수·import/AST edge는 정밀 완료 근거가 아니다. 과거에 읽지 않았다고 소급 단정하지도 않는다. 과거 기록이 특정 함수/경로까지만 증명하면 그 범위만 승계한다.

- **정밀 검토 완료:** 이 모듈 자체의 업무본문, 실제 caller·소비/저장, 실패·상태 소유·동시/종료(해당 시)를 전체 책임 범위에서 확인했다. 다른 모듈/상속 mixin·전체 기능·실제 환경 수락까지 완료라는 뜻은 아니다. docstring-only/export-only initializer도 별도 표시한다.
- **부분 정밀(잔여 미검토):** 일부 함수·실행 경로는 깊이 확인했지만 모듈 전체는 미완료다. 이를 정적 분석만 했다고 낮춰 쓰거나, 모듈 전체 완료로 올리지 않는다. 요청한 세 상태에 이 구분을 추가했다.
- **정적 분석만 수행:** 초기 AST/목록/구문·정적 계약은 있으나 caller→본문→소비/실패의 충분한 심층 근거가 없다. 기능이 미연결이라는 뜻이 아니다.
- **미검토:** 정적 목록조차 없는 모듈. 현재 집합은 최초394개와 같아서0개다. **정밀 미완료는 부분 정밀+정적만의 합**으로 따로 계산한다.

| 기준 | 정밀 검토 완료 | 부분 정밀·잔여 미검토 | 정적 분석만 수행 | 목록도 미검토 | 합계 |
|---|---:|---:|---:|---:|---:|
| 1·2차 지속 근거만 엄격 승계 | 0 | 106 | 288 |0|394|
| 직전 누락 범위 추가 감사 후(보존) | 25 | 175 | 194 |0|394|
| 잔여 책임 정밀 감사 종결(현재) | **394** | **0** | **0** |**0**|**394**|

과거 모듈 전체 완료0은 과거 모든 코드를 안 읽었다는 뜻이 아니라 **모듈 전체 완료를 입증하는 지속 근거가 없었다**는 뜻이다. 직전 대조 시점에는 **정밀 미완료369개**가 남았고, 이번 후속에서 그 잔여 책임을 검토해 코드감사를 종결했다. 기존77개 부분경로의 의미 있는 실패·성능 측정은 그대로 보존한다. 직전 결과를 전체394개 정밀 완료로 확대하지 않았다. 현재의394개 코드감사 완료 역시 앱 전체 운영 정상을 뜻하지 않는다.

직전 단계에서 추가 본문 범위를 확인한 모듈은110개였으며, 기존 부분검토 모듈의 빠진 분기도 포함한다. 전수 재조사 없이 미확인 범위만 더 읽었다. 아래394행과 [상세 기계 원장](docs/workstreams/refactor-module-coverage-20261018.json)은 같은 변경의 산출물이다. **진행·우선순위·R3/F 상태의 정본은 이 문서**이고 JSON은 모듈별 함수/범위·잔여·근거를 보존한다. `actual_functions`/`inventory_functions` 등 AST 잔여 목록은 다음 범위 선택용이다.

### 기능별 대조

아래 수량은 각F에 실제 연결 근거가 기록된 모듈의 집합이다. 한 모듈은 여러F에 속하며 합산하면394가 되지 않는다. 기능 매핑이 비어 있으면 파일명으로 자동 추정하지 않았다. 모든 표 행은 부모F의 운영 연결 상태와 별개다. 본문/모듈 코드감사 완료가 실제 기능 연결 정상/외부 환경 수락을 뜻하지 않는다.

| 기능 | 기존에 확인한 범위 | 이번 추가·부족한 깊이 | 연관 모듈: 완료/부분/정적만 |
|---|---|---|---|
| F01 | P01/02·앱/서버 조립 | 미검토 본문은 종결; 전체 own-body 감사는 종결했고 기동/복원 운영 수락은 별도다. | 35/0/0 |
| F02 | P04/P09·순위 수신/표 갱신 | 미검토 본문은 종결; 실제 caller/소비/실패 책임 검토, 운영 수락은 별도. | 34/0/0 |
| F03 | S15~17·broker/TOP20 | 미검토 본문은 종결; 실제 caller/소비/실패 책임 검토, 운영 수락은 별도. | 26/0/0 |
| F04 | S01~04/S21·등록/팬아웃 | 미검토 본문은 종결; 실제 caller/소비/실패 책임 검토, 운영 수락은 별도. | 29/0/0 |
| F05 | P05/S06~07·0B봉 권한/저장 | 미검토 본문은 종결; 실제 caller/소비/실패 책임 검토, 운영 수락은 별도. | 18/0/0 |
| F06 | R3-002/003/026 해당batch/read | 미검토 본문은 종결; 실제 caller/소비/실패 책임 검토, 운영 수락은 별도. | 16/0/0 |
| F07 | S05~11·native/opID/overlay | 미검토 본문은 종결; 실제 caller/소비/실패 책임 검토, 운영 수락은 별도. | 23/0/0 |
| F08 | S11~14·live/recent/ingest | 미검토 본문은 종결; 숨김 live page075와 paint081은 비용 미실측 후보로 분리. | 61/0/0 |
| F09 | S17~19/P09·index/program | 미검토 본문은 종결; outbox 읽기실패 원본교체065 재현, 장후 coverage SELECT078 실측. | 33/0/0 |
| F10 | P04/P10/S20·기본정보 후속 | 미검토 본문은 종결; cached include_nxt 누락068 actual service 재현; 첫 이미지 OCR080 호출수 확인. | 65/0/0 |
| F11 | N/R6/T03/T09·Family 봉소비 | 미검토 본문은 종결; PG Shadow immutable silent ACK는010 추가 범위, 실제 PG 미재현. | 37/0/0 |
| F12 | S10/T10·0B leader 관측 | 미검토 본문은 종결; PG Shadow 동일 ID 충돌은010 추가 범위이며 실제0B 정상 수락으로 확대하지 않음. | 20/0/0 |
| F13 | 기존Runtime range/sharedcut | 미검토 본문은 종결; 실제 caller/소비/실패 책임 검토, 운영 수락은 별도. | 22/0/0 |
| F14 | T16/T21·RAM계좌·실전read | 미검토 본문은 종결; 실제 caller/소비/실패 책임 검토, 운영 수락은 별도. | 32/0/0 |
| F15 | R6/R7·지원Family/게시계보 | 미검토 본문은 종결; MDD ppm067 실제 helper 재현; 연구→범용 live 적용 미정024 유지. | 32/0/0 |
| F16 | T11~15/T18·Risk/nativeclaim/UNKNOWN | 미검토 본문은 종결; 실제 caller/소비/실패 책임 검토, 운영 수락은 별도. | 29/0/0 |
| F17 | T01~12/T18~20·Supervisor/Runner | 미검토 본문은 종결; MDD067은 candidate eligibility 입력에 연결됨; 실제 주문 수락은 미실행. | 47/0/0 |
| F18 | T16/17/21·FILL/RAM | 미검토 본문은 종결; snapshot 재전달064·real 종료미저장066·summary/statistics074·hidden live075·paint081 추가. FILL 원장 전체 손실로 일반화하지 않음. | 99/0/0 |
| F19 | N1~7·news revisions/jobs/AI | 미검토 본문은 종결; legacy schema069·NAS 수동 AI 버튼072·동일기사 쓰기073 추가, 모든 NAS AI 미연결로 일반화하지 않음. | 87/0/0 |
| F20 | P13·장전AI/context | 미검토 본문은 종결; 실제 caller/소비/실패 책임 검토, 운영 수락은 별도. | 19/0/0 |
| F21 | R1~7·연구 child/claim/final | 미검토 본문은 종결; 일반 run terminal race063와 MDD067, legacy schema069 추가. owned final/search 전체 fence와 구분. | 67/0/0 |
| F22 | N8·delta/theme/journal sync | 미검토 본문은 종결; journal fullsync071·native 기사073·일지074·catalog076·미사용 profile077·OCR080 추가. | 96/0/0 |
| F23 | P02/03/T04/05/22·API/settings | 미검토 본문은 종결; 제외종목찾기070 인자 연결 단절과 NAS 수동AI072, real 종료066 추가. 도달불가 옛 layout082는 실행비용0. | 92/0/0 |
| F24 | P12/S23/T14/20·owned close | 미검토 본문은 종결; 일반 run063/snapshot064/outbox065/realclose066의 잔여를 구분; 기존 수명 owner 유지 판단은 전체 안전 수락이 아님. | 137/0/0 |
| F25 | D1~4·진단/capture/replay 일부 | 미검토 본문은 종결; 진단 모듈 코드 책임 완료. v4 clock 삭제 범위079는 조건부 가설/실제 PG 재현 필요. | 54/0/0 |

직전에는 **기능 매핑 미확인194개**가 있었으며, 이번에는 실제 검토한 caller/consumer와 책임에 따라 기능 매핑을 보완했다. 독립 업무가 없는 namespace/docstring만의 모듈은 그 이유를 기록하고 기능 단절로 취급하지 않는다. 미매핑은 미연결 확정이 아니며 export-only module도 포함한다. 기능별 표를 분모394개에 대한 완전한 실행범위 증명으로 사용하지 않는다.

### 기존77개 세부 경로와 모듈 대조

아래 모듈 집합은 기존 보고서에 명시된 연결의 최소 집합이며 새 importgraph 추정이 아니다. 같은 이름의 함수 검색으로 검토완료를 생성하지 않았다. 원래 호출/상태/실패/미확인 내용은 앞77행을 보존하고 상세 JSON의 `prior_documented_routes`에도 같은 원문을 넣었다. 직전에는 경로 근거를 모듈 전체 완료로 확장하지 않았다. 이번에는 그 모듈의 미검토 책임까지 별도로 보완했으므로 현재 열은 모두 코드완료다. 기존77경로의 실행 한계는 보존하며 실제환경 수락으로 승격하지 않는다.

| 기존 경로 | 명시된 연관 모듈(패키지 기준) | 모듈전체 완료/부분/정적만 | 남은 깊이 |
|---|---|---|---|
| P01 | `__main__.py`, `bootstrap.py`, `presentation/main_window.py`, `presentation/app_controller.py`, `infrastructure/persistence/strict_restore.py` | 5/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| P02 | `presentation/api_settings_dialog.py`, `presentation/app_controller.py`, `bootstrap.py`, `infrastructure/kiwoom_rest/client_factory.py`, `infrastructure/persistence/entry_snapshot_writer.py` | 5/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| P03 | `bootstrap.py`, `infrastructure/central_settings_sync.py`, `infrastructure/persistence/settings_repository.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| P04 | `presentation/main_window.py`, `presentation/app_controller.py`, `presentation/ranking_worker_controller.py`, `infrastructure/kiwoom_rest/ranking_worker.py` | 4/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| P05 | `presentation/main_window.py`, `presentation/app_controller.py`, `application/minute_trade_value.py`, `infrastructure/persistence/market_cache_writer.py`, `infrastructure/persistence/minute_bar_repository.py`, `infrastructure/persistence/stock_repository.py` | 6/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| P06 | `presentation/main_window.py`, `infrastructure/persistence/entry_snapshot_writer.py`, `infrastructure/persistence/journal_snapshot_repository.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| P07 | `infrastructure/persistence/entry_snapshot_writer.py`, `infrastructure/persistence/journal_snapshot_repository.py`, `presentation/app_controller.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| P08 | `presentation/main_window.py`, `infrastructure/persistence/minute_bar_repository.py` | 2/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| P09 | `presentation/main_window.py`, `presentation/top20_trade_value.py`, `application/top20_trade_value_collector.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| P10 | `presentation/app_controller.py`, `presentation/main_window.py`, `presentation/daily_high_worker_controller.py`, `presentation/fundamentals_worker_controller.py` | 4/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| P11 | `bootstrap.py`, `infrastructure/central_content_sync.py`, `infrastructure/central_settings_sync.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| P12 | `presentation/app_controller.py`, `infrastructure/persistence/market_cache_writer.py`, `infrastructure/persistence/entry_snapshot_writer.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| P13 | `presentation/main_window.py`, `presentation/morning_market_brief_dialog.py`, `infrastructure/central_operational_settings.py`, `central_server/morning_market_brief.py`, `central_server/ai_service.py`, `application/morning_market_brief.py` | 6/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S01 | `central_server/app.py`, `central_server/realtime_collector.py`, `central_server/realtime_hub.py`, `infrastructure/kiwoom_rest/realtime_registration.py` | 4/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S02 | `central_server/realtime_collector.py` | 1/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S03 | `central_server/realtime_collector.py`, `central_server/mock_account_monitor.py` | 2/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S04 | `central_server/realtime_collector.py`, `central_server/realtime_hub.py`, `central_server/autonomous_top20.py`, `central_server/market_theme_shadow_monitor.py`, `central_server/realtime_routes.py` | 5/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S05 | `central_server/realtime_collector.py`, `central_server/database_realtime_snapshot.py` | 2/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S06 | `central_server/realtime_collector.py`, `central_server/minute_bars.py`, `central_server/database_market_bars.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S07 | `central_server/realtime_collector.py`, `central_server/database_market_bars.py` | 2/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S08 | `central_server/realtime_collector.py`, `central_server/database_documents.py`, `central_server/market_events.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S09 | `central_server/realtime_collector.py`, `central_server/database_datasets.py` | 2/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S10 | `central_server/realtime_collector.py`, `central_server/market_theme_shadow_monitor.py`, `central_server/market_theme_shadow_inputs.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S11 | `central_server/market_read_routes.py`, `central_server/realtime_collector.py`, `central_server/database_market_bars.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S12 | `central_server/market_read_routes.py`, `central_server/realtime_collector.py`, `central_server/database_market_bars.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S13 | `central_server/rest_broker.py`, `central_server/market_ingest.py`, `central_server/database_market_bars.py`, `central_server/database_market_metadata.py` | 4/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S14 | `central_server/market_ingest.py`, `central_server/market_read_routes.py`, `central_server/database_documents.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S15 | `central_server/rest_broker.py`, `infrastructure/kiwoom_rest/client.py`, `central_server/database_query_cache.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S16 | `central_server/rest_broker.py`, `central_server/market_ingest.py` | 2/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S17 | `central_server/app.py`, `central_server/autonomous_top20.py`, `central_server/realtime_collector.py`, `central_server/database_datasets.py` | 4/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S18 | `central_server/autonomous_top20.py`, `central_server/persistent_outbox.py`, `central_server/database_datasets.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S19 | `central_server/autonomous_top20.py`, `central_server/program_snapshot_writer.py`, `central_server/database_datasets.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S20 | `central_server/autonomous_top20.py`, `central_server/rest_broker.py`, `central_server/market_ingest.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S21 | `central_server/realtime_routes.py`, `central_server/realtime_hub.py`, `central_server/realtime_collector.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S22 | `central_server/app.py`, `central_server/database.py`, `central_server/postgres_access.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| S23 | `central_server/app.py`, `central_server/realtime_collector.py`, `central_server/rest_broker.py`, `central_server/autonomous_top20.py` | 4/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| T01 | `presentation/mock_automation_dialog.py` | 1/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| T02 | `presentation/mock_automation_dialog.py`, `central_server/mock_publication_routes.py`, `infrastructure/persistence/forward_evaluation_repository.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| T03 | `application/mock_automation_specification.py`, `central_server/mock_publication_routes.py` | 2/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| T04 | `presentation/nas_credentials_dialog.py`, `infrastructure/central_credentials_client.py`, `central_server/credential_runtime.py`, `central_server/mock_runtime.py` | 4/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| T05 | `central_server/database_account_settings.py`, `central_server/mock_runtime.py`, `central_server/real_runtime.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| T06 | `central_server/mock_automation_supervisor.py`, `central_server/mock_runtime.py`, `central_server/execution_runtime.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| T07 | `central_server/mock_runtime.py`, `central_server/mock_automation_supervisor.py`, `central_server/mock_automation_runner.py`, `central_server/app.py` | 4/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| T08 | `central_server/mock_automation_supervisor.py` | 1/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| T09 | `central_server/mock_automation_runner.py`, `application/research_families.py` | 2/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| T10 | `central_server/mock_automation_runner.py`, `application/leader_breakout.py` | 2/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| T11 | `application/mock_automation_execution.py`, `infrastructure/persistence/forward_evaluation_repository.py` | 2/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| T12 | `central_server/execution_runtime.py`, `central_server/database_execution.py`, `infrastructure/persistence/execution_repository.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| T13 | `central_server/execution_runtime.py`, `infrastructure/persistence/execution_repository.py` | 2/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| T14 | `central_server/execution_runtime.py`, `central_server/mock_runtime.py` | 2/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| T15 | `application/order_lifecycle.py`, `infrastructure/kiwoom_rest/mock_execution.py` | 2/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| T16 | `central_server/mock_account_monitor.py`, `central_server/database_execution.py`, `application/mock_automation_risk.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| T17 | `application/order_lifecycle.py`, `central_server/mock_automation_runner.py`, `infrastructure/persistence/execution_repository.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| T18 | `central_server/mock_automation_runner.py`, `application/mock_automation_execution.py` | 2/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| T19 | `central_server/mock_automation_runner.py`, `application/mock_automation_risk.py` | 2/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| T20 | `central_server/mock_automation_runner.py`, `central_server/database_documents.py` | 2/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| T21 | `central_server/real_runtime.py`, `central_server/account_query.py`, `infrastructure/kiwoom_rest/mock_account.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| T22 | `infrastructure/persistence/settings_repository.py`, `bootstrap.py` | 2/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| N1 | `central_server/app.py`, `central_server/news_service.py`, `central_server/news_sources.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| N2 | `central_server/news_sources.py`, `central_server/database_news_sources.py` | 2/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| N3 | `central_server/market_news_sources.py`, `central_server/database_news_sources.py` | 2/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| N4 | `central_server/news_jobs.py`, `central_server/database_news_jobs.py`, `central_server/database_news_job_writes.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| N5 | `central_server/news_service.py`, `central_server/news_jobs.py`, `central_server/ai_service.py`, `central_server/database_news_revisions.py` | 4/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| N6 | `presentation/news_window_coordinator.py`, `news_process.py`, `presentation/stock_news_window.py`, `presentation/news_workers.py`, `infrastructure/central_news_client.py`, `central_server/news_service.py`, `infrastructure/persistence/stock_news_repository.py` | 7/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| N7 | `presentation/news_workers.py`, `infrastructure/news_ai.py`, `infrastructure/persistence/news_ai_repository.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| N8 | `news_process.py`, `infrastructure/central_content_sync.py`, `infrastructure/central_theme_sync.py`, `infrastructure/persistence/theme_backup.py` | 4/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| R1 | `presentation/research_dialog.py`, `presentation/process_control.py`, `research_process.py`, `infrastructure/persistence/strict_restore.py` | 4/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| R2 | `research_process.py`, `application/research_search.py`, `infrastructure/persistence/research_repository.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| R3 | `presentation/research_dialog.py`, `research_process.py`, `application/research_hypotheses.py`, `infrastructure/persistence/research_repository.py` | 4/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| R4 | `presentation/research_dialog.py`, `research_process.py`, `application/research_evaluation.py`, `infrastructure/persistence/research_repository.py` | 4/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| R5 | `central_server/research_read_routes.py`, `central_server/database_research_export.py`, `infrastructure/research_data_source.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| R6 | `application/research_families.py`, `central_server/candidate_monitor.py`, `central_server/mock_automation_runner.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| R7 | `application/mock_automation_candidate.py`, `central_server/mock_publication_routes.py`, `infrastructure/persistence/forward_evaluation_repository.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| D1 | `central_server/app.py`, `central_server/diagnostic_run_routes.py`, `central_server/diagnostic_runs.py`, `central_server/diagnostic_sampling.py` | 4/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| D2 | `central_server/database.py`, `central_server/diagnostic_replay_contract.py` | 2/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| D3 | `central_server/diagnostic_trace.py`, `central_server/diagnostic_trace_ram.py`, `central_server/diagnostic_replay.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |
| D4 | `central_server/diagnostic_replay_runtime.py`, `central_server/diagnostic_replay_baseline.py`, `central_server/diagnostic_recorded_execution.py` | 3/0/0 | 기존 실행 근거·한계 보존. 모듈 own-body 미독0; 실제환경은 JSON runtime_unverified/관련 R3에 유지 |

### G01~G09 조사 계획과 현재 종결 상태

기존에 확인한 함수를 다시 전수 조사하지 않는다. 다음 목록은 직전의 **감사 잔여 G01~G09 계획(보존)**이며 발견된 제품문제 R3-001~062와 구분한다. 모듈마다 정확한 함수/분기는 아래394행 및 상세JSON에 있다. ‘본문을 읽지 못함’과 ‘실제환경 실행이 필요함’을 구분하여 전자는 추가 코드감사, 후자는 격리 검증/환경 수락으로 진행한다.

| 종전 잔여 | 우선 | 종전 조사 계획·현재 상태 |
|---|---|---|
| G01 native 저장/스키마 | P1 | QueryStore의 모든leaf/table consumer·revision/ACK/동시성, central_schema1~18 data repair와 local journal v5~7/legacy repair. 이미확인한 _upsert_many/batch/range는 제외. 각 책임의writer→reader→API/restore와 rollback 근거 확보; **현재 코드 책임 검토 종결**, 실제 환경·장애 조합은 runtime_unverified와 관련 R3 완료 기준으로 이관 |
| G02 일지 worker/복원 | P1 | History/confirm/enrichment/backfill/marketindex의 전체cancel·wait timeout·최종ACK, mainentrywriter와수동일지복원 격리, review최종저장. 새051/052/055 기준이 확보된 부분은 재조사 없이 fixture전환; **현재 코드 책임 검토 종결**, 실제 환경·장애 조합은 runtime_unverified와 관련 R3 완료 기준으로 이관 |
| G03 승인/transport/settings | P1 | 계좌query 내부paging/credential, 모든client endpoint·failover 세대/재구독, vault/profile/activation·secureproxy/각hook의 failure matrix. 기존control/Risk/nativeclaim read는 증거없이제거하지 않음; **현재 코드 책임 검토 종결**, 실제 환경·장애 조합은 runtime_unverified와 관련 R3 완료 기준으로 이관 |
| G04 연구/Family/state | P2 | 모든config/kernel/entry/exit/FILL·연구discovery/storage/budget/final partition·lease crash; context/thesis producer/feedback dispatcher의명시등록. registry가공유됐다는근거를전체연결로확장하지 않음; **현재 코드 책임 검토 종결**, 실제 환경·장애 조합은 runtime_unverified와 관련 R3 완료 기준으로 이관 |
| G05 진단/replay | P2 | diagnostic_account/archive/context/top20 experiments/sampler/window/chunk/manifest 전체본문·소비, baseline thread/nativePG restore 소유. 기존D1~4 wrapper 부분은재사용, 파일수/캡처hash만으로정상판정금지; **현재 코드 책임 검토 종결**, 실제 환경·장애 조합은 runtime_unverified와 관련 R3 완료 기준으로 이관 |
| G06 뉴스 원본/이력 | P2 | 모든공급자parser·article/body/AI adapter/계정회전·historical archive준비·봉인/hold/import·이력cursor/오류복구. 기존claim043/ACK044/CLI046은반복발견안함; **현재 코드 책임 검토 종결**, 실제 환경·장애 조합은 runtime_unverified와 관련 R3 완료 기준으로 이관 |
| G07 theme/OCR/catalog/backup | P2 | OCR native 실행과뒤parser·모든themeprofile/alias/exclusion/import원자성, Drivev2components/legacy/다중PC·복원실패. callback054/ACK056은 기존재현으로 이어감; **현재 코드 책임 검토 종결**, 실제 환경·장애 조합은 runtime_unverified와 관련 R3 완료 기준으로 이관 |
| G08 UI/표시·설정 | P3 | MainWindow/StockNewsWindow/JournalWindow/ResearchDialog 나머지화면·paint/mouse/layout·저장설정/숨김재표시·변경영향. 파일분리자체를목표로하지 않음; **현재 코드 책임 검토 종결**, 실제 환경·장애 조합은 runtime_unverified와 관련 R3 완료 기준으로 이관 |
| G09 정적만/미매핑 | P2~P4 | 아래 정적만인 모듈과 기능미매핑의 caller/body/consumer를 우선도별로 확정. 순수helper/contract도 실제입력·소비/예외경계를확인하기전완료금지; 이름검색0으로deadcode제거금지; **현재 코드 책임 검토 종결**, 실제 환경·장애 조합은 runtime_unverified와 관련 R3 완료 기준으로 이관 |

구현 이어가기는 기존 **R3-002+023**의 원거래일 실패 재전달부터다. 이번에 G01~G09 코드 미독 잔여는0으로 종결했다. 앞으로는 해당 R3의 반례·완료 기준과 관련 소스 지문만 확인하며, 실제 수정으로 근거가 무효화된 범위만 다시 감사한다. 둘 다 이 원장과 관련 코드 지문만 대조하고 전체394개를 재목록화하지 않는다.

### 새 발견의 우선순위와 검증

R3-051~062는 모두 **대기/미해결/제품미변경/commit없음**이다. 원인·caller/consumer·방법·위험·의존·완료기준은 아래각ID에 쓴다. 052/056은 사용자편집보존, 054는미연결후속,060/061은수집수명/결측의미 오류다.062는본문상await인과만확인해 **blocked-store 계측부터**이며운영병목으로단정하지않는다. journal_process(051/052/055/057~059), MainWindow(054/056), external collector(060/061), Collector/MarketEvents(062)는각각공유상태/파일소유를직렬조정하고그룹이겹치지않으면계속병렬진행한다.

이번 오프라인 진단은 **root5 + application3 + desktop4 + server2 = 14조건**이다. 현재문제/비용재현과migration보호확인으로, 제품수정회귀14건이나전기능정상수락이아니다. root/application/server의boundedworker는exit0·worker/자식종료·임시자료정리를확인했고desktop4조건은QApplication/DB/network없는actualmethod+fakeowner의단일process assertion이다. root최초sandbox TEMP접근거부2오류/보존자료는같은진단의사용자TEMP재실행과구분한다. 서버원장생성기최초path계산assert실패도제품오류로세지않는다.

현재 QueryStore 정적계약도읽기전용재확인했다: `audit_query_store_consumers.py --check`는 **exit1/review_required**(107protocol/379reviewedsites/39same-name/61forwarding,parse0/stale0·site/candidate/forwarddelta0,contractsignature차이)다. 이결과는정적 계약이며depth완료를생성하지않는다. 승인baseline을이번에변경하거나실패를성공으로덮지않았다. 이전통과/실패는역사로보존하고최신근거는`query-store-current.json`이다.

제품394개 source before/after와집합동일성,기존 R3-001/004 구간보존,ID/394행/77경로/F25중복·누락검증및diff check는`data/diagnostics/refactor-r3-coverage-20261018/verification.json`에기록한다. 운영DB/NAS/실제GUI·Kiwoom/AI/주문/전수회귀/commit/push/배포는실행하지않았다.

### 추가 정밀 범위의 실행 경로·근거

1. **일지 진입 스냅샷 소비**: JournalWindow.__init__:138~151의 snapshot loader 주입 → `_show_trade_analysis` → TradeAnalysisPreparationService.prepare:136~290 → load_episode_entry_snapshots → JournalRepository.load_entry_snapshots/StockNewsRepository.load → 비어 있는 뉴스만 backfill → prepared entry_snapshots → analyze_trade_cycles/build_trade_analysis_view_model. 기존 P06/P07은 writer측만 다뤘으므로 이 read-side 반복/선택쓰기 오류는 새 범위다.
2. **복기 편집·닫기**: `_review_changed`:2039 → dirty+700ms timer → `_save_active_review`:2046 성공 후 dirty 해제. closeEvent:2258 → shutdown:2244 → app.exit, child command/parent loss도 같은 shutdown을 쓴다. 저장 대기 처리 누락은 entry snapshot queue R3-031과 다른 사용자 편집 책임이다.
3. **업데이트 적용**: desktop update worker/서비스 → helper CLI → wait PID → apply_archives:153 → extract/path+manifest 검증 → changed 순차 copy2/deleted unlink → archive 제거 → cleanup → 재실행. 이전 P01/P11의 앱 시작/종료 본문과 별개의 설치파일 실패 경계다.
4. **Drive 백업·다음 기동 복원**: worker/controller → GoogleDriveSyncService.upload(원 native snapshot→immutable generation→legacy aliases→manifest 최종 게시), stage_restore→download(stage_only)→StrictRestoreCoordinator.stage(검증/동결)→bootstrap.enter_main(독점 lease)→before image→PREPARED/APPLYING→settings/theme/news AI→COMMITTED. crash APPLYING은 before-image 검증 후 rollback. child lease는 apply 중 진입을 막는다. legacy direct download의 partial DB applied 표식도 확인했다. v2 download 전체/OAuth·실제 Drive 경합은 남는다.
5. **설정 백업의 경계**: SettingsBackupService는 DB 변경을 한 transaction으로 확정한 뒤 자산/naver_news 설정을 기록한다. 뒤 단계 실패는 `local_database_applied=True`로 caller에 알린다. StrictRestore는 이를 전체 before-image로 되돌릴 수 있지만 standalone import의 부분 적용은 명시 계약이다. 단순히 서로 다른 저장 경계가 있다는 이유로 새 결함으로 등록하지 않았다.
6. **native schema helper**: main/journal/news/research initialization → SQLiteMigrationRunner.plan/prepare/ensure_compatible → 각 savepoint/marker → release/rollback. 해당 모듈의 전체 본문과 실제 caller를 확인했지만 각 DB의 모든 migration 함수를 완료 처리하지 않았다. journal schema v5~7 계좌 이전/legacy repair는 잔여다.
7. **메타데이터·관측 helper**: local minute/daily observation의 관측시각/complete/value kind/source → 같은 writer transaction의 upsert guards → reader codec; 별도 MarketDataMetadataRepository class 자체의 production 조립은 발견하지 못했다. import 이름 검색만으로 미연결 기능이라고 확정하지 않는다. optional clock fallback/legacy SQL CURRENT_TIMESTAMP는 기존 R3-023에 보강한다.
8. **일지 백업**: MainWindow._import_journal_backup은 child에 shutdown을 보내고 wait 성공 뒤 SQLite backup으로 대체한다. main의 EntrySnapshotWriter/native job까지 같은 복원 경계에서 멈추는지는 확인되지 않았다. 실제 동시 복원 시나리오의 미검증으로 R3-025에 남기며 실제 유실로 단정하지 않는다.

아래 경로의 `src/kiwoom_monitor/` 접두어는 생략했다. 정확한 모듈별 읽기 범위는 JSON에 있다. 상태 owner와 최종 소비를 구분하며 전체 기능을 정상 연결로 승격하지 않는다.

|경로|실제 entry→본문→상태/저장→소비|확인한 성공·실패·동시·종료|잔여/연결 상태|
|---|---|---|---|
|D01 KRX→OCR/import, F10/F22/F23/F24|`MainWindow._start_image_theme_ocr:2315→_with_krx_stock_catalog:2359→KrxStockCatalogWorkerController.start:30→StockCatalogWorker.run:18→stock_lookup.upsert_many/settings 성공표시→completed→OCR worker→검토 rows→기존 theme change writer`|catalog HTML parser 정규화·빈결과/헤더 오류, fetch 실패 callback, 성공 날짜, optional KIND 실패, worker interruption, OCR 검토/저장 caller, native OCR 종료 대기 본문 확인. busy start에서 새 continuation 미등록을 actual method로 재현|**부분 연결**. G-D1. OCR 엔진 초기화/predict/run 및 뒤쪽 parser 미확인; 실제 Paddle/native 종료·파일 import/store 원자성·실제 KRX 미실행|
|D02 분리 차트, F08/F18/F24|`JournalWindow._sync_detached_charts:1726→선택종목/일봉/지수 repo reads→DetachedChartWindow.set_data:274→6Panel.set_data:115/_apply_source:162→MinuteChart.set_rows:230/set_interval:246→aggregate_chart_rows→뷰`|분리창 source/interval/shared 상태, export의 선택 패널·HTML escaping·파일오류, QSettings close body 전부 확인. 숨김창+미선택 지수라도 read6, 동일 source6패널 aggregation12회 actual method 재현. 일봉 worker 완료의 code/target identity와 latest pending 인접 consumer 확인|**부분 연결**. G-D2. chart paint/mouse/geometry·전체 분석/실시간 갱신 트리거 주기·native DB 비용·GUI hidden/show/reopen 수락 미실행|
|D03 복기 수정/저장/닫기, F18/F24|`JournalWindow._review_changed:2039→dirty+700ms timer→_save_active_review:2046→repo.save_trade_review`; close→`shutdown:2244/closeEvent:2258`|저장 본문은 성공 뒤 dirty 해제. shutdown worker stop/wait와 close QSettings/자식창 닫기 본문에 dirty 복기 flush 없음을 확인. ViewModel summary/cost와 AnalysisEnrichmentWorker success/item_failed/interruption도 일부 확인|**부분 연결**. root의 R3-052 통합 대상이며 여기서 새 ID/별도 구현 없음. 전체 trade_review_view_model/episode enrichment·DB 실패·실제 editor close 타이밍은 root/잔여|
|D04 Google Drive, F22/F23/F24|`MainWindow._schedule_google_drive_upload:1466→dirty/settings marker/debounce→_start_google_drive_sync→GoogleDriveWorkerController→background GoogleDriveSyncWorker.run→service.upload/download/stage_restore→_on_google_drive_sync_completed:1609→설정cache/layout/ranking/UI 재적용`|UI dirty owner, staged restore 차단의 기존 역할, busy/start/finished release, download partial 실패시 메모리 재적용 경로, 충돌 선택·첫백업 후 초기순위 연계 본문. 이전 ACK가 이후 dirty/pending을 지우는 실제 caller 메서드 재현|**부분 연결**. G-D3. persistence 서비스 export snapshot·원격 전송/실제 손실은 이 fixture 미확인(root 별도 담당). 다운로드/restore 데이터 보존·native QThread completion/close 실제 경합 미실행|
|D05 설정/테마/일지 backup UI, F18/F22/F23/F24|`MainWindow:2028-2152→파일 선택→기존 settings/theme/journal backup repository→settings._apply_downloaded_google_drive_data/프로필 적용/일지창 재시작`|동기 native backup UI caller, JSON 로컬키 제외, import 뒤 cache/layout/display refresh, 테마프로필 검증·변경 판단, 일지프로세스 stop/wait5초 실패경고 확인|**부분 정밀**. root persistence/strict restore의 계약과 연결해 읽음. 전체 backup store 구현/DB fault/복원 도중 동시 writer/실제 프로세스 중단은 여기서 검증하지 않음|
|D06 앱 업데이트, F01/F24|`MainWindow:1240-1378→UpdateCheckWorker→build_update_plan→UpdateWorkerController→UpdateDownloadWorker→zip.part/hash/replace→helper runas launch→close`|release 정렬·버전·draft/prerelease 제외·모든 중간 archive 필요·checksum 부재, HTTP 오류 caller, chunk interruption/hash 불일치 part 삭제, controller 종료 해제, helper 누락/launch 실패 UI 본문 확인|**부분 정밀**. planner만 모든 정적 책임 범위 완료. 실제 다운로드/사용자 권한/GUI 종료·helper 원자교체/부분실패 복구는 root의 updater 감사와 R3-053에 연결|
|D07 local/remote/failover, F01/F03/F04/F08/F10/F18/F23/F24|`client_factory.build...→remote/local/failover/parallel→FailoverKiwoomRestClient.request/stored readers/read_account_snapshot→remote transport 또는 local→상위 consumer`; local config/binding과 realtime controller/sender도 일부 연결|CentralServerUnavailable만 local fallback, order TR 차단, stored API None sentinel 계약, 계좌 batch fallback은 첫page부터 재시작, local account binding scope 불일치 실패, factory 모드 분기, realtime LOGIN/REG 승인필터·deadline/retry/cancel gather body 확인|**부분 정밀**. 모든 remote/client endpoint, DPAPI save/기존profile 전체, binding load/full동시성, 실제 local→remote 전환/재구독/계좌수락은 미실행. 안전계층은 제거 대상으로 간주하지 않음|
|D08 historical news archive/review/readiness, F15/F19/F21/F24|`HistoricalNewsArchiveDialog→archive client capabilities/v1-v2/search/page/detail→requestid+dataset generation 검증→표시`; `HistoricalNewsReviewDialog→CSV human_save→immutable decisions→development inputs/split`; readiness caller→`historical_research_readiness`|archive와 live endpoint 구분, 최신 요청/종목 검증, page queue 병합·실패 cursor 보존, review dirty-save 실패 close ignore, frozen decisions/source hash/split matching·OOS 제외 본문. readiness의 frozen candidate/date·연속 봉 pair·부족사유·모르는 입력을 보존하는 본문 확인|**부분 정밀**. archive 마지막 상세표시/close, review queue/profile/event split 중간, archive/import 저장소·CLI readiness consumer본문 미확인. minute 구현 범위를 연구 전체 요구 충족으로 확대하지 않음|
|D09 parser/표시/SSL 보조|xlsx 첫sheet/sharedstrings/inline/header parser→반환 rows; exchange date ZIP/HTML 명시시각 parser→script caller; `system_ssl.ssl_context→content/credentials/article client`|parser 전체 본문·오류 전파와 SSL default trust/Windows cert fallback 분기는 확인; `time_display.format_kst_time→archive/trade 화면`과 badge 색→MainWindow는 모든 정적 책임 범위 확인|xlsx 실제 import consumer, exchange dates script 소비본문, SSL 모든 client 실패 lifecycle은 잔여. 작은 모듈도 consumer 미확인이면 완료false 유지|

| 누락되었던 경로 | 추가로 읽은 본문/실제 caller·consumer | 실패/상태/종료 확인 | 잔여 |
|---|---|---|---|
| 복기 자동판정 | `journal_process.JournalWindow._show_trade_analysis:1926 → TradeAnalysisPreparationService.prepare:136 → classify_with_strategy_packs:47 → generic_strategy_evaluator.evaluate_strategy_pack:14 → save_trade_analysis_setup/analysis_revision → analyze_trade_cycles:33 → build_trade_analysis_view_model` | 일봉 import 오류 무시 후 저장분 사용, draft 누락·disabled·unapproved 제외, legacy/manual cycle override, 계산 후 자동판정 저장, snapshot/revision 후속. 순수 분석기는 독립 task/close 없음 | 실제 Qt 전체/DB저장 실패·각 account scope 경합은 미실행. snapshot/news N+1은 root 담당으로 중복 등록하지 않음 |
| 사용자 전략팩 추출/검토/복원 | `StrategyPackExtractionWorker → extract_strategy_draft → merge/extend/review → JournalStrategySettingsRepository save draft/version → manifest 승인 → JournalWindow settings reload/preparer` | worker failed signal, 검토취소/승인 상태 분리, 추가강의 version 보관/restore. raw source 이동 시 DB source_texts 보존 | PDF/docx 추출 helper 전체·대용량·실제 창 종료 경합/불완전 저장복구는 미검증 |
| 복기 규칙의 출처/표시 | `strategy_pack_for_result → strategy_review_reference → view_model format_cycle_analysis_block` | 선택 pack와 default 강의 분기. prefix 출처 오인 G-A1 | 실제 QWidget 렌더/모든 source pack 조합은 미검증 |
| 복기 당시/사후 가격 경계 | `analyze_trade_episode`의 held_prices 경계와 `entry_context`/설정 거래대금 판정 → view model | MFE/MAE는 입·청산 분의 전체 OHLC 사용을 제한하는 기존 보호가 있음. 별도 진입분 거래대금 조건은 G-A2 | 모든 분할/재진입·미확인 분봉·timezone 조합은 미검증 |
| 개인 강의 원칙 | JournalWindow init/settings reload → personal rules Markdown/docx 추출 → journal_settings fallback → lesson/source count/참고문장 | 누락/읽기 실패/손상 시 빈 추출, 저장된 원칙 소비 경계 확인 | 모든 문서 style/한글 heading 변형·복구 소비는 미전수 |
| 과거 learning/review | `prepare_historical_learning_cases CLI → build/write/load_historical_learning_cases → prepare_historical_news_review_queue CLI → queue → HistoricalNewsReviewDialog sheet/decision freeze/development input action` | 사후 종목선택·retrospective 뉴스·outcome 분리 유지, hash/count/immutable staging 검증, UI가 모델학습 미완료를 표시 | learning outcome helper, 모든 archive parser/계획/OOS/봉인/hold·실제 학습은 미검증 |
| feedback 재검증 | `dispatch_feedback_revalidation`의 version/request 저장→campaign enqueue→exact spec readback→receipt 저장 본문 | final template 금지·입력 계약 대조·부분단계 실패 가능 경계 확인 | src/scripts의 명시적 caller는 확인 못했고 unit fixture에서만 dispatcher 호출 발견. 자동 앱 적용 완료 아님. cross-store 실패 재실행은 미검증, 기존 R3-024 연결 범위 |
| Context/Thesis | `build_context_hypothesis/build_news_hypotheses/advance_context_hypothesis`; `build_entry_thesis/evaluate_entry_thesis`; 실제 `market_theme_shadow._stock_thesis_result → evaluate_entry_thesis` 소비 | 대상/가용시점/venue/terminal 상태 보존, unknown leadership HOLD·order_authorized=False | Context builder/advance 및 thesis builder의 src/scripts 직접 producer caller는 확인 못함. stored input을 받는 shadow 소비와 별개. 모든 생성/전이/설정 UI는 미검증 |
| 보존 Family 내부 | `evaluate_breakout_bar → compute_rolling_high_breakout → evaluate_factor_entry_bar`, pullback adapter→같은 공통 evaluator | available_at/분 coverage/session/실제 reference/미확인 factor를 구분하는 rolling 경로를 추가 확인 | `_decide`/청산·전체 rank/pullback 계산·비용/FILL·모든 Family 동등성은 미검증. registry 공유가 내부 전체 감사와 같지 않음 |
| 새 leader 입력 | LeaderBreakoutContext/Policy/ExecutionContract, document_breakout_reference/observation | explicit ref/필수 미정·이전 완료분 범위·0B availability/source continuity/WAIT 보존 | 실제 crossing/확인수/포지션 kernel 전체 및 모든 연구/live adapter는 미검증. 미정 조건 생성 없음 |
| domain/parser/calculation | theme header/rows/text → 실제 ThemeDialog import/preview/save, ranking code/label·strength badge 소비, field provenance/entry_context observation/metadata availability, bounded realtime gap·TOP20 cohort prefix | 검증 오류 표시/저장 전 preview, unknown/missing/TypeError availability 실패 보존; 순수 helper 수명 역할 확인 | 전체 입력조합/저장 rollback/정확한 snapshot export caller·후속 canonical hash/TOP20 전체 reconcile는 미검증 |

| 경로 | actual caller→body→owner/native→consumer | 정상·실패/재시도 | 동시·종료와 남은 범위 |
| --- | --- | --- | --- |
| **G1 외부 시세** | `app.create_app:410~416 → YahooDelayedMarketCollector → start:93/_run:269 → collect_once:144/_tracked_collect_once:158/_collect_once:181 → _fetch/parse_yahoo_chart → database_external_market.save_external_bars + documents roll/status → market_read_routes.external_bars:286` | 정상은 active/next5m+당일미완료1d별 저장, 일봉계약별성공 RAM mark, 실패계약 다음poll재시도. 저장소는 unchanged OHLCV에 no-op WHERE가 이미 있어 동일bar 무조건UPDATE로 재등록하지 않음. G-S1/S2 두 누락분기를 실제코드/fake상류로 재현 | collection은shared task/shield, include_daily escalation은기존작업합류후 새cycle. settings_lock이stop/drain후새설정start, close가업데이트task+collection합류. 실제공급자/모든cancel/자정·정렬·NaN/Inf·PG 계약은미완료 |
| **G1 futures helper** | collector의active/next rows→`trailing_volume/common_end`, `latest_price/previous_daily_close/change_percent`, `evaluate_roll`→native roll-state | 모든helper body와quarter/month/year코드 계산을 읽음. 기존confirmation임계값을변경하지않음. 공급자실패를0volume으로바꾸는문제는helper단독정책이아니라caller의자료누락경계 G-S2 | latest_bar_date/latest_session_volume 활성caller는현재증거로확정못함. 이두함수를미연결이라고단정하지않으며invalidtimezone·nonfinite 숫자 등실행미검증 |
| **G1 native bars/read** | collector→SQLite/PG external-bar UPSERT, API→instrument/timeframe LIMIT→contract필드포함 반환 | unique identity(provider,instrument,contract,timeframe,bar_time), no-op의값비교와updated_at보존 의미 확인. API는계약별row를그대로전달 | 한instrument에같은시각두contract가함께읽히는것은코드사실. PC consumer의선택/차트정책까지확인하지않아오류로등록하지않음. 실제PG/순서·LIMIT parity 잔여 |
| **G2 운영 설정** | `app operational_state → operational_settings_routes.PUT:173 → state.lock/CAS → native server_operational_settings → condition/external/AI/news/Shadow apply → applied_revision/status` | DB저장실패는기존runtime유지, 적용중실패는revision저장/applied불일치503, 같은rev요청으로recovery_required 재적용. no-op은DB쓰기생략. 후보Shadow replacement는저장전검증 | state.lock과각service settings/close owner는역할유지. 여러service부분적용/모든설정필드·provider model조회·condition runtimephase 조합은이번미실행. 이부분확인으로운영설정모듈전체완료false |
| **G3 condition 등록** | `Collector._receive:789 → market_events.handle_ws_message:357 → poll/list/register/clear:257~630 → signal/metadata queue → _retry_cohort_input/_run_cohort_work → native cohort revision/current → _cohort/Hub subscription → condition/read API` | seq/policy revision fence,오래된초기응답/잘못된cursor/queue상한은RECOVERY_REQUIRED,수락된신호는nativeACK이후RAM게시. 같은cursor/현재snapshot이retry동안고정된다. G-S3의receive-await경계추가발견 | state_lock은ELIGIBILITY/signal/expiry의oldsnapshot덮기를막고ownednative/retry/drain은정지전접수입력을보존. close는signal→후손metadata→fact native순서로합류. VI/모든session/factadmission/실제ACKdrop·PG는잔여 |
| **G3 cohort native** | `_save_cohort_revision → database_market_events.record_hot_cohort_revision → revision unique key/current UPSERT → load_hot_cohort → app restore/read API` | SQLite와PG의같은업무transaction·nullableNXT coalesce를대조. native동일revision은멱등성을보유 | 모든VI/fact/nativehistory helper나stale concurrentrevision의current순서는미검토. metadata의알수없는NXT응답이False가되는현재식은관찰했으나정확한요구계약/consumer영향은잔여로남기고별도ID를임의확정하지않음 |
| **G4 vault 경계** | `app:120~124 → CredentialStore/compose → Runtime.prepare/apply/recover → vault.load/save/marker → native metadata activation → provider owner` | file commitpoint,marker후행실패는다음load조정,허용provider/profile·expectedrevision/AAD·credentials field검사,unconfigured와damaged의env fallback차이를읽음. damagedprovider는복구필요·다른provider계속 | threadRLock/processlease/fsync/atomicrename/close는실제역할로유지. **vault/key/secret/OS보안조작은실행하지않음**. symlink/권한/디스크fault·암호화commit/DBACK경합을확인했다고말하지않음 |
| **G4 credential runtime/HTTP** | secure/bounded routes→prepare/dedupe/TTL/guard→validatecandidate→drain→begin_commit→encrypted save→native finalize→publish/resume→safe operation.document/status | `_Operation.document`에secret/digest없음,repr에서credential/candidate제외,body허용키/크기/중복JSON검사,query scrub·HTTPS/trustedproxy와safeerrorcode확인. commit전실패resume와commit불명RECOVERY_REQUIRED 구분 | op task/shield,close는prepare의민감자료해제와진행apply합류. provider별hooks전체·모든cancel/deadline·secure proxy 실제요청·임시후보release·rollback의실행미검증. 보안수락완료로보고하지않음 |
| **G4 activation native/privacy** | Runtime/compose→database_credentials helpers→profile requestHMAC/activationmetadata/account binding/settings 동일cursor→safe receipt/currentowner | DBcolumns는메타/operationdigest·UUIDscope이고vault암호화원문키를저장하는새SQL은없음. profile불변성·replay가뒤에변경된accountsettings를되돌리지않는분기·disableoldprofile보호확인 | wrapper별native transaction/PGlock/모든archive/rename/profilelimit·ACKloss 조합은미완료. 개인정보나실제DB를조회하지않음 |
| **G5 account/market role** | account_settings_routes GET/PUT 및 credential nested PUT→admitted real/mockowner→real_runtime.change_market_role:513/_change:526→native account/marketsettings CAS→drained broker swap→monitor/collector resume→stored/applied status | typedverifiedscope·profilebinding·revisionCAS/no-op,모의주문허용은mock+monitor조건,role commit불명은old-keyrollback주장없이fence. 저장settings와appliedrevision을구분 | 같은owner/no-op상세는기존trading근거재사용. 추가role변경의accountreads/brokers/Collector drain→nativecommit→swap→resume본문읽음. 실제계좌/보안실행금지. contextmanager/모든bindingguard와동시credential조합은잔여 |
| **G6 schema/native초기화** | nativeSQLite initialize(outertransaction/keeper)·PG observedconnection→central_schema_migrations→MigrationRunner.validate/savepoint/apply/ledger→release/rollback→app | plan1부터연속/name불일치/미지원신규버전fail,실패rollbacksavepoint,batch plan원자보존본문확인. credential19/five-minute20/PGshadow21 schemahelper만추가대조 | migration1~18의모든DDL/datarepair/원본자료변환미전수. 실제PG DDL/savepoint/동시기동/권한·migration실행은없음. schema파일존재·SQL목록을무손실전환증거로쓰지않음 |

G2/G4/G5/G6는 **실행검증이 아닌 추가 본문 정밀 범위**다. 추가 실행은 G1의 fake상류·메모리metadata2개 분기뿐이며 보안/vault·계좌·DB·외부네트워크를 실행하지 않았다. `external-{result,execution}.json`:2건,exit0/worker·자식종료/TEMP제거. 이통과는현재문제를재현한것이지수정통과가아니다.

### 사용 중인 경로와 구현만 있는 capability의 구분

- Journal 기본 Mimosa/사용자 strategy pack은 **실제 복기 분류/표시 연결**이다. 자동매매의 research Family와 이름이 비슷하다는 이유로 같은 전략 owner로 합치지 않는다. 활성·승인된 사용자 pack만 자동분류하는 기존 정책을 유지하며 예시 기준을 사용자 live 전략으로 승격하지 않는다.
- `breakout_strategy.evaluate_realtime_breakout`는 src/scripts의 명시적 호출을 확인하지 못했고 단위 fixture에서만 호출을 찾았다. registered `evaluate_breakout_bar`의 실제 연구/Shadow/Runner 연결과 구분한다. 이 사실만으로 삭제하지 않으며 명시 비교용 경로인지 이전 계약과 대조할 잔여다.
- Context 뉴스/장중 가설 builder/advance와 feedback revalidation dispatcher는 **함수·저장소 capability 및 테스트만으로 앱 자동연결 완료를 인정하지 않는다.** exact-name caller search와 해당 함수 본문 확인을 했지만 동적 외부 caller 부재까지 증명한 것은 아니다. 기존 R3-024의 부분 연결 보강이며 새 UI/자동학습 구현 승인이 아니다.
- thesis 평가는 실제 RAM Shadow 결과 생성에서 호출된다. thesis **생성** helper가 앱에서 자동 생성·영속·복원된다는 것은 별도다. explicit stored thesis 입력을 받아 검사하는 consumer를 generic 연구→live 자동승격으로 부르지 않는다.
- retrospective learning/review의 CLI·GUI artifact 경로를 확인했다. 이 경로는 publication-time/collected-time·posthoc 모집단/모델 입력/outcome을 구분하며 실제 LLM 학습·live 규칙 적용은 확인하지 않았다. NAS transfer hold/봉인을 변경하지 않았다.

### 새 R3 상세 — 051~062

### R3-051 — 일지 episode snapshot 뉴스 read의 N+1 및 선택 backfill 쓰기와 표시의 결합

- 분류/우선순위/상태: **재설계 / P2 / 대기**.
- 실제 경로/파일: 위1번, `infrastructure/persistence/journal_snapshot_service.py:news_at_execution:41/load_episode_entry_snapshots:81`, `journal_snapshot_repository.py:load_entry_snapshots:120/save_snapshot_news_backfill:223`, `journal_process.py:145/_show_trade_analysis`.
- 원인: 같은 code/같은 한 번의 episode 준비에서 snapshot마다 동일 limit50 뉴스 목록을 다시 읽는다. 시간 필터는 snapshot별이지만 입력 SQL 집합은 동일하다. 또한 표시용 loader가 optional enrichment를 DB에 저장하고 그 오류를 처리하지 않아 유효 snapshot의 반환까지 막는다.
- 실제 측정/가설: 실제 임시 JournalRepository와 StockNewsRepository, 같은 code 빈뉴스 snapshot10개에서 native news.load10회/최종 snapshot10개. 별도 실제 service+주입 save OperationalError에서는 유효 snapshot1개가 있어도 read 결과 대신 예외 전파. 실제 Qt 분석 오류 표시/GUI 지연·운영 DB크기/SQL시간은 미측정이다.
- 권장: 기존 함수 한 호출 범위에서 code별 뉴스 입력을 한 번 가져와 각 실행시각으로 필터한다. writer/save 실패와 read-side 표시 결과의 계약을 나눠 기존 backfilled provenance와 오류 관측을 보존한다. 전역 cache/TTL/새 서비스는 불필요하다.
- 효과/위험: 같은 episode의 반복 native read를 줄이고 선택쓰기 실패가 분석을 막는 경계를 줄일 수 있다. 48시간~직후5분 창·뉴스 정렬/최대10·계좌 scope/기존 스냅샷 원본은 유지해야 한다. 선택쓰기 무조건 생략이나 오류 무음성공으로 바꾸지 않는다.
- 의존: R3-031은 writer 실패, R3-019는 서버 뉴스 수집으로 다른 owner다. 준비·draft load R3 신규 후보와 같은 prepare에서 만나므로 공용 fixture는 순서 조정한다.
- 완료: 빈/있는 뉴스 snapshot1/10·서로 다른 실행시각·기존뉴스 보존·실패후 재시도/재조회·계좌별 결과를 대조, native load10→1, 저장실패 때 정확한 분석/오류 계약과 GUI 실제 소비를 검증한다. F18/F19/F22.
- 변경/해결 결과/commit: 제품 변경 없음, counterexample2조건 통과, 미해결/없음.

- 정본 판정: **재설계 / P2 / 대기**. 영향 F18/F19/F22. 의존: R3-031은 writer, 이번은 read-side; R3-059와 prepare 상태 직렬. 변경 내용/해결 후 측정/관련 commit: **제품 변경 없음 / 미해결 / 없음**. 위 진단은 현재 조건의 재현이며 해결 회귀가 아니다.


### R3-052 — 700ms debounce 복기 편집이 종료 경계에서 저장되지 않음

- 분류/우선순위/상태: **재설계 / P1 / 대기**.
- 실제 경로: `JournalWindow._review_changed:2039 → _review_save_timer(700) → _save_active_review:2046`; UI closeEvent:2258/command shutdown/parent loss → shutdown:2244 → app.exit.
- 원인: shutdown은 다른6개 timer만 중단하고 review timer/dirty 상태를 처리하지 않는다. close는 저장 확인 없이 종료를 승인한다.
- 측정/가설: actual shutdown 메서드에 dirty=True·save spy·timer를 공급하면 save0/dirtyTrue/reviewtimer.stop0. 전체 native Qt close/700ms 이전 이벤트 실제배달/프로세스 종료 뒤 DB 유실은 이번 미실행이다. 호출 본문에 최종 저장이 없고 app.exit으로 끝나는 정적 근거와 구분한다.
- 권장: 기존 `_save_active_review`와 shutdown owner 안에서 pending 편집 저장/실패 결과를 소비한다. 새 저장 서비스/별도 파일·DB는 추가하지 않는다.
- 효과/위험: 종료 시 사용자가 마지막에 입력한 복기 보존. 잘못된 account/group 저장·중복 저장·DB 오류를 무시한 종료 승인·현재 에피소드 교체와 debounce timer 경합을 보호해야 한다.
- 의존: R3-025·기존 review 저장 scope, parent/command 종료 모두 같은 owner. entry writer R3-031과 합쳐 구현하지 않는다.
- 완료: 임시 실제 DB와 Qt 사용자 편집→700ms 내 close/정상700ms 경과/episode 변경/parent·command 종료/저장실패→재시도 fixture, final readback·계좌 scope·거짓 저장표시0. F22/F24.
- 변경/해결 결과/commit: 제품 변경 없음, 좁은 actual shutdown 재현1조건 통과, 미해결/없음.

- 정본 판정: **재설계 / P1 / 대기**. 영향 F18/F22/F24. 의존: R3-025; R3-031과 별개 편집 dirty owner. 변경 내용/해결 후 측정/관련 commit: **제품 변경 없음 / 미해결 / 없음**. 위 진단은 현재 조건의 재현이며 해결 회귀가 아니다.


### R3-053 — 업데이트 순차 copy 실패 후 혼합 버전 설치 상태

- 분류/우선순위/상태: **재설계 / P2 / 대기**.
- 경로/근거: update worker/서비스가 helper 실행 → `update_helper.apply_update → apply_archives:153 → shutil.copy2` 순차 실행. 각 파일 이전상태 보존/실패 rollback 없이 finally가 staging을 제거한다. archive는 성공 뒤만 삭제한다.
- 실제 측정/가설: 임시 install a/b와 ZIP에서 두번째 copy2만 OSError 주입. 실제 helper 결과 a=new/b=old, archive는 남았다. 실제 설치EXE/잠긴Windows파일/중간 프로세스 강제종료·재실행 실패는 미실행이다.
- 권장: 기존 staging/application 경계에서 변경·삭제 대상의 실패 복원 또는 재개 계약을 명시하고 최소 단위의 원자 교체를 적용한다. 새 범용 installer/관리계층은 만들지 않는다.
- 효과/위험: 확인된 ordinary copy failure 뒤 혼합버전 남김을 줄임. 디스크공간/기존파일 삭제·여러 archive 순서/최종 실행파일/로그·성공 archive 삭제/진행화면 계약을 보존해야 한다.
- 의존: self_update package hash/manifest 검증은 유지한다. 런타임 writer drain(R3-025)과 helper 설치파일 원자성은 구분한다.
- 완료: 실제 임시 파일에서 첫/중간/마지막 copy/delete 오류 및 archive2단계, 최종 old전체 또는 new전체/명시재개 가능상태·사용자파일보존, 실제 helper CLI/Windows 대기·재기동은 별도 격리 수락. F01/F24.
- 변경/해결 결과/commit: 제품 변경 없음, 재현1조건 통과, 미해결/없음.

- 정본 판정: **재설계 / P2 / 대기**. 영향 F01/F24. 의존: 기존 update planner/hash/manifest 보존. 변경 내용/해결 후 측정/관련 commit: **제품 변경 없음 / 미해결 / 없음**. 위 진단은 현재 조건의 재현이며 해결 회귀가 아니다.


### R3-054 — catalog 실행중 진입한 OCR 후속 callback 유실

1. **대상/분류:** `MainWindow._with_krx_stock_catalog:2359`와 `KrxStockCatalogWorkerController.start:30`; **재설계**, 기존 singleworker 격리는 유지.
2. **경로/상태 경계:** 일일/수동 catalog worker가 controller의 `_worker`를 소유한 동안 이미지 OCR을 선택하면 새 UI 후속 `continuation`이 필요하다.
3. **코드 원인:** caller가 continuation을 completed/failed closure로 전달하지만 controller는 `is_running`이면 callback 연결 전에 True 반환한다. 작업 중복 방지 성공과 새 요청 후속 연결 성공을 같은 bool로 표현한다.
4. **측정/가설:** actual caller/controller fixture에서 busy=True, 등록/실행 continuation0 확인. 실제 클릭·QThread callback 스케줄은 미실행. 무조건 매번 발생한다고 하지 않는다.
5. **최소 방법:** 기존 worker의 완료/실패 결과에 새 continuation을 합류시키거나, busy 결과를 caller가 받아 기존 완료 뒤 재시도하도록 한 owner에서 처리한다. 새 worker/manager/polling을 추가하지 않는다. 정확히 한 번 실행/실패후 저장목록 fallback 계약을 먼저 고정한다.
6. **효과/위험:** 필요 catalog fetch 중복 없이 OCR 다음 단계 복구. 완료/실패 경합 시 callback 중복, 취소된 OCR 후속 부활, 종료 중 dialog 생성 위험을 기존 수명 안에서 검증해야 한다.
7. **의존/중복:** R3-014 controller 수명 경계와 연계하지만 기존 broad 유지 판단과 다른 구체적 연결 누락이다. R3-015 기본정보 bulk와는 다른 caller. 정본 R3-054.
8. **우선/상태/영향:** P2/대기 제안; 조사·재현 완료/제품 변경0. F10/F22/F23/F24의 부분 연결 근거. OCR 기능전체 정상으로 승격하지 않음.
9. **완료 기준:** 실제 controller에 격리 signal worker를 넣어 idle/busy→success/failure/cancel/close별 continuation 한번 또는 기존 취소계약 준수 확인. native catalog DB/실제 network 없이 consumer까지 검증하고 기존 한 worker 제한 유지.

- 정본 판정: **재설계 / P2 / 대기**. 영향 F10/F22/F23/F24. 의존: R3-014/015의 single-worker는 유지. 변경 내용/해결 후 측정/관련 commit: **제품 변경 없음 / 미해결 / 없음**. 위 진단은 현재 조건의 재현이며 해결 회귀가 아니다.


### R3-055 — 숨김 분리차트의 조회와 같은 source 중복집계

1. **대상/분류:** `journal_process.py:JournalWindow._sync_detached_charts:1726`, `presentation/detached_chart_window.py:DetachedChartWindow.set_data:274/DetachedChartPanel._apply_source:162`, `presentation/journal_chart_widget.py:MinuteChart.set_rows:230/set_interval:246`; **제거/통합** 후보.
2. **경로/상태 경계:** active episode/data refresh가 분리창 객체 존재만 확인한 뒤 repository read와6패널 재계산. 패널 source/interval은 각 panel 소유, 창 열림 여부와 데이터 세대 동기화는 window/journal owner가 책임진다.
3. **코드 원인:** caller는 visibility/선택 지수에 관계없이 KOSPI/KOSDAQ 분·일봉을 읽고 set_data가 모든 패널에 동일 입력을 재전달한다. panel은 set_rows로 현 interval 집계 뒤 set_interval로 같은 집계를 다시 한다.
4. **측정/가설:** 숨김·추가선택0에서 read메서드6,동일6패널 aggregate12 확인. SQL/물리I/O/장중 부하·중복 전체row크기 미측정. 자동 timer 호출 빈도는 이번 fixture로 추정하지 않는다.
5. **최소 방법:** 기존 source/visible owner에서 필요한 데이터만 읽고 같은 source/interval 집계를 한 번 적용한다. 숨김 중 refresh를 생략한다면 show/reopen에서 최신 data를 즉시 적용하는 기존 consumer를 함께 검증한다. 새 캐시/타이머/중간층을 바로 추가하지 않는다.
6. **효과/위험:** 미소비 DB reader 호출과 동일변환 감소 예상; 수치효과는 관련 전후 fixture로 확정. hidden→show stale, custom 비교종목, index backfill 재시도/빈데이터, interval 변경, 주석/뷰 count 보존 위험.
7. **의존/중복:** R3-027은 메인 TOP20의250ms index chart caller이며 이번은 journal 분리창 repo/source/6패널 경로라 별도 후보가 적절하다. R3-038/039는 서버봉조회 내부와 달라 대체하지 않음. 정본 R3-055; journal_process는 root와 변경 직렬 조율.
8. **우선/상태/영향:** P2/대기 제안; F08/F18/F24. 조사/4조건 중2개 재현 완료, 제품 미변경.
9. **완료 기준:** 동일 fixture의 hidden read수/aggregate수 전후 비교와 actual show/reopen/source/interval/custom/index 변경의 최종 데이터/annotations 동일성, 실패뒤 재시도, 닫기/재시작 계약을 제한 회귀로 확인. 물리I/O 감소로 과장하지 않는다.

- 정본 판정: **제거 / P2 / 대기**. 영향 F08/F18/F24. 의존: R3-027은 별도 TOP20 caller; 052와 journal 소유 조정. 변경 내용/해결 후 측정/관련 commit: **제품 변경 없음 / 미해결 / 없음**. 위 진단은 현재 조건의 재현이며 해결 회귀가 아니다.


### R3-056 — 이전 Drive 업로드 ACK가 이후 로컬 변경 표식을 지움

1. **대상/분류:** `presentation/main_window.py:MainWindow._schedule_google_drive_upload:1466/_on_google_drive_sync_completed:1609`; **재설계** 후보. GoogleDrive worker/controller와 persistence의 원격송신 격리는 유지.
2. **경로/상태 경계:** dirty/pending/changed_at은 UI가 기록, upload service는 별도 worker에서 snapshot 전송, 완료신호가 UI의 현재 dirty를 ACK한다.
3. **코드 원인:** 완료 handler는 어떤 edit/snapshot을 보낸 완료인지 대조하지 않고 upload라면 `_google_drive_dirty=False`,unsynced='0',현재 last_upload_success_at을 기록한다. 이전 전송중 새 edit가 있어도 같은 분기다.
4. **측정/가설:** 실제 schedule+completion에 고정시계를 주입해 새 edit dirty가 old ACK로 clean으로 바뀜을 확인. 실제 snapshot 시점/원격 자료유실은 미재현이며 root persistence 조사와 결합해야 확정한다. fake에서 upload가 먼저 진행 중이었다는 전제는 명시한다.
5. **최소 방법:** 기존 업로드 요청에 연결된 로컬 변경 기준과 ACK를 대응시키고, 그 뒤 변경은 pending으로 남겨 기존 debounce/retry 흐름에서 보낸다. 새 관리계층/임의 동기화 주기/차단정책을 만들지 않는다.
6. **효과/위험:** 편집 후 자동재전송/종료 재시도 의도 보존 예상. settings/themes/both 대상별 ACK, 업로드가 기록하는 marker의 자기 재trigger, restore staged/첫backup/종료업로드와의 정합성 위험.
7. **의존/중복:** R3-044는 `news_process→CentralContentSyncService` local content signature ACK이고, 이번은 MainWindow Google Drive settings/theme cloud backup owner의 별도 경로다. 같은 원칙을 검증하되 같은 구현이라고 합치지 않는다. R3-021 delta/029 theme revision 및 root strict restore와 계약 연계; 정본 R3-056.
8. **우선/상태/영향:** P1/대기 제안(실제 저장성 우선, snapshot 계약 확인 후 확정), F22/F23/F24 부분 연결; 제품 변경0.
9. **완료 기준:** 실제 격리 service export→fake upload transport→새 edit→old ACK→기존 재시도까지 old payload와new payload 순서/dirty marker 검증. target별 실패/취소/close/restart와 staged restore 보존, 관련 설정·테마 소비자 regression이 필요하다.

- 정본 판정: **재설계 / P1 / 대기**. 영향 F22/F23/F24. 의존: R3-044와 다른 MainWindow/Drive owner; snapshot/ACK 검증. 변경 내용/해결 후 측정/관련 commit: **제품 변경 없음 / 미해결 / 없음**. 위 진단은 현재 조건의 재현이며 해결 회귀가 아니다.


### R3-057 — 전략팩 이름 prefix로 잘못된 출처/규칙 수를 표시

1. **분류·우선순위:** 통합 / P2. 표시용 문자열로 판단 원본 owner를 역추론하는 좁은 책임 결합이다. 이 절의 R3 ID로 관리한다.
2. **실제 경로/owner:** `application/trade_strategy_coordinator.py:strategy_pack_for_result:125` → `TradeAnalysisPreparationService.prepare`의 draft_rule_counts, `presentation/trade_review_view_model.py:build_trade_analysis_view_model` → `strategy_review_reference` → `format_cycle_analysis_block`. 원본 manifest/draft 소유는 journal 설정 저장소다.
3. **원인·본문 근거:** `result.subtype.startswith(pack.name)`으로 첫 enabled non-mimosa pack을 고른다. evaluator는 `subtype=f'{manifest.name} · ...'`로 결과를 만든다. 이름은 식별자와 달리 prefix 충돌이 가능하고, UI manifest 입력은 유일한 UUID pack_id와 자유로운 name을 따로 만든다.
4. **실측/추정 구분:** 실제 helper에 승인 enabled 이름 `전략`(id short), `전략확장`(id long)와 subtype `전략확장 · 1/1개 진입조건 일치`를 넣으면 **short**를 선택한다. 실제 `strategy_review_reference`도 **short source**를 반환한다(`application/observations.json`의 `G-A1`(정본 R3-057)). viewmodel의 이 반환값 소비는 본문 확인, 실제 QWidget은 미실행. 주문/매매 선택이 바뀌었다는 주장은 아니다.
5. **최소 방법·범위:** 실제 선택 pack의 식별자를 기존 결과/prepare 전달 안에서 유지하여 reference를 찾는 안을 검토한다. 이름 제한/새전략 조건/새 service는 필요 없다.
6. **예상 효과:** 긴 이름 결과에 짧은 이름 pack의 출처·규칙 수가 붙는 오류 제거. 자동판정 자체의 전략 의미는 보존한다.
7. **부작용·회귀:** 기본분석/사용자팩/구형 저장 subtype, duplicate display names, manual override 시 참고자료의 역할, version restore의 같은 pack_id.
8. **의존:** 실제 journal prepare/viewmodel/strategy_review_context, 원본 draft와 manifest 등록. root snapshot N+1 및 기존 R3-018의 trading Family registry와 다른 기능이다.
9. **완료 검증:** prefix/동명이름/비활성·미승인팩/기본분석/구형결과를 실제 prepare→viewmodel에서 대조하고 selected pack_id/source/rule_count/판정 결과 보존을 확인한다.

- 정본 판정: **통합 / P2 / 대기**. 영향 F18/F22. 의존: R3-059와 prepare 전달 계약 직렬. 변경 내용/해결 후 측정/관련 commit: **제품 변경 없음 / 미해결 / 없음**. 위 진단은 현재 조건의 재현이며 해결 회귀가 아니다.


### R3-058 — 사후 완결 진입분 거래대금으로 ‘매수 직전5분 충족’을 판단

1. **분류·우선순위:** 재설계 / P2. 복기 기준의 가용시점/표시 의미를 일치시키는 범위이며 새 매매전략 조건이 아니다.
2. **실제 경로/owner:** `JournalWindow._show_trade_analysis → analyze_trade_cycles → application/trade_review_analysis.py:analyze_trade_episode:81` → user_setting_matches/warnings/verdict → 실제 review view model.
3. **원인·본문 근거:** `entry_context:185`가 `minute <= start_minute`를 선택하고 마지막5개 분의 **전체 완결 거래대금**을 사용한다. 결과 문장은 `매수 직전 5분 중 ...`이다. 같은 함수의 MFE/MAE는 intraminute 입·청산 OHLC의 미확인 경로를 제한하지만 거래대금 분기는 별도다.
4. **실측/추정 구분:** 실제 매수09:05:30·청산09:06:40, 이전 완료분 데이터는 동일하게 두고 **09:05 전체분 거래대금만1→100억**으로 바꾸면 10억 기준의 결과가 matches없음→`매수 직전 5분 중 1개 분봉 ... 충족`으로 바뀐다(`G-A2`, 정본 R3-058). 09:05:30 이전에100억을 관측했다는 자료는 fixture에 없다. 실제 장중 영향·자동주문 적용은 확인하지 않았다. 이는 연구 수익/실시간 타점 판정의 결함으로 확대하지 않는다.
5. **최소 방법·범위:** 당시 확인된 입력과 사후 진입분 전체값의 의미를 분리한다. 본래 ‘직전’ 기준을 유지할지 사후 복기 지표로 표시할지는 기존 계약/사용자 의도를 확인하고 같은 분석/표시 소비를 함께 바꿔야 한다. 가짜 초봉·틱이나 임의 분내량을 만들지 않는다.
6. **예상 효과:** 매수 후 같은 분의 거래가 매수 전 기준을 충족한 사실로 표시되는 문제 제거.
7. **부작용·회귀:** 정확한 분 경계 진입, 미완결/누락 봉, 다일/분할/재진입 회차, 기존 사용자 threshold·원본 자료 보존, posthoc 지표와 당시 가용 정보 구분.
8. **의존:** trade_episode_analysis_service/viewmodel/formatting 및 실제 분봉 공급. 기존 R3-023 clock 출처 문제와 구분하되 시각 표현 호환을 같이 확인한다.
9. **완료 검증:** 분 정각/분 중간 진입에서 직전 완료분과 진입분 전체값을 독립 변경하여 판정·설명·데이터 상태를 비교하고 관련 UI 저장/readback을 확인한다. 기존 MFE/MAE 경계 회귀도 보존한다.

- 정본 판정: **재설계 / P2 / 대기**. 영향 F18/F22. 의존: 사후 복기와 당시 가용 입력 구분; 새 전략조건 금지. 변경 내용/해결 후 측정/관련 commit: **제품 변경 없음 / 미해결 / 없음**. 위 진단은 현재 조건의 재현이며 해결 회귀가 아니다.


### R3-059 — 한 복기 prepare에서 같은 전략 draft를 전체·회차·표시별 재조회

1. **분류·우선순위:** 제거 / P3. 비용·관측 일관성 후보. 운영 시간 지연은 미측정이다.
2. **실제 경로/owner:** `JournalWindow._show_trade_analysis → TradeAnalysisPreparationService.prepare:136 → classify_with_strategy_packs:47`의 overall·각 cycle; 이후 `prepare`의 draft_rule_counts loop. native `JournalStrategySettingsRepositoryMixin.load_strategy_pack_draft`는 호출마다 새 connection/SELECT를 만든다.
3. **원인·본문 근거:** 같은 prepare 호출에 넘긴 manifest 집합에 대해 draft_loader를 overall1회 + 각cycle1회 + 최종 선택 pack의 표시 rule count1회 실행한다. method는 외부 storage read를 매번 수행하는 계약이며 prepare 안에 공유된 draft snapshot이 없다.
4. **실측/추정 구분:** actual prepare/coordinator/generic evaluator와1개 custom pack·2개 실제 episode cycle을 사용한 repository seam probe에서 **같은 key draft_loader 4회**를 확인했다(`G-A3`, 정본 R3-059). seam은 draft 반환/호출 횟수만 대역이다. 실제 SQL trace/connection·실제ms·실패/동시편집 중 혼합판정은 이번 측정이 아니다. N회차 시 호출 증가가 있다는 본문 사실과 운영 병목을 구분한다.
5. **최소 방법·범위:** 한 prepare 안에서 필요한 draft 한 벌을 읽고 전체/회차/표시가 공유하는 범위부터 비교한다. 장기 RAM cache·새 repository 계층·다른 조회를 무조건 묶는 안은 필요 없다.
6. **예상 효과:** 동일 read 반복 감소와 한 화면의 동일 draft 관측. 효과 수치는 native 재측정 후 확정한다.
7. **부작용·회귀:** draft 없음/손상/미승인, prepare 중 사용자편집·version restore, 실패 재시도·서로 다른 pack·manual override의 표시 근거.
8. **의존:** journal preparer/coordinator/settings repository만. root가 따로 확인한 snapshot별 linked-news N+1과 다른 read다. R3-057 식별자 전달과 같은 prepare 상태를 쓰므로 수정 시 직렬 통합을 검토한다.
9. **완료 검증:** actual native SQLite로 pack수/회차수별 SELECT·연결을 전후 대조하고 모든 classification/source/count 및 실패 시 동작이 같음을 확인한다. 병행 편집은 단일 prepare 관측 계약을 정한 후 검증한다.

- 정본 판정: **제거 / P3 / 대기**. 영향 F18/F22. 의존: R3-057 및051과 prepare 상태 조정. 변경 내용/해결 후 측정/관련 commit: **제품 변경 없음 / 미해결 / 없음**. 위 진단은 현재 조건의 재현이며 해결 회귀가 아니다.


### R3-060 — 외부 시세의 DB read/status 오류가 poll owner를 종료

1. **고유 임시 ID/대상:** R3-060, `external_market_collector.py:_run:269`, `_collect_once:181~193`, `_save_status:286`.
2. **현재 경로:** app/settings→start→_run→collect_once/sharedtask→_collect_once의roll-state metadata load/최종statuswrite→diagnostic/runtime/API. F20/F23/F24(외부지연시장자료 경로).
3. **원인/코드 근거:** `_run`에는 개별cycle 예외를수집하고다음poll로가는분기가없다. `_load_roll_state`와계약코드계산이innertry밖이며, 마지막failedstatus저장도다시실패하면예외가빠져나간다. 정상수집기주석의“다른중앙서비스격리”와별개로이수집owner가그대로종료된다.
4. **실측/가설 구분:** actualstart/_run/sharedcollect에fakeStore metadata read1회OSError→task_done true/load_calls1/operational_enabled true/running false. `external-poll-failure.json`. 현재close도이미실패한task를await해OSError가전파됨을fixture가관찰. 실제DB장애빈도·운영누락기간은미측정이다.
5. **권장 방법/분류:** **재설계/P2**. 기존poll owner안에서cycle실패관측·기존poll주기재시도를소유하는작은안을검토한다. 정상close/cancel을오류재시도로바꾸거나새worker/service/간격을추가하지않는다.
6. **효과/위험:** 일시metadata장애뒤자동수집회복가능. 영구설정오류를무한무음루프로숨기면안되며diagnostics/실패상태를보존해야한다. outerlifespan의dependency보존계약을이유없이변경하지않는다.
7. **의존:** 기존R3-023(host시간잔여)·R3-025(ownedclose)/operational설정applied상태와대조. R3-019뉴스continue나R3-035Top20lock과다른owner다.
8. **우선순위/상태:** P2/대기,제품변경없음·미해결·commit없음.
9. **완료 기준/검증:** 첫read/후속save/statuswrite각fault→동일owner의다음cycle성공,정상disable/설정변경/close·cancel/진행중native합류·일봉계약별성공표식보존. 실제운영실측은별도.

- 정본 판정: **재설계 / P2 / 대기**. 영향 F20.1/F23/F24. 의존: R3-061과 같은 collector 직렬. 변경 내용/해결 후 측정/관련 commit: **제품 변경 없음 / 미해결 / 없음**. 위 진단은 현재 조건의 재현이며 해결 회귀가 아니다.


### R3-061 — active 월물 수집 실패를 volume0으로 바꿔 롤 근거로 사용

1. **고유 임시 ID/대상:** R3-061, `external_market_collector.py:_collect_once:226~255`, `futures_roll.py:trailing_volume:37/evaluate_roll:91`의caller경계.
2. **경로:** active/nextfetch→실패계약을intraday_by_contract에넣지않음→`get(active,[])`→trailing_volume0→next거래량우위confirmation→active_contract교체/roll문서→외부bars/statusconsumer. F20(외부시장관측의roll reference).
3. **원인/코드 근거:** 없는자료와실제0거래량을구분하지않아active월물비교입력이없는데도next-only양수자료가dominance로평가된다. provider오류를status failed로기록해도roll-state는이미교체될수있다.
4. **실측/가설 구분:** actualcollect_once2회, 기존roll_confirmations=2,activefetch항상OSError,nextvalidrows100. 첫회active NQU26/count1/active_volume0,두번째active NQZ26로교체. `external-roll-missing-active.json`. 네트워크0/메모리metadata만사용. 실제시장월물선택오류·손익·발생률은입증하지않았다.
5. **권장/분류:** **재설계/P2**. 기존입력availability/비교의의미를보존해없음과실제0을구분하는범위로검토. 새roll임계값/전략조건/봉해상도를임의로만들지않는다.
6. **효과/위험:** 공급자일시실패를실제거래량열세로위장하지않음. 양쪽정상0·한쪽실제0·불완전기간·계약별daily성공·기존유효reference를보존해야한다. 원본bar삭제·null덮어쓰기가목표가아니다.
7. **의존:** R3-060재시도수명과같은파일이므로구현소유직렬조정. R3-024의주식전략미정규칙과다른지연선물roll관측경로다. 시간표현잔여는R3-023을중복등록하지않는다.
8. **우선/상태:** P2/대기,제품변경0/미해결/commit없음.
9. **완료/검증:** active실패/next실패/둘다실패/실제0/정상역전/일시회복/동일window/날짜교차/재시작persistedconfirmation을기존정상규칙과대조;각계약bar/상태/오류/roll선택동반확인. 기존임계값유지.

- 정본 판정: **재설계 / P2 / 대기**. 영향 F20.1. 의존: R3-060; 입력결측과 실제0 구분. 변경 내용/해결 후 측정/관련 commit: **제품 변경 없음 / 미해결 / 없음**. 위 진단은 현재 조건의 재현이며 해결 회귀가 아니다.


### R3-062 — condition 목록 진단 저장을 single WebSocket receive가 기다림

1. **고유 임시 ID/대상:** R3-062, `realtime_collector.py:_receive:789`, `market_events.py:handle_ws_message:357/_condition_list:530~573`.
2. **경로:** singleCollectorrecv→admittedmessage→awaitMarketEvents.handle_ws_message→CNSRLST→awaitto_thread(upsertcondition_search_status)→return뒤에다음recv/0Bparse. F04/F07/F10.
3. **원인/근거:** condition목록선택/등록packet후진단document저장까지같은messagehandler의await에포함된다. Python eventloop전체가block되는주장이아니라그수신owner가DBACK를기다리는동안다음message소비를하지못하는호출관계다.
4. **실측/가설:** **현재본문의인과는확정,지연실측/blocked-store통합fixture는미실행**. actualsocket상0B가쌓이는량/PG대기/장중재등록빈도는모른다. 앞선2개externalfixture와합쳐3개실행통과로세지않는다.
5. **권장/분류:** **재설계/P2(우선 계측)**. 등록/seq/revision admission과부가status저장의수명을구분할수있는지기존ownedbackground/drain경계에서검토한다. 새Manager/새수신기/독립pool을먼저만들지않는다.
6. **효과/위험:** protocol상태와무관한DB대기를quote수신에서줄일가능성. lateolderpolicy-status덮기/상류ACK순서·저장오류표시·close미저장누락을막아야한다. 부가진단이라고무조건fire-and-forget/예외은폐하지않는다.
7. **의존:** R3-007의REST송신/persistence분리와다른WSconditionbranch이며R3-035indexlock과도별도다. 동일Collector/MarketEvents파일수정과직렬통합. 먼저blocked-statusstore+fakeWS인과fixture확보.
8. **우선/상태:** P2(우선 계측)/대기,제품0·미재현실행분기·commit없음. 실제운영병목으로단정하지않음.
9. **완료:** actualCollector/fakeWS로CNSRLST statuswrite를막은상태에서다음0B소비시점전후비교,정상/실패/정책교체·clearACK/재연결/close에condition정합성·진단ACK소유보존. DB쿼리횟수/receive지연분리.

- 정본 판정: **재설계 / P2 / 대기**. 영향 F04/F07/F10.1. 의존: 미실측: blocked-store fixture 먼저; R3-007/034와 소유 조정. 변경 내용/해결 후 측정/관련 commit: **제품 변경 없음 / 미해결 / 없음**. 이 항목은 본문상 await 인과 확인이며 실행 재현·해결 회귀는 미실행이다. 담당 제안 P1(추가 계측 후)을 주 담당자가 P2(우선 계측)로 조정했다.


### 394개 모듈별 범위 원장

경로 접두어 `src/kiwoom_monitor/` 생략. 직전25/175/194 판정과 당시 범위는 상세 JSON의 `previous_depth_assessment`에 원형 보존했다. 아래는 **현재 own-body 코드감사 상태**다. 함수군은 수동 본문 추론의 요약이며 AST inventory는 탐색용으로만 남긴다. 각 행의 caller/state/consumer·실패·동시/종료·운영 미검증 상세는 같은 JSON에 있다. 기능 태그는 실제 연결 대응이며 운영 정상 판정이 아니다.

| 모듈 | 직전 코드 판정 | 이번 보완 또는 승계 근거 | 현재 코드 판정·F | 코드 잔여 / 실행 미검증 |
|---|---|---|---|---|
| `__init__.py` | 정밀 검토 완료 | Prior whole-module own-body evidence retained without replay: 본문 전체: version2.1.0 선언, import 부작용 없음. bootstrap/패키지 소비와 대조 | 정밀 검토 완료; F01,F24 | 코드 잔여0. 기존 모듈 코드완료 근거 승계. 실제 앱/외부환경 수락은 별도 미실행. |
| `__main__.py` | 정밀 검토 완료 | Prior whole-module own-body evidence retained without replay: 본문 전체: bootstrap.main import와 __name__ guard 호출; P01 실제 진입에 대응 | 정밀 검토 완료; F01,F24 | 코드 잔여0. 기존 모듈 코드완료 근거 승계. 실제 앱/외부환경 수락은 별도 미실행. |
| `application/__init__.py` | 부분 정밀(잔여 미검토) | prior entire five-line exports RankingService/MinuteChartService/StockFundamentalsService | 정밀 검토 완료; F02,F08,F10 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/account_identity.py` | 정적 분석만 수행 | protocol boundaries18-25; async verify->prepare/store/binding/mirror28-95; local alias immutable scope98-134 | 정밀 검토 완료; F16,F17,F18,F23 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/breakout_strategy.py` | 부분 정밀(잔여 미검토) | config/session policy/deadline+typed DTO validation1-288; prior evaluate_breakout_bar/evaluate_factor_entry_bar289-430; _decide/open/candidate/cooldown/dedup/session/rank/capital433-563; universe/hash/logical_document/time566-604; realtime policy/RAM windows/s…(상세 JSON) | 정밀 검토 완료; F11,F15,F17,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/context_candidates.py` | 부분 정밀(잔여 미검토) | typed session/evidence/carryover/response/hypothesis hash guards27-174; prior build/news/global evidence177-322; carryover calendar predecessor325-367; prior intraday/advance370-464; merge status/source union467-524; unique canonical documents/time/hash527-551 | 정밀 검토 완료; F13,F14,F15,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/daily_bar_coverage.py` | 정적 분석만 수행 | source_integer/normalize/fingerprint; DailySourceWindow all continuation/order/overlap/bounds/exhaustion/evidence; assess/choose prefix/date/scope validation | 정밀 검토 완료; F08,F10 | 코드 잔여0. Actual broker/GUI/production calendar/native contention unexecuted |
| `application/daily_high_service.py` | 정적 분석만 수행 | DailyBar/targets DTO19-49; from_daily_bars52-103; service ctor/load/NXT eligibility106-150; coverage targets152-170; stored/direct paged source172-211; stored conversion214-217; KRX/NXT merge221-244 | 정밀 검토 완료; F10 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/feedback_strategy_revision.py` | 부분 정밀(잔여 미검토) | DTO strategy/request/receipt validators+serialization30-175; protocol store boundaries178-192; prior build/spec/dispatch195-365; decoders/content identity/awaretime367-448 | 정밀 검토 완료; F15,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/forward_evaluation.py` | 정적 분석만 수행 | forward gates/report and feedback outcome/evidence/review/proposal DTO39-436; history group/episode/cost/research timing evidence439-648; review assessment/registered one-parameter proposals651-853; feedback->forward evidence856-893; all decoders/policy/select…(상세 JSON) | 정밀 검토 완료; F11,F15,F17,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/generic_strategy_evaluator.py` | 부분 정밀(잔여 미검토) | prior evaluate_strategy_pack/strategy_metrics/_mapped/_matches14-96 all bodies; added import/operator/header1-13 | 정밀 검토 완료; F18,F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/high_price_policy.py` | 정적 분석만 수행 | merge fundamentals with adjusted verified250/source fallback; all selected_high periods/todaymax/missing | 정밀 검토 완료; F10,F18 | 코드 잔여0. Actual broker/GUI/production calendar/native contention unexecuted |
| `application/historical_high_service.py` | 정적 분석만 수행 | evidence/target/cache DTO18-39; stored/cache/fresh load51-82; incremental84-130; whole market132-173; year/month/day refinement175-202; bounded pages204-224; date/month/adjustment/evidence/price227-253 | 정밀 검토 완료; F10 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/historical_learning_cases.py` | 부분 정밀(잔여 미검토) | prior build model_input/outcome separation1-174; immutable artifact IO177-236; publication cutoff/dedup evidence239-295; future observed outcome1m preferred5m fallback298-368 | 정밀 검토 완료; F19,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/historical_news_blind_validation.py` | 정적 분석만 수행 | allowlist/recursive target denial/build39-104; directory publish/load107-158; exact request/source/count/hash binding168-257 | 정밀 검토 완료; F19,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/historical_news_development_inputs.py` | 정적 분석만 수행 | source exact coverage/event role/model target separation33-136; staging write/hash checked load139-194; record/source binding204-273; dataset count/hash/role/disjoint event guards276-361 | 정밀 검토 완료; F19,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/historical_news_event_split.py` | 정적 분석만 수행 | reviewed relevant event grouping/chronological partition23-129; write/read/validate132-165; event publication/source validation168-240; exact plan fields/atomic events/counts/hash243-341 | 정밀 검토 완료; F19,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/historical_news_method_evaluation.py` | 정적 분석만 수행 | sample binding/opaque pair clustering/normalized theme exact sets64-196; immutable report IO199-226; source/method/metrics/hash/boundary validation229-297; label/ratio/F1/hash300-320 | 정밀 검토 완료; F19,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/historical_news_method_results.py` | 정적 분석만 수행 | exact coverage/method identity/results30-96; directory writer/loader99-150; prediction normalization/method contract157-199; full dataset/hash/count guards202-291 | 정밀 검토 완료; F19,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/historical_news_review_decisions.py` | 정적 분석만 수행 | CSV export/load/source binding53-109; editable field update temp replacement112-144; human decision freeze/build146-267; immutable directory write/hashchecked load270-338; source/sheet/review/type/hash helpers341-450 | 정밀 검토 완료; F19,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/historical_news_review_queue.py` | 부분 정밀(잔여 미검토) | prior article dedup/case link rule hint build1-184; immutable writer/hashchecked loader187-255; article identity/doc/hash encoding258-289 | 정밀 검토 완료; F19,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/historical_research_split.py` | 정적 분석만 수행 | case-date chronological plan24-99; immutable writer/read validation102-181; case date/time/hash helpers184-200 | 정밀 검토 완료; F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/investor_flow_service.py` | 정적 분석만 수행 | stored/SOR/KRX load17-57; request59-63; signed integer66-70; same-day filter73-80 | 정밀 검토 완료; F18,F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/journal_chart_layout.py` | 정적 분석만 수행 | price grid15-33; fills36-37; chart_close_information40-77; callout80-82; value formatting85-96; time step/ticks99-126; daily grouping129-148; average label151-158 | 정밀 검토 완료; F08,F18,F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/journal_enrichment.py` | 정적 분석만 수행 | scope/task/revision/link/projection DTO42-156; repository/source protocols159-196; register/can_start/start/finish/fail/recover199-330; fingerprint/id/revision/research link333-423; evidence timing426-446; page validation449-498; bounded sync501-541; event nor…(상세 JSON) | 정밀 검토 완료; F18,F22,F24 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/leader_breakout.py` | 부분 정밀(잔여 미검토) | prior explicit context/policy/document reference/observation/execution contract1-341; config hashes/missing/session345-430; kernel observed cross/continuity/confirm/pinned hardstop433-565; final minute authority/research rows568-606; shadow recomputation/posit…(상세 JSON) | 정밀 검토 완료; F12,F15,F17,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/market_data_coverage.py` | 정적 분석만 수행 | state/report16-54; evaluate57-118; missing intervals121-150; floor153-156; safe between159-163 | 정밀 검토 완료; F08,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/market_data_finalization.py` | 정적 분석만 수행 | all target/business-date/ready-cut/attempt/map/candidate/minute proof/outcome policies | 정밀 검토 완료; F08,F10,F24 | 코드 잔여0. Actual broker/GUI/production calendar/native contention unexecuted |
| `application/market_index_chart_service.py` | 정적 분석만 수행 | minute load19-39; daily load41-62; request64-69; records72-75; value78-80 | 정밀 검토 완료; F08,F18,F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/market_research_features.py` | 정적 분석만 수행 | member/frame/feature/price DTO34-208; second/0B candidate windows215-361; immutable minute inputs+1m/5m candidates364-537; context changes/candidate history540-813; market features/classification816-1006; strategy assessment/frame adapter1009-1080; turnover/hi…(상세 JSON) | 정밀 검토 완료; F11,F12,F13,F15,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/market_session_schedule.py` | 정적 분석만 수행 | verified delayed dates/enum/window/session policy27-170; session and mock order entry174-262; full-day/research allowed/profile/key265-410; KRX/NXT windows+eligibility413-498; realtime target/boundary/pause/TOP20 collection501-604 | 정밀 검토 완료; F08,F10,F11,F16,F17,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/market_theme_shadow.py` | 부분 정밀(잔여 미검토) | input DTO/version/resolution/thesis parse34-119; shared market/price cache+quality135-286; candidate relation/hash/scope289-316; market quality/change319-379; stock+batch isolation382-430; prior request/thesis validation433-488; missing/conflicting ref guard48…(상세 JSON) | 정밀 검토 완료; F12,F13,F15 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/minute_chart_service.py` | 정적 분석만 수행 | coverage authoritative/load_today/load completion21-71; recent/two days73-100; stored two days102-131; stored today133-158; stored normalize161-192; direct page194-204; paged completion206-228; request/records230-244; raw parse247-279; combine282-316; last two…(상세 JSON) | 정밀 검토 완료; F05,F08 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/minute_trade_value.py` | 정적 분석만 수행 | MinuteOhlcv/estimate20-37; aggregator state43-56; ingest58-156; gap mark/state/confirm/failure/empty/storage158-239; range/bucket/recent quality244-274; estimate correction276-295; source reset297-316; delta/volume318-353; sum/buckets355-381; seed383-452; day …(상세 JSON) | 정밀 검토 완료; F04,F05,F08,F09 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/mock_automation_admission.py` | 부분 정밀(잔여 미검토) | control/admission/lease DTO1-148; protocols151-199; deterministic run202-205; builder208-254; admit257-334; codecs337-394; final verification397-441 | 정밀 검토 완료; F17 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/mock_automation_candidate.py` | 부분 정밀(잔여 미검토) | policy/package/receipt DTO and identities28-188; build final lineage191-219; evaluate222-264; codecs267-315; publication size318-321; canonical family/execution/session/evidence324-376; unsafe field/path/type/hash/time379-430 | 정밀 검토 완료; F11,F15,F17,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/mock_automation_execution.py` | 부분 정밀(잔여 미검토) | DTO metrics/gate/dispatch/stop1-325; protocols328-404; deterministic intent407-411; safety context414-523; assess526-644; dispatch647-780; replacement bundle783-887; frozen policy890-906; replacement dispatch909-1045; risk validation1054-1090; stop/resume1093-…(상세 JSON) | 정밀 검토 완료; F16,F17,F18 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/mock_automation_recovery.py` | 정적 분석만 수행 | metrics/decision33-138; protocols141-171; assess174-298; record301-376; codecs379-423; scope/risk validation426-458; fingerprint461-499 | 정밀 검토 완료; F17 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/mock_automation_risk.py` | 부분 정밀(잔여 미검토) | stop/leader proof33-80; triggers83-125; protection workflow128-191; protection limits194-243; risk snapshot DTO246-348; account-scoped FIFO351-417; build snapshot420-534; codec537-568; exact fills/aggregate/run holdings/basis571-646 | 정밀 검토 완료; F14,F17,F18 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/mock_automation_session_clock.py` | 정적 분석만 수행 | session_clock_assessment and session_clock_exit_decision1-109 including policy/deadline/open position/price freshness and content id | 정밀 검토 완료; F17 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/mock_automation_specification.py` | 부분 정밀(잔여 미검토) | strategy refs/default criteria/build profile+stage+operating publication1-197; protocol207-218; publish_ready221-373: package/eligibility/final lineage/family/session/stages/shadow/binding/readiness/stored conflict plus ordered saves; mapping200-204 | 정밀 검토 완료; F11,F15,F17,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/morning_market_brief.py` | 정적 분석만 수행 | prompts/settings/schema and strict evidence/calendar/market/fact/event/theme/stock/hypothesis/research DTO14-390; schema compaction393-412; prompts415-454; hash425-427; aware457-461; validate_output464-686 (all ref/time/numeric/source/market/research branches)…(상세 JSON) | 정밀 검토 완료; F20,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/news_analysis.py` | 정적 분석만 수행 | assessment and complete lexical/pattern registries7-253; direction/direct-company/body scopes256-317; extractive summary320-431; price reaction434-475; assess relevance/category/direction478-553 | 정밀 검토 완료; F19 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/news_auto_analysis.py` | 정적 분석만 수행 | auto_candidate_identities10-34; unanalyzed_groups37-47; next_auto_groups50-69 complete body | 정밀 검토 완료; F19 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/news_grouping.py` | 정적 분석만 수행 | group31-73; cached features76-128; reaction131-137; URL dedup140-164; legacy same_event167-187; normalize/ngram/set190-210; anchor extraction/conflict213-240; legacy window243-250; timestamps/representative/source/stage/republication254-294 | 정밀 검토 완료; F19 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/news_rules.py` | 정적 분석만 수행 | supply regex/constants20-32; evidence/result DTO36-72; classifier75-180; group candidates183-197; input hash200-206; certainty/money/counterparty/event key209-268; span/item/identity271-297 | 정밀 검토 완료; F19 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/nxt_eligibility_service.py` | 정적 분석만 수행 | stored/direct strict Y/N is_enabled14-25 complete | 정밀 검토 완료; F10 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/order_lifecycle.py` | 부분 정밀(잔여 미검토) | protective cancel recovery31-71; native attempt seal/validation/projection80-213; preflight216-320; policy/evidence324-481; assessment484-516; management decision519-695; document parser698-731; OrderLifecycle construction/queue/submit/cancel739-803; reconcile…(상세 JSON) | 정밀 검토 완료; F16,F17,F18 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/personal_trade_rules.py` | 부분 정밀(잔여 미검토) | new lesson/structured DTO1-43 and role/target heuristics46-74; prior load markdown/docx rules/structured/applicable77-196 full bodies | 정밀 검토 완료; F18,F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/position_target.py` | 정적 분석만 수행 | dynamic EV/full-exit/policy/candidate contracts26-177; allocation180-384; document387-432; account/Runner/study DTO435-459; portfolio462-497; Runner state500-550; broker exposure553-566; reconciliation569-652; primitives655-668; sizing671-700; target request70…(상세 JSON) | 정밀 검토 완료; F14,F15,F17,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/program_trade_service.py` | 정적 분석만 수행 | load_day17-45; request47-50; signed integer53-57 | 정밀 검토 완료; F18,F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/pullback_reacceleration_strategy.py` | 부분 정밀(잔여 미검토) | config validations/to_dict45-113; prior factor evaluator adapter116-151 | 정밀 검토 완료; F11,F15,F17,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/ranking_execution.py` | 정적 분석만 수행 | outcome/state ctor24-40; flags/schedule42-61; timer/worker due63-79; response81-111; take deferred113-116 | 정밀 검토 완료; F02 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/ranking_schedule.py` | 정적 분석만 수행 | archive KST window; rank/signature/changes; partial response two retries/modal defer; every query nextcut+250ms/preparation | 정밀 검토 완료; F02 | 코드 잔여0. Actual broker/GUI/production calendar/native contention unexecuted |
| `application/ranking_service.py` | 정적 분석만 수행 | construct/config/server clock24-46; stored stale/partial retry48-95; direct bounded retry97-138; parse/upsert/log provenance139-199; snapshot freshness201-219; partial count222-239; retry delay242-248; parse clock251-264; numeric/current price267-288 | 정밀 검토 완료; F02 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/realtime_subscription.py` | 정적 분석만 수행 | all Coordinator state/reset/properties/plan and commit; closing/available/session/signature/start-stop-update | 정밀 검토 완료; F02,F04,F23 | 코드 잔여0. Actual broker/GUI/production calendar/native contention unexecuted |
| `application/research_evaluation.py` | 정적 분석만 수행 | historical context cutoff/validate/replay29-120; outcome DTO/source scientific identity124-371; unfilled/legacy/target exact event replay374-710; study case/native-policy replay720-942; cohort/conditional/prepare+decode publication945-1316; allocationEV/native…(상세 JSON) | 정밀 검토 완료; F14,F15,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/research_execution.py` | 부분 정밀(잔여 미검토) | full-exit scenario/policy+stress simulation41-208; cost/execution DTO exact decimal/period/portfolio/event212-399; engine initialization/legacy pending+intrabar risk402-583; target valuation/order target585-638; pending gap/session limit/unfilled action640-728…(상세 JSON) | 정밀 검토 완료; F14,F15,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/research_factors.py` | 부분 정밀(잔여 미검토) | factor DTOs/parameter validators/registry1-260; theme leadership wrapper263-293; prior+remaining rolling window296-371; pullback window/peak-trough374-464; rank residency warmup/gap467-543; market regime546-575; coverage/session/missing/hash578-637; minute the…(상세 JSON) | 정밀 검토 완료; F11,F15,F17,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/research_families.py` | 부분 정밀(잔여 미검토) | labels/immutable registry three config/evaluator/contracts; get/parse/config-family validation; shadow monitor identity | 정밀 검토 완료; F11,F15,F17,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/research_hypotheses.py` | 정적 분석만 수행 | immutable DevelopmentEvidenceSnapshot/ref/ID41-132; hypothesis exact schema/config normalization/single field/id136-283; generation/followup request guards286-383; deterministic single parameter generation386-480 | 정밀 검토 완료; F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/research_implementation.py` | 정적 분석만 수행 | legacy fallback/profile validation/hash path list16-48 | 정밀 검토 완료; F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/research_queue.py` | 정적 분석만 수행 | decision campaign/spec decode/count17-88; policy validation/serialization/backoff100-168; job identity/hypothesis experiment171-229; worker delay/card signature232-265 | 정밀 검토 완료; F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/research_replay.py` | 정적 분석만 수행 | candidate normalization/asof replay53-104; strict minute authority/session/revision selection107-188; UTC cutoff/revision/time parsers191-289; cursor offset/latest/bycode day/universe199-265 | 정밀 검토 완료; F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/research_resources.py` | 정적 분석만 수행 | budget validation/Windows RSS/Linux proc33-59; guard check/checkpoint/preflight62-101 | 정밀 검토 완료; F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/research_search.py` | 정적 분석만 수행 | ExperimentSpec validation/canonical ID31-186; trial/outcome/card DTO189-242; bounded deterministic baseline/grid/ablation/cost generation245-301; search resume/budget/cancel/record326-376; evidence/outcome constraints/card/hash304-421 | 정밀 검토 완료; F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/research_splits.py` | 정적 분석만 수행 | fold/evaluation chronological gap/access validation26-132; final batch exact fields/hash/window135-207; symbol hash partition/development partition210-293; interval purge/warmup/time303-336 | 정밀 검토 완료; F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/secondary_data_schedule.py` | 정적 분석만 수행 | all callback ordering/start/finalization/afterhours; candidate force/loaded/refreshed/NXT/catalog filters | 정밀 검토 완료; F02,F10,F23 | 코드 잔여0. Actual broker/GUI/production calendar/native contention unexecuted |
| `application/stock_fundamentals_service.py` | 정적 분석만 수행 | positive integer7-12; dstr stock multiplier18; stored/direct load25-49 | 정밀 검토 완료; F10 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/strategy_pack.py` | 부분 정밀(잔여 미검토) | result mode registry14-19 plus prior select candidate/manifest/default personal pack22-102 complete | 정밀 검토 완료; F18,F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/strategy_pack_extraction.py` | 부분 정밀(잔여 미검토) | metric/comparison registries12-24; prior rule/draft DTO and extract/merge/extend/file dispatch28-150; docx153-158; pdf161-182; paragraph/category/required/automatable185-215; metric mapping218-237; data burden240-247 | 정밀 검토 완료; F18,F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/strategy_review_context.py` | 부분 정밀(잔여 미검토) | new protocols/reference DTO/lesson mapping11-35; prior strategy_review_reference38-58 complete | 정밀 검토 완료; F18,F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/theme_leadership.py` | 부분 정밀(잔여 미검토) | all typed 0B/second/minute DTO conversion1-330; row/snapshot conversion333-412; leadership cutoff/quality/window/rank/state415-599; prior thesis builder/evaluator602-676; unavailable display preservation678-724; leader confirmation/trend/expansion/limit727-842…(상세 JSON) | 정밀 검토 완료; F12,F13,F15,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/theme_matching.py` | 정적 분석만 수행 | StockLookup duck shape/MatchedThemeRow/match_theme_rows; glued names DP pos+cost with editdistance1; partial coverage DP and unknown gaps | 정밀 검토 완료; F22 | 코드 잔여0. Actual broker/GUI/production calendar/native contention unexecuted |
| `application/theme_preview.py` | 정적 분석만 수행 | ThemeChange frozen row; all preview beforelookup/casefold-set comparison | 정밀 검토 완료; F22 | 코드 잔여0. Actual broker/GUI/production calendar/native contention unexecuted |
| `application/theme_ranking.py` | 정적 분석만 수행 | visible normalized frequency; aggregate totals; sortable string tie-key; top themes exclusions/limit | 정밀 검토 완료; F02,F04,F09,F18 | 코드 잔여0. Actual broker/GUI/production calendar/native contention unexecuted |
| `application/theme_suggestions.py` | 정적 분석만 수행 | all frozen input/profile evidence DTOs and key property | 정밀 검토 완료; F19,F22 | 코드 잔여0. Actual broker/GUI/production calendar/native contention unexecuted |
| `application/top20_trade_value_collector.py` | 정적 분석만 수행 | minute record/metadata/observation29-62; update66-71; state77-89; prepare/subscription91-109; advance111-140; partial/value/count142-162; roll164-185; cohort activate187-201; record/clear/gap/market203-235 | 정밀 검토 완료; F04,F09 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/trade_analysis_preparation_service.py` | 부분 정밀(잔여 미검토) | new repository/pack/snapshot/news protocols37-103; PreparedTradeAnalysis DTO106-118; ctor121-134; prior prepare136-276 all body incl source loads/revisions/links | 정밀 검토 완료; F18,F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/trade_chart.py` | 정적 분석만 수행 | DailyTradeChartService load13-56; target59-72; cache reuse75-87; aggregate90-119 | 정밀 검토 완료; F08,F18,F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/trade_cost_service.py` | 정적 분석만 수행 | all scope DTOs/estimated closed cost/scoped proportional actual allocation; verified query context/settlementrange; grouped page parser and record/date/code/number converters | 정밀 검토 완료; F17,F18,F22 | 코드 잔여0. Actual broker/GUI/production calendar/native contention unexecuted |
| `application/trade_episode_analysis_service.py` | 부분 정밀(잔여 미검토) | new analyzer/snapshot protocols13-30; prior analyze_trade_cycles33-72 full body | 정밀 검토 완료; F18,F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/trade_group_edit_service.py` | 정적 분석만 수행 | all merge/split/reset same stock/account policy; keys/scopes; UUID/result/error behavior | 정밀 검토 완료; F18,F22 | 코드 잔여0. Actual broker/GUI/production calendar/native contention unexecuted |
| `application/trade_history_query_service.py` | 정적 분석만 수행 | protocol/results/reconciliation DTO20-119; episode/fill selection122-166; scoped loads/filter169-253; backfill cutoff/select/state/incomplete/selection256-323; reconciliation326-412; identity/evidence parser415-490; totals/status493-526 | 정밀 검토 완료; F18,F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/trade_history_service.py` | 부분 정밀(잔여 미검토) | TradeFill/account scope/TradeHistoryBatch23-53; constructor/load_day/load_day_batch56-80; raw record normalization82-115 | 정밀 검토 완료; F18,F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/trade_journal_statistics.py` | 정적 분석만 수행 | all stat DTO ratios + day/weekISOyear/month aggregation/order | 정밀 검토 완료; F18,F22 | 코드 잔여0. Actual broker/GUI/production calendar/native contention unexecuted |
| `application/trade_journal_summary.py` | 부분 정밀(잔여 미검토) | new summary/episode/review DTO14-72; prior account-aware fill key/grouping/cycles/summary75-230; final result231-238 | 정밀 검토 완료; F18,F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/trade_review_analysis.py` | 부분 정밀(잔여 미검토) | new analysis DTO/display13-49 and buy/sell cycle counts52-78; prior analyze_trade_episode81-231 fullbody | 정밀 검토 완료; F18,F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/trade_setup_classification.py` | 정적 분석만 수행 | setup normalization/DTO/session contexts24-163; lecture limitations166-213; cycle composition216-267; single-cycle270-601: missing buys/bars, preentry price context, listing/close session, consolidation/W/triangle, limit-up/listing/closing/oversold/breakout/pu…(상세 JSON) | 정밀 검토 완료; F18,F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/trade_snapshot_context.py` | 부분 정밀(잔여 미검토) | prior complete1-87 snapshot protocol/select_entry_snapshot/matching entry/resolve_unverifiable_items and provenance checks | 정밀 검토 완료; F18,F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/trade_strategy_coordinator.py` | 부분 정밀(잔여 미검토) | new protocol/type/CycleTypeSelection22-44; prior classify_with_strategy_packs/resolve_cycle_type_selection/strategy_pack_for_result47-132 all bodies | 정밀 검토 완료; F18,F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `application/trade_strength.py` | 정적 분석만 수행 | all DTO unit-preserving floatcap reference/current shares/current marketcap fallback + strength zeroNone | 정밀 검토 완료; F02,F04,F06,F10 | 코드 잔여0. Actual broker/GUI/production calendar/native contention unexecuted |
| `bootstrap.py` | 부분 정밀(잔여 미검토) | main actual dispatch central/journal/news/research vs desktop; resources/taskbar; strict restore lease; Desktop database/config/local server/central clients/news migration/content sync initial daemon; API runtime factories all market services + credential prov…(상세 JSON) | 정밀 검토 완료; F01,F03,F10,F22,F23,F24 | 코드 잔여0. Actual GUI/bootstrap/credentials; NAS failure/Drive/child lifecycle |
| `central_server/__init__.py` | 정밀 검토 완료 | 기완료 승계: docstring/config re-export/__all__와 package caller 근거를 보존. 새 실행 검증 없음. | 정밀 검토 완료; F01,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/__main__.py` | 부분 정밀(잔여 미검토) | 이전 감사의 main/_log_retention_days/_ensure_standard_streams/_stop_with_parent/_process_is_alive 본문 근거를 승계한다. 진입 guard는 main만 실행한다. 환경설정→logging→uvicorn app startup, 부모 PID 감시 daemon이 server.should_exit에 반영하는 수명 경계이며 process 존재 검증은 OS 분기다. 기존 본문을 반복 조사하지 않았다. 코드 잔…(상세 JSON) | 정밀 검토 완료; F01,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/account_query.py` | 정적 분석만 수행 | - 함수군/역할: _Session과AccountQuerySessionManager 전체. endpointallowlist/bodyhash/binding·pageindex·cursor/TTL를RAMsession에동결,첫pageUUID·256session/32task상한,queryownedtask/shield로HTTPcancel뒤작업합류. _query는lock안에서brokerrequest를기다려동일ownerpage순서를보존하고선택limiter를공유한다. - 연결/실…(상세 JSON) | 정밀 검토 완료; F14,F18,F23 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/account_query_routes.py` | 정적 분석만 수행 | - 함수군/역할: scoped_context와contexts/router의모든Pydantic·endpoint·errorbranch. v2legacy와v3scoped계약구분,profilelabel은archived제외,expectedbinding과응답context를대조한다. - 연결/실패: appauthorize/selected_account→manager→typedcontext→PC. v3는invalid/busy/context/upstream를409/503/502…(상세 JSON) | 정밀 검토 완료; F18,F23 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/account_settings_routes.py` | 부분 정밀(잔여 미검토) | - 함수군/역할: 기존라우터전체body근거승계,Protocoltyping계약과app/router/native/owner연결을대조. storedrevision과appliedrevision/recovery_required가같다고가정하지않는다. - 연결/실패: 인증GET/PUT→selectedreal/mockowner→CAS/drain/native→readstatus. route는새stateowner/commit을소유안함. code residual 없음;실제계좌set…(상세 JSON) | 정밀 검토 완료; F14,F18,F23 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/ai_credentials.py` | 정적 분석만 수행 | - 함수군/역할: candidate/owner의init/hooks/prepare/drain/begin_commit/publish/resume/release전체. 유료검증호출없이UNVERIFIEDcandidate를준비,기존servicepause/drain후commit시구키를무효화,게시성공시revision/validation반영·candidatekey해제. - 연결/실패: app→CredentialRuntimeHooks→vaultcommit→AIservice. co…(상세 JSON) | 정밀 검토 완료; F19,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/ai_service.py` | 부분 정밀(잔여 미검토) | 기존 analyze/prepare_and_run/run을 승계하고 credential snapshot/accepted task drain·settings/job version·research_market/_research_market·body preparation/helper를 검토했다. lock은 수락/신원 고정, execution lock은 provider inference+usage 한계의 단일 owner다. plan 실패는 pause 후 명시 resume…(상세 JSON) | 정밀 검토 완료; F19,F20,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/app.py` | 부분 정밀(잔여 미검토) | - 함수군/역할: 기존lifespan/start/close/RAMprepare/apply6개지속근거승계+create_app의모든나머지조립/restore/errorbranch/AccountTarget/auth/health/capabilities/selectedaccount・mock/모든router등록·clockmiddleware·diagnosticinternaldispatch추가. real/mock/native/vault/news/AI/external/market…(상세 JSON) | 정밀 검토 완료; F01,F03,F04,F07,F09,F10,F11,F12,F13,F14,F16,F17,F18,F19,F20,F22,F23,F24,F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/autonomous_top20.py` | 부분 정밀(잔여 미검토) | 기존 live ranking/event/index/program/flush/close/ensure fundamentals를 승계하고 전체 잔여를 보완했다. entrant recovery는 documents+membership snapshot/revision을 합쳐 partial commit을 회복하며 한도 도달은 실패다. catalog/task callback·revision subscription loop·NXT cache→Hub universe→Collect…(상세 JSON) | 정밀 검토 완료; F03,F04,F09,F10,F13,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/candidate_monitor.py` | 부분 정밀(잔여 미검토) | 기존 run_once/consume/leader publication/document reference refresh를 승계하고 constructor/config/required inputs/quality/start/observe_trade/close·safe page consume·restore/bootstrap/checkpoint·shared delivery를 보완했다. 개별 worker와 shared delivery는 동시 작동을 막고 monitor_id …(상세 JSON) | 정밀 검토 완료; F11,F12,F17,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/central_schema.py` | 부분 정밀(잔여 미검토) | - 역할/함수군: baseline SQLite/PG table/index DDL, central_schema_migrations의1~21 조립 전체를 추가 읽음. v3만 기존 T88:88 snapshot/meta를 saved_at의 KST 분으로 INSERT/충돌 UPDATE 후구키 DELETE하는 repair이며 같은 migration transaction 안에 있다. v4초봉/v5theme/v6~11뉴스/v12observation/v13export/v14op…(상세 JSON) | 정밀 검토 완료; F01,F03,F07,F08,F09,F11,F16,F18,F19,F22,F23,F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/chatgpt_sessions.py` | 정적 분석만 수행 | - 함수군/역할: nas_chatgpt_auth의POSIX+secretDir지원경계,라우터전체status/models/select/disconnect/resume와change/owned_change. asynciochangeslock→servicecredentialpause→storelockedactiveaccount/generation변경또는models검증→pause_analysis/resume→finallycredentialresume. owned task는…(상세 JSON) | 정밀 검토 완료; F19,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/config.py` | 정적 분석만 수행 | - 함수군/역할: CentralServerSettings모든field/default/from_environment/parsed_*·configured/property/ai_key를추가. 환경값별필수token/port/env/numericclamp/budget분할·shadow구성·모의monitor/order의종속·identityHMAC32byte·trustedproxyIP를검사하고query/provider목록dedup상한을적용한다. - 연결/실패: __main__…(상세 JSON) | 정밀 검토 완료; F01,F03,F19,F23 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/content_routes.py` | 부분 정밀(잔여 미검토) | model/collection allowlist·journal v1/v2 identity+alias·read/upsert/replace·theme notification/history 전체를 검토했다. native store가 데이터 소유자이며 route는 namespace/account policy를 맡는다. theme metadata write는 drain_thread_call로 실제 thread settle 뒤 callback을 보장하되 commit 실패라…(상세 JSON) | 정밀 검토 완료; F10,F13,F19,F22,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/contracts.py` | 부분 정밀(잔여 미검토) | - 함수군/역할: 이전as_document/health승계,모든ServerCapabilitiesflag를app실제service조건/router와대조. DTO는機能지원광고이며health는serverclock/build와partialcoverage를반환한다. - 연결/실패: apphealth/capabilities→PCfeature선택;가동중개별worker정상증명이아님. code residual 없음;실제모든PCflagconsumer운영수락별도. 순수contract…(상세 JSON) | 정밀 검토 완료; F01,F03,F19,F23,F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/credential_runtime.py` | 부분 정밀(잔여 미검토) | - 함수군/역할: 이전34개body승계+constructor/register/provider/OperationError·DTO/Hookdefaults,routebody의boundedstream16KiB/duplicateJSONkey·allowedrequiredset/operationerrorJSON까지추가. 모든prepare/apply/recover/cancel/TTL/close와secureproxy/queryscrub의지속근거를native/helpers/pro…(상세 JSON) | 정밀 검토 완료; F19,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/credential_store.py` | 부분 정밀(잔여 미검토) | - 함수군/역할: 모든17개함수/compose의기존명시body근거승계. 이번에는caller_native_activation/providerhooks와commit/filemarker·fallback의소유를양방향대조하여이전runtime미검증을code잔여와분리했다. vaultfile/key/secret은열거나실행하지않음. - 연결/실패: composebootstrap→encryptedload/import→Runtimeprepare/save→activationmetad…(상세 JSON) | 정밀 검토 완료; F19,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database.py` | 부분 정밀(잔여 미검토) | - 역할/함수군: QueryStore의모든정의는ellipsis Protocol임을확인, 별도실행wrapper없음. SQLite/Postgres MRO는각기능mixin을합치며constructor의clock/history/delivery/capture설치, create_query_store의sqlite URLunquote/PGscheme분기를추가검토. initialize/_connect/_connection/close는기존S22/S23/G6의명시본문근거승계. - 연…(상세 JSON) | 정밀 검토 완료; F01,F03,F07,F08,F09,F11,F16,F18,F19,F22,F23,F24,F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_account_identity.py` | 정적 분석만 수행 | - 역할/함수군: scope/binding/alias value·row·identity helper 전부, SQLite/Postgres register_account_identity/append_account_binding/load_account_bindings/register_account_scope_alias/resolve_account_scope의 전체 본문. broker/environment와 UUID·ka00001 증명 검사→registry unique…(상세 JSON) | 정밀 검토 완료; F16,F17,F18,F23 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_account_settings.py` | 부분 정밀(잔여 미검토) | - 역할/함수군: 기존 scope/write fence/recovery/event/save helpers를 승계하고 _load_market_profile_settings의 strict key·revision·profile 형식, default legacy role 및 SQLite/PG 모든 wrapper를 추가 검토. recovery/event는 settings_revision·active profile과 verified binding으로 stale write를…(상세 JSON) | 정밀 검토 완료; F14,F18,F23 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_codec.py` | 정적 분석만 수행 | - 역할/함수군: bar_columns/bar_value_rows/bar_result_rows 및 five/second codecs는 고정 column 순서와 strict zip을 공유한다. 시간 native형은 isoformat 경계에서 변환한다. bounded_limit/offset, document_select_query는 owner/updated_after 조건과 안정 정렬을 파라미터로 만든다. document/dataset/observation 결과는 …(상세 JSON) | 정밀 검토 완료; F03,F07,F08,F09,F11,F16,F18,F19,F22,F23 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_credentials.py` | 부분 정밀(잔여 미검토) | - 역할/함수군: 이전 profile create/rename/archive/find/finalize/claim helpers 전체 근거 승계. 추가로 SQLiteCredentialStoreMixin/PostgresCredentialStoreMixin의 8개 public wrapper씩 읽어 모든 mutation이 같은 transaction·credential-activation lock에서 helper를 호출하고 reader가 별도 read context를 쓰…(상세 JSON) | 정밀 검토 완료; F19,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_datasets.py` | 부분 정밀(잔여 미검토) | - 역할/함수군: 양backend단건→batch/reader전체는S09/S17~19이전본문승계. _uses_async_dataset_commit와두kind집합을추가대조하여동일batch의모든kind가허용일때만asynccommit선택함을확인; batch문자budget은PGstatement조각경계다. - 연결/실패: ingestor/top20outbox/programwriter→snapshot+metadata+revision/cacheinvalid같은transacti…(상세 JSON) | 정밀 검토 완료; F03,F07,F09,F10,F13 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_documents.py` | 부분 정밀(잔여 미검토) | - 역할/함수군: 기존upsert/load4개승계. 양backend replace/singleload/themehistory와_theme_source/append/result·article_source/newsappend모두추가. replace는collection전체DELETE+INSERT,themefull일때만snapshotchain,news동일(stock,identity,collector,hash)는historyskip하고새BODYjob을같은cursor에넣는…(상세 JSON) | 정밀 검토 완료; F07,F10,F13,F19,F22,F23 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_execution.py` | 부분 정밀(잔여 미검토) | - 역할/함수군: _require_execution_ownership이전승계에leaderdecision재현·replacementchildfixedbinding/parentcancel·protectionworkflow/Risk/stopproof·cancelretry의모든claimhelper,retainclaim/value/immutablelineageUNION,SQLite/PG publicledger/control/lease/read전체를추가읽음. QUEUED직전…(상세 JSON) | 정밀 검토 완료; F16,F17,F18,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_external_market.py` | 부분 정밀(잔여 미검토) | 이전 감사에서 모든 SQLite/PG save/load와 values/result codec 본문을 확인한 근거를 승계한다. collector→canonical external bars UPSERT→bounded instrument/timeframe reader→external HTTP 소비다. backends는 연결 context commit/rollback이며 별도 polling/retry/task가 없다. 신규 코드 잔여 없음; 실제 PG/provider …(상세 JSON) | 정밀 검토 완료; F20,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_historical_news.py` | 정적 분석만 수행 | - 역할/함수군: 양backendexternalclaim/complete/importbatch와sourcepagebuilder/_complete_external_news_job전체. stage/scope/exclusion과attempt lease를확인하고BODYhash/size/status→revision/publication/RULEjob, RULEbodyhash/target/version→assessment/event→COMPLETED가같은transactio…(상세 JSON) | 정밀 검토 완료; F19 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_market_bars.py` | 부분 정밀(잔여 미검토) | - 역할/함수군: 기존overlay/load/save/finalize/replace 본체9개승계. 추가로secondtrade검증/정규화·limit+1완전창,minuteoperation/hash/authority/finalobservation,SQLite/PGlatestrevisionbatch lookup/insert,정렬advisoryscope/dayscope,metadatachangedkey/no-op분기,duplicatepartition/multirowups…(상세 JSON) | 정밀 검토 완료; F07,F08,F09,F11,F13 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_market_events.py` | 부분 정밀(잔여 미검토) | - 역할/함수군: cohortnative기존승계에VI/factappend/history양backend와모든tuple/row/table허용목록helper추가. VI event_key/fact_key UNIQUE의ONCONFLICTskip,count는실제rowcount합;cohortnullableNXT를현재row에서복원하며document보다native열을우선한다. - 연결/실패: MarketEventsownedqueue→samecursorhistory/current…(상세 JSON) | 정밀 검토 완료; F04,F07,F10 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_market_metadata.py` | 부분 정밀(잔여 미검토) | - 역할/함수군: range본체기존승계,_metadata_from_range_row/_metadata_time_key및양backend save/load추가. invalidtime은None으로제외,range는실제순간비교하며시간문자열정렬대체가아님. 저장upsert는bar/dataset의sharedhelper와같다. - 연결/실패: ingestor/coverageAPI→native metadata→CoverageObservation/consumer. 독립task없고w…(상세 JSON) | 정밀 검토 완료; F07,F08,F10,F11 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_news_job_writes.py` | 정적 분석만 수행 | - 역할/함수군: _insert_sqlite/_postgres_news_job의stablejobkey(stage,target,inputrevision/hash/version),conflictDO NOTHING과postcommit_notify callback실패logging을전부검토. - 연결/실패: source/doc/body/AIenqueuewriters→samecursorjob→serviceEvent. notification실패가성공commit을실패로되돌리지…(상세 JSON) | 정밀 검토 완료; F19 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_news_jobs.py` | 부분 정밀(잔여 미검토) | - 역할/함수군: claim/finish/retry이전승계에enqueue양backend및bodylatestlookup,requestbudget양backend와callbacksetter/rowdecode,PGphasewait/explain/read-onlyanalyze를추가. queue우선순위는preferredstage/선택stock/BODY/AI,stale120s복구,claimlimit<=4. budget은SQLiteBEGIN IMMEDIATE/PGexclusi…(상세 JSON) | 정밀 검토 완료; F19 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_news_revisions.py` | 정적 분석만 수행 | - 역할/함수군: 양backend모든publicbody/AI/event/history/singleexact/confirmedlookup와모든SQL/decoder helper를읽음. bodyinsert→article조회/target별RULEenqueue;AIresult+usage는같은commit;event는동일入力멱등후identity/eventkeychain·noveltyUPDATE/REPUBLICATION·membership을원자보존한다. PGeventtable…(상세 JSON) | 정밀 검토 완료; F19 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_news_sources.py` | 정적 분석만 수행 | - 역할/함수군: publiccursor/page/diagnostics/feed양backend및모든page/sourceitem/hash/target/reuse/RULEenqueue/cursor/summary/feedSQL·decodehelper전부. publisher명/domain만hash에서제외,동일source혹은GLOBALlatestcontenthash재사용,processing_excluded는원본observation보존·후속job만생략,confirmedta…(상세 JSON) | 정밀 검토 완료; F19 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_observation_readers.py` | 정적 분석만 수행 | - 역할/함수군: ObservationDeliveryState.advance의version/epoch/highregression/pendingowner집합과safe advancement,normalizer/pagehelper,양backend모든reader를추론. SQLite동일lock에서max+page; PG17/sequencecache1/increment1/no2PC/readcommitted/primary조건을검사하고sequenceowner lock소멸뒤saf…(상세 JSON) | 정밀 검토 완료; F07,F09,F11,F17,F21 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_observation_writes.py` | 정적 분석만 수행 | - 역할/함수군: 모든metadata UPSERT/append/insert/valuehelper추가. distinct WHERE는available_at만변한경우freshness를다시쓰지않고semantic열만비교한다. source logicalkey별latestpayloadhash동일이면historyskip,변경이면UUID·revision_ofchain,received_at은주입시계와SQLiteISO/PGnative형으로출력한다. - 연결/실패: bar/datas…(상세 JSON) | 정밀 검토 완료; F07,F09,F11,F17,F21 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_query_cache.py` | 정적 분석만 수행 | - 역할/함수군: StoredQuery/QueryCacheStore와SQLite/PG load/save전체,_cache_wall_time. 키·expiry로읽고성공fetch결과UPSERT뒤만료DELETE를같은transaction에둔다. PGencode/connect/upsert/cleanup/commit별계측은기존writercontext에연결되고계측실패는명시best-effort. - 연결/실패: CentralRestBroker RAMmiss→durablecach…(상세 JSON) | 정밀 검토 완료; F03,F10,F18,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_realtime_snapshot.py` | 부분 정밀(잔여 미검토) | - 역할/함수군: 기존양backendsave/load/latestcap6개body승계. _latest_market_cap_rows추가:JSONdocument→payload.market_cap_eok float,invaliddecode/형식skip,빈code·nonpositive제외,receivedepoch KST표현. RAM없는restore/API의durable관측adapter다. - 연결/실패: Collectorflush→latesttable→API/stock…(상세 JSON) | 정밀 검토 완료; F04,F07,F10 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_research_export.py` | 부분 정밀(잔여 미검토) | - 역할/함수군: create기존승계,양backendpage·input/manifest/value/SQL/resulthelper추가. awarestart<end<=24h·허용kind검사→고정revisionID집합/hash→ordinalpage(limit+1,nextcursor)이며후행revision추가가회원집합을바꾸지않음. UTCmanifest는외부canonical경계다. - 연결/실패: research_read_routes→storemanifest/member…(상세 JSON) | 정밀 검토 완료; F15,F21,F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_shadow_state.py` | 정적 분석만 수행 | - 역할/함수군: decision/candidatecanonical동일ID내용검사·INSERT멱등,모든state/evaluation/candidate양backend reader/writer와pagehelper추가. PGcheckpoint는featureflag에따라framescodec또는wholeJSON+clearframes;성공뒤writerdiagnostic을기록한다. cursor/highwatermark는expiry를수신consumer가해석하도록그대로반환. -…(상세 JSON) | 정밀 검토 완료; F11,F12,F17 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_storage_diagnostics.py` | 정적 분석만 수행 | - 역할/함수군: _storage_category의table/collection분류,_storage_breakdown_rows의shared document논리byte별물리크기비례배분과잔여마지막할당,양backend size/breakdown전체. SQLite COUNT각table+dbstat(없으면size0),PG live tuple추정/total relation size+document합계가서로다른추정값이다. - 연결/실패: resource/diagnosticA…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_top20_reconciliation.py` | 정적 분석만 수행 | - 역할/함수군: _series raw/reconciledbasis선택,_inputs cohortsegments→200codebatchbars/meta/NXT/catalog→code*venue coverage,_statistics 역사일cache/advisorylock/display분모,양backend3개public본문추가. 입력읽기는SQLiteBEGIN/PGrepeatableread readonly,displaystatistics는read명칭이나cache쓰기t…(상세 JSON) | 정밀 검토 완료; F09 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/database_top20_statistics.py` | 정적 분석만 수행 | - 역할/함수군: aggregate/group/day/cachekey/merge/marketvalues전체와양backendcachemiss경로추가. realtime_complete만집계·08~20시간/09~15:31일합·시장trde_prica/100억단위,weightedmerge는samplecount로합성한다. 완료일cache만영속화하고당일은재계산,PGdaylocks는정렬획득. - 연결/실패: marketAPI→durabletop20/indexchart→stat…(상세 JSON) | 정밀 검토 완료; F09 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/deployment_check.py` | 정적 분석만 수행 | result/check_http/check_realtime/run_check/get_json/CLI 전체를 검토했다. 명시 operator CLI→health unauthenticated·capabilities/content authenticated read→WebSocket ready 확인이며 resource/context는 with로 닫힌다. HTTP/JSON/timeout 예외는 실패 exit로 전달한다. realtime=true는 ready 수신만 뜻하며…(상세 JSON) | 정밀 검토 완료; F04,F23,F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_account_archive.py` | 정적 분석만 수행 | - 함수군: iter_index엄격chunk/key/hash/finiteupdated정렬,pack_index의rowbytebudgets와frontierseed전체. seed는recordednativeargs로유도한본문hash와initialindex교집합만복원하며없는원본을새로만들지않는다. - 연결/실패: accountcontextsnapshot→archiveindex→offlinebaselinepreflight. native문서helper는순수변환만빌리고DB/au…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_account_context.py` | 정적 분석만 수행 | - 함수군: version/table/collectioncontract·digest/value/fingerprint·recordedcontextread·snapshotread·validate·seed/sealedcontract/_apply_account_context전체. readonlyrepeatablesnapshot의row/byte/10s예산과fingerprint대체·leasealias·JSONsizesafe CASE·sequence정의/nextvalue·l…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_account_input.py` | 정적 분석만 수행 | - 함수군: allowedMETHODS/errors,source_execution_ownercontext,AccountInputProjectionproject/lease/HMACalias,resolve/validate/restore전체. 원ownertoken제거하고trace별HMACalias+runprefix참거짓만기록,restore는testauthority로만재구성·서로상충prefixchain거절. - 연결/실패: storecapture→accountproje…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_collector_replay.py` | 정적 분석만 수행 | 전체 고정 360초 입력 생성·중복/late tick·ReplayClock·측정 store counter·run_input_replay/cleanup를 검토했다. 전용 DB의 빈 scope 확인 후 native Collector에만 입력하고 no_token은 외부 호출을 금지한다. measurement_done과 uncancellable drain 이후 값/metadata/최신·window를 검증하고 scoped DELETE+COUNT로 정리한다. 실제 DB/r…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_control_routes.py` | 정적 분석만 수행 | - 함수군: capabilityDTO/설명·controlupdate·trace start/status/stop/manifest/chunk모두,_trace_start_phase. expectedsession/revision/masterTTL/busystate검사와conditionalcleanup,blockingcontrol/capture작업to_thread,historywrite실패는history_error_type으로응답한다. - 연결/실패: appauthori…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_delivery_record.py` | 정적 분석만 수행 | - 함수군: _size/Context불변/DeliveryIdentity/DeliveryStageMapping,additional_charge/retain/release전체. 공유subscriber/sourcecontext를refs0→1때만charge하고마지막release에자식까지반환,preview는상태를안바꾼다. - 연결/실패: hubdelivery→traceadmissionlock→deferredstage→RAMwriter→release. underflow명시…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_metrics.py` | 정적 분석만 수행 | - 함수군: capturelease refresh/token/clear/record DB·bar·writer/모든summary·percentile·rawcopy·commitdiagnostic/flush그룹전체. boundeddeque+dropcounts와capturegeneration으로lateDBcall의새세션혼입을막는다. phase/waitsample은표본횟수와duration구분,successfulwriter만count하며failed/zeroflush분모추정…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_read_routes.py` | 정적 분석만 수행 | - 함수군: resources/gather·workloadsnapshot·bar/dbcall/writers/claimplan/read-onlyanalyze모든endpoint와Protocol. workloadconfigured/effective를구분하고external은operational+running으로실제owner상태까지계산,일반기능은지원여부이지실행수락아님. - 연결/실패: authenticatedAPI→store/metrics/control/service→P…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_recorded_execution.py` | 부분 정밀(잔여 미검토) | 기존 run_owned_recorded_experiment 범위를 승계하고 _prepare/account bindings/result digest/source key/collector inputs/RecordedCollectorStore 및 _execute_recorded_operations 전체를 보완했다. v2/v3/v4 clock·actor·native method allowlist·signature와 prefix를 쓰기 전에 검증한다. actor 내부 순…(상세 JSON) | 정밀 검토 완료; F24,F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_replay.py` | 정적 분석만 수행 | profile compile·합성 seed·전용 DB resolver·ReplayStore 및 run_replay 전체를 검토했다. after-hours synthetic은 원본 동등성이 아니며 source fidelity false를 유지한다. native query recipe/호출 수 상한, 전용 DB/scope empty preflight, lane 실행·future 오류·measurement handshake·finally 검증/삭제 경로를 확인했다. …(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_replay_baseline.py` | 정적 분석만 수행 | - 함수군: URL/role/databaseidentity/config/hash/marker,OwnedConnectionclose,_ReplayStoreclock/nativefactory,ReplayDatabaseLease모든entry/retire/schema/sequence/tablehash/seal/restore/status/store/runtimebind/connect/provision본문추가. dedicatedDB명/role/no-superuser/noo…(상세 JSON) | 정밀 검토 완료; F24,F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_replay_comparison.py` | 정적 분석만 수행 | - 함수군: _encoded/_multiset_digest/_project_row/collect전체. 네명시table의storedcontent와generatedtimefield를따로hash,revisionUUID를validatedacceptedsequence/parentscope로투영하며identitypass도budget에포함한다. - 연결/실패: drainedofflineleasecursor→comparisonreport. timing/functional_eq…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_replay_contract.py` | 부분 정밀(잔여 미검토) | - 함수군: 이전capture계약4scope승계에모든taggedfreeze/thaw/budget/enumDTOallowlist/secretfieldreject·collectorvalidate·operationclassification/accountclock·owner/actorsequence/capturewrapper·recordedwindowseal/compileplan/queuefrontier를추가검토. registry는capture가능성이지execution…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_replay_database_cli.py` | 정적 분석만 수행 | parser와 provision/seal/restore/run 명령 전체, env-only URL/token, source hash/selection/origin/capture policy, v3 account-clock capsule, temporary observer env restore를 검토했다. preflight는 작업 실행 없이 검증하며 출력은 SQL bind/DB 비밀을 노출하지 않는다. 관리자가 명시 호출하는 전용 replay CLI이며 live …(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_replay_runtime.py` | 부분 정밀(잔여 미검토) | - 함수군: 이전ownedtask/thread2helper승계+payloadcredit/Scope의모든admit/activate/create/to_thread/shutdown/drain/require/status·close_resources추가. thread는실제invoke finally에만종료표시해취소future로drain오판안함. timeoutquarantine이후명시cleanup만허용하고errorreceipt유지한다. - 연결/실패: replayrunner…(상세 JSON) | 정밀 검토 완료; F24,F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_replay_sampling.py` | 정적 분석만 수행 | - 함수군: replay_process_usage/ActivitySampler전체/correlate. managementconnection을독점borrow하여readonly500ms/65rows/4096samples로backendidentity와wait표본을읽고threadnon-daemon종료까지join한다. commit인과는samepid+backendstart+전체queryinterval이commitwindow안에있을때만연결한다. - 연결/실패: offline…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_rest_input.py` | 정적 분석만 수행 | request/catalog observer·group/link/effect scope·read_request_tape·RequestTapeClient/TaskTapeBindings 전체를 검토했다. native broker 정책은 그대로 두고 market allowlist 입력만 수집한다. request signature/ordinal/parent/shared ownership, transport/effect pair와 cache origin을 prefligh…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_run_routes.py` | 정적 분석만 수행 | - 함수군: requestDTO/snapshot/runstart/status/cancel/reportlist/rawsummary/history전체. PG없는진단501,section/시간/limit제약,summary만rawsamples제거하고동일report원본은owner가다시읽도록한다. - 연결/실패: app→DiagnosticRuns/sampling→report→PC. native단계오류503/409/400 구분,runowner단일수명에위임하며route새task…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_runs.py` | 부분 정밀(잔여 미검토) | 기존 start/close를 승계하고 init/recover/_run 전체/report quota·manifest·status/cancel/history를 검토했다. RLock/run lock이 current 소유자를 정하고 replay worker ready/start/drain 후 call_id/domain coverage 대조를 한다. 실패·보고서 생략·cleanup 누락은 incomplete, 기존 producer 재기동은 aborted로 기록한다. bo…(상세 JSON) | 정밀 검토 완료; F24,F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_sampling.py` | 정적 분석만 수행 | DB-call capture 경계·host/device delta·COMMIT sample correlation·optional PG statistics·read_postgres_snapshot/read_host_snapshot 전체를 검토했다. session/process/control revision·truncation·counter reset을 구분하고 같은 backend COMMIT 안의 표본만 연결한다. SQL은 read-only/2초 timeout이며…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_top20_display_input.py` | 정적 분석만 수행 | - 함수군: _validate/_encoded/environment_scope/value,DisplayCapture/Replay와prepare_environment/result_digest전체. native가실제로소비한today/computed_at/cachepause/saved_at순서만기록,ordinal/hash/startendseq·resultprofile검증,재생은누락/과잉소비거절한다. - 연결/실패: storecapture→displaynativehel…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_top20_execution.py` | 정적 분석만 수행 | source input prepare·store-clock binding·native session 조립·cold collector inventory·stop driver·execute session 전체를 검토했다. 실제 ingestor callback/Top20/Hub/Collector를 연결하고 외부 token은 금지한다. 새 subscription 승인으로 source 입력을 필터하고 peer operation mask를 검증한다. finally subs…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_top20_experiment.py` | 정적 분석만 수행 | implementation hash·session plan·execute owned fixture 전체를 검토했다. TOP20/collector sink frontier를 닫고 peer owner/selection과 manifest clock을 native 쓰기 전에 확인한다. 기존 lease restore→cold seed→runtime bind→execute→drained table digest→finally DB/outbox restore다. file ch…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_top20_flow_input.py` | 정적 분석만 수행 | capture decorator/marker/request/broker link/scope·candidate_flow_cycle·execute/owned experiment 전체를 검토했다. 첫 진입 marker/read→최대 두 request→ingest confirmed→marker write의 순서를 receipt와 baseline dependency로 검증한다. offline broker는 actual MarketDataIngestor를 호출하고 nati…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_top20_input.py` | 정적 분석만 수행 | queue/dequeue/drop/consume fence·captured ranking cycle/request·compile delivery coverage와 offline broker를 모두 검토했다. 실제 소비 시 ContextVar cause를 전파하고 full sequence prefix/parent fragment/parser ordinal/subscriber order/consume end를 검증한다. observer 실패는 rejected inp…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_top20_lifecycle_input.py` | 정적 분석만 수행 | v1 observer/record_*·read_realtime_tape·delivery_source·compile_lifecycle_descendants·SubscriptionTape 전체를 검토했다. topology 전이와 ACK 완결 뒤에만 READY가 유효하다. selected cause→native operation start/end ownership closure를 확인하고 shared/ownerless 경로는 preflight 실패다. match는 실…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_top20_outbox.py` | 정적 분석만 수행 | JSON/schema/bounded data 검증과 owned directory seed/restore 전체를 검토했다. UUID child/.owner/inode/hardlink/symlink 경계 및 drained runtime 확인 후 원래 atomic outbox writer로 복원하고 hash 확인한다. DB와 파일의 독립 commit을 cleanup 결과에 구분한다. 실제 파일 장애 미실행; 코드 잔여 없음. | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_top20_seed.py` | 정적 분석만 수행 | cold RAM inventory·clock·baseline identity·seal/preflight 전체를 검토했다. 실제 Top20/Broker/Hub/ProgramWriter/Collector의 상태 목록을 검사하며 historical output/READY를 주입하지 않는다. fixture clock은 aware origin과 monotonic arm-once이며 manifest/hash 일치만 execution authorization으로 승격하지 않…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_top20_subscription_v2.py` | 정적 분석만 수행 | capture observer들과 read_v2 전체를 검토했다. command start/sent/ACK/batch end/superseded, cold generation, retained approval topology, upstream_ready 한 번 사용, control event 현재 approval, gap과 market_operation을 sequence/epoch/byte bound로 검증한다. 실패 observer는 rejection; inc…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_top20_transport.py` | 정적 분석만 수행 | OfflineSocket/Top20SubscriptionTransport 전체를 검토했다. 새 native desired registration을 SubscriptionTape에 대응하고 REG/REMOVE 및 기록 ACK/approval projection만 적용한다. gap은 승인 상태를 지우고 registered_message는 현재 구독·approval을 만족한 입력만 actual collector로 보낸다. LOGIN/token/network API는 …(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_trace.py` | 부분 정밀(잔여 미검토) | 기존 stop/recover_interrupted/recorded_events를 승계하고 나머지 전체 함수군을 검토했다. start의 TTL/generation/quota/symlink/account capsule 선검증 → run lock/RAM credit → nonblocking payload freeze → worker pack/bundle fsync/rename → chunk fsync/rename 뒤 RAM ACK → manifest 경로다. oper…(상세 JSON) | 정밀 검토 완료; F24,F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_trace_payload.py` | 정적 분석만 수행 | - 함수군: BlockPayloadReference.load/block_reference전체. checksummed manifest descriptor를privateblockreader로검증뒤joinJSON,잘못된encoding/JSONValueError. 참조생성시한번load하여검증한다. - 연결/수명: tracewindowreader→immutablepayloadreference→offlineoperationdecoder. producerpayload가아닌저…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_trace_ram.py` | 정적 분석만 수행 | - 함수군: encode_payload_blocks/pack_records,Segment/PackedRow/BlockSegment/BlockRow모든method. JSONiterencode를byteboundblock으로나누고순증seq·header/frame길이검증,issued와committedcursor분리,rewind는미ACKprefix만되돌린다. payload없는metadatacharge와largeblockcharge를별도계산한다. - 연결/실패: trace…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_workloads.py` | 정적 분석만 수행 | - 함수군: instance/controlfile/evaluate/status/pause/capture/tracehelpers·_set/_set_capture/_set_trace/_set_tool/_history와OSfilelock/runlock/write전체. parentinstance/session/revision+wall/monotonicTTL을한immutablefilegeneration으로해석,childTTL은parent안에둔다. atomictemp/fs…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/diagnostic_writer_registry.py` | 정적 분석만 수행 | - 함수군: WRITERS전항목의metric_kind/tables/durability/commonobserved여부/개별scope설명과writer_registry의dictcopy본문을nativeDBWriterContext호출과대조. staticregistry는관측되는목록이며rawwriter전체측정주장이아님. - 연결/실패: diagnosticwritersAPI/tracecatalog→registry→PC/report분모. runtimeI/O/상태없음,동적kind…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/execution_runtime.py` | 부분 정밀(잔여 미검토) | - 함수군/역할: 기존automationguard/submit/manualsubmit/load7개승계에Runtimeconstructor/state/observationrevision/lease/startstopheartbeat/submit/reconcile/cancel/cancelautomation·gatewaycommand/drain/cancel/error/intentid전체추가. lock순서operation→state/observation이며callback은…(상세 JSON) | 정밀 검토 완료; F16,F17,F18,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/external_market_collector.py` | 부분 정밀(잔여 미검토) | 이전 감사의 전체 함수 본문과 실제 fake upstream/memory store 두 진단을 승계한다. settings update/collection 작업 identity·poll owner와 status/roll state→bar writer→API 소비·close drain 경계까지 확인된 범위다. R3-060 metadata 저장 오류의 poll owner 종료와 R3-061 active 실패의 잘못된0 volume 비교는 미해결로 유지한다. 코드 잔여…(상세 JSON) | 정밀 검토 완료; F20,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/futures_roll.py` | 부분 정밀(잔여 미검토) | 이전 감사 전체 순수 함수 근거를 승계한다. 월물 parser/다음 quarterly·session/trailing volume/latest price/previous close·confirmation 평가가 external collector의 rolling state 소비자다. 함수 자체는 입력 수치와 pure result만 처리하며 결측 active를0으로 만드는 문제는 caller R3-061이다. 코드 잔여 없음; 실제 제공자/계약 검증 미실행. | 정밀 검토 완료; F20,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/historical_news_archive.py` | 정적 분석만 수행 | Reader 전체를 검토했다. 앱 시작 시 sealed manifest/projection/schema와 pinned file identity를 확인하고 요청별 mode=ro/query_only SQLite를 열고 닫는다. v1/v2 signed cursor는 dataset/filter/order tuple에 묶이며 exact article/body/target/membership/context를 조회한다. 잘못된 cursor/ID mismatch/file 교체…(상세 JSON) | 정밀 검토 완료; F19,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/historical_news_archive_routes.py` | 정적 분석만 수행 | factory와 모든 nested endpoint/helper를 검토했다. 앱이 소유한 pinned reader를 authorize+pause 뒤 to_thread로 호출하고 v1/v2 schema gate, dataset conflict, cursor400, missing404, unavailable503을 구분한다. wrapper는 HTTP 정책을 보유하므로 유지하며 native store를 복제하지 않는다. 코드 잔여 없음; ASGI 실행 미검증. | 정밀 검토 완료; F19,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/historical_news_processing_routes.py` | 정적 분석만 수행 | request schema/claim/import/complete 전체를 검토했다. 인증→stage/scope/excluded 제한→native claim→exact article/body 전달, import는 aware publication의 KST target_date와 source/batch 검증 뒤 native atomic writer로 간다. completion은 attempt token을 native store가 검증한다. malformed/missi…(상세 JSON) | 정밀 검토 완료; F19,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/market_dataset_read_routes.py` | 정적 분석만 수행 | 모든 query를 검토했다. latest-membership opt-in/limit1만 live RAM을 우선하며 나머지는 native snapshot/statistics/series를 to_thread로 읽는다. 날짜/기간/basis 검증을 route가 소유하고 Top20 수명을 소유하지 않는다. 코드 잔여 없음; 실제 API 미실행. | 정밀 검토 완료; F03,F09 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/market_events.py` | 부분 정밀(잔여 미검토) | 기존 start/condition FSM/metadata/cohort/fact writer/close를 승계하고 select_condition/상태진단·session boundaries/event loop·trade fact admission·VI backfill/live dedup·background/helper 잔여 전체를 보완했다. condition→cohort RAM/store revision→subscription, 0B upper-limit은 pric…(상세 JSON) | 정밀 검토 완료; F04,F07,F10,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/market_ingest.py` | 부분 정밀(잔여 미검토) | 기존 ingest/minute/SOR 비교를 승계하고 daily/ranking/flow/program/high/fundamental/NXT 및 모든 date/number/parser/completeness helper를 검토했다. broker persistence owner→ingestor→native transaction→daily invalidation callback→Top20 ready version으로 연결한다. uncertain daily write도…(상세 JSON) | 정밀 검토 완료; F03,F07,F08,F09,F10 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/market_news_sources.py` | 부분 정밀(잔여 미검토) | 기존 close/run_once/collect를 승계하고 생성/start/settings/catalog/loop/article 변환을 보완했다. FLASH/WORLD 독립 날짜 cursor는 app-owned collector가 관리하고 pause/error는 loop에서 격리한다. catalog는 bounded current documents이며 시장 기사와 종목 query filter의 역할을 혼용하지 않는다. 코드 잔여 없음; 실제 endpoint 미검증. | 정밀 검토 완료; F19,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/market_observations.py` | 정적 분석만 수행 | 모든 observation/revision payload/date/subject helper를 검토했다. ingestor/Top20/Collector→explicit effective/available/venue/unit/value-kind/origin/completeness→native canonical+revision reader로 연결한다. minute payload의 operation_id/session version은 중복/권한 판단 소비자 계약이며 q…(상세 JSON) | 정밀 검토 완료; F07,F08,F09,F11,F13 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/market_query_routes.py` | 부분 정밀(잔여 미검토) | 기존 stored/archive response body를 승계하고 factory 전체를 보완했다. account recovery TR은 scoped API로 배제, 첫 페이지 archive-first 다음 broker fallback, credential planned reconnect는503이다. 입력 continuation/response shape와 current app-selected collector를 확인했다. 코드 잔여 없음; 실제 broker/n…(상세 JSON) | 정밀 검토 완료; F03,F08,F10 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/market_read_routes.py` | 부분 정밀(잔여 미검토) | 기존 minute/recent/comparison/external read 근거를 승계하고 latest cap/event/cohort/current condition·daily coverage·generic coverage 및 combined/summary/helper 전체를 검토했다. native metadata와 explicit finalized day 증거를 구분하고 available_by/half-open date를 보존한다. COMBINED는 SOR 우…(상세 JSON) | 정밀 검토 완료; F08,F09,F10,F20 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/market_theme_shadow_inputs.py` | 부분 정밀(잔여 미검토) | 기존 RAM theme adapter를 승계하고 선택/모델 publication v1/v2 decode·position study goal binding·freeze bundle·allocation inputs·persisted thesis/source-check/read-input·RAM cut/index/price-trade/live membership 전체를 검토했다. bounded publication에서 paired outcomes/full-exit c…(상세 JSON) | 정밀 검토 완료; F13,F14,F15,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/market_theme_shadow_monitor.py` | 부분 정밀(잔여 미검토) | 기존 minute key/read/dispatch/close 경계를 승계하고 selection/bindings/monitor latest pending/coalesced shared calculation/runtime sources·recording·position/allocation·snapshot/status/close 전체 잔여를 검토했다. validation은 명시 선택/같은 account/venue를 유지하고 monitor iteration/lifecy…(상세 JSON) | 정밀 검토 완료; F13,F14,F15,F24,F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/minute_bars.py` | 부분 정밀(잔여 미검토) | 기존 add/drain/pending/gap/source reset을 승계하고 dataclass/as_record·closed_observation_snapshot·increment/dedup/prune·RAM snapshot/retention을 보완했다. Collector 한 loop의 canonical accumulators가 owner, dirty와 RAM completed view는 분리돼 read가 drain하지 않는다. source approval/d…(상세 JSON) | 정밀 검토 완료; F07,F08,F13 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/mock_account_monitor.py` | 부분 정밀(잔여 미검토) | 기존 realtime handler/recover_once/publish_risk를 승계하고 공통 socket/real subclass·monitor constructor/listener/position cut/start/close/owned/queue/heartbeat/reconcile 전체 잔여를 검토했다. real 계좌는 verified resolver+00/04 filter와 LOGIN/REG ACK 이후만 전달한다. account monitor는 ser…(상세 JSON) | 정밀 검토 완료; F14,F17,F18,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/mock_automation_routes.py` | 부분 정밀(잔여 미검토) | strict request model·status/control/error adapter·stream bounded native publication 전체를 검토했다. app-owned supervisor에 expected revision/credential/account/spec을 그대로 전달하고 raw JSON 상한·구조 오류를 HTTP로 변환한다. route가 주문 실행을 직접 소유하지 않는다. 코드 잔여 없음; 계좌/주문/HTTP 실행하지 않았다. | 정밀 검토 완료; F14,F17,F18,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/mock_automation_runner.py` | 부분 정밀(잔여 미검토) | 기존 strategy run_once/leader/dispatch/checkpoint/restore/fill-core/native-input build를 승계하고 나머지 전체 그룹을 보완했다. constructor의 package/family/session/cost parsing과 listener rollback; position state의 nonblocking borrow/revision fencing; native Risk→RAM protection cue…(상세 JSON) | 정밀 검토 완료; F11,F12,F14,F15,F17,F18,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/mock_automation_supervisor.py` | 부분 정밀(잔여 미검토) | 기존 activate/stop/resume/restore/close를 승계하고 constructor/price·trade forwarding/position enrichment/status/native population publication/close implementation을 보완했다. 계좌 lock 안 current bundle/control/run identity를 재대조하고 이미 등록된 Runner에만 자료를 전달한다. position cut은 원본 …(상세 JSON) | 정밀 검토 완료; F14,F17,F18,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/mock_order_routes.py` | 정적 분석만 수행 | - 함수군/역할: 모든legacy/scopedDTO/submit/get/cancel/events·mock_order_document/scoped_order_record를추가. selected_mock는기존bindinggate소비,scopedrecord는환경/account/run3중확인,quantity/price는v2strict,계약상v1coercion보존. - 연결/실패: app인증→선택bundle/gateway→Runtime→native→record/event…(상세 JSON) | 정밀 검토 완료; F16,F17,F18,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/mock_publication_routes.py` | 부분 정밀(잔여 미검토) | 기존 publish candidate/spec evidence 검증을 승계하고 current binding/list/find-shadow/list specs 전체를 검토했다. mock verified binding을 native account reader로 대조하고 shadow pagination은 100k 상한, repository profile/stage로 readiness를 표시한다. 계층은 HTTP evidence policy와 기존 repository …(상세 JSON) | 정밀 검토 완료; F11,F12,F15,F17 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/mock_runtime.py` | 부분 정밀(잔여 미검토) | - 함수군/역할: _update_settings/switch_execution_mode기존승계,MockContext검증·Owner의constructor/bootstrap/prepare-disable/account/drain/publish/resume/release/close와Bundle전체추가. account/profilepending·계좌별lock·probe/bootlimiter가credential정체성과준비수명을소유한다. bundle는account/run/c…(상세 JSON) | 정밀 검토 완료; F16,F17,F18,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/morning_market_brief.py` | 정적 분석만 수행 | 전체 schedule/settings/request/generate/latest/router를 검토했다. lock+revision으로 설정 저장 후 RAM publish, daily scheduled durable claim이 inference보다 앞서며 shield join이 취소 후 claim owner를 유지한다. immutable run 입력/prompt/output hash, cutoff/TTL/context, failed raw recovery와 av…(상세 JSON) | 정밀 검토 완료; F19,F20,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/news_credentials.py` | 정적 분석만 수행 | - 함수군/역할: Naver/Dartowner와candidate전체. Naver최소search는기존budgetclaim사용,Dartvalidateerror는stablecode변환. 준비disabled는새client없음;pause/drain→commitinvalidate→replace/revision→resume/release가AI와같은commit의미를쓴다. - 연결/실패: app→Runtimehooks→Newsservice. provider별독립validatio…(상세 JSON) | 정밀 검토 완료; F19,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/news_jobs.py` | 부분 정밀(잔여 미검토) | 기존 claim/run_once/body/AI/rule/start/close를 승계하고 constructor/wake/metrics/loop를 검토했다. commit 후 callback은 loop.call_soon_threadsafe로 모든 wake event를 세우며 idle exponential delay 상한과 busy/failure/pause delay를 분리한다. worker끼리는 store SKIP LOCKED claim이 owner이고 stage m…(상세 JSON) | 정밀 검토 완료; F19,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/news_read_routes.py` | 정적 분석만 수행 | Protocol과 세 endpoint 전체를 검토했다. history kind/limit/as_of/before_revision_id를 native reader에 전달하고 current body만 assessment를 추가한다. source diagnostics/market feed는 bounded read로 분리하며 route factory가 auth/HTTP 계약을 맡는다. 코드 잔여 없음; 실제 PG/HTTP 미검증. | 정밀 검토 완료; F19,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/news_service.py` | 부분 정밀(잔여 미검토) | 기존 close/refresh_once/auto_analyze/collect 정밀 근거를 승계하고 생성·credential pause/replace/resume·settings·start·search/stored_page·cache merge/직렬화 전체를 보완했다. 앱이 단일 service/job runner/query/market collector를 소유하며 search 동시 요청은 code/name별 shield task를 공유한다. 저장 읽기는 bound…(상세 JSON) | 정밀 검토 완료; F19,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/news_service_routes.py` | 정적 분석만 수행 | 모든 model과 search/stored-page/analyze endpoint를 검토했다. 앱의 동일 News/AI owner를 사용하고 입력 자동화 설정이 실제 service에 전달된다. unavailable/validation/provider/plan/runtime 오류를 HTTP로 변환하며 별도 inference owner를 만들지 않는다. 코드 잔여 없음; 실제 provider/ASGI 미검증. | 정밀 검토 완료; F19,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/news_sources.py` | 부분 정밀(잔여 미검토) | 기존 start/close/run_once를 승계하고 credential drain·run_query/collect_query/error/catalog/budget/loop와 모든 filter helper를 검토했다. durable source cursor→페이지→targets/provider filter→save_news_source_page 원자 저장→다음 cursor이며 cap은 truncated/gap으로 명시한다. 오류는 최신 cursor 재조회 후 r…(상세 JSON) | 정밀 검토 완료; F19,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/observation_frame_recovery.py` | 정적 분석만 수행 | 모든 trim/advance/receipt/drain_thread_call을 검토했다. CandidateMonitor/Runner legacy checkpoint→bounded native safe prefix→parse-all-before-RAM-publish→scratch frame→complete checkpoint ACK 후 authoritative frame 교체다. safe cursor/order/scope/no-progress 오류는 중단하며 과거 …(상세 JSON) | 정밀 검토 완료; F11,F12,F17,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/operational_settings_routes.py` | 부분 정밀(잔여 미검토) | - 함수군/역할: 기존operationalread/PUT3개승계+모든DTOfield/Protocol/state,선택설정GET/PUT·allocationmodelPOST의락/expectedrevision/nullablefieldset·ownedjoin/에러분기를추가. state.lock하나가save/apply/close와RAM선택을직렬화한다. - 연결/실패: PCoperations/selection→revisionCAS→native→serviceapply/stat…(상세 JSON) | 정밀 검토 완료; F03,F10,F11,F12,F13,F19,F20,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/persistent_outbox.py` | 정적 분석만 수행 | JsonRecordOutbox 전체를 검토했다. Top20 constructor/start load→queue put→native save ACK→remove의 파일 owner이며 RLock은 같은 instance 재진입, _write는 sibling temp replace다. load는 FileNotFound뿐 아니라 IO/JSON/잘못된 최상위 타입도 빈 dict로 숨긴다. 이후 put/remove가 기존 미확인 내용을 덮을 수 있는 별도 오류 후보 S-F0…(상세 JSON) | 정밀 검토 완료; F09,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/postgres_access.py` | 정적 분석만 수행 | - 역할/함수군: ContextVar source/request와DBWriterContext,ObservedDBCursor/Connection 전체,caller-ownedObservedExistingDBTransaction,openfactory connect-fault,tracepayload,waitsample/summary/probecapacity/SQLexecuteproxy를추가검토. rawexecute/commit/rollback/close의예외는재전파하고…(상세 JSON) | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/program_snapshot_writer.py` | 부분 정밀(잔여 미검토) | 기존 enqueue/flush/drain/save/write 전체 본문 근거를 승계하고 Protocol/constructor와 Top20 소비를 대조했다. latest pending per code와 detached batch 하나의 save task가 중복/실패 복원/취소 drain 경계를 보유하므로 단순 wrapper가 아니다. 모든 native batch는 같은 app store로 간다. 코드 잔여 없음; 운영 PG 비용/종료 미실행. | 정밀 검토 완료; F09,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/real_runtime.py` | 부분 정밀(잔여 미검토) | - 함수군/역할: 기존read/cycles/update/rolefence8개승계에모든schedulepurehelper·Context/Plan·Owner준비/boot/예약contextmanager/rolevalidate/credentialdrain·publishresume/release/close/계좌eventmonitor/status를추가. market는기존collector/broker를공유하고다른계좌는조회전용context;accountquery·reads·mo…(상세 JSON) | 정밀 검토 완료; F03,F14,F16,F17,F18,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/realtime_collector.py` | 부분 정밀(잔여 미검토) | 기존 receive/registration FSM·parse/RAM aggregate·flush retry/read overlay/close·credential drain을 승계하고 constructor/status/reconnect deadline/start/replay/source approval helpers·capture receipt·account publish/automation price·snapshot schedule·subscription gro…(상세 JSON) | 정밀 검토 완료; F04,F07,F08,F10,F11,F12,F13,F14,F18,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/realtime_hub.py` | 부분 정밀(잔여 미검토) | 기존 connect/disconnect/update/requested/publish/ready를 승계하고 receipt dataclass·drop scope/date 해석·take_gap·priority/program sorting과 상태 getter를 보완했다. subscribers bounded queues가 owner이며 drop은 시간/venue 범위를 보존하거나 unknown으로 기록한다. signal/capture observer는 공개 dict와 별…(상세 JSON) | 정밀 검토 완료; F04,F07,F08,F09,F10,F11,F12,F13,F14,F18,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/realtime_routes.py` | 부분 정밀(잔여 미검토) | 기존 sender body를 승계하고 websocket auth/accept/connect/subscribe/initial snapshot/live overlay/readiness/finally 전체를 보완했다. Hub가 subscriptions owner이고 native durable snapshot 뒤 current Collector RAM overlay를 보낸다. sender cancel await 오류가 disconnect를 건너뛰는 R3-037은 그대로…(상세 JSON) | 정밀 검토 완료; F04,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/research_read_routes.py` | 부분 정밀(잔여 미검토) | 기존 observations export를 승계하고 v1/v2 source-check·RAM Shadow/live+recorded·candidate expiration/status projector를 검토했다. DB await 뒤 현재 app monitor getter로 replacement를 반영하며 decision은 주문권한 false다. source-check 파라미터 단위 seconds/minutes를 기존 callback 분기로 전달한다. 만료/lead…(상세 JSON) | 정밀 검토 완료; F11,F12,F13,F14,F15,F21,F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/resource_usage.py` | 정적 분석만 수행 | 모든 OS helper/진입 함수를 검토했다. diagnostic_read_routes→bounded disk/RSS/cgroup read이며 Linux 실패는 Windows API fallback 또는 None, disk 실패는 cwd fallback이다. task/저장/계좌 상태를 소유하지 않고 측정 시각은 host diagnostic counter 영역이다. 코드 잔여 없음; 실제 Linux/cgroup/Windows API 실행 안 함. | 정밀 검토 완료; F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/rest_broker.py` | 부분 정밀(잔여 미검토) | 기존 request/cache lookup/transport/persist/run/close를 승계하고 credential prepare/drain/activate/swap/resume·start/publicrequest·reservation/TTL/prune/validate/fingerprint/wallclock 전체를 보완했다. 단일 priority transport worker와 별도 persistence owner는 정책/실제thread 수명 때문에 유지…(상세 JSON) | 정밀 검토 완료; F03,F04,F10,F18,F23,F24 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/schema_migrations.py` | 부분 정밀(잔여 미검토) | - 역할/함수군: 이전 runner전체body/SQLCursor와dataclass 근거승계. SQLCursor는 runtime전달층아닌typing계약, migration.statements가dialect선택; validate는연속버전·중복/name/빈plan을검증하고 appliedhistory를다룬다. - 연결/실패: backend initialize의caller-ownedcursor→savepoint단위plan→schema ledger; 예외rollback/r…(상세 JSON) | 정밀 검토 완료; F01,F03,F07,F08,F09,F11,F16,F18,F19,F22,F23,F24,F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/server_logging.py` | 정적 분석만 수행 | formatter/rotation/delete policy/config 전체를 검토했다. __main__→dictConfig→delayed daily file/console로 연결되고 UTC15:00 rotation을 KST 날짜 archive로 명명한다. 기존 날짜 collision은 suffix로 보존하고 retention은 날짜군 단위다. Python logging handler가 thread lock/close를 소유하며 이 모듈은 추가 task를 만들지…(상세 JSON) | 정밀 검토 완료; F01,F24,F25 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `central_server/shadow_checkpoint.py` | 정적 분석만 수행 | - 역할/함수군: CheckpointParts/split의ambiguousfallback,DDL/LOADSQL/restore의version·barorder·key·frame누락검사,save_frames의parentFORUPDATE/changedframeUPSERT/retaineddelete,clear/downgrade전체. unsupportedlogical문서는losslessinline,새schema는기존stateparent에FKcascade한다. - 연결/실패…(상세 JSON) | 정밀 검토 완료; F11,F12,F17 | 코드 잔여0. 실제 PG/운영DB/NAS/외부 provider·네트워크·vault/계좌·주문 실행 없음; 코드 분기 검토를 운영 수락으로 승격하지 않음; OS/crash/장중 부하/모든 branch runtime는 미실행; 각 모듈 manual evidence의 범…(상세 JSON) |
| `domain/__init__.py` | 부분 정밀(잔여 미검토) | prior entire three-line RankedStock export | 정밀 검토 완료; F02 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `domain/credential_contract.py` | 정적 분석만 수행 | provider exact credential-field map3-9 | 정밀 검토 완료; F23 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `domain/execution_activation.py` | 정적 분석만 수행 | criteria/forward spec49-147; unfilled frozen operating policy151-199; mock operating spec/TBD/identity203-347; readiness357-403; forward evidence406-467; stage transition map/revision470-527; all decoders/hash/int/time530-648 | 정밀 검토 완료; F11,F15,F17 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `domain/market_data_contract.py` | 부분 정밀(잔여 미검토) | prior enums/metadata/observation/available_at/legacy adapter1-155; venue alias and complete legacy state table159-185 | 정밀 검토 완료; F05,F07,F08,F11,F21 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `domain/news_observation.py` | 정적 분석만 수행 | extractor/schema/rule versions10-12; canonical document hash15-19; NUL framed job identity22-26 | 정밀 검토 완료; F19 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `domain/order_contract.py` | 정적 분석만 수행 | account scope/binding/alias14-100; order side/type/state/terminal103-124; intent/submission/fill/snapshot/account127-226; order event canonical identity/document245-290 | 정밀 검토 완료; F16,F17,F18 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `domain/ranking.py` | 부분 정밀(잔여 미검토) | prior entire1-25 RankedStock/new_high_label/normalize_stock_code | 정밀 검토 완료; F02,F04,F11 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `domain/realtime_gap.py` | 부분 정밀(잔여 미검토) | prior whole1-68 market_clock, GapRange/document and RealtimeGap parse/document including invalid shape/range bounds | 정밀 검토 완료; F04,F05,F08 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `domain/research_contract.py` | 부분 정밀(잔여 미검토) | ObservationRevisionSource.from_observation21-102 plus UTC aware validator105-110; prior ThemeSnapshotSource124-153 content hash excludes created_at; new record fields114-121 and source handling | 정밀 검토 완료; F03,F07,F09,F15,F21,F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `domain/snapshot_provenance.py` | 부분 정밀(잔여 미검토) | prior entire1-55 SnapshotLike, snapshot_field_origins, mark_news_backfilled, news/mapping origin | 정밀 검토 완료; F18,F21,F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `domain/strength_level.py` | 부분 정밀(잔여 미검토) | prior entire seven-line strength_badge threshold/None formatting | 정밀 검토 완료; F04,F09 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `domain/theme_import.py` | 부분 정밀(잔여 미검토) | prior entire1-22 ThemeImportRow and header/row validation | 정밀 검토 완료; F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `domain/theme_parser.py` | 부분 정밀(잔여 미검토) | prior entire1-19 theme_key and parse_themes | 정밀 검토 완료; F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `domain/theme_text_import.py` | 부분 정밀(잔여 미검토) | prior entire1-62 parse_theme_text heading/row extraction and stock-code/name patterns | 정밀 검토 완료; F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `domain/time_basis.py` | 부분 정밀(잔여 미검토) | KST/as_kst7-11; explicit fallback now/wall/today14-25; aware ISO KST28-33 | 정밀 검토 완료; F05,F06,F17,F21,F23 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `domain/top20_reconciliation.py` | 부분 정밀(잔여 미검토) | prior minute keys/coverage/input generations/weighted selected membership1-180; market allocation/output181-200; series selection/native raw fallback203-228; projection validity231-249; display statistics252-275 | 정밀 검토 완료; F03,F09 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `domain/trade_snapshot_observations.py` | 부분 정밀(잔여 미검토) | prior entire1-129 EntrySnapshotLike; entry_context_observations; presence/mapping/source/as_kst helpers | 정밀 검토 완료; F18,F21,F22 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `infrastructure/__init__.py` | 정밀 검토 완료 | 기존 whole-module 본문 근거 승계(해시 동일): 전체파일을열어 docstring-only임확인; 실행업무/caller/저장/실패책임 없음 | 정밀 검토 완료; 업무 실행 없는 namespace/docstring | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/app_paths.py` | 정적 분석만 수행 | 모든path properties/for_current_user/legacy copy/defaultsounds본문 | 정밀 검토 완료; F01,F24,F25 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/article_text.py` | 정적 분석만 수행 | HTMLParser 모든state callbacks, clean/relatedsections/tail/photo판정, fetch/parse/publication 모든본문 | 정밀 검토 완료; F19 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/central_ai_client.py` | 정적 분석만 수행 | analyze/_analysis와nested evidence decoder 전본문 | 정밀 검토 완료; F19 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/central_content_client.py` | 부분 정밀(잔여 미검토) | health/load/load_all/upsert/replace/history/jobclaim/import/observations/candidates/shadow/sourcecheck endpoints 전체본문; publication/spec/control/nativeunfilled/dynamicallocation prepare/publish 전본문; capabilities/_request/errors/clock capture/helpers 전체 | 정밀 검토 완료; F03,F08,F09,F10,F11,F12,F13,F14,F15,F16,F17,F18,F19,F21,F22,F23 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/central_content_sync.py` | 부분 정밀(잔여 미검토) | push/pull/themesnapshot/replace/apply·manifest/cursorload/save/seed/capability 전본문; read_news/read_themes SQLexport와write_news/write_themes nativeimport 전본문; scoped/legacy link identity/hash/tombstone/revisionmerge/upsert helpers 전체 | 정밀 검토 완료; F18,F19,F22,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/central_credentials_client.py` | 부분 정밀(잔여 미검토) | 전체source HTTPS/no-redirect request·profile/revision validators; load/create/delete/rename/prepare mock/real/global 및operation accept/status/apply/cancel; accountsettings/update_account target/context검증 전본문 | 정밀 검토 완료; F14,F16,F17,F23 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/central_journal_sync.py` | 부분 정밀(잔여 미검토) | 모든TableSpec/v1-v2와revisioncompare; sync전체remote/load/merge/fullupload; account_scoped_document/syncstates/tombstones/legacyreceipts/hash/key/document/rows helpers; CentralJournalSyncRunner 전체event/schedule/daemon/run | 정밀 검토 완료; F18,F19,F22,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/central_news_client.py` | 부분 정밀(잔여 미검토) | 모든market_feed/search/stored_page/archivev1page/detail/archivev2page 및deserialize 전체본문 | 정밀 검토 완료; F19,F22 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/central_operational_settings.py` | 부분 정밀(잔여 미검토) | 전체load/save·RAMrevision·ChatGPT·morningbrief endpoints/_request/apply_to_local_news | 정밀 검토 완료; F11,F12,F13,F14,F19,F20,F23 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/central_server_config.py` | 정적 분석만 수행 | DataSourceSettings.validate와DataSourceConfig.load/save 전체본문 | 정밀 검토 완료; F01,F23 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/central_server_process.py` | 정적 분석만 수행 | 전체init/start/stop/is_ready/port와환경설정전달본문 | 정밀 검토 완료; F01,F23,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/central_settings_sync.py` | 부분 정밀(잔여 미검토) | is_shared_setting/localkeys·모든sync native table/version/update/uploaddiff branches | 정밀 검토 완료; F01,F22,F23,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/central_sync_utils.py` | 정적 분석만 수행 | normalize_batch_size/upload_documents/central_document/table_names 전체본문 | 정밀 검토 완료; F22 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/central_theme_sync.py` | 부분 정밀(잔여 미검토) | 이전_run·revision경로승계;notify/close/flush/retry/pending/token compatibility전체보완 | 정밀 검토 완료; F22,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/chatgpt_auth.py` | 정적 분석만 수행 | 전체safeerror/redaction/no-redirect/JSON/request helpers; credentialstore inprocess+OSlock/DPAPI/atomic/fsync; identityJWT/claims/tokenrecord; Auth discovery/status/select/connectloopbackPKCE/sessionrotation/welcome/disconnect 모든본문 | 정밀 검토 완료; F19,F20,F23,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/chatgpt_linux_store.py` | 정적 분석만 수행 | 전체directory/read/publish/locked/load/save와privatefile·symlink·owner·mode 검사 | 정밀 검토 완료; F19,F20,F23,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/chatgpt_plan.py` | 정적 분석만 수행 | reasoning/groundedevidence/modelcatalog/response/research 전본문; SSE completedresponse/최종text/usage/reconstruction/diagnosticshape 전본문 | 정밀 검토 완료; F19,F20,F23,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/dart_disclosures.py` | 정적 분석만 수행 | validate/search/list_disclosures/corpcodes/json 전체본문 | 정밀 검토 완료; F19 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/excel/theme_repository.py` | 부분 정밀(잔여 미검토) | load/load_rows/load_header_and_rows/_key/_shared_strings 전체 | 정밀 검토 완료; F02,F10,F22 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/exchange_effective_dates.py` | 부분 정밀(잔여 미검토) | 이전전체parser승계+scriptcollect90-147의실제rawfetch/cache/hash→parser→nativeevents 소비추가 | 정밀 검토 완료; F21 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/historical_backfill.py` | 정적 분석만 수행 | 1-625 모든모델/candidate readonly/seed/nonstock/daily·rangeclaim; 626-1190 continuation claim/release/split/finish·publicationstate batchread/stock·historicalsearch parsers/fetch; 1191-1742 metadata/title/date parser·deadline/open/read/parse fetch 진단·전체DDL/migrati…(상세 JSON) | 정밀 검토 완료; F19,F21,F22,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/historical_reconstruction.py` | 정적 분석만 수행 | export전체candidate/bar/news SQLsnapshot→manifest→immutablepublish; load/hash/ordinal; adapt전체casepopulation/outcome1m→researchbar/closingauction·write; preferredinterval/jobstate/awaretime/readonlyUNC/path/hash/atomicexport helpers | 정밀 검토 완료; F21 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/historical_research_readiness.py` | 부분 정밀(잔여 미검토) | 이전전체함수범위승계+CLI실제consumer assess_package/main본문추가 | 정밀 검토 완료; F15,F21 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/__init__.py` | 정적 분석만 수행 | 전체8행 reexport/import body와 __all__ | 정밀 검토 완료; 업무 실행 없는 namespace/docstring | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/account_identity.py` | 정적 분석만 수행 | VerifiedAccountIdentity.__post_init__; reader init/verify/identity_from_payload; normalized number 및 HMAC fingerprint 전체본문 | 정밀 검토 완료; F14,F16,F18,F23 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/account_query.py` | 정적 분석만 수행 | 모든exception/dataclass __post_init__; LegacyAccountQueryAdapter pagination, BoundDirect delegation/scoping, as_account_batch_client | 정밀 검토 완료; F14,F18,F23 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/central_realtime_worker.py` | 부분 정밀(잔여 미검토) | constructor/update/run/_receive 전본문; _dispatch 각event·plannedgeneration/deadline; fallback/validation start/stop·coverage predicate; _from_payload 전본문 | 정밀 검토 완료; F04,F18,F24,F25 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/client.py` | 부분 정밀(잔여 미검토) | 52-217 credential prepare/token→verify→drain/fork/activate/disable/end 전본문; 219-456 clock/request/request_once/continuation/token/auth/post/retry/rate/expiry 전본문 | 정밀 검토 완료; F03,F04,F08,F10,F14,F16,F18,F23 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/client_factory.py` | 부분 정밀(잔여 미검토) | QueryClient, _with_verified_local_account/create_query_client 모든모드/credential 분기 | 정밀 검토 완료; F01,F23 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/daily_high_worker.py` | 정적 분석만 수행 | 전체 __init__/run/stop 본문; 입력 snapshot 보관, interruption 전후와 per-item 예외, 성공/실패 signal 및 wait 반환 | 정밀 검토 완료; F08,F10,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/failover_client.py` | 부분 정밀(잔여 미검토) | request/request_with_continuation; 모든stored delegator/_stored_primary; account scope/querybatch/fallback 검증; clock/token 전본문 | 정밀 검토 완료; F03,F04,F08,F10,F14,F16,F18,F23 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/fundamentals_worker.py` | 정적 분석만 수행 | 전체 __init__/run/stop 본문; 입력 snapshot 보관, interruption 전후와 per-item 예외, 성공/실패 signal 및 wait 반환 | 정밀 검토 완료; F08,F10,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/historical_high_worker.py` | 정적 분석만 수행 | 전체 __init__/run/stop 본문; 입력 snapshot 보관, interruption 전후와 per-item 예외, 성공/실패 signal 및 wait 반환 | 정밀 검토 완료; F08,F10,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/local_account_binding.py` | 부분 정밀(잔여 미검토) | load_bindings/save_binding; _binding_document/_binding_from_document 모든분기 | 정밀 검토 완료; F14,F18,F23 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/local_config.py` | 부분 정밀(잔여 미검토) | DPAPI protect/unprotect와finally LocalFree; ApiProfiles/load_profiles/load/save_profiles 모든본문 | 정밀 검토 완료; F01,F23 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/minute_history_worker.py` | 정적 분석만 수행 | 전체 __init__/run/stop 본문; 입력 snapshot 보관, interruption 전후와 per-item 예외, 성공/실패 signal 및 wait 반환 | 정밀 검토 완료; F08,F10,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/mock_account.py` | 부분 정밀(잔여 미검토) | reader constructor/read/read_trade_cost_pages/_pages/awareclock·mock/real 환경guard; 모든unfilled/filled/orderdetails/account/merge/execution/state/rows/symbol/nonnegative/time변환본문 | 정밀 검토 완료; F14,F16,F18 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/mock_execution.py` | 부분 정밀(잔여 미검토) | 전체transport init/submit/cancel/_send/make_mock_transport와ORDER_API_IDS | 정밀 검토 완료; F16 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/nxt_eligibility_worker.py` | 정적 분석만 수행 | 전체 __init__/run/stop 본문; 입력 snapshot 보관, interruption 전후와 per-item 예외, 성공/실패 signal 및 wait 반환 | 정밀 검토 완료; F08,F10,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/ranking_worker.py` | 정적 분석만 수행 | 전체 __init__/run/stop 본문; 입력 snapshot 보관, interruption 전후와 per-item 예외, 성공/실패 signal 및 wait 반환 | 정밀 검토 완료; F02 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/realtime.py` | 정적 분석만 수행 | 전체불변event모델; parse_trade/order/balance/index/operation/program/reference/VI; code/market/number/decimal/cumulativefill helpers | 정밀 검토 완료; F04,F06,F07,F09,F10,F18 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/realtime_registration.py` | 정적 분석만 수행 | group_atoms/_limits/encode/all_atoms/trade_sources/ready_codes/filter_message 전본문; RegistrationCommand/Plan와 plan_registration 안정placement·remove-before-reg; RegistrationProgress start/sent/ack/commit 전본문 | 정밀 검토 완료; F04,F18 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/realtime_worker.py` | 부분 정밀(잔여 미검토) | 이전35-263 body승계+이번1-35/264-282 보완; session/intent/receive/diagnostics/subscriptionpacket/stop 전범위 | 정밀 검토 완료; F04,F18,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/remote_client.py` | 부분 정밀(잔여 미검토) | 42-76 HTTP detail/plannedreconnect 검증; 81-251 계좌선택/모의명령 pending 동일request/ledger cursor/query 전본문; 253-555 저장minute/recent/cap/ranking/daily/flow/high/fundamental/NXT/index/TOP20 API 및schema/error 분기; 557-673 accountbatch context/batch/page validation·servercl…(상세 JSON) | 정밀 검토 완료; F03,F04,F08,F10,F14,F16,F18,F23 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/settings.py` | 정적 분석만 수행 | KiwoomSettings.base_url/from_env_file 전체; env parser/path/keys/environment 분기 | 정밀 검토 완료; F01,F23 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/kiwoom_rest/validation_client.py` | 정적 분석만 수행 | metrics lock/count/percentile; ParallelValidationClient 모든query/stored/account/work/record/canonical 함수; RealtimeValidationRecorder observe/match/timeout/evict/duplicate/outoforder/reportqueue/writeloop; source_ref/clock/unit/helper 전본문 | 정밀 검토 완료; F03,F04,F24,F25 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/krx/__init__.py` | 정밀 검토 완료 | 기존 whole-module 본문 근거 승계(해시 동일): 전체파일을열어 docstring-only임확인; 실행업무/caller/저장/실패책임 없음 | 정밀 검토 완료; 업무 실행 없는 namespace/docstring | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/krx/kind_name_history.py` | 정적 분석만 수행 | 모든decode/clean/list/formernames parser; Sync init/sync/search/loadformer/get/post본문 | 정밀 검토 완료; F02,F10,F22 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/krx/stock_catalog.py` | 부분 정밀(잔여 미검토) | 전체 _KrxTableParser HTML tag/data/end와normalize_krx_market/parse_krx_stock_catalog/fetch_krx_stock_catalog | 정밀 검토 완료; F02,F10,F22 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/krx/stock_catalog_worker.py` | 부분 정밀(잔여 미검토) | 전체init/run와completed/failed | 정밀 검토 완료; F02,F10,F22,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/local_storage_diagnostics.py` | 정적 분석만 수행 | 전체category/inspect_local_storage walk/entrylimit/symlink/error/retentionlabels | 정밀 검토 완료; F23,F25 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/logging_config.py` | 정적 분석만 수행 | KSTFormatter.converter/configure_logging 전체본문 | 정밀 검토 완료; F01,F24,F25 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/market_data_metadata_codec.py` | 정적 분석만 수행 | 모든document/fromdocument/storagevalues/fromrow/datetime/enum 변환본문 | 정밀 검토 완료; F05,F07,F08 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/naver_news.py` | 정적 분석만 수행 | 모든settings/item/page dataclass; LocalNaverNewsConfig load/save/DPAPI/atomic파일·source/shortcut/filter 변환; NaverNewsClient search/pagination/search_page/hub→legacyauthfallback/requestclaim 및provider/html/time helpers | 정밀 검토 완료; F19 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/naver_stock_market_news.py` | 정적 분석만 수행 | pageurl/publication/parse/fetch 전체; initialize/daystate/store_page/error/collect_day 전체SQLite·재개·callback·retry 분기 | 정밀 검토 완료; F19 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/naver_stock_news.py` | 정적 분석만 수행 | StockNewsBatch와client init/update_endpoint/fetch/search 모든page/cluster/identity/date/cutoff branch | 정밀 검토 완료; F19 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/news_ai.py` | 부분 정밀(잔여 미검토) | 모든AI결과/usage모델·bodyhash/prompts/providerdispatch/planJSONvalidation; HTTP3회429/5xxretry·researchwebsearchrequired·openai/gemini/claudeadapter·parse/parsemany/value 모든본문 | 정밀 검토 완료; F19 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/ocr/__init__.py` | 정밀 검토 완료 | 기존 whole-module 본문 근거 승계(해시 동일): 전체파일을열어 docstring-only임확인; 실행업무/caller/저장/실패책임 없음 | 정밀 검토 완료; 업무 실행 없는 namespace/docstring | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/ocr/paddle_theme_ocr.py` | 부분 정밀(잔여 미검토) | 전체body: console suppression/token/badge regions+merge/theme rows/lazy engine/extract lines·rows/worker run | 정밀 검토 완료; F02,F10,F22,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/persistence/__init__.py` | 정밀 검토 완료 | Prior whole-module own-body evidence retained without replay: 본문 전체: SQLite 영속성 계층이라는 docstring 한 줄뿐임을 실제 read로 확인. 함수/상태/SQL/수명/실패 책임 없음; package namespace 유지 | 정밀 검토 완료; 업무 실행 없는 namespace/docstring | 코드 잔여0. 기존 모듈 코드완료 근거 승계. 실제 앱/외부환경 수락은 별도 미실행. |
| `infrastructure/persistence/backup_file.py` | 정밀 검토 완료 | Prior whole-module own-body evidence retained without replay: write_json_backup 전체: serialize→동일폴더temp→flush/fsync→replace→finally unlink. Settings/Theme/NewsAI export caller와 대조 | 정밀 검토 완료; F22,F23 | 코드 잔여0. 기존 모듈 코드완료 근거 승계. 실제 앱/외부환경 수락은 별도 미실행. |
| `infrastructure/persistence/column_settings_repository.py` | 부분 정밀(잔여 미검토) | ColumnSetting immutable DTO; __init__/list reconstruct ordered native rows; save/reset batch and _record_versions table ensure in same commit | 정밀 검토 완료; F02,F22,F23 | 코드 잔여0. actual settings dialog/UI close during SQLite lock; multi-process layout CAS semantics |
| `infrastructure/persistence/daily_bar_repository.py` | 부분 정밀(잔여 미검토) | load_targets PRAGMA optional legacy OHLCV→DailyHighTargets bars250 and high5/20 without certifying coverage; refreshed_today/refreshed_since/finalized_codes select actual collected/final markers; mark_finalized clears unconfirmed; mark_unconfirmed and table en…(상세 JSON) | 정밀 검토 완료; F05,F06,F08,F10 | 코드 잔여0. actual250day provider collection/calendar and failure timing; native multiwriter read snapshots/PG not this local store |
| `infrastructure/persistence/database.py` | 정적 분석만 수행 | Full v1 baseline/default columns and compatibility stock/minute/index/daily/sync/evidence DDL; initialize v1..8 migration registry and default alias/profile setup; theme profile one-time legacy copy; news suggestion/context/transfer ledger migrations; stock op…(상세 JSON) | 정밀 검토 완료; F01,F05,F06,F08,F10,F22,F23,F24 | 코드 잔여0. Production legacy schemas/restore; crash between compatibility DDL/migration marker |
| `infrastructure/persistence/entry_snapshot_writer.py` | 부분 정밀(잔여 미검토) | Reused030/031 run/backfill/investor cache bodies; constructor queue/injected loaders and scope; enqueue snapshot/news/flow command tuples; setters clear respective caches; stop_and_drain interruption+wait | 정밀 검토 완료; F05,F06,F08,F18,F24 | 코드 잔여0. Real QThread shutdown timing/DB locked |
| `infrastructure/persistence/execution_repository.py` | 부분 정밀(잔여 미검토) | ExecutionStore narrow native protocol; typed immutable record/event/page codecs; owner immutable bind + automation claim options; create/apply duplicate/conflict/load fallback; bounded active401/account events limit+1 and identity/time/lineage validation; clai…(상세 JSON) | 정밀 검토 완료; F16,F17,F18 | 코드 잔여0. Broker orders/reconciliation; PG and concurrent lease loss |
| `infrastructure/persistence/forward_evaluation_repository.py` | 부분 정밀(잔여 미검토) | Profile/spec/binding/risk snapshots and current head; mock-only verified binding contract; admission by-spec/lease by-admission; candidate policy anchored before package/receipt; recovery/gate/risk lineage and immutable approved/dispatch-by-intent/stop current…(상세 JSON) | 정밀 검토 완료; F15,F16,F17,F21 | 코드 잔여0. Concurrent writers/PG; live admission/execution gates |
| `infrastructure/persistence/google_drive_sync.py` | 부분 정밀(잔여 미검토) | Result/error partial-applied contract; config/client import/disconnect; metadata-only latest query; upload main DB read snapshot + separate news AI export; v2 immutable generations/legacy aliases/manifest last; legacy/v2 download full prevalidation/digest+size…(상세 JSON) | 정밀 검토 완료; F22,F23,F24 | 코드 잔여0. Actual OAuth/Drive retries/permissions; remote concurrent uploads and restore OS fence |
| `infrastructure/persistence/journal_backup.py` | 정밀 검토 완료 | Prior whole-module own-body evidence retained without replay: export/import 전체 및 MainWindow._export/_import_journal_backup: temp SQLite backup/replace, required tables/version 검사, child shutdown/wait 후 import; 예외UI 소비 | 정밀 검토 완료; F18,F22 | 코드 잔여0. 기존 모듈 코드완료 근거 승계. 실제 앱/외부환경 수락은 별도 미실행. |
| `infrastructure/persistence/journal_bar_repository.py` | 부분 정밀(잔여 미검토) | Reused prior minute source/metadata import code; missing daily import/load/upsert paths; atomic backfill values+completion and no-data failed status; confirmed/partial authority guards; readonly monitor live/daily/index/catalog imports; chart prior-day/range r…(상세 JSON) | 정밀 검토 완료; F08,F18,F22 | 코드 잔여0. GUI chart/actual TR coverage; multi-process import and confirmed metadata races |
| `infrastructure/persistence/journal_database.py` | 정밀 검토 완료 | Prior whole-module own-body evidence retained without replay: 모듈 자체 constructor→initialize_journal_database 및 remember_stock→scoped row upsert 전체; JournalWindow.set_stock→refresh/chart consumer | 정밀 검토 완료; F18,F22 | 코드 잔여0. 기존 모듈 코드완료 근거 승계. 실제 앱/외부환경 수락은 별도 미실행. |
| `infrastructure/persistence/journal_schema.py` | 부분 정밀(잔여 미검토) | Reused original migration baseline/enrichment scope review; Full v5 scoped trade/cost rebuild, v6 scoped snapshot/review/analysis/research indexes/table rebuild; v8 provenance upgrade, v9 event projection ledger; initialize compatibility DDL+marker+runner; leg…(상세 JSON) | 정밀 검토 완료; F18,F22 | 코드 잔여0. All historical migrations with production data; native crash/retry/lock timing |
| `infrastructure/persistence/journal_snapshot_repository.py` | 부분 정밀(잔여 미검토) | Reused prior snapshot serialization/read/backfill main bodies051/058; Additional full insert/upsert SQL investor backfilled CASE and unconditional news replacement; same-connection metadata; TradeEntrySnapshot scope post-init/effective_scope/scoped_execution_k…(상세 JSON) | 정밀 검토 완료; F18,F22 | 코드 잔여0. Actual broker fills capture; simultaneous process enrichments |
| `infrastructure/persistence/journal_snapshot_service.py` | 정밀 검토 완료 | Prior whole-module own-body evidence retained without replay: news_at_execution/load_episode_entry_snapshots 전체; JournalWindow inject→TradeAnalysisPreparationService.prepare→viewmodel. 반복native읽기/선택backfill쓰기 실패 전파 진단 | 정밀 검토 완료; F18,F19,F22 | 코드 잔여0. 기존 모듈 코드완료 근거 승계. 실제 앱/외부환경 수락은 별도 미실행. |
| `infrastructure/persistence/journal_strategy_settings_repository.py` | 부분 정밀(잔여 미검토) | Personal plain/structured rule JSON load/save; Packs default Mimosa seed onread and missing-default addition; enabled/priority sorting; draft source text/rule serialization; delete; version snapshot and prefix LIKE load; malformed row handling | 정밀 검토 완료; F18,F21,F22 | 코드 잔여0. UI restore/version workflow; write interruption and malformed hand-edited data |
| `infrastructure/persistence/journal_trade_repository.py` | 정적 분석만 수행 | Projection BEGIN IMMEDIATE cursor gap/replay/content hash/fill identity checks; account fill pages/date filtering/unresolved aggregate; setup/read batch/cycle overrides/analysis migration; same commit sync tombstones; fills+cost atomic history sync with scope …(상세 JSON) | 정밀 검토 완료; F18,F22 | 코드 잔여0. Concurrent journal owners/recoveries; live execution page delivery and actual broker costs |
| `infrastructure/persistence/local_bar_observations.py` | 정밀 검토 완료 | Prior whole-module own-body evidence retained without replay: local_minute/daily/_as_kst 전체; minute/daily/journal writer의 available_at/품질/source 보존 소비 대조 | 정밀 검토 완료; F05,F08,F10,F18 | 코드 잔여0. 기존 모듈 코드완료 근거 승계. 실제 앱/외부환경 수락은 별도 미실행. |
| `infrastructure/persistence/market_cache_writer.py` | 부분 정밀(잔여 미검토) | QThread constructor/enqueue every command copied; stop_and_drain; all queue dispatch branches; minute/index and history/bar+sync two-step saves/error payload; generation-aware quality callbacks; daily verified250 stock update then daily bars; fundamentals nati…(상세 JSON) | 정밀 검토 완료; F05,F06,F08,F10,F24 | 코드 잔여0. OS interruption at commit; native lock latency/live thread drain |
| `infrastructure/persistence/market_data_metadata_repository.py` | 정밀 검토 완료 | Prior whole-module own-body evidence retained without replay: upsert guard 및 class save/load 전체; journal bar/snapshot/minute writers의 동일transaction helper 소비 대조. standalone class의 production caller는 찾지 못해 API전체연결로 승격하지 않음 | 정밀 검토 완료; F05,F08,F10,F18 | 코드 잔여0. 기존 모듈 코드완료 근거 승계. 실제 앱/외부환경 수락은 별도 미실행. |
| `infrastructure/persistence/market_data_metadata_schema.py` | 정밀 검토 완료 | Prior whole-module own-body evidence retained without replay: create_market_data_metadata_table 전체DDL/key, main/journal migration→native writer/reader의 column/key 소비 대조 | 정밀 검토 완료; F01,F05,F08,F18 | 코드 잔여0. 기존 모듈 코드완료 근거 승계. 실제 앱/외부환경 수락은 별도 미실행. |
| `infrastructure/persistence/minute_bar_repository.py` | 부분 정밀(잔여 미검토) | Reused001 upsert_many/native authority/purge; gap partial/missing markers, empty confirmations, market complete-vs-realtime writes; market minute/daily/history replacements with same-transaction statistic invalidation; top20 capture state/splits/cohorts writer…(상세 JSON) | 정밀 검토 완료; F05,F06,F08,F09 | 코드 잔여0. All native multiwriter schedules; live cohort/capture gaps/calendar and index total correctness |
| `infrastructure/persistence/news_ai_backup.py` | 정밀 검토 완료 | Prior whole-module own-body evidence retained without replay: export/read/validate/import 전체: transaction/read snapshot/atomicbackup, v1/v2 조건/기존theme_candidates 보존 및 shared분석 동시commit; Drive/StrictRestore caller | 정밀 검토 완료; F19,F22,F23 | 코드 잔여0. 기존 모듈 코드완료 근거 승계. 실제 앱/외부환경 수락은 별도 미실행. |
| `infrastructure/persistence/news_ai_repository.py` | 부분 정밀(잔여 미검토) | StoredAINewsAnalysis prompt marker; identity key; load/load_many stock-specific analysis and name-matched company-impact shared fallback; save per-stock analysis+shared company impacts together; theme suggestion join/decoding; daily_count/log_request/daily_usa…(상세 JSON) | 정밀 검토 완료; F19,F22 | 코드 잔여0. Actual AI credentials/provider charges; legacy malformed manual DB contents |
| `infrastructure/persistence/news_database.py` | 부분 정밀(잔여 미검토) | initialize delegate to schema ownership; legacy copy destination transaction, verify counts, source tables drop+transfer ledger transaction; already completed requires destination exists/no reappeared legacy tables; DB v8 ledger required | 정밀 검토 완료; F01,F19,F22,F24 | 코드 잔여0. Actual historical DB data integrity; simultaneous legacy source writers |
| `infrastructure/persistence/news_schema.py` | 정적 분석만 수행 | initialize_news_schema version/name full-plan fast read path vs Runner; connection/mkdir lifetime; v1 all news/article/AI/shared/request/sync/link tables and legacy naver_checked_at addition; v2 account scope rebuild/legacy-unassigned migration, existing-scope…(상세 JSON) | 정밀 검토 완료; F01,F19,F22,F24 | 코드 잔여0. all historical schema variants/crash/native locked DB; user operational old DB data preservation |
| `infrastructure/persistence/research_repository.py` | 부분 정밀(잔여 미검토) | Full schema1..27 DDL and registry; readonly17..27 version boundary; Final window/access/event chronological UTC canonical keys, exposure vs active execution, recovery generation/owner consistency; Immutable scientific runs/independent owner history and orphan …(상세 JSON) | 정밀 검토 완료; F11,F15,F21,F24 | 코드 잔여0. actual NAS staging/disk timeout and crash/OS ownership; all production historical migrations and long-lived concurrent campaigns; live resea…(상세 JSON) |
| `infrastructure/persistence/schema_migrations.py` | 정밀 검토 완료 | Prior whole-module own-body evidence retained without replay: Runner 전체: plan 검증/metadata준비/버전확인/marker/각savepoint. main/journal/news/research initialize caller 대조, 실제SQLite 단계2실패 rollback 진단 | 정밀 검토 완료; F01,F21,F22,F24 | 코드 잔여0. 기존 모듈 코드완료 근거 승계. 실제 앱/외부환경 수락은 별도 미실행. |
| `infrastructure/persistence/settings_backup.py` | 정밀 검토 완료 | Prior whole-module own-body evidence retained without replay: export/read/validate/import/한transaction/legacythemes/assets/news settings 전체 본문; Drive import/staged restore→runtime 설정 소비. DB commit후파일실패 local_database_applied 표식/secret 보존/경로·size boundary 확인 | 정밀 검토 완료; F22,F23 | 코드 잔여0. 기존 모듈 코드완료 근거 승계. 실제 앱/외부환경 수락은 별도 미실행. |
| `infrastructure/persistence/settings_repository.py` | 부분 정밀(잔여 미검토) | __init__/database_path; existing get/clear_cache R3-011 reused; set delegates set_many; empty set skip; shared one KST stamp; version-table ensure+values+versions transaction then cache.update only after commit | 정밀 검토 완료; F22,F23 | 코드 잔여0. live multi-process sync conflicts; actual UI/native lock waits |
| `infrastructure/persistence/sqlite_connections.py` | 정밀 검토 완료 | Prior whole-module own-body evidence retained without replay: sqlite_read_connection/sqlite_transaction 전체; native close/commit/rollback과 기존 cache batch caller 소비 대조 | 정밀 검토 완료; F05,F06,F08,F19,F23 | 코드 잔여0. 기존 모듈 코드완료 근거 승계. 실제 앱/외부환경 수락은 별도 미실행. |
| `infrastructure/persistence/stock_aliases.py` | 부분 정밀(잔여 미검토) | Known dated alias data eight entries and seed SELECT known code -> pending namehistory -> DELETE only matching autoalias pending row | 정밀 검토 완료; F10,F22 | 코드 잔여0. Real KRX catalog names/current validity |
| `infrastructure/persistence/stock_news_repository.py` | 부분 정밀(잔여 미검토) | constructor/schema/load and UTC-legacy datetime conversion; recently_checked/last_naver_checked_at; upsert existing identities/all article fields/sync receipt/retention pin exclusion in one transaction; scoped journal link/reactivate/tombstone/load join; scope…(상세 JSON) | 정밀 검토 완료; F18,F19,F22 | 코드 잔여0. Live news provider pagination, GUI refresh and multi-process contention |
| `infrastructure/persistence/stock_repository.py` | 부분 정밀(잔여 미검토) | Reused026 ranking compound read and002/003 price/fundamental paths; catalog upsert+namehistory/alias pending/review/seed; exact/fuzzy/concatenated name and market reads; daily/historical highs plus evidence same commit/cache validity; NXT value-vs-receipt stam…(상세 JSON) | 정밀 검토 완료; F02,F05,F06,F08,F10,F22 | 코드 잔여0. Actual KRX rename chronology; UI fuzzy matching and KST migration production data |
| `infrastructure/persistence/strict_restore.py` | 정밀 검토 완료 | Prior whole-module own-body evidence retained without replay: AppDataLease/atomic_bytes/sqlite_backup/stage→enter_main/child→pointer/input 검증→before capture→PREPARED/APPLYING→COMMITTED/rollback 전체; Drive stage 및 bootstrap/child consumers | 정밀 검토 완료; F01,F22,F24 | 코드 잔여0. 기존 모듈 코드완료 근거 승계. 실제 앱/외부환경 수락은 별도 미실행. |
| `infrastructure/persistence/theme_backup.py` | 부분 정밀(잔여 미검토) | Reuse029 revision and045 created_at export issues; legacy/full-profile/single-profile format validation/export/import; transaction-owned all-profile apply vs expected revision BEGIN IMMEDIATE; decision/suggestion import/export and catalog/alias merge; foreign …(상세 JSON) | 정밀 검토 완료; F22,F23 | 코드 잔여0. Actual legacy backup restoration; cross-PC whole snapshot conflict |
| `infrastructure/persistence/theme_repository.py` | 부분 정밀(잔여 미검토) | Profile create/copy/select/delete/rename and case-insensitive theme merge; catalog exact/fuzzy/concatenated name/alias review duplicates; bulk stock-theme replace; name decisions recursive split/alias resolution with cycle/keep-separate guards; AI suggestion i…(상세 JSON) | 정밀 검토 완료; F02,F22,F23 | 코드 잔여0. UI profile changes/suggestion staging callback; remote import while local cache read |
| `infrastructure/research_data_source.py` | 부분 정밀(잔여 미검토) | development/final prepare와independentpartition validation 전체본문; CentralResearchDataSource page/watermark/ordinal/hash/capacity전체; frozen load/optionaltheme·bundlepath/manifest/write/load·observationorder/load_input·campaign fingerprint/encodedbytes 전체본문 | 정밀 검토 완료; F15,F21,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/research_storage.py` | 부분 정밀(잔여 미검토) | inspect/delete incomplete staging와redirect/path/knownentries검사; remainingbytes/textbytes/markerpublish·inventory의ownership/overlap/size nested함수 전본문 | 정밀 검토 완료; F21,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/system_ssl.py` | 부분 정밀(잔여 미검토) | system_ssl_context 전체 Windows/nonWindows/certificate 오류 분기 | 정밀 검토 완료; F03,F19,F22,F23 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/time_display.py` | 정밀 검토 완료 | 기존 whole-module 본문 근거 승계(해시 동일): timestamp/date/naive/aware/epoch/missing/failure 표시 모든body; archive/trade review 사용; persistence 변환없음 | 정밀 검토 완료; F02,F06,F08,F18,F19,F20,F21 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `infrastructure/update_planner.py` | 정밀 검토 완료 | 기존 whole-module 본문 근거 승계(해시 동일): version/assets/release sorting/intermediate steps/setup tradeoff 모든body와 BackgroundWorkers/MainWindow 소비·missingasset 분기 | 정밀 검토 완료; F01 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `journal_process.py` | 부분 정밀(잔여 미검토) | Main imports/dispatch/strict child gate/parent JSON command and shutdown previously audited052; missing full UI constructor/widgets/signals/clock/timers/auto paths added; Account selection/context response guard; history fill+cost atomic native save and task c…(상세 JSON) | 정밀 검토 완료; F08,F18,F21,F22,F23,F24 | 코드 잔여0. actual GUI lifecycle/close/kill/parent loss; all native DB faults while callbacks queued; multiple simultaneous journal children/enrichment …(상세 JSON) |
| `news_process.py` | 부분 정밀(잔여 미검토) | Actual parser/strict child restore lease/account scope command validation; Taskbar/resource/windows docking/parent liveness/file signature; typed geometry bounds and visibility atomic replace; App/database/StockNewsWindow+MarketNewsWindow; 80ms command/100ms d…(상세 JSON) | 정밀 검토 완료; F19,F22,F24 | 코드 잔여0. Windows handle/PID reuse/real parent death; GUI docking/activation/close and network sync |
| `presentation/__init__.py` | 정밀 검토 완료 | 기존 whole-module 본문 근거 승계(해시 동일): 전체파일을열어 docstring-only임확인; 실행업무/caller/저장/실패책임 없음 | 정밀 검토 완료; 업무 실행 없는 namespace/docstring | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/api_settings_dialog.py` | 정적 분석만 수행 | 1~782 전체 UI/sourcevalues/validate/credentials/operational load-save/resources/connection/done | 정밀 검토 완료; F01,F03,F12,F19,F20,F23,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/app_controller.py` | 부분 정밀(잔여 미검토) | 1~1022 전체: init/signal조립/ranking/RT followups/APIreload/cachequeue/실패병합/shutdown stages | 정밀 검토 완료; F01,F02,F04,F05,F06,F08,F09,F10,F23,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/app_metadata.py` | 정밀 검토 완료 | 기존 whole-module 본문 근거 승계(해시 동일): 표시상수/frozen marker 전body; MainWindow/update worker 소비. I/O/lifecycle 없음 | 정밀 검토 완료; F01,F24,F25 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/background_workers.py` | 부분 정밀(잔여 미검토) | 전체UpdateCheck/Download/GoogleDrive worker init/run/progress/metadata/operation분기 | 정밀 검토 완료; F01,F22,F23,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/candidate_monitor_dialog.py` | 부분 정밀(잔여 미검토) | 1~1468 전체 CandidatePoll/SettingsWorker/position preparation/settings·baseline·RAM선택·원본대조·결과render·stop | 정밀 검토 완료; F11,F12,F13,F14,F18,F21,F23,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/column_manager_dialog.py` | 정적 분석만 수행 | 전체constructor/embedded signals/bulk/move/_save_if_embedded/_save | 정밀 검토 완료; F02,F23,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/daily_high_worker_controller.py` | 정적 분석만 수행 | 전체 constructor/worker/available/is_running/set_factory/start/_release_finished_worker body; factory/type/busy 및 finished identity 분기 | 정밀 검토 완료; F08,F10,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/detached_chart_settings.py` | 정적 분석만 수행 | PanelSettings/SettingsDialog init/save/color/reset 전체body | 정밀 검토 완료; F08,F18,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/detached_chart_window.py` | 부분 정밀(잔여 미검토) | 전체397행 Panel init/selectmode/view/draw/settings/setdata/pin 및Window6panels/layout/update/close | 정밀 검토 완료; F08,F18,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/fundamentals_worker_controller.py` | 정적 분석만 수행 | 전체 constructor/worker/available/is_running/set_factory/start/_release_finished_worker body; factory/type/busy 및 finished identity 분기 | 정밀 검토 완료; F08,F10,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/google_drive_worker_controller.py` | 부분 정밀(잔여 미검토) | 전체 workerproperty/start/_release/_finish/_complete/_metadata/_fail | 정밀 검토 완료; F22,F23,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/historical_high_worker_controller.py` | 정적 분석만 수행 | 전체 constructor/worker/available/is_running/set_factory/start/_release_finished_worker body; factory/type/busy 및 finished identity 분기 | 정밀 검토 완료; F08,F10,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/historical_news_archive_dialog.py` | 부분 정밀(잔여 미검토) | 이전1-237범위승계;상세판본label/원문HTML/targetassessment와shutdown잔여추가 | 정밀 검토 완료; F19,F21,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/historical_news_review_dialog.py` | 부분 정밀(잔여 미검토) | 기존367-432/477-520/579-end승계+이번constructor/reload/queue/workingsheet/table/selection/profile/clear/open·eventsplit잔여본문 | 정밀 검토 완료; F19,F21,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/image_theme_ocr_worker_controller.py` | 부분 정밀(잔여 미검토) | 전체 factory/type/start/busy/finished/stop_for_shutdown | 정밀 검토 완료; F02,F10,F22,F23,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/journal_chart_widget.py` | 부분 정밀(잔여 미검토) | 1~834 전체 chart state/setrows·annotations/texteditor/view/paint/hover·drag·wheel/drawinghit/delete | 정밀 검토 완료; F08,F18,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/journal_delegates.py` | 정적 분석만 수행 | JournalCellMarkerDelegate.__init__/set_selected_cell/paint 전체 | 정밀 검토 완료; F18 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/journal_settings_dialogs.py` | 정적 분석만 수행 | 전체 JournalSettings/ChartSettings init/reset/managepacks/movingaverages | 정밀 검토 완료; F08,F18,F21,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/journal_workers.py` | 부분 정밀(잔여 미검토) | 이전DailyChart/AnalysisEnrichment 범위승계; ConfirmWorker/MarketIndexBackfillWorker/HistoryWorker/BackfillWorker 전체init/run와interruption/error 분기추가 | 정밀 검토 완료; F08,F18,F19,F22,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/krx_stock_catalog_worker_controller.py` | 부분 정밀(잔여 미검토) | 전체 catalogworker start/busy/parent/completed/failed/finishedidentity | 정밀 검토 완료; F02,F10,F22,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/main_table_column_controller.py` | 정적 분석만 수행 | 전체restore/save/resize/hold/visibility/autofit/reset와properties | 정밀 검토 완료; F02,F23,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/main_table_formatting.py` | 정적 분석만 수행 | 모든format/color/background/digits/upperlimit 함수 전체 | 정밀 검토 완료; F02,F06,F09 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/main_window.py` | 부분 정밀(잔여 미검토) | 282~1113 scopeparse/compat properties/전체 constructor: concrete AppController·시계주입·writer/controller/dialog/child명령·RAM/timer/toolbar/table/signal/수명조립; 1115~2484 settings/API/news/journal/backup/Drive/update/theme/OCR/catalog caller·완료·실패·reload (기확인1240~1378/…(상세 JSON) | 정밀 검토 완료; F01,F02,F04,F05,F06,F08,F09,F10,F11,F12,F13,F14,F16,F17,F18,F19,F20,F21,F22,F23,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/main_window_components.py` | 정적 분석만 수행 | NameChangeReview/ClickableLabel/AlertSettings/NxtMarkerDelegate 전체body | 정밀 검토 완료; F02,F06,F10,F22,F23 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/main_window_layout.py` | 정적 분석만 수행 | 모든 rowheight/columnwidth/windowfit/position 함수 전체 | 정밀 검토 완료; F02 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/market_news_window.py` | 정적 분석만 수행 | MarketNewsLoadWorker init/run/localpager/cache전체; Window setup/settingsload/tabconfigure/refresh/render/detail/open/shutdown/close 전본문 | 정밀 검토 완료; F19 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/minute_history_worker_controller.py` | 정적 분석만 수행 | 전체 constructor/worker/available/is_running/set_factory/start/_release_finished_worker body; factory/type/busy 및 finished identity 분기 | 정밀 검토 완료; F08,F10,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/mock_automation_candidate_dialog.py` | 정적 분석만 수행 | 1~398 전체 fileworker execute 모든operation/publicationsummary/UIrequests/reviewinvalidate/close-stop | 정밀 검토 완료; F15,F17,F21,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/mock_automation_dialog.py` | 부분 정밀(잔여 미검토) | 1~665 전체 profiles/accountreasons/availability/worker context+shadowpaging/publication/dialog actions/render/stop | 정밀 검토 완료; F15,F16,F17,F18,F21,F23,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/morning_market_brief_dialog.py` | 정적 분석만 수행 | 1~306 전체 form/request/load/model·effort/CAS/save/run/refresh/reportHTML/close | 정밀 검토 완료; F20,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/nas_credentials_dialog.py` | 부분 정밀(잔여 미검토) | 1~439 전체 UI/profile/load/settings/prepare/apply/cancel/rename/delete/statuspoll/failure/done | 정밀 검토 완료; F14,F16,F17,F23 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/news_execution.py` | 정적 분석만 수행 | FinishedWorker/dispose_finished_worker 및3개AI허용/표시함수 전체본문 | 정밀 검토 완료; F19,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/news_settings_dialog.py` | 부분 정밀(잔여 미검토) | 1~821 전체 UI/localNASsources/planOAuth/status/models/settingsload-save/failure/done | 정밀 검토 완료; F19,F20,F23,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/news_view_model.py` | 정적 분석만 수행 | 모든display/effectivejudgment/related/AI/detail/core/body/event/label/score/escape helpers와models | 정밀 검토 완료; F19 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/news_window_coordinator.py` | 정적 분석만 수행 | 전체constructor/properties/is_visible/window_mode/ensure_started; show/send/windowgeometry sync/minimize restore/stop 전본문 | 정밀 검토 완료; F19,F22,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/news_workers.py` | 정적 분석만 수행 | NewsSearchWorker 전체source/knownload/upsert/completed/failed; NewsPrepareWorker load/filter/group/AIcache/recentcheck; NewsEvidenceWorker retry/cancel와load_stored_news_evidence의article/body/membership/event 엄격revision결합 및helpers; AINewsWorker 중앙/직접body fallback…(상세 JSON) | 정밀 검토 완료; F19,F22,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/nxt_eligibility_worker_controller.py` | 정적 분석만 수행 | 전체 constructor/worker/available/is_running/set_factory/start/_release_finished_worker body; factory/type/busy 및 finished identity 분기 | 정밀 검토 완료; F08,F10,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/process_control.py` | 정적 분석만 수행 | 전체AuxiliaryProcessManager, JsonCommandChannel/Inbox; PID alive/starttoken/identity read/state; atomiccommand, build/launch/stop helpers 전체본문 | 정밀 검토 완료; F01,F18,F19,F21,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/ranking_worker_controller.py` | 정적 분석만 수행 | 전체 constructor/worker/available/is_running/set_factory/start/_release_finished_worker body; factory/type/busy 및 finished identity 분기 | 정밀 검토 완료; F02 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/realtime_worker_controller.py` | 부분 정밀(잔여 미검토) | 이전전체body승계; optionalreference/gap/sources signal 및subscriptionfollowup/finishedidentity 전분기대조 | 정밀 검토 완료; F04,F18,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/research_dialog.py` | 부분 정밀(잔여 미검토) | 1~2261 전체 ResearchDialog/campaign registration·policy·budget·sources·claim·retry/resultpoll/DevelopmentValidation/FinalHoldout/IndependentComparison/formatting | 정밀 검토 완료; F21,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/settings_dialog.py` | 정적 분석만 수행 | 1~1184 전체 init/layout/reset/lookup/icon·audio/size/API/source/save callbacks | 정밀 검토 완료; F02,F06,F10,F22,F23,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/settings_request_worker.py` | 정적 분석만 수행 | 전체worker init/start/run/release와atexitdrain | 정밀 검토 완료; F19,F20,F22,F23,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/similar_stock_dialog.py` | 정적 분석만 수행 | 전체 dialog init/reload150msdebounce/selected/cancelall/choose/confirm_pending_name_change | 정밀 검토 완료; F02,F10,F22 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/stock_news_window.py` | 부분 정밀(잔여 미검토) | 전체constructor/delegate·setstock/prepare/search/refresh/pagination; renderchunk/select/detail/evidenceLRU/link/UI판단; AI후보/수동batch/완료저장/usage/failure·settings 적용; show/close/shutdown·windowmode/geometry/source/status helpers | 정밀 검토 완료; F19,F22,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/strategy_pack_dialogs.py` | 정적 분석만 수행 | 1~310 전체 extraction/draft/review/manager/sourceextension/versionrestore/remove/approve/packs | 정밀 검토 완료; F08,F18,F21,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/theme_colors.py` | 정밀 검토 완료 | 기존 whole-module 본문 근거 승계(해시 동일): RGB hue/밝기/invalid input fallback 모든body; MainWindow badge text_color consumer | 정밀 검토 완료; F02,F10,F22 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/theme_dialogs.py` | 정적 분석만 수행 | 1~1302 전체: unmatched resolve/preferences/preview/image/text/edit/color/bulk/suggestion/manager profiles·search·import·asyncsave | 정밀 검토 완료; F02,F10,F19,F22,F23,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/top20_market_repair_worker_controller.py` | 정적 분석만 수행 | 전체 singleworker factory/type/startlowpriority/finished identity/delete/workerproperty | 정밀 검토 완료; F09,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/top20_trade_value.py` | 정적 분석만 수행 | 1~429 전체 chart series/placeholder/view/mouse/paint/windowgeometry/date/mode/close/repair·NAS workers | 정밀 검토 완료; F09,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/trade_review_formatting.py` | 정적 분석만 수행 | 모든 analysis/news/cycle/snapshot formatting와 Protocol전체 | 정밀 검토 완료; F18 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/trade_review_view_model.py` | 부분 정밀(잔여 미검토) | 전체body build_history/build_analysis/_trade_setup_display/dataclasses | 정밀 검토 완료; F18 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `presentation/update_worker_controller.py` | 부분 정밀(잔여 미검토) | 전체 check/download workers create/start/busy/progress/failed/finishedrefs/delete | 정밀 검토 완료; F01,F23,F24 | 코드 잔여0. 실제 GUI/OS/HTTP/운영DB/계좌·주문 미실행; 정적 코드감사와 분리 |
| `research_process.py` | 부분 정밀(잔여 미검토) | prior final execution claim/cached/run/recovery/progress264-426 and CLI2191-2414; request DTO/final candidate publication1-263; final prepare/load/execute/exposure429-628; general/leader request parse/development/search validation631-789; campaign registration…(상세 JSON) | 정밀 검토 완료; F15,F21,F24 | 코드 잔여0. No live AI/provider/account/network/operating database, GUI/PG/OS acceptance execution in this code audit |
| `update_helper.py` | 정밀 검토 완료 | Prior whole-module own-body evidence retained without replay: 모든함수: safe path/extract/manifest→archive순서copy/delete→cleanup→wait parent/restart. 실제copy2 두번째실패시mixed files 진단 | 정밀 검토 완료; F01,F24 | 코드 잔여0. 기존 모듈 코드완료 근거 승계. 실제 앱/외부환경 수락은 별도 미실행. |


### 이번 대조의 교차 검토

담당3명이 각 담당 원장과 통합 JSON을 다시 대조했다. application108행/desktop119행/server124행의 핵심 scope·잔여·완료 판정에 차이가 없었다. root의 persistence initializer가 상위 패키지와 같은 basename이라는 이유로 버전 선언 설명을 잘못 공유한1행은 실제 본문을 따로 확인해 **docstring 한 줄뿐**으로 바로잡았다. 이 정정은 파일 열기만으로 업무 모듈을 완료 처리한 것이 아니라, 해당 initializer에 함수/SQL/상태/수명 책임 자체가 없음을 확인한 범위다. 하위 저장소를 완료 처리하지 않는다. 이외 관측 JSON의 임시 key↔정본 ID, F24의054/055 참조, P13 합계, 미실행062의 증거 문구를 교정했다.

진단 관측 key는 원형을 유지한다: root는`root-measurements.json`의 episode_news_reads/optional_news_fault/review_shutdown/update_copy_failure/migration_boundary, application은`observations.json`의 G-A1/G-A2/G-A3(정본057/058/059), desktop은`probes.json`의 catalog_busy_continuation/hidden_detached_reads/detached_repeat_aggregation/drive_late_dirty_ack, server는 external-poll-failure/external-roll-missing-active다. R3-062에는 실행 JSON이 없다. 경로/상태를 숫자나 이름 검색으로 다시 추정할 필요 없이 이 대응을 사용한다.


## 잔여 정밀 감사 종결 — 2026-10-18

**완료394 / 부분0 / 정적만0 / 코드 미독0**. 기존 완료25는 같은 소스 지문/명시 근거를 승계했고 잔여369의 미검토 함수와 책임을 보완했다. 실제 모든 분기를 운영 환경에서 실행했다는 뜻은 아니다. 필요한 기능이 미연결이거나 실패 계약이 잘못돼도 해당 책임을 확인해 원인·후속을 기록하면 코드감사는 완료이고, 기능 상태·수정 완료는 별개다. `scripts`174개는 이전 보조 목록과 연결 caller 조사 범위이며 이394모듈 완료 분모에 포함하지 않는다.

| 담당 | 대상 | 직전 완료 승계 | 잔여 책임 보완 | 현재 코드 잔여 |
|---|---:|---:|---:|---:|
| root: 진입·로컬 persistence |43|16|27|0|
| server: native·수집·계좌·진단 |124|1|123|0|
| desktop: transport·worker·UI |119|8|111|0|
| application/domain/research process |108|0|108|0|
| 합계 |**394**|**25**|**369**|**0**|

root가 application14개 순수/정책 모듈을 명시 인수해 보완했고 application108에 중복 없이 합쳤다. 담당자는 실제 본문/소비·실패·동시/종료의 수동 근거를 남겼고, root가 동일 경로 집합·근거 존재·완료 판정·현재 해시·신규/기존 문제 중복을 대조했다. 전체 source394 SHA256 전후 동일이며 별도 관리 계층/제품 파일 추가0이다.

최종 독립 문서 검토에서 서버124행과 신규 항목의 근거·한계에 오류가 없음을 확인했다. application 검토에서 발견한63행의 이전 `Unverified` 현재 필드는 이번 수동 호출·실패 근거로 갱신하고 이전 값은 `previous_depth_assessment`에 보존했다. desktop 검토에서 발견한 R3-072 자동 실행 함수명은 실제 `_auto_analyze_next`와 helper 호출 구간을 구분해 바로잡았다. 교정·보존·소스 불변 검증은 최종 `verification.json`에 기록한다.

### 이번 진단과 한계

| 범위 | 확인한 실행/자료 | 결과와 한계 |
|---|---|---|
| root | 기존 Windows 격리 실행기로 임시 native DB/순수 함수 반례7건 | 최종7/7, exit0·worker/자식 종료·TEMP제거. forward 두성공/덮어쓰기010, snapshot news1→0(064), stale failed가completed 덮음063, 동일뉴스5건 changes6(073), 비용배분8episode/16fills에서9pass/144visit(074), unusedprofile NameError077, awarefinalization023. 해결 검증이 아닌 현행 반례/비용 기준 |
| server | 소유 outbox 파일2조건 + 실제 _inputs/in-memory SQLite2조건 | unreadable 원본교체065; coverage SELECT3code10/20code44(078). 실제 PG/운영 파일/장중 지연 미측정 |
| application | 실제 순수 helper/서비스 + 고정 source 대역2조건 | MDD250000 vs500000ppm(067); cached NXT100/fresh200(068). 네트워크·실제 가격/주문 수락 없음 |
| desktop | legacy native schema1 + service/인자/predicate/OCR5조건 | 구형 열/index 순서069, 동일3문서 반복sync071, 인자TypeError070, NAS manualFalse/autoTrue072, OCR1장2회·2장3회080. GUI/유료AI/실제모델 미실행 |

최초 실패는 보존했다. root sandbox DB/TEMP 접근 오류와 사용자 환경의 unusedprofile close 누락에 따른 정리 WinError32, desktop의 첫 누락열 기대값/빈OCR대역·fixture close 오류를 성공으로 덮지 않았다. 진단 fixture의 정리·기대만 보완했고 제품 코드는 바꾸지 않았다. 최종 실행의 임시 자료/프로세스 종료와 원본 실패 로그는 별도로 기록한다. 기존 감사의14/21조건이나 과거 회귀 건수와 이번 결과를 고유 회귀 수로 합산하지 않는다.

### 기존 항목 보강·중복 처리

- **R3-010:** 실제 SQLite generic 문서 `_save_immutable` interleaving에서 B와 A가 모두 성공했으나 B가 덮어써짐. 기존 atomic immutable/ACK 작업의 새 실측이다. 서버 S-F01은 PG Shadow의 다른 native table에서 선행SELECT→INSERT DO NOTHING의 **first-winner 보존/loser silent ACK** 정적 후보로 확장한다. 이 PG 경합은 미실행이며 generic 덮어쓰기와 결과를 혼용하지 않는다. 각 backend/collection 동시 내용충돌·같은내용·rollback·ACK 계약을 별도로 검증한다. 새 ID 중복 등록 없음.
- **R3-023:** actual corrected aware `ranking_now`→`finalization_retry_allowed`의 빈 retry map/default naive `datetime.min` TypeError를 재현했다. MainWindow `_execution_pressure_at_entry:3863`의 같은 default 비교는 `_on_order_execution:3749` enqueue 전에 있어 일부 snapshot 경로도 막을 수 있다(그 경로는 본문 확인, 별도 실제 GUI 미실행). 기존 원거래일 실패 payload와 다른 시계 경계이며 둘 다023 범위다.
- **R3-025/032:** validation client/recorder unowned daemon, CentralRealtimeWorker fallback/validation stop 실패 시 참조 해제, CentralJournalSyncRunner daemon·ThemeDispatcher timer cancel만의 수명 범위를 보강한다. StrategyPackManager 추출 중 row 삭제/닫기/재추출 및 callback row identity는 D-F07의 정적 동시/종료 가설로025의 좁은 후속 검사에 남긴다. 실제 thread 유실/운영자료 손실로 단정하지 않는다.
- **R3-027:** MainWindow live-only render에서도 다른 기간 집계/quality를 계산하는 내부 범위를 보강한다. 기존 TOP20 same-cut 측정과 같은 항목으로 재등록하지 않는다.
- **R3-011/012/013/014/015/017/024/041/054~059:** 설정 개별set·호환 property·worker busy/identity·실제 API 조립·연구 capability/원본단위·ACK 및 이미 알려진 일지 read 경계의 기존 근거를 승계한다. AppController/QueryStore/native·Gateway·Runtime·Lifecycle의 필요성과 내부 책임의 적절성은 별도로 평가한다. 순수 위임을 늘리는 새 Manager/Service로 분리하지 않는다.
- **반증/제한:** NAS 뉴스60초는 DB prepare이며 동일기사쓰기 주기로 오인하지 않음. Theme profile 삭제 뒤 UI가 명시 새profile을 선택함. proposal import와 종목theme 적용 callback은 별개다. `MarketCacheWriter`는 빈queue에서 계속 대기하므로 idle exit 가설은 반증. `BROKER_FILL_AGGREGATE.quantity`는 증가분이어서 unresolved 합산의 누적중복 가설도 반증. staging delete DB fence는0.25초 제한/소유·참조검사를 갖추므로 무제한 잠금으로 등록하지 않음.

### 새 후보 우선순위와 다음 실행

신규063~082는 전부 **대기/미해결/제품 변경0/commit 없음**. P1은 원본 보존·종료/terminal 정합성, P2는 확인된 연결과 반복 DB/동기화 비용, P3는 낮은 빈도 비용·구조 또는 추가 측정, P4는 실행비용0의 정리다. 차트 paint081·옛 layout082는 기존055 setter/read·028 import와 trigger/삭제 대상이 달라 별도 ID로 분리했다. generic immutable/PG native immutable은 같은 기존010 계약 안에서 범위를 명시했다.

**다음 구현은 기존 R3-002+023의 원거래일 실패 재전달부터**다. 기존 COMMIT3→1 구현을 다시 만들지 않는다. 그 뒤 보존/수명 P1(034~037/042/044/052/056/063/065/066), 장중·반복 비용, 국소 연결 복구와 연구 수치의 검증 위험을 함께 비교해 작은 실행 경로를 고른다. new063/067/068은 같은 연구 결과/입력 소비를 조정하되 자동 재승인·기존 결과 덮어쓰기를 하지 않는다.

G01~G09의 코드 미독 조사는 종료했다. 추가 전체 재조사 없이 항목의 기준/반례→최소 변경→관련 통합·계약/비용 비교→해당 F 행 동반 갱신으로 이어간다. actual PG/OS/GUI/provider/다중PC·장중·계좌 수락은 각 `runtime_unverified`/완료 기준에 남으며, 필요한 해당 항목 검증만 수행한다. 운영 DB·실계좌 주문·배포가 필요한 것으로 임의 확대하지 않는다.

### 신규 R3 상세 계약

### R3-063 — 연구 실행의 지연 실패·취소가 완료 상태를 덮어쓸 수 있음

1. **R3-063 / 연구 실행의 지연 실패·취소가 완료 상태를 덮어쓸 수 있음**. 분류 재설계; 영향 F21, F24
2. 실제 경로: research_process/scripts.run_research → ResearchRepository.start_run/finish_run/fail_run/cancel_run → research_runs → 결과·비교 UI
3. 원인·본문 근거: research_repository.py:2695/3084/3098의 일반 run 전이는 SELECT 상태 확인 뒤 WHERE run_id만으로 UPDATE한다. 조회와 DML 사이에 상태가 바뀌어도 조건을 재검증하지 않는다. owned final/campaign/search의 별도 fence 전체가 무효라는 뜻은 아니다.
4. 실측·미검증: 실제 임시 SQLite에서 fail_run을 상태 SELECT 뒤 정지 → 다른 repository.finish_run 완료 → 옛 fail_run 재개 결과 completed→failed, 완료 hash는 남는다. 소유 thread join 완료. 운영 발생률·파일 결과와의 실제 불일치는 미측정.
5. 권장 개선·범위: 기존 run 저장 경계에서 terminal 상태 확인과 변경을 같은 transaction/CAS로 처리하고 stale 전이 결과를 반환한다. 새 scheduler나 승인 정책을 만들지 않는다.
6. 효과·회귀 위험: 완료/취소/실패 정합성 개선. 재개·멱등 실행·이미 저장된 immutable 결과·명시 orphan 취소 계약을 보존해야 한다.
7. 의존 관계: R3-046/024와 다른 native 상태 전이 문제. R3-010은 일반 문서 immutable 저장 경계로 별도. 연구 DB 변경은 단독 진행하고 결과 게시·UI 소비 동반 확인.
8. 우선순위 **P1**, 상태 **대기**. 제품 변경 없음 / 해결 검증 없음 / commit 없음.
9. 완료 기준·검증: 동일 native 중간정지 반례와 역순·중복완료·cancel/failed 재개·commit 전후 중단을 검증한다. 완료 hash/상태/실제 결과 게시가 일치하고 stale writer가 성공으로 보고하지 않아야 한다.

- 코드/운영 구분: 원인·본문 감사 완료, **해결·운영 정상 검증은 미완료**. 정본 분류 **재설계 / P1 / 대기**. 영향 F21, F24.

### R3-064 — 체결 스냅샷 재전달이 이미 보강한 뉴스를 비움

1. **R3-064 / 체결 스냅샷 재전달이 이미 보강한 뉴스를 비움**. 분류 재설계; 영향 F18, F24
2. 실제 경로: 실시간 order_execution → MainWindow._on_order_execution:3709 → EntrySnapshotWriter.enqueue/run → JournalSnapshotRepository.save_entry_snapshot; news backfill → save_snapshot_news_backfill → 일지 소비
3. 원인·본문 근거: journal_snapshot_repository.py:save_entry_snapshot의 ON CONFLICT가 news_json을 incoming 값으로 무조건 교체한다. MainWindow는 실행키 RAM 중복검사 없이 이벤트를 enqueue하며 초기 news가 비어 있을 수 있다. 투자자 수급의 backfilled 보존 CASE와 뉴스 정책이 다르다.
4. 실측·미검증: 실제 임시 일지 DB에서 원본 저장→뉴스 보강1건→같은 원본/key 재저장 후 뉴스1→0. 모든 중복의 영구 유실은 미입증: writer의 후속 재보강이 복구할 수 있다. 실제 duplicate 공급 빈도·운영 손실은 모른다.
5. 권장 개선·범위: 같은 실행키의 원본 재시도와 나중의 보강 갱신을 구분해 이미 확인한 보강 자료를 보존한다. 원본 관측을 사후 뉴스로 위장하지 않고 값·provenance를 함께 검증한다.
6. 효과·회귀 위험: 재시도 멱등성과 뉴스 보존. 실제 새로운 원본 뉴스가 들어오는 갱신·빈 뉴스의 명시 의미·account scope·다른 보강 필드와 metadata 불일치 위험이 있다.
7. 의존 관계: R3-023의 aware entry-pressure 예외가 현재 특정 입력에서 enqueue를 막으므로 그 복구 뒤 실제 수신 연결을 함께 검증한다. R3-030/031은 writer, 051은 read-side로 중복 등록하지 않는다.
8. 우선순위 **P2**, 상태 **대기**. 제품 변경 없음 / 해결 검증 없음 / commit 없음.
9. 완료 기준·검증: 같은 key 중복·원본/뉴스 보강 순서 교차·수급/프로그램 보강·다른 계좌 동일 브로커키·DB 실패 재시도를 actual writer/native 재조회로 확인한다.

- 코드/운영 구분: 원인·본문 감사 완료, **해결·운영 정상 검증은 미완료**. 정본 분류 **재설계 / P2 / 대기**. 영향 F18, F24.

### R3-065 — outbox 읽기 실패를 빈 상태로 삼아 기존 pending 파일 교체

1. **R3-065**. **대상/분류:** `persistent_outbox.py:JsonRecordOutbox.load:18/put:34/remove:40/_write:48`, 재설계. F09/F24. 정본 분류 **재설계**, 영향 F09, F24. 파일: `src/kiwoom_monitor/central_server/persistent_outbox.py`.
2. **실제 경로:** AutonomousTop20Service.__init__:133→start:199 outbox.load→pending index 복원; _queue_index→outbox.put→native dataset save→outbox.remove. app은 outbox_path를 제공한다.
3. **원인/근거:** load가 missing뿐 아니라 OSError/JSON/type/최상위 non-dict를 모두{}로 반환한다. put은 그 결과에 새 key만 넣어 atomic replace하므로 기존 unreadable pending 내용은 확인 없이 교체한다. remove는 load={}일 때 no-op이어서 그 호출만으로 overwrite한다고 주장하지 않는다.
4. **확정/측정/가설:** 실제 JsonRecordOutbox+소유 local fixture로 malformed 원본이 새 내용으로 교체되는 결과, valid old pending에 **read_text OSError만 주입**한 put이 old key를 잃는 결과를 safe-probes.json에 기록했다. 실제 operating file/전원 장애/PG 데이터 손실/발생빈도는 미검증이다.
5. **최소 방법:** missing과 unreadable/corrupt를 구분해 현재 owner에게 실패를 전달하고 기존 bytes를 보존한다. 기존 retry/close 계약으로 복구하며 새 archive service/파일 format을 만들지 않는다.
6. **효과/위험:** 확인되지 않은 원본/재시도 payload의 silent replacement 방지. 시작 실패 정책·write 실패 pending 보존·사용자 명시 복구·동일 instance lock/atomic rename·close가 함께 검증돼야 한다.
7. **의존/중복:** R3-035/036과 같은 Top20 outbox 소비자이나 flush 경합과 다른 read-error→replace 원인이다. 동일 파일 수정은 serial로 조정한다. R3-025 종료 수락과 연계한다.
8. 정본 우선순위 **P1 / 대기**. 제품 변경 없음 / 해결 검증 없음 / commit 없음. **우선/상태:** P1 / 대기(원인·소유 fixture 재현 확보).
9. **완료/검증:** missing은 empty 유지, malformed/non-dict/IO실패는 원본 bytes 보존 및 명시 실패, put/remove 정상+retry·Top20 start/lateACK/close 통합. 실제 NAS/운영 file 변경 없음.

- 코드/운영 구분: 원인·본문 감사 완료, **해결·운영 정상 검증은 미완료**. 정본 분류 **재설계 / P1 / 대기**. 영향 F09, F24.

### R3-066 — real account event 종료 실패가 정상 close로 반환

1. **R3-066**. **대상/분류:** `real_runtime.py:_write_account_events:365`, `_drain_account_reads:259`, `_close:1059`; 재설계. F18/F23/F24. 정본 분류 **재설계**, 영향 F18, F23, F24. 파일: `src/kiwoom_monitor/central_server/real_runtime.py`.
2. **실제 경로:** real00/04/account-only 또는 shared market routing→context event_queue/pending_event→native save_real_account_event→journal/read API. app close→RealRuntimeOwner.close가 closing=true→drain→account queries/broker/probe close→bundles/revisions clear.
3. **원인/근거:** writer 저장 예외에서 stop.is_set이면 pending_event 유지 후 return(:383). drain은 errors/잔여 검사에 모두 `not self._closing` 조건(:277/:280)을 둔다. 실제 종료는 항상 closing=true라 pending 또는 writer 예외가 있어도 drain 성공으로 처리할 수 있다. pending은 context RAM에 남지만 bundle을 퇴역하고 재시도 task를 재개하지 않는다.
4. **확정/측정/가설:** 오류+stop+closing 분기 인과는 정적 확정. 실제 운영 데이터 손실·저장 장애·모든 concurrent close는 **미재현**. FILL 원장 전체 손실이나 주문 전송 오류로 확대하지 않는다.
5. **최소 방법:** 기존 owner 안에서 close의 durable event ACK/명시 실패 계약을 정한다. 종료 admission을 닫되 실제 native 작업 종료/미저장 pending을 성공과 구분하고 안전한 재시도 owner를 보존한다. 신규 queue/service/운영 policy 금지.
6. **효과/위험:** 정상 종료와 미저장 상태 구분. 영구 장애에서 무한 shutdown/의존 broker 조기 close·event generation/binding/settings fence·duplicate event 멱등 계약 주의.
7. **의존/중복:** R3-025 넓은 수명 유지의 새 구체 실패,031은 PC backfill writer라 별개. root close_services failure-preserve와 조정한다. 계좌/주문 안전 gate를 제거하지 않는다.
8. 정본 우선순위 **P1 / 대기**. 제품 변경 없음 / 해결 검증 없음 / commit 없음. **우선/상태:** P1 / 대기(격리 failure fixture 먼저).
9. **완료/검증:** 소유 fake/native temporary store에서 in-flight late commit·permanent fail·retry success·pending queue·반복/취소 close를 재현하고 성공 시 durable row, 실패 시 의존 owner 보존/명시 실패를 확인. 운영 계좌/실송신 없음.

- 코드/운영 구분: 원인·본문 감사 완료, **해결·운영 정상 검증은 미완료**. 정본 분류 **재설계 / P1 / 대기**. 영향 F18, F23, F24.

### R3-067 — 최대 낙폭 ppm을 낙폭 시점의 peak가 아닌 이후 최고자산으로 나눔

1. **R3-067 / 최대 낙폭 ppm을 낙폭 시점의 peak가 아닌 이후 최고자산으로 나눔**. 분류 재설계; 영향 F15, F17, F21
2. 실제 경로·파일: src/kiwoom_monitor/application/research_evaluation.py:_maximum_drawdown:2956-2960; mock_automation_candidate.py:evaluate_candidate_eligibility:239,250; 같은 fold의 낙폭 뒤 자산이 새 고점을 만드는 경우. equity 100 → 50 → 200.
3. 원인·본문 근거: maximum abs drawdown 50은 유지하나 최종 peak200으로 나누어250000ppm 반환. 실제 최대 peak-relative 낙폭은500000ppm. probe.py actual helper 출력 observations.json 보존.
4. 실측·미검증: maximum abs drawdown 50은 유지하나 최종 peak200으로 나누어250000ppm 반환. 실제 최대 peak-relative 낙폭은500000ppm. probe.py actual helper 출력 observations.json 보존.; 실제 운영·GUI·주문/외부 제공자는 미실행.
5. 권장 개선·범위: 수정/재설계 P2: _maximum_drawdown에서 각 시점 peak-relative 비율의 최대값을 따로 축적. 절대 낙폭 값과 기존 보고서 스키마 보존.
6. 효과·회귀 위험: fold report max_drawdown_ppm과 이를 그대로 비교하는 candidate maximum_drawdown_ppm eligibility가 낙폭을 과소평가할 수 있음. 실제 매매/운영 결과는 미실행. research report/final candidate eligibility 및 이전 결과와의 수치 비교에 영향. 과거 저장 결과 자동 덮어쓰기 금지. R3-018 evaluator 소유권과 다른 계산 오류이며 R3-001~062의 동명 후보 없음.
7. 의존 관계: research report/final candidate eligibility 및 이전 결과와의 수치 비교에 영향. 과거 저장 결과 자동 덮어쓰기 금지. R3-018 evaluator 소유권과 다른 계산 오류이며 R3-001~062의 동명 후보 없음.
8. **P2 / 대기**, 제품 변경·해결 검증·commit 없음.
9. 완료 기준·검증: 100/50/200, 계속 하락, 새 고점 후 두 번째 낙폭, 무이벤트/0peak를 고정 입력으로 확인하고 candidate threshold250000~500000 사이에서 BLOCKED가 되는 실제 연결 검증. 현재는 오류 재현만 완료, 제품 수정 없음.

- 코드/운영 구분: 원인·본문 감사 완료, **해결·운영 정상 검증은 미완료**. 정본 분류 **재설계 / P2 / 대기**. 영향 F15, F17, F21.

### R3-068 — NXT 포함 사상고가의 캐시 증분 경로가 KRX만 조회

1. **R3-068 / NXT 포함 사상고가의 캐시 증분 경로가 KRX만 조회**. 분류 재설계; 영향 F10
2. 실제 경로·파일: src/kiwoom_monitor/application/historical_high_service.py:load:66-67,_load_incremental:84-130; bootstrap.py:306-315; presentation/main_window.py:_on_historical_high_received:4406-4419; 직접 모드의 include_nxt=True + 기존 evidence cache, NXT만 새 고가, KRX/보조 high250은 이전 이하. NAS stored-loader가 target을 바로 반환하거나 high250에 새NXT고가가 이미 있으면 해당 재현 조건에 속하지 않음.
3. 원인·본문 근거: load가 cache evidence 있으면 incremental 선택. 그 함수는 include_nxt를 쓰지 않고 원 code의 yearly/monthly/daily만 조회. 동일 fixed-day fake source에서 cache 경로100/요청KRX1회, fresh 경로200/KRX+NXT4회. bootstrap 실제 direct factory가 해당 구성 사용.
4. 실측·미검증: load가 cache evidence 있으면 incremental 선택. 그 함수는 include_nxt를 쓰지 않고 원 code의 yearly/monthly/daily만 조회. 동일 fixed-day fake source에서 cache 경로100/요청KRX1회, fresh 경로200/KRX+NXT4회. bootstrap 실제 direct factory가 해당 구성 사용.; 실제 운영·GUI·주문/외부 제공자는 미실행.
5. 권장 개선·범위: 수정/통합 P2: 기존 fresh/증분의 market별 수집 책임을 일치시켜 NXT 포함 contract를 증분에도 보존. 시장 출처/조정주가 근거를 보존하며 단순 KRX 결과를 통합고가로 확정하지 않음.
6. 효과·회귀 위험: 신규 NXT 최고가가 price/evidence에 반영되지 않은 target을 UI/StockRepository checked_on에 저장할 수 있어 당일 재조회가 생략됨. 운영 종목 발생 빈도는 미측정. 추가 NXT 조회/증분 비용과 오래된 evidence의 시장 출처 호환을 검토. NAS authoritative fresh adapter 경로는 별도 유지. 기존 R3-023 시계,034 reference retry,002 pricebatch와 중복 아님.
7. 의존 관계: 추가 NXT 조회/증분 비용과 오래된 evidence의 시장 출처 호환을 검토. NAS authoritative fresh adapter 경로는 별도 유지. 기존 R3-023 시계,034 reference retry,002 pricebatch와 중복 아님.
8. **P2 / 대기**, 제품 변경·해결 검증·commit 없음.
9. 완료 기준·검증: 같은 cached/fresh fake-source parity, KRX-only/NXT-only 증가, 기존 최고NXT와 수정주가 이벤트, NXT 실패/고가250 보조·checked_on 저장을 관련 worker/UI 경계로 검증. 현재는 오류 재현만 완료, 제품 수정 없음.

- 코드/운영 구분: 원인·본문 감사 완료, **해결·운영 정상 검증은 미완료**. 정본 분류 **재설계 / P2 / 대기**. 영향 F10.

### R3-069 — 기존 과거뉴스 스키마의 후행 열을 먼저 참조하는 index

1. **R3-069**. 대상/분류: `infrastructure/historical_backfill.py:initialize_probe_database` / **재설계**, P2(국소 legacy 자료 복구), 대기. 정본 분류 **재설계**, 영향 F19, F21, F22. 파일: `src/kiwoom_monitor/infrastructure/historical_backfill.py`.
2. 실제 경로: 과거뉴스 archive/import/복원 준비 → initializer1466 → expression index1521~1523 → original_url/portal_url ALTER1678/1682 → 후속 writer/readiness/UI. F19/F21/F22.
3. 원인/코드 근거: COALESCE(NULLIF(original_url...),portal_url...) index를 열 추가 전에 CREATE한다.
4. 실측/가설: actual 임시 legacy형 DB에서 portal_url 누락 오류/추가열0 재현. 운영 archive가 이 schema인지·영향건수는 미확인.
5. 최소안: 기존 열 migration 뒤 동일 index 생성. 새 schema/자료조건·관리계층 추가 불필요.
6. 효과/위험: 기존 자료를 지우지 않고 기동 migration 연결 복구. partial schema/이미 index 있음/실패 rollback·보존 위험.
7. 의존/중복: R3-023 시간변환과 별개 schema 순서. 기존001~062 동일 문제 없음. 운영자료 수정 선행 불필요, fixture로 먼저 검증.
8. 정본 우선순위 **P2 / 대기**. 제품 변경 없음 / 해결 검증 없음 / commit 없음. 범위/상태: initializer 순서와 관련 임시DB 계약 검사만; 조사·재현 완료/제품 대기. 담당 P1 제안은 운영 구형 DB 영향 미확인과 장중 작업 우선순위를 고려해 P2로 통합했다.
9. 완료/검증: fresh/기존형/일부열/기존index DB 모두 같은 논리행·index 보존, 주입실패/재시작 후 재실행, archive/import/readiness 관련 회귀. 커밋 없음.

- 코드/운영 구분: 원인·본문 감사 완료, **해결·운영 정상 검증은 미완료**. 정본 분류 **재설계 / P2 / 대기**. 영향 F19, F21, F22.

### R3-070 — 제외종목 찾기의 부모 인자가 themes로 전달됨

1. **R3-070**. 대상/분류: `SettingsDialog._find_theme_excluded_stock:735~746` → `SimilarStockDialog.__init__:24~43` / **재설계**, P2, 대기. 정본 분류 **재설계**, 영향 F02, F06, F22, F23. 파일: `src/kiwoom_monitor/presentation/settings_dialog.py` / `src/kiwoom_monitor/presentation/similar_stock_dialog.py`.
2. 실제 경로: MainWindow 기본설정 → 표시형식 → 제외 종목 목록의 종목 찾기 →739 생성자 → themes join → 후보선택 → 목록저장/테마합계. F02/F06/F22/F23.
3. 원인/근거: `(lookup, "", self)`와 `(lookup, original_name, themes=(), parent=None)`의 positional 불일치.
4. 실측/가설: 실제 caller+signature bind에서 self가 themes, join TypeError 확인. actual QWidget constructor/GUI 전체 실행은 하지 않았다.
5. 최소안: 기존 생성자 계약대로 `parent=self` 전달. 검색/제외 정책 추가 없음.
6. 효과/위험: 클릭 경로 복원. 다른 theme import caller는 명시 themes를 유지해야 한다.
7. 의존/중복: R3-013 controller aliases나 catalog054와 다른 caller; stock lookup 계약 유지.
8. 정본 우선순위 **P2 / 대기**. 제품 변경 없음 / 해결 검증 없음 / commit 없음. 범위/상태: 한 caller 인자 및 관련 연결검사, 제품 변경0.
9. 완료/검증: 버튼 caller→dialog 후보선택→현재 제외목록 중복 없는 추가→설정저장→메인 일반/제외 합계 반영; 취소/검색없음/기존 테마 import 인자 회귀. 커밋 없음.

- 코드/운영 구분: 원인·본문 감사 완료, **해결·운영 정상 검증은 미완료**. 정본 분류 **재설계 / P2 / 대기**. 영향 F02, F06, F22, F23.

### R3-071 — JournalSync의 동일 자료 전량 왕복

1. **R3-071**. 대상/분류: `infrastructure/central_journal_sync.py:CentralJournalSyncService.sync95~189/_rows_for_spec502~518` / **제거**, P2(자료량에 따라 재평가), 대기. 정본 분류 **제거**, 영향 F18, F22, F24. 파일: `src/kiwoom_monitor/infrastructure/central_journal_sync.py` / `src/kiwoom_monitor/journal_process.py`.
2. 실제 경로: journal_process의 sync schedule → CentralJournalSyncRunner.schedule → service.sync → collection load_all → merge → native SELECT* → documents 전체 upsert → related journal UI/계좌범위. F18/F22/F24.
3. 원인/근거: sync134/142/144는 매 collection 전량 load_all·native rows·upload_documents이며 journal_sync_states146~184도 전체 읽기/전송이다. generic content manifest/hash delta와 다른 경로다.
4. 실측/가설: actual service/3행 임시DB에서 두 번 모두 upsert3, 둘째 load_all3, payload 동일. 실제 HTTP bytes/행 수·주기·장중 지연은 미측정.
5. 최소안: 기존 journal identity/revision/tombstone owner를 쓰는 변경집합 경로를 조사해 동일문서 재전송부터 줄인다. 새 독립 polling/cache/임의authority는 만들지 않는다.
6. 효과/위험: 반복 네트워크 직렬화/native읽기 감소 가능. 다중PC edit, tombstone deletion, legacy/v2 account scope·충돌해결·ACK 손실·원장 authority 위험이 있다.
7. 의존/중복: **R3-021은 이미 구현된 CentralContentSyncService delta 유지**이므로 이를 완료 근거로 journal fullsync를 종료하면 안 된다. R3-044/056의 ACK 후속 변경 보호·R3-029 revision 관측과 관련,021의 journal 하위 미완료로 보강하거나 별도ID 권고.
8. 정본 우선순위 **P2 / 대기**. 제품 변경 없음 / 해결 검증 없음 / commit 없음. 범위/상태: 전체 sync body/state/consumer 감사 완료, local3행 비용 측정, 제품 대기.
9. 완료/검증: 동일한 native/remote 계약 fixture의 두 번째 upsert0 또는 실제 변화만, 정상merge/tombstone/v1v2/partialfailure/retry/다중PC계약 보존; 전송·read·SQL 비용 구분. 커밋 없음.

- 코드/운영 구분: 원인·본문 감사 완료, **해결·운영 정상 검증은 미완료**. 정본 분류 **제거 / P2 / 대기**. 영향 F18, F22, F24.

### R3-072 — NAS-only AI의 수동 버튼과 실행 경로 가용성 불일치

1. **R3-072**. 대상/분류: `StockNewsWindow._show_detail:846~852`, `_start_ai_groups973~1004`, `_auto_analyze_next(1041~1072; 가용성 helper 호출1051~1056)`, `news_execution.automatic_ai_run_allowed29~46` / **통합**, P2, 대기. 정본 분류 **통합**, 영향 F19, F23. 파일: `src/kiwoom_monitor/presentation/stock_news_window.py` / `src/kiwoom_monitor/presentation/news_execution.py`.
2. 경로: NAS 설정으로 provider/model mirror → 뉴스선택 → 수동 AI 버튼; 실제 worker는 central_client를 받고 서버 AI를 사용한다. F19/F23.
3. 원인/근거: 수동 버튼851은 local_credentials_configured 필수, 자동helper는 local credential 또는 central client를 허용한다.
4. 실측/가설: 현재 enable expression/actualhelper fixture에서 NAS만키보유 manualFalse·autoTrue 확인. GUI 클릭/실제AI 미실행. `_on_ai_finished1157`의 별도 enable과 셀 double-click 경로도 있어 **전체 NAS AI 미연결**로 단정하지 않는다.
5. 최소안: 기존 actual execution의 가용성 계약과 수동 버튼 표시를 맞춘다. 새 계정승인/과금조건 추가 없음.
6. 효과/위험: NAS-only 수동 기능 연결. local/key없음/provider none/daily limit/busy·ChatGPT계정별권한 보존 필요.
7. 의존/중복: 기존 R3-019의 조용한오류/뉴스 read보다 구체적 가용성 분기이며 R3-024 부분연결 보강 가능. 현재 실제 central_client 조립과 workerbody는 확인했다.
8. 정본 우선순위 **P2 / 대기**. 제품 변경 없음 / 해결 검증 없음 / commit 없음. 범위/상태: predicate 재현 및 경로확인/제품 대기. F19 **부분 연결** 근거, 정상 승격 없음.
9. 완료/검증: local/NAS-key-only/provider-none/busy/afterfinished/doubleclick/automatic 동일 계약과 실제 worker central 선택 확인; 네트워크 없는 관련 UI 계약 회귀. 커밋 없음.

- 코드/운영 구분: 원인·본문 감사 완료, **해결·운영 정상 검증은 미완료**. 정본 분류 **통합 / P2 / 대기**. 영향 F19, F23.

### R3-073 — PC 뉴스의 동일 기사 재수신도 전체 article 행 UPDATE

1. **R3-073 / PC 뉴스의 동일 기사 재수신도 전체 article 행 UPDATE**. 분류 제거; 영향 F19, F22
2. 실제 경로: StockNewsWindow 초기·종목 재선택/강제 refresh/load_more → NewsSearchWorker.run:119~130 → StockNewsRepository.upsert → prepare/load/그룹 표시
3. 원인·본문 근거: stock_news_repository.py:upsert는 기존 identity 전체 SELECT 후 동일 기사도 모든 열 UPSERT UPDATE한다. checked_at/naver_checked_at의 확인 시각 갱신은 별도 의미이며 보존 대상이다. NAS 동일종목 60초 timer는 로컬 prepare여서 이 쓰기 발생 주기로 주장하지 않는다.
4. 실측·미검증: 실제 임시 SQLite의 같은 기사5건 재저장: 반환 신규0, 기사 내용 동일, SELECT1/row changes6(기사5+sync1). 물리 WAL/장중 지연·실제 호출 빈도는 미측정.
5. 권장 개선·범위: 내용이 같은 기사 행의 UPDATE만 생략하고 확인 receipt·새 AI 판단/수정 기사·보존용 journal pin·정리 정책을 유지한다. RAM 캐시나 새 저장 계층을 추가하지 않는다.
6. 효과·회귀 위험: 반복 SQL 반영 감소. NULL/시각 직렬화·원문수정·판단 변경·중복 페이지·삭제/보존 계약에 대한 비교가 필요하다.
7. 의존 관계: R3-019는 서버 수집 결과 재조회, R3-021은 콘텐츠 delta로 다른 저장 경계. news child 설정/원본 sync와 같은 native DB는 직렬 통합.
8. 우선순위 **P2**, 상태 **대기**. 제품 변경 없음 / 해결 검증 없음 / commit 없음.
9. 완료 기준·검증: 동일5건에서 article 반영0/sync1과 값 동일, 한 열 변경1건·신규·retention/pin·페이지 재수신·worker→native→prepare 연결을 확인한다.

- 코드/운영 구분: 원인·본문 감사 완료, **해결·운영 정상 검증은 미완료**. 정본 분류 **제거 / P2 / 대기**. 영향 F19, F22.

### R3-074 — 일지 summary·statistics가 같은 복기와 비용 분모를 반복 조회·계산

1. **R3-074 / 일지 summary·statistics가 같은 복기와 비용 분모를 반복 조회·계산**. 분류 통합; 영향 F18, F22
2. 실제 경로: JournalWindow._apply_history_filters → _render_summaries(load_reviews batch) → _refresh_statistics(episode별 load_review); TradeReviewViewModel.build_trade_history_summary_view_model → 매 episode allocate_episode_cost
3. 원인·본문 근거: journal_process.py:1266/1506에서 이미 batch로 읽은 reviews를 전달하지 않고 통계가 N connection/SELECT로 다시 읽는다. trade_cost_service.allocate_episode_cost는 각 episode마다 동일 all_fills 분모와 cost_map을 재생성한다. 모두 GUI rendering 경로다.
4. 실측·미검증: 실제 view-model + typed fills/episodes의 계수 fixture: episode8/fills16에서 전체 fill iterable9회/144회 방문(배분8회+표시일1회). 통계 N native read는 본문상 호출 수이며 이번 별도 SQL 실행·CPU wall time은 미측정.
5. 권장 개선·범위: 한 렌더의 reviews와 account/date/code/side별 비용 분모를 한 번 만들고 요약/통계/각 episode에 공유한다. 기존 함수/모델에 필요한 값을 전달하며 새 cache/service를 만들지 않는다.
6. 효과·회귀 위험: N 재조회와 O(episode×전체fills) 계산을 줄일 수 있다. 부분기간 매도에 필요한 lookback/계좌·origin/canonical·실제비용 배분/반올림·편집 직후 새 render 일관성 보존.
7. 의존 관계: R3-051/055/059와 같은 일지 UI 파일이므로 병렬 수정하지 않는다. R3-049는 NAS Risk 계산으로 다른 owner.
8. 우선순위 **P2**, 상태 **대기**. 제품 변경 없음 / 해결 검증 없음 / commit 없음.
9. 완료 기준·검증: actual UI caller→native read count와 모델 출력/정산배분을 전후 비교. 복기 편집·필터·다중 계좌·미정산/보유 매매를 포함하고 DB read와 fill 방문 수를 별도로 측정.

- 코드/운영 구분: 원인·본문 감사 완료, **해결·운영 정상 검증은 미완료**. 정본 분류 **통합 / P2 / 대기**. 영향 F18, F22.

### R3-075 — 표시하지 않는 오늘 일지 페이지가 초마다 하루 봉을 복사·저장·재표시

1. **R3-075 / 표시하지 않는 오늘 일지 페이지가 초마다 하루 봉을 복사·저장·재표시**. 분류 재설계; 영향 F08, F18, F24
2. 실제 경로: JournalWindow.__init__의 미등록 _live_page + _refresh_timer1초 → set_stock/_live_code → refresh → import_live_bars → upsert_bars/metadata → load_bars/load_chart_bars → 숨김 table/chart 렌더
3. 원인·본문 근거: journal_process.py:465~571은 live page를 tabs에 추가하지 않지만 보관하고 타이머를 시작한다. refresh:2164는 _live_code만 검사해 같은 날 전체 monitor 봉을 매번 읽어 native UPSERT/metadata를 하고 숨긴 widget을 재구성한다.
4. 실측·미검증: 등록/본문/최종 소비 인과 확인. 하루 봉수×1초 반복 SQL·metadata·paint 비용은 이번 실측하지 않았으며 운영 병목으로 확정하지 않는다. 화면이 숨겨졌다는 이유만으로 수집/보존을 중단해도 된다고 판단하지 않는다.
5. 권장 개선·범위: 숨김 widget의 무소비 렌더는 제거하고, 필요한 journal 봉 복사의 실제 소비·보존 주기를 먼저 확인해 신규/변경분 또는 명시 소비 시점에 갱신한다. visible history 분석/분봉 확인·원본 수집은 보존한다.
6. 효과·회귀 위험: 순수 표시 낭비와 반복 DB 작업 감소 후보. monitor retention 이후 일지 봉 보존·확정봉 권한·현재 selection/다른 차트 소비를 잃을 위험이 있어 측정·계약부터 좁힌다.
7. 의존 관계: R3-055는 별도 detached chart, 027은 TOP20로 중복 아님. 같은 journal UI/봉저장 수정은 R3-051/052/055/074와 직렬 조정.
8. 우선순위 **P2**, 상태 **대기**. 제품 변경 없음 / 해결 검증 없음 / commit 없음.
9. 완료 기준·검증: 실제 hidden/live/history 소비의 read/write/render 계수 기준 확보 후 동작 비교. 확정봉·일자변경·main 선택·visible history·close/drain 보존을 확인한다.

- 코드/운영 구분: 원인·본문 감사 완료, **해결·운영 정상 검증은 미완료**. 정본 분류 **재설계 / P2 / 대기**. 영향 F08, F18, F24.

### R3-076 — 테마와 종목 저장소의 catalog 판단 중복 및 import 행별 조회

1. **R3-076 / 테마와 종목 저장소의 catalog 판단 중복 및 import 행별 조회**. 분류 통합; 영향 F10, F22, F23
2. 실제 경로: ThemeDialog OCR/Excel/manual → match_theme_rows/resolve/preview_theme_changes → StockRepository 또는 ThemeRepository → replace_many
3. 원인·본문 근거: StockRepository의 find_code/name candidates/concatenated matching/pending review와 ThemeRepository:178~264가 같은 공용 catalog/alias/namehistory 판단을 복제한다. import match/preview는 각 행마다 native name/themes 조회를 다시 연다. 동일 책임인데 진입점별 소유가 둘이다.
4. 실측·미검증: 양쪽 실제 본문과 ThemeDialog:165/1097/1138의 소비를 대조했다. 행수별 DB 호출 인과는 확인했으나 이번 native SQL/장중 시간은 미측정. 이 import는 사용자 작업으로0B hot path가 아니다.
5. 권장 개선·범위: 이미 주입된 stock lookup에 catalog 책임을 모으고 ThemeRepository의 중복 catalog API 제거를 먼저 검토한다. 한 import 안의 name/alias/theme 자료는 필요한 묶음으로 한 번 조회한다. 새 인터페이스/manager를 만들지 않는다.
6. 효과·회귀 위험: 정책 복사·변경 영향과 대량 import 조회 감소. 사용자 승인 과거명/현재 다른 종목의 이름 선점방지·OCR fuzzy 수동확정·프로필 scope/테마 before/after 보존.
7. 의존 관계: R3-013은 MainWindow property alias로 별개다. R3-029/054 및 신규 제외종목찾기 연결 복구와 actual UI consumer 조정. 파일수/호출깊이를 늘리는 위임 분리는 금지.
8. 우선순위 **P3**, 상태 **대기**. 제품 변경 없음 / 해결 검증 없음 / commit 없음.
9. 완료 기준·검증: 각 입력 진입점의 동일결과/취소/별칭 승인/다중프로필 및 SQL 횟수·파일/호출깊이 비교. 실제 단일 catalog owner와 bulk apply transaction 유지.

- 코드/운영 구분: 원인·본문 감사 완료, **해결·운영 정상 검증은 미완료**. 정본 분류 **통합 / P3 / 대기**. 영향 F10, F22, F23.

### R3-077 — 사용되지 않는 개별 테마 프로필 백업 경로의 종료 오류·중복 구현

1. **R3-077 / 사용되지 않는 개별 테마 프로필 백업 경로의 종료 오류·중복 구현**. 분류 제거; 영향 F22, F24
2. 실제 경로: ThemeBackupService(profile_name=...) public export_to/import_from → _export_profile/_import_profile. 현재 production caller는 whole profile document만 사용
3. 원인·본문 근거: theme_backup.py:_import_profile finally에서 정의되지 않은 owns_connection을 읽어 NameError가 난다. _export_all_profiles도 현재 export_to/export_document 경로에 쓰이지 않는 별도 중복 본문이다. 단순 함수명 검색이 아니라 전체 caller와 실제 조립을 대조했다.
4. 실측·미검증: 임시 native DB에서 public 개별 profile export→import 후 NameError 재현. 이미 commit될 수 있고 connection.close가 건너뛰어 GC에 정리가 의존한다. 최초 진단은 이 연결로 WinError32 정리 실패, 제품 변경 없이 진단의 unreachable handle만 GC 정리한 재실행7건 통과. 실제 GUI에서 이 경로 사용은 확인되지 않았다.
5. 권장 개선·범위: 필요한 제품 기능에 연결되지 않은 중복 public/private 경로를 제거할지 먼저 결정하고, 지원해야 하면 현재 whole-profile 계약에 맞춰 단순화한다. 미사용 코드를 새 서비스로 재구성하지 않는다.
6. 효과·회귀 위험: 오류/GC 의존 수명과 이중 포맷 유지비 감소. 외부 스크립트·과거 파일 형식 호환이 필요하면 명시 계약을 확인한다. 현재 whole restore/expected revision/외부 동기화는 유지.
7. 의존 관계: R3-045는 current document delta, R3-053은 update files로 별개. R3-021/029/056 backup/restore 소비와 직렬 검증.
8. 우선순위 **P3**, 상태 **대기**. 제품 변경 없음 / 해결 검증 없음 / commit 없음.
9. 완료 기준·검증: 제품 caller가 필요한 경로만 남는지, legacy/whole-profile/Drive/strict-restore와 파일 호환·실패 rollback/명시 close를 검증한다. 미연결 기능을 최적화 완료로 보고하지 않는다.

- 코드/운영 구분: 원인·본문 감사 완료, **해결·운영 정상 검증은 미완료**. 정본 분류 **제거 / P3 / 대기**. 영향 F22, F24.

### R3-078 — TOP20 장후 reconciliation coverage 2N SELECT

1. **R3-078**. **대상/분류:** `database_top20_reconciliation.py:_inputs:38/:86`, 통합. F09. 정본 분류 **통합**, 영향 F09. 파일: `src/kiwoom_monitor/central_server/database_top20_reconciliation.py`.
2. **실제 경로:** Top20 backfill/reconcile→native `load_top20_reconciliation_inputs:130/:147`→_inputs→build_top20_reconciliation→dataset generation→series/API/PC 그래프.
3. **원인/근거:** 분봉/metadata·NXT eligibility·catalog는 200code batch인데 coverage document만 code×KRX/NXT마다 별도 SELECT한다.
4. **확정/측정/가설:** `safe_probes.py`는 **실제 _inputs+in-memory SQLite SQL**을 실행했다. 3code SELECT10(coverage6), 20code SELECT44(coverage40). 현재 formula `1+3ceil(N/200)+2N`. 빈 bars fixture이며 값 계산 kernel/실제 PG 지연/WAL/운영빈도는 미측정이다.
5. **최소 방법:** 동일 transaction 안 coverage owner 목록을 기존 batch IN/ANY read로 읽어 pair에 배치한다. batch 크기·snapshot isolation·missing coverage 의미 유지, 새 index/pool/gather 불필요.
6. **효과/위험:** roundtrip/SELECT 수 선형항 제거. code/venue owner mapping, missing+empty,5000한도·PG repeatable-read 보존.
7. **의존/중복:** R3-038은 분봉 ingest metadata/SOR,039는 recent-date API라 다른 caller다. R3-006 실운영 대기 계측과 독립적인 local SQL 감소지만 장후 P2다. 035/036 장중 경합보다 뒤.
8. 정본 우선순위 **P2 / 대기**. 제품 변경 없음 / 해결 검증 없음 / commit 없음. **우선/상태:** P2 / 대기(고정 native 비용 기준 확보).
9. **완료/검증:** 동일3/20/200/201 fixture의 최종 Top20ReconciliationInput·coverage/source/generation 동일, SELECT 감소·transaction1/connection1·실패rollback, native series API 계약 확인. 실제 PG 수락 별도.

- 코드/운영 구분: 원인·본문 감사 완료, **해결·운영 정상 검증은 미완료**. 정본 분류 **통합 / P2 / 대기**. 영향 F09.

### R3-079 — account-context/v4 clock projection의 삭제 범위 불일치

1. **R3-079**. **대상/분류:** `diagnostic_account_context.py:_collections:46`, `_apply_account_context:382/:390`, 재설계. F25. 정본 분류 **재설계**, 영향 F25. 파일: `src/kiwoom_monitor/central_server/diagnostic_account_context.py`.
2. **실제 경로:** capture v4 account context(read-only source)→recorded reader/validate→ReplayDatabaseLease.seal/_seal:440/:466→apply context→전용 baseline snapshot→native recorded execution.
3. **원인/근거:** v4의 허용/read scope는 `DOCUMENT_COLLECTIONS_V4`(account settings/control+clock). apply 시 DELETE는 v1이 아니면 구 `DOCUMENT_COLLECTIONS`(settings/control+real recovery/event)만 사용한다. clock은 삭제하지 않은 채 captured clock INSERT를 한다.
4. **확정/측정/가설:** 상수/branch scope 불일치는 정적 사실. 복원된 v1 controlled parent fixture에 clock key가 이미 있으면 INSERT 충돌 또는 context에 없는 clock 잔류가 가능하다는 **조건부 가설**. 실제 PG/valid v4 seal fixture는 실행 안 했다. parent digest fence가 임의 dirty DB를 차단한다는 사실은 보존한다.
5. **최소 방법:** 기존 `_collections(document)`와 apply의 삭제 scope를 대조해 필요한 버전별 projected collection만 정확히 replace하는 안을 검토한다. whole DB delete나 새 compatibility layer 금지.
6. **효과/위험:** 원본 account context projection/전용 fixture fidelity 개선. v1/v2/v3 호환·archive seed·clock-free context·rollback·parent digest/sequence transaction 보호.
7. **의존/중복:** R3-022 진단 계층 유지와 별개 내부 version scope 불일치다. 실제 provider/운영 자료 없이 dedicated replay 계약만 다룬다.
8. 정본 우선순위 **P2 / 대기**. 제품 변경 없음 / 해결 검증 없음 / commit 없음. **우선/상태:** P2 / 대기(조건부 재현 전 제품 수정 금지).
9. **완료/검증:** valid controlled parent에 old clock 있고/없고×v4 captured clock 있고/없음을 격리 SQL contract/전용 PG에서 확인하고 exact context/result digest와 rollback·v1~v3 유지. 실제 PG 미검증 명시.

- 코드/운영 구분: 원인·본문 감사 완료, **해결·운영 정상 검증은 미완료**. 정본 분류 **재설계 / P2 / 대기**. 영향 F25.

### R3-080 — OCR 첫 이미지의 준비와 본처리에서 동일 predict 반복

1. **R3-080**. 대상/분류: `PaddleThemeOcr.extract_lines275~318/extract_rows338~342`, `ImageThemeOcrWorker.run432~454` / **제거**, P3, 대기. 정본 분류 **제거**, 영향 F10, F22, F24. 파일: `src/kiwoom_monitor/infrastructure/ocr/paddle_theme_ocr.py`.
2. 경로: MainWindow image import → worker → extract_lines(first)441(초기인식) → 매image extract_rows451 → 같은engine.predict → rows → 검토/저장. F10/F22/F24.
3. 원인/근거: 엔진 준비와 inference가 extract_lines에 결합돼 첫 이미지 예측 결과를 버리고 extract_rows에서 다시 읽는다. badge mode의 추가crop OCR은 별도 기능이므로 제거 대상이 아니다.
4. 실측/가설: actual worker/extractmethods + fakeengine/parser에서 이미지1개predict2, 이미지2개predict3. 실제모델 첫기동시간/CPU/정확도는 미측정.
5. 최소안: 기존 lazy engine 준비와 첫 rows inference에서 필요한 결과 재사용을 검토. 새로운 worker/서비스 계층 없이 같은 책임 안에서 준비와 사용 경계만 맞춘다.
6. 효과/위험: 첫 이미지 본추론1회 절감 가능. progress/cancel/checkpoint/native모델시작·badge별추론 의미 보존.
7. 의존/중복: catalog callback054 선행 실패와 별개 정상 OCR body 비용. 관련기능 연결 보존 후 작은 후속 가능.
8. 정본 우선순위 **P3 / 대기**. 제품 변경 없음 / 해결 검증 없음 / commit 없음. 범위/상태: 정적/호출수 재현·제품 대기.
9. 완료/검증: 동일 fakeengine에서N장N회 기본predict, 3mode별실제 rows동등·badge보조추론 유지·중단/모델실패/파일실패 회귀. 커밋 없음.

- 코드/운영 구분: 원인·본문 감사 완료, **해결·운영 정상 검증은 미완료**. 정본 분류 **제거 / P3 / 대기**. 영향 F10, F22, F24.

### R3-081 — 차트 paint의 전체 원본 MA/좌표 재계산

1. **R3-081**. 대상/분류: `MinuteChart.paintEvent347~659`, `_drawing_data_point734~755`, `_find_annotation_at757~805` / **통합/재설계**, P3, 대기후보. 정본 분류 **통합**, 영향 F08, F18, F24. 파일: `src/kiwoom_monitor/presentation/journal_chart_widget.py`.
2. 경로: Journal/DetachedChartPanel RAM rows/fills → hover/move update → paint → whole _all_rows closes와기간별MA/체결nearest indices → 그림. F08/F18/F24.
3. 원인/근거: paint470~478에서 매 paint 전체closes/MA를 재계산하고, fill_indices400~403와 annotation_x488~490은 후보봉을반복탐색한다. drawing/hit 두 함수에도 가격grid 계산 본문이 반복된다.
4. 실측/가설: 본문 사실. 실제paint호출수/Qt CPU·latency 미측정, 장중부하로단정안함. 기존055의 setter aggregate12회 측정과 섞지 않는다.
5. 최소안: 기존 data/view/style 변경에 맞춰 같은 chart상태 안에서 재사용 가능한 결과 확인. 독립cache계층/임의TTL 추가 없음.
6. 효과/위험: hover 때 같은 자료 재계산 감소 가능, annotation·reference/index/fill 범위·가시window·이평 warmup 정확성 위험.
7. 의존/중복: 기존 R3-055는 hiddenread+setter aggregation이다. 같은기능 후속으로 보강할 수 있지만 paint 본문은 다른 trigger다. MainWindow render_trade_values4454~4464가live_only에서도7개집계/quality반복하는 것은 **기존027 보강**으로 관리. 정본은 R3-055의 setter/read와 다른 paint/hit trigger의 별도 항목으로 분리한다. 같은 chart state 변경은 직렬 조정한다.
8. 정본 우선순위 **P3 / 대기**. 제품 변경 없음 / 해결 검증 없음 / commit 없음. 범위/상태: 코드감사 완료/성능가설·제품 대기.
9. 완료/검증: 실제paint와setter/read 구분계측→같은geometry/rows/fills/view/annotation 결과동등·한mousemove의계산횟수비교. 커밋 없음.

- 코드/운영 구분: 원인·본문 감사 완료, **해결·운영 정상 검증은 미완료**. 정본 분류 **통합 / P3 / 대기**. 영향 F08, F18, F24.

### R3-082 — 기본설정의 unreachable 구형 layout

1. **R3-082**. 대상/분류: `SettingsDialog.__init__:281~312` / **제거**, P4, 대기후보. 정본 분류 **제거**, 영향 F23. 파일: `src/kiwoom_monitor/presentation/settings_dialog.py`.
2. 경로: 현재constructor는_build_grouped_layout276/geometry280뒤281 return. 따라서282~312 구형layout은 실행될수없고, _refresh_interval/_interest 등 현재조립에없는옛필드를참조한다. F23.
3. 원인/근거: 조기return뒤 남은동일생성자deadbody.
4. 실측/가설: 코드상unreachable 사실, 실행비용절감0. 유지보수 혼동만.
5. 최소안: 해당deadbody삭제, 새파일·계층추가없음.
6. 효과/위험: 읽기부담/오인감소; 현재reachable동작영향없음.
7. 의존/중복: R3-028 unused import와같은정리분류에보강가능, 우선순위최하. 정본은 R3-028 import와 다른 도달 불가 실행본문이므로 별도 최하위 항목으로 관리한다.
8. 정본 우선순위 **P4 / 대기**. 제품 변경 없음 / 해결 검증 없음 / commit 없음. 범위/상태: 정적확인/제품 대기.
9. 완료/검증: 현재groupedlayout/연결버튼/설정저장계약유지, 필요한관련검사만. 커밋 없음.

- 코드/운영 구분: 원인·본문 감사 완료, **해결·운영 정상 검증은 미완료**. 정본 분류 **제거 / P4 / 대기**. 영향 F23.

### R3-083 — 개선된 책임·실행 비용·기능 연결의 퇴행 방지

1. **대상/분류:** 구조 퇴행 방지의 지속 적용 / **통합, P1, 진행**. 2026-10-18 사용자 추가 요구이며 기존 제품 결함063~082와 구분한다. 새 runtime owner/관리 계층을 만들지 않고 기존 검사와 책임 경계를 연결한다.
2. **실제 경로:** AGENTS → `docs/architecture/r3-ownership.md`의 관련 기능군 → 변경 diff의 caller/owner/consumer → 기존 선택 조회/실행. `check_architecture` → 기존 import 영향 graph → 정적 delta/검토 계약. push/PR → 기존 Windows workflow의 CLI1회 → 기존 전체 회귀/필수 결과. 활성 pre-push hook은 현재 없고 새 주기 검사/배포 경로는 추가하지 않았다.
3. **원인/근거:** 기존 QueryStore/PG native owner·직접 연결·계약·회귀는 이미 CI에 있으나 계층 방향/새 cycle edge와 로컬 DB 지점/지정 RAM I/O 증가의 자동 검사는 없었다. AppController/QueryStore 유지 판단을 전체 내부 책임/성능 정상으로 확대하지 않는다. 파일/클래스 수 임계값은 사용하지 않는다.
4. **기준/실측 구분:** 초기 source-site 채택1366keys(boundary23/cycle261/DB1041/dynamic41)는 기존 조건부/타입 import·미해결 R3·미커밋 코드를 포함한 기계 목록이며 해결·승인이 아니다. 실제 비용은 기존 R3-001 SELECT20, 002 연결/BEGIN/COMMIT각1/SELECT0, 004 native 반환200행으로 재현한다. 이번004 양성 대조는400→200, 과거9000→200의 현재 재측정이 아니다. 실제PG/장중/WAL/벽시계 비용은 미검증이다.
5. **적용 범위:** 기능군9개/F01~25의 RAM/영속/transaction·실패/재시도·구독/작업/종료·공식 경계 정리. 새 정적 checker와 기계 계약1개, fixture13건, 기존004 검사1개와 runner/CI 검사2개 보강. 기존기능 의미 검증·QueryStore/PG audit와 선택 실행 재사용. 전체 원장/성공 로그를 반복해서 읽지 않고 실패·영향 범위만 조회한다.
6. **효과/위험:** 신규 역방향 의존·cycle확대·DB/동적/RAM I/O 지점을 초기 변경에서 드러낸다. 실제 반복 횟수/불필요 wrapper·광범위한 상태 소유/간접 I/O는 정적 수로 확정할 수 없어 관련 실행 비용·연결/설계 검토를 유지한다. 정당한 비용/책임 변경은 정확한 key·이유·연결·검증 및 필요시 규칙 전후 hash를 append-only review로 허용한다. R3완료 시 제거된 budget을0/감소값으로 좁혀 재유입을 막는다.
7. **의존/다음:** 모든 R3의 완료 계약에 동반하되 별도 전면 선행 정리로 제품 개선을 막지 않는다. **다음 제품 작업002+023 유지**. 신규고빈도 경로/각 R3완료 때 관련 owner·기계/실제 비용·F 행을 점진 갱신한다. 기존 뉴스 QueryStore source 감사의 미결은 그대로 보존하며 이 체계 도입으로 승인하지 않는다.
8. **현재 검증/commit:** 관련 고유95건 통과(새 fixture13 + R3-004 관련16 + 기존 도구/비용66, 실행기42 재검증은 중복 계산하지 않음). 최초 sandbox TEMP 접근오류/004 bounded timeout, 잘못된 실행 인자에 따른 로딩0건, 기존 runner profile 기대값1실패를 보존하고 각각 원인과 재실행을 구분했다. 최종 worker/자식 종료 확인·성공 TEMP제거. 구조 CLI/등록 catalog/증분 검토 확인. 제품 소스 변경0, 로컬 미커밋, push/hosted CI/운영DB/주문/NAS/배포/전수회귀 없음. 근거 `data/diagnostics/structure-regression-20261018/verification.json`.
9. **최종 완료 기준:** 아래 체크리스트와 함께 R3의 마지막에 종결한다. 현재는 기반 적용·관련 로컬 검증 단계 완료이며 이후 개선별 계약 반영과 실제 CI 실행 확인이 남는다.

- [x] 현재 기능별 owner·공식 경계·실패/수명과 미해결을 분리하고 선택 참조로 연결.
- [x] 새 구조 위반/별칭·package우회/기존cycle확대/DB·RAM I/O 증가·watch이동·잘못된syntax를 실패시키는 fixture, 필요한 비용/책임 변경 허용과 adoption/history보존 검증.
- [x] 기존 native 비용/연결 검사를 재사용하고004 native 반환행 퇴행 구간만 보강.
- [x] 기존 전체 CI/필수 환경·일정 유지, CLI1회와 profile 등록·별도 artifact 연결을 로컬 계약으로 확인.
- [ ] 남은 R3별 개선 완료 때 해당 책임 계약·현행 정상 비용/실패/중복·종료 검사·F 원장을 갱신하고 제거된 정적 예외/budget을 좁힘.
- [ ] 정당한 책임 이관 후 caller/consumer·저장 설정·재시작/호환·구독해제가 함께 전환됐는지 해당 범위 확인.
- [ ] 사용자 요청에 따른 push 이후 실제 hosted CI 결과 확인, R3 최종 owner/계약과 구현 일치 확인. 운영 미검증을 자동 정상/전체회귀 완료로 승격하지 않음.

#### R3-083 최종 차단 검증 — 2026-10-18

**판정:** 로컬 가드레일 차단/정상 허용·CI shell 실패 전달 검증 완료. 기존 push의 정상 가드 단계는 실제 성공 확인. 상태는 **진행**을 유지한다(남은 R3별 계약 반영, 새 검사 hosted 반영과 전체 CI/PR·의도적 위반 hosted 검증은 별도). 시작 HEAD/local·origin은 `2c3888f7474a7ce111261dcca2f7f631b23da89c`, 작업 시작 clean. 제품394모듈과 원래 checker/selector/계약/workflow에는 테스트용 위반·변경을 남기지 않았다. 이번 수정은 기존 실행기 테스트의 CI 검증 보강과 관련 문서·추가형 테스트 원장뿐이며 commit/push/배포는 하지 않았다. 위8번95건은 기반 도입 당시 이력이고 아래 실행과 합산하지 않는다.

| 검증 | 현재 실행 근거와 결과 |
| --- | --- |
| 기존 구조·CI 검사 | 13개 구조 mutation + 실행기43개 = **56건 통과**, 실패/오류/skip0, worker/자식 종료·TEMP제거 |
| 실제 CLI 경계 | 복사한 원본 checker/selector와 temp Git/native exit를 사용하는 **2개 검사·19조건** 기대 일치. 정상 기능/정당한 limit·규칙+watch이관은0, 금지 dependency/새 cycle/DB·현재8RAM watch I/O는1. 손상 구문/잘못된 Git 기준도 error/1이며 성공으로 처리하지 않음 |
| 실제 workflow shell | 현재 YAML의 architecture `run`을 그대로 pwsh에서 실행: 정상0 / 위반1. GitHub job 자체를 격리 모방 실행한 것은 아니며 required 전파는 아래 계약으로 확인 |
| 성능 차단 | 기존 원본 검사 재사용한 **4개 진단**, 내부 정상4회/의도 위반2회. metadata 중복 SELECT20→40, full-day 반환200→400을 기존 assertion이 거절. patch identity 복원·같은 정상 검사 재통과. 기대 실패 trace 보존 |
| 발견·최소 수정 | 기존 workflow 단위검사가 `if:false`, `continue-on-error:true`, `exit0`, PR paths필터를 놓쳤다. 기존 CI 테스트에 실제 YAML trigger/job/step/required 검증을 붙이고 **한 메서드**에서 위4종+가드명령누락+required오류무시의6변이를 모두 거절. 현재 workflow 자체는 정상이라 수정하지 않음. 이미 lock된 PyYAML 재사용·새 profile/라이브러리/job0 |
| 실제 GitHub | [push39110380368](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/39110380368), [push39110380280](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/39110380280)의 동일 SHA architecture step 각각 completed/success. 조회 시 전체 workflow·Windows job은 in_progress. 이번6변이 보강은 로컬 미커밋이어서 이 hosted 성공에 포함되지 않음. 실제 PR/의도 위반 push·전체 CI 성공은 주장하지 않음 |

**자동/설계 경계:** 새 금지 의존·cycle edge·인식 가능한 native DB 호출 지점·지정 RAM 경로의 인식 가능한 I/O/동적 import·watch 유실·계약 재작성과 위6종 CI gate 퇴행은 자동 검증 대상이다. 불필요한 얇은 relay 변이는 실제 CLI가 **통과**했다. 레이어의 실익·중복 책임/상태 소유·기존 데이터를 재사용할 수 있는지·필요 비용 증가의 타당성과 review 근거의 내용은 사람/Codex가 관련 diff/owner/소비자를 검토해야 한다. 임의 클래스/파일 수 차단을 추가하지 않는다.

**잔여 위험:** 정적 호출 지점 수가 같은 반복 증가, 간접 helper/동적 receiver·별칭 재대입/반사 호출의 모든 I/O, 지정되지 않은 새 고빈도 경로, 테스트되지 않은 실패/재시작/종료·실제 PG/장중 비용은 자동 완전 검출되지 않는다. 이번 성능 차단은 기존001/004 fixture 범위이며 다른 미해결 R3 비용을 정상으로 승인하지 않는다. 신규 기능은 관련 owner·입력/저장/소비·수명과 해당 비용/중복/실패 검사만 보완한다.

**작업·토큰 부담:** AGENTS의4개 핵심 bullet은 전체 감사/백로그 재독을 요구하지 않고 기능군·변경 caller/consumer만 선택한다. static checker는 전체 관련 소스를 기계 AST 파싱하나 감사 문서를 읽거나 테스트/DB를 실행하지 않는다. 이번 PC에서 repository static check **7.082초**, 추가 CI 변이 메서드 본문 **0.116초**(worker 기동/fixture제외) 관찰. 토큰 수·hosted 시간의 보장값은 아니며 측정값만 기록한다. 전체 회귀·주기 검사·중복 DB audit는 추가하지 않았다. 정적 출력은 실패최대8개, 성공은 요약만, 정확한 토큰 비용은 미측정이다. 진단용 CLI19조건/성능 mutation을 매 개발마다 실행하도록 등록하지 않았다.

최초 sandbox TEMP 접근 오류/CLI0조건과 사용자 환경 재실행은 별도로 보존했다. 성능·CLI 패치는 각각 scope 종료 및 source hash 동일, 성공 worker/자식 종료·누수0·TEMP제거를 확인했다. 정본 근거는 `data/diagnostics/r3-083-final-validation-20261018/verification.json`, 상세 `results/verification.json`, `performance-measurements.json`, `structural-runner-v1/`, `incremental-static.json`, `github-*-verified*.json`이다. F08/F13/F24는 이번 정상·위반·복원 범위를 보강하며 운영 상태를 일괄 승격하지 않는다.

#### R3-083 커밋·push 및 CI 지연 후속 — 2026-10-18

사용자 요청으로 기존 보강을 커밋·push하고 실제 전체 CI를 확인한다. 전체 검사 범위·timeout·Job 격리·실패 수집/종료 판정·기존 CI 일정은 유지한다. 제품 동작/DB/주문/NAS 배포 변경은 없다.

- **실제 지연:** 동일 `2c3888f`가 main과NAS작업브랜치에 동시에 push돼 전체 workflow가2회 실행됐다. main을 유지하고 중복39110380368만취소해 중간자료를 보존했다. requiredPG 약1분56초/sealed 약4분·준비의존성1분30초/가드11~12초에 비해 Windows가30분이상 지연했다. 중간 worker051은 scoped_account_api의 첫 계좌조회에서 **1700.437초** 대기했다. 취소된 실행을 전체 회귀 통과로 취급하지 않는다.
- **확정 원인·최소 수정:** fake `server_now`가 예약창phase0에 고정돼 우선순위45 계좌 요청이 delay5.0으로 반복 재queue됐다. 로컬같은조건45초 bounded timeout·stack/phase고정 재현. 해당검사만 같은날quiet phase10을 주입해 단일4.138초/모듈8건/기존reservation2건 통과. 제품예약정책·API/default binding assertions 유지.
- **추가 확인:** market event4검사의 naive KST epoch 기대값이 UTC GitHub에서32400초 달랐다. 실제생산자 as_kst→동결→native저장계약으로 oracle만교정,9건통과. Qt검사의 global DeferredDelete flush가 다른owner QObject까지삭제함을 sentinel로 확인해 receiver를해당dialog로한정,모듈34건+sentinel통과. 기존core nativeexit3221226505의원인이라고확정하지않으며 hosted재실행으로확인한다.
- **계측·범위:** CI에 worker 시작/완료·포함준비/정리시간을 flush 출력하고 PYTHONFAULTHANDLER를켜 native crash근거를남긴다. 기존 runner stop/continue 회귀에 이벤트순서와status를확인해 도구93건통과. 시장9+UI34+계좌8+reservation2를포함한관련고유146건 통과. 전수로컬실행/새병렬worker/검사삭제·skip·timeout축소0. PC관련기록은본문92.15%·fingerprint각0.58/0.49초로 분석되어,작은정적단계를주원인으로단정해재설계하지않았다.
- **원격 확인:** 아래 실제 신규커밋/SHA·workflow결과·전후worker시간을 최종 확인 뒤 갱신한다. 기존R3-083의로컬검증과새hosted성공은구분한다. 새push는main에만전송해같은SHA의다중branch중복실행을만들지않는다.
- 근거: `data/diagnostics/ci-speed-r3-083-20261018/`의 jobs-before/run/worker자료, scoped-query-verification, candidate-alerts-after-v1, market-event-clock, runner-related-v1, 추가형검토기록. 다른미완료R3/미정전략/실제운영검증은변경하지않고 다음제품002+023을유지한다.

# 현재 모듈 지도

기준: 2.1.0 / 2026-09-22 · [현재 아키텍처](ARCHITECTURE_CURRENT.md) · [문서 안내](docs/README.md)

아래 경로는 현재 있는 파일이다. 파일을 나누는 계획이 아니라 변경 책임자를 찾는 지도다. 상세 단계별 계보는 [이전 지도](docs/archive/2026-09-22/root/MODULE_MAP.md)에 보존했다. 작업 전 [개발 불변 규칙](DEVELOPMENT_GUARDRAILS.md)을 읽는다.

## 앱·시장·저장

`src/kiwoom_monitor/` 기준 경로다.

| 영역 | 먼저 볼 파일 | 책임 |
|---|---|---|
| 조립·프로세스 | `bootstrap.py`, `news_process.py`, `journal_process.py`, `research_process.py` | 실행모드·자식 프로세스·작업 수명 |
| 사용자 데이터 | `infrastructure/app_paths.py` | 설치/개발/지정 data 경로 |
| 순위 | `application/ranking_schedule.py`, `application/ranking_execution.py`, `central_server/autonomous_top20.py` | 순위 시각·재시도·실행·NAS 독립 수집 |
| REST | `central_server/rest_broker.py`, `infrastructure/kiwoom_rest/client.py`, `infrastructure/kiwoom_rest/remote_client.py` | 중앙 우선순위·직접/원격 경계 |
| 실시간 | `central_server/realtime_collector.py`, `infrastructure/kiwoom_rest/realtime_worker.py`, `infrastructure/kiwoom_rest/central_realtime_worker.py` | 중앙/직접 구독·REG·장애전환 |
| 세션 | `application/market_session_schedule.py`, `application/realtime_subscription.py` | 거래일별 venue/phase·구독 대상 |
| 봉·관측 | `central_server/minute_bars.py`, `central_server/market_ingest.py`, `central_server/market_observations.py` | 1초/1분 집계·TR 적재·revision 의미 |
| TOP20 | `application/top20_trade_value_collector.py`, `presentation/top20_trade_value.py` | 코호트·지수·차트 |
| 로컬 쓰기 | `infrastructure/persistence/market_cache_writer.py`, `infrastructure/persistence/minute_bar_repository.py` | 비동기 직렬 쓰기·봉 저장 |
| 중앙 DB/API | `central_server/app.py`, `central_server/database.py`, `central_server/central_schema.py` | 라우트·DB 트랜잭션·스키마 |
| 화면 | `presentation/main_window.py`, `presentation/main_table_formatting.py` | 조정·표시, 계산/수명 책임은 기존 모듈 재사용 |

## 뉴스·테마·일지

| 영역 | 먼저 볼 파일 | 책임 |
|---|---|---|
| 뉴스 입력·작업 | `central_server/news_sources.py`, `central_server/news_jobs.py` | 현재 Naver 검색 수집과 BODY/RULE/AI 단계 |
| 뉴스 판단 | `application/news_rules.py`, `application/news_grouping.py`, `application/news_analysis.py` | 규칙·사건 묶음·분석 |
| AI 공급자 | `infrastructure/news_ai.py` | 기존 외부 공급자 연동 |
| 테마 | `infrastructure/persistence/theme_repository.py`, `application/theme_matching.py`, `application/theme_preview.py` | 프로필·종목연결·가져오기·미리보기 |
| 테마 동기화 | `infrastructure/central_theme_sync.py` | 로컬 편집·pending·retry·NAS 스냅샷 |
| 테마/시장 연구 | `application/theme_leadership.py`, `application/market_research_features.py`, `application/context_candidates.py` | 대장·시장 특징·맥락 가설 |
| 시점 근거 | `domain/snapshot_provenance.py`, `application/trade_snapshot_context.py` | 당시 관측과 사후 보완 구분 |
| 일지 | `presentation/journal_workers.py`, `infrastructure/central_journal_sync.py` | 일지 조회/분석·계좌 scope 동기화 |

## 연구·타점

| 저장소 상대경로 | 책임 |
|---|---|
| `scripts/export_research_dataset.py` | NAS 일별 동결 export·bundle |
| `src/kiwoom_monitor/infrastructure/research_data_source.py` | 입력 로드·독립 시간/종목/final projection |
| `scripts/export_historical_reconstruction.py`, `scripts/prepare_historical_research_input.py`, `infrastructure/historical_reconstruction.py` | 사후 후보·대신 봉·과거 뉴스의 불변 복원과 기존 1분 전략 입력 어댑터. strict TOP20 재생과 분리 |
| `src/kiwoom_monitor/research_process.py` | campaign worker·가설·순차/final 평가 프로세스 |
| `scripts/run_research.py` | 기존 runner의 재생·실행·평가 저장 |
| `src/kiwoom_monitor/application/research_replay.py` | 시점별 입력 재생 |
| `src/kiwoom_monitor/application/research_execution.py` | PaperExecutionEngine·봉 체결·현금/비용 |
| `src/kiwoom_monitor/application/research_evaluation.py` | 연구 성과·품질·평가 |
| `src/kiwoom_monitor/application/research_families.py` | 등록 Family·Factor·허용 파라미터 |
| `src/kiwoom_monitor/application/breakout_strategy.py` | 완료봉 돌파 판단 |
| `src/kiwoom_monitor/application/pullback_reacceleration_strategy.py` | 눌림 후 재가속 판단 |
| `src/kiwoom_monitor/application/research_search.py` | 제한 trial·개발 근거·후보 선택 |
| `src/kiwoom_monitor/application/research_hypotheses.py` | 결정적 가설·후속 생성·부모 계보 |
| `src/kiwoom_monitor/infrastructure/persistence/research_repository.py` | v23 run/campaign/lease/final/가설 원장 |
| `src/kiwoom_monitor/presentation/research_dialog.py` | 연구·campaign·가설·순차/final UI |
| `src/kiwoom_monitor/application/research_resources.py` | RSS/preflight·CPU 양보 |
| `src/kiwoom_monitor/application/forward_evaluation.py` | feedback evidence/review/제안·forward 평가 |
| `src/kiwoom_monitor/application/feedback_strategy_revision.py` | 명시 채택 버전·재검증 spec/dispatch |

## 계좌·인증·모의 실행

`src/kiwoom_monitor/` 기준 경로다.

| 영역 | 먼저 볼 파일 | 책임 |
|---|---|---|
| 계좌 신원 | `application/account_identity.py`, `infrastructure/kiwoom_rest/account_identity.py` | scope/binding; A4B 직접 연결 미완료 범위 별도 |
| 인증 | `central_server/credential_store.py`, `central_server/credential_runtime.py` | 암호화 vault·prepare/apply·세대·drain |
| 인증 UI | `infrastructure/central_credentials_client.py`, `presentation/nas_credentials_dialog.py` | HTTPS 요청·입력·worker |
| 후보 감시 | `central_server/candidate_monitor.py` | 현재 shadow producer·후보 알림 |
| 운용 명세·입장 | `application/mock_automation_specification.py`, `application/mock_automation_admission.py` | 동결 spec·적격성·단일 계좌 lease |
| 위험·복구·전달 | `application/mock_automation_risk.py`, `application/mock_automation_recovery.py`, `application/mock_automation_execution.py` | 실제 위험 근거·대사·Decision gate |
| 주문 원장 | `application/order_lifecycle.py`, `central_server/execution_runtime.py` | intent·unknown·멱등·owner |
| 자동 모의 수명 | `central_server/mock_automation_runner.py`, `central_server/mock_automation_supervisor.py`, `presentation/mock_automation_dialog.py` | runner·영속 제어·UI |

## 다음 개발의 진입점

외부 `kiwoom_history_backfill`의 후보 DB·기존 수집 스크립트는 앱 모듈이 아니다. 현재 표본 경계는 `infrastructure/historical_backfill.py`, `scripts/probe_historical_backfill.py`, 32비트 COM 표본·연속조회 브리지 `scripts/daishin_stockchart_probe.ps1`과 `scripts/daishin_stockchart_backfill.ps1`에 있다. [과거 수집 기획](docs/HISTORICAL_BACKFILL_PLAN.md)으로 원천별 표본을 확인한 뒤 기존 `research_data_source`, 뉴스 revision, 프로필 저장 경계에 연결한다.

관련 테스트는 각 책임의 `tests/unit/test_*.py`와 `scripts/check_*.py`에서 찾는다. 문서 정리에서 이전 테스트 개수를 새 실행 결과로 복사하지 않는다. 실제 코드 변경 때 해당 경계의 의미 있는 회귀와 필요한 통합 검증을 수행한다.

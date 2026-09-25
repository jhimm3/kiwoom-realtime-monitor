# 현재 모듈 지도

기준: 2.1.0 / 2026-09-23 · [현재 아키텍처](ARCHITECTURE_CURRENT.md) · [문서 안내](docs/README.md)

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
| 뉴스 입력·작업 | `central_server/news_sources.py`, `central_server/news_service.py`, `central_server/news_jobs.py`, `infrastructure/naver_stock_news.py` | Naver 검색·증권 종목 목록, TOP20 수집 범위와 BODY/RULE/AI 단계 |
| 과거 뉴스 PC 전처리·시황 업로드 | `scripts/preprocess_historical_news_locally.py`, `scripts/probe_historical_backfill.py`, `scripts/run_naver_stock_market_news.py`, `scripts/import_prepared_historical_news_to_nas.py`, `scripts/historical_collection_monitor.py`, `central_server/database.py` | 두 원천 수집기가 기사별 BODY/RULE 준비를 병렬 실행해 PC 원장에 저장하고, 준비된 결과만 불변 스냅샷으로 NAS 정상 뉴스 테이블에 적재 |
| 종목 뉴스 조회 | `central_server/app.py`, `central_server/database.py`, `infrastructure/central_news_client.py`, `presentation/news_workers.py`, `presentation/stock_news_window.py`, `infrastructure/persistence/stock_news_repository.py` | NAS 저장분 200건 페이지, 스크롤 추가 조회, PC 직접 연결 보존 건수 |
| 네이버 증권 시황 피드 | `infrastructure/naver_stock_market_news.py`, `central_server/market_news_sources.py`, `scripts/run_naver_stock_market_news.py`, `scripts/historical_collection_monitor.py` | FLASH/WORLD 날짜별 응답·발행시각, NAS 독립 cursor 수집, 역사 원응답 보존·진행 확인·재시작/정지 |
| 시장 뉴스 화면 | `presentation/market_news_window.py`, `news_process.py`, `infrastructure/central_news_client.py`, `central_server/database.py` | TOP20 앞 뉴스 진입, 공통/속보/해외 탭. NAS 저장 소스 조회와 PC 직접 연결의 화면 요청을 분리 |
| 후보 기업행동 백필 | `scripts/collect_historical_market_context.py`, `scripts/daishin_market_context_backfill.ps1`, `scripts/classify_historical_stock_adjustments.py`, `scripts/collect_candidate_event_disclosures.py`, `scripts/collect_candidate_exchange_disclosures.py`, `infrastructure/dart_disclosures.py` | 주도후보에 한정한 CREON 누적 수정계수 경계와 상장·거래량 0·거래 재개 후보 날짜 수집, DART 사건 인접 및 전체 거래소 공시 목록 연결, 원응답·재개·NAS 게시 gate |
| 거래소 공시 효력일 | `scripts/collect_candidate_exchange_effective_dates.py`, `infrastructure/exchange_effective_dates.py`, `scripts/reconcile_candidate_exchange_effective_dates.py`, `scripts/publish_historical_market_context_to_nas.py` | DART 접수일과 거래정지·재개·상폐 효력일/시각을 별도 보존하고 키움 일봉 거래량으로 대조. 원문 ZIP과 대조 원장을 시장 맥락 NAS 스냅샷에 게시 |
| 뉴스 판단 | `application/news_rules.py`, `application/news_grouping.py`, `application/news_analysis.py` | 규칙·사건 묶음·분석 |
| AI 공급자 | `infrastructure/news_ai.py` | 기존 외부 공급자 연동 |
| 테마 | `infrastructure/persistence/theme_repository.py`, `application/theme_matching.py`, `application/theme_preview.py` | 프로필·종목연결·가져오기·미리보기, 프로필별 대표명/분리 결정 재적용 |
| 테마 동기화 | `infrastructure/central_theme_sync.py` | 로컬 편집·pending·retry·NAS 스냅샷 |
| 테마/시장 연구 | `application/theme_leadership.py`, `application/market_research_features.py`, `application/context_candidates.py` | 대장·시장 특징·맥락 가설 |
| 시점 근거 | `domain/snapshot_provenance.py`, `application/trade_snapshot_context.py` | 당시 관측과 사후 보완 구분 |
| 일지 | `presentation/journal_workers.py`, `infrastructure/central_journal_sync.py` | 일지 조회/분석·계좌 scope 동기화 |

## 연구·타점

| 저장소 상대경로 | 책임 |
|---|---|
| `scripts/export_research_dataset.py` | NAS 일별 동결 export·bundle |
| `src/kiwoom_monitor/infrastructure/research_data_source.py` | 입력 로드·독립 시간/종목/final projection |
| `scripts/export_historical_reconstruction.py`, `scripts/prepare_historical_research_input.py`, `infrastructure/historical_reconstruction.py` | 사후 후보·대신 봉·과거 뉴스의 불변 복원과 기존 1분 전략 입력 어댑터. 후보 원본 DB의 현재 `stocks.market_code`를 보존하고 명시적 `--individual-stocks-only` 투영에서 개별 주식만 연구 모집단에 포함. strict TOP20 재생과 분리 |
| `scripts/plan_historical_research_split.py`, `application/historical_research_split.py` | 다기간 역사 사례의 시간순 TRAIN/VALIDATION/SEALED OOS 배정과 데이터셋 hash 결합 |
| `scripts/prepare_historical_development_inputs.py`, `infrastructure/research_data_source.py` | 봉인 계획에서 역사 provenance를 유지한 TRAIN·VALIDATION 독립 입력 투영. OOS는 별도 gated 경계 |
| `scripts/build_historical_exchange_case_context.py` | 월별 TRAIN·VALIDATION 후보에 해당하는 공식 거래소 효력일과 접수일·일봉 대조 결과를 불변 동반 자료로 투영. 역사 장중 접수 시각 미검증이므로 전략 신호·주문 가능성 판정에 자동 투입하지 않음 |
| `scripts/prepare_historical_baseline_requests.py`, `scripts/summarize_historical_baseline_results.py` | OOS 없는 역사 개발 입력의 두 기존 Family 고정 요청 생성과 실행·검열 사유 요약 |
| `scripts/audit_historical_minute_readiness.py`, `scripts/audit_historical_monthly_bar_quality.py`, `scripts/audit_historical_monthly_gap_causes.py`, `scripts/audit_historical_monthly_gap_raw.py`, `scripts/audit_historical_nas_minute_alignment.py`, `scripts/audit_historical_five_minute_clock.py`, `scripts/project_historical_minute_exclusions.py` | 후보일·종목일별 1분봉 최소 조건과 종일 봉 개수·긴 공백의 키움 일봉/분봉·CREON 일봉·VI 및 원응답과 저장 봉 일치를 읽기 전용 감사, NAS/CREON 거래일별 마감 체결 시각 대조와 CREON 5분봉 종료시각·해상도 구분, 원본 SHA-256에 묶인 종목일 제외 투영 |
| `scripts/plan_historical_monthly_case_selection.py` | 가격 결과를 보지 않고 월별 첫 후보일 16개를 고정하고 기존 봉인 OOS 날짜·제외 원장의 해시에 결합 |
| `scripts/audit_historical_monthly_selection_coverage.py` | 동결 선택일과 개발 전체 후보일의 후보 수·기초 분봉 수량을 원본 해시로 대조하는 읽기 전용 범위 감사. 가격 결과·OOS 미사용 |
| `scripts/prepare_historical_learning_cases.py`, `application/historical_learning_cases.py` | 사후 후보 선정·복원 뉴스 근거·미생성 AI 해석·미래 가격 결과를 분리한 불변 LLM 학습 준비 사례. strict 시점 재생이나 모델 학습 완료로 사용하지 않음 |
| `scripts/prepare_historical_news_review_queue.py`, `application/historical_news_review_queue.py` | 학습 준비 사례의 기사 식별자 중복 제거, 종목·사례별 비AI 규칙 힌트와 사람 검토 빈칸을 보존하는 불변 검토 대기열. 규칙 힌트는 정답이 아님 |
| `scripts/historical_news_review_workflow.py`, `application/historical_news_review_decisions.py` | 우선 검토 CSV 내보내기와 사람 판정·사건 ID·테마 프로필 검증, 불변 결정 결과 저장. 편집 CSV와 최종 결과를 분리하고 모델 학습 준비 상태는 false 유지 |
| `scripts/plan_historical_news_event_split.py`, `application/historical_news_event_split.py` | 사람 검토 관련 기사를 canonical event 단위로 유지한 시간순 TRAIN·VALIDATION·봉인 OOS 불변 계획. 무관·보류와 일부 검토 상태를 분리 |
| `scripts/prepare_historical_news_development_inputs.py`, `application/historical_news_development_inputs.py`, `presentation/historical_news_review_dialog.py` | 사건 분할에서 TRAIN·VALIDATION 사람 검토 기사만 투영한 RAG·미세조정 비교 공통 입력. 앱과 CLI 생성 경로, OOS payload 제외와 사건 중복 금지 검증 |
| `scripts/prepare_historical_news_blind_validation.py`, `application/historical_news_blind_validation.py` | 공통 VALIDATION에서 사람 target·사건 ID·테마명을 제거한 방법 중립 평가 요청. 개발 dataset ID·validation hash 결합과 정답 누수 재귀 검증 |
| `scripts/prepare_historical_news_method_results.py`, `application/historical_news_method_results.py` | prompt baseline·RAG·미세조정 예측을 동일 블라인드 요청 전체에 결합하는 불변 결과. 누락·중복·추가 응답과 OOS·정답 사용 표시 차단 |
| `scripts/evaluate_historical_news_method.py`, `application/historical_news_method_evaluation.py` | 평가기 전용 VALIDATION target 결합, 사건 pairwise 군집·테마 집합·프로필·coverage 지표. 관련성 분류와 자동 모델 승격은 지원하지 않음 |
| `scripts/assess_historical_development_readiness.py`, `infrastructure/historical_research_readiness.py` | 역사 개발 분할의 모든 사례·종목별 분봉·연속 1분쌍 완전성 gate. 이전 분할 재생용 후보군 seed는 검사 모집단에서 제외. 희소 구조 실행은 명시적 예외 |
| `src/kiwoom_monitor/research_process.py` | campaign worker·가설·순차/final 평가 프로세스 |
| `scripts/run_research.py` | 기존 runner의 재생·실행·평가 저장. 판단은 최대 128건씩 저장하고 사건 참조 전 즉시 flush하며 논리 결과 해시는 순차 계산 |
| `src/kiwoom_monitor/application/research_replay.py` | 시점별 입력 재생. 투영된 역사 입력은 별도 역사 후보군 종류를 명시해 재생하며 대상 봉·같은 종목/당일 이력을 돌파·눌림 Factor에 전달 |
| `src/kiwoom_monitor/application/research_execution.py` | PaperExecutionEngine·봉 체결·현금/비용. 기존 정수 `fixed_bps/v1`과 소수 bp 문자열 `fixed_bps/v2` 구분 |
| `src/kiwoom_monitor/application/research_evaluation.py` | 연구 성과·품질·평가 |
| `src/kiwoom_monitor/application/research_families.py` | 등록 Family·Factor·허용 파라미터 |
| `src/kiwoom_monitor/application/breakout_strategy.py` | 완료봉 돌파 판단 |
| `src/kiwoom_monitor/application/pullback_reacceleration_strategy.py` | 눌림 후 재가속 판단 |
| `src/kiwoom_monitor/application/research_search.py` | 제한 trial·개발 근거·후보 선택 |
| `src/kiwoom_monitor/application/research_hypotheses.py` | 결정적 가설·후속 생성·부모 계보 |
| `src/kiwoom_monitor/infrastructure/persistence/research_repository.py` | v24 run/campaign/lease/final/가설·순차 소유 원장. 판단 묶음은 한 SQLite 트랜잭션으로 불변성·순서를 검증 |
| `src/kiwoom_monitor/presentation/research_dialog.py`, `presentation/historical_news_review_dialog.py` | 연구·campaign·가설·순차/final UI와 과거 뉴스 사람 검토·작업표 저장·불변 결과 동결·사건 분할 계획 실행 |
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

외부 `kiwoom_history_backfill`의 후보 DB·기존 수집 스크립트는 앱 모듈이 아니다. 현재 표본 경계는 `infrastructure/historical_backfill.py`, `scripts/probe_historical_backfill.py`, 32비트 COM 표본·연속조회 브리지 `scripts/daishin_stockchart_probe.ps1`과 `scripts/daishin_stockchart_backfill.ps1`에 있다. CREON의 종목 분봉·시총/주식수·수정주가 작업은 참조 `stocks.market_code` 0/10인 코스피·코스닥 개별 주식만 대상으로 하며 다른 상품의 기존 원응답은 삭제하지 않고 작업 원장에서 `excluded`로 분리한다. [과거 수집 기획](docs/HISTORICAL_BACKFILL_PLAN.md)으로 원천별 표본을 확인한 뒤 기존 `research_data_source`, 뉴스 revision, 프로필 저장 경계에 연결한다.

관련 테스트는 각 책임의 `tests/unit/test_*.py`와 `scripts/check_*.py`에서 찾는다. 문서 정리에서 이전 테스트 개수를 새 실행 결과로 복사하지 않는다. 실제 코드 변경 때 해당 경계의 의미 있는 회귀와 필요한 통합 검증을 수행한다.

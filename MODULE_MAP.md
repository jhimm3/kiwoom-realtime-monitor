# 모듈 지도

수정 요청을 받으면 먼저 이 표에서 범위를 좁힌 뒤 해당 파일과 연결 테스트만 읽는다. `main_window.py`나 `journal_process.py` 전체를 먼저 읽지 않는다.

## 실행·구성

| 기능 | 실제 담당 파일 | 먼저 볼 테스트 |
| --- | --- | --- |
| 앱 조립, 프로세스 시작, 데이터 모드 선택 | `src/kiwoom_monitor/bootstrap.py` | `test_kiwoom_client_factory.py`, `test_news_process.py` |
| 사용자 데이터 경로 | `infrastructure/app_paths.py` | 관련 저장소 테스트 |
| 로컬/NAS 모드 설정 | `infrastructure/central_server_config.py` | `test_central_server_config.py`, `test_api_settings_dialog.py` |
| Google Drive 동기화·업데이트 확인/다운로드 worker | 전송과 NAS와 동일한 공통설정 제외 정책은 `infrastructure/persistence/google_drive_sync.py`, `infrastructure/central_settings_sync.py`; 설정 묶음은 `settings_backup.py`, 전체 테마 프로필 직렬화 원본은 `theme_backup.py`; worker 생성·연속 실행·결과 수명은 `presentation/google_drive_worker_controller.py`; 업데이트 확인·다운로드는 `presentation/update_worker_controller.py` | `test_settings_backup.py`, `test_theme_backup.py`, `test_google_drive_sync.py`, `test_google_drive_worker_controller.py`, `test_update_worker_controller.py` |
| 뉴스·매매일지 보조 프로세스 명령 구성·요청 번호·수신 중복 차단·숨김 실행·생존 확인·종료 단계 | `presentation/process_control.py` | `test_process_control.py` |
| 키움 API·NAS 연결 설정 UI/자원 확인 worker | `presentation/api_settings_dialog.py` | `test_api_settings_dialog.py` |
| 기본 설정 탭·저장·초기화 UI | `presentation/settings_dialog.py` | `test_settings_api_hub.py` |
| 메인 창의 종목명 변경 확인·클릭 라벨·신고가 알림 설정·셀 표시 delegate | `presentation/main_window_components.py` | `test_main_window.py` |
| 메인 표 거래대금·시가총액 단위, 상위 테마 HTML, 행 배경 우선순위, 순위 강조 시간, 소수점 제한과 등락률 색상 | `presentation/main_table_formatting.py` | `test_main_table_formatting.py` |
| 메인 표 반응형 행 높이·열 비율·내용 맞춤 창 크기·저장 위치 해석 | 순수 계산은 `presentation/main_window_layout.py`; 열 표시·순서·폭 저장/복원과 자동 맞춤 상태는 `presentation/main_table_column_controller.py`; Qt 이벤트와 메뉴 호출은 `presentation/main_window.py` | `test_main_window_layout.py`, `test_main_table_column_controller.py`, `test_column_settings_repository.py`, 메인 화면 회귀 테스트 |
| 앱 이름·버전·저작권·투자 유의문 | `presentation/app_metadata.py` | 메인 화면·설정·업데이트 테스트 |
| 로컬 중앙 서버 자식 프로세스 | `infrastructure/central_server_process.py` | `test_central_server_process.py` |

## TR Scheduler와 키움 API

| 계층 | 실제 담당 파일 |
| --- | --- |
| 직접 REST 호출 제한·토큰·재시도 | `infrastructure/kiwoom_rest/client.py`, `settings.py`, `local_config.py` |
| 중앙 단일 큐·우선순위·중복 요청 병합 | `central_server/rest_broker.py` |
| 로컬/원격/장애전환/병행검증 선택 | `infrastructure/kiwoom_rest/client_factory.py`, `remote_client.py`, `failover_client.py`, `validation_client.py` |
| 순위 TR | `application/ranking_service.py`, `infrastructure/kiwoom_rest/ranking_worker.py` |
| 분봉·일봉·기본정보·신고가 TR | `application/minute_chart_service.py`, `daily_high_service.py`, `historical_high_service.py`, `stock_fundamentals_service.py`; 대응 `*_worker.py` |
| 화면 신고가 출처 우선순위·ka10001 보완 시 수정주가 보존 | `application/high_price_policy.py` | `test_high_price_policy.py`, `test_main_window.py` |
| 체결·비용·수급 TR | `application/trade_history_service.py`, `trade_cost_service.py`, `investor_flow_service.py`, `program_trade_service.py` |

순위 우선순위 수정은 `rest_broker.py`, `main_window.py`의 `_refresh_rankings`/후속 작업 예약부, 그리고 `test_central_rest_broker.py`, `test_main_window.py`만 먼저 확인한다.

## 실시간 종목조회순위

- NAS 독립 순위·편입 이력·TOP20 지수·장후 차트 보완 및 키움 실시간 원본 단절 시 분 행 중단: `central_server/autonomous_top20.py`
- NAS TOP20 KOSPI/KOSDAQ 분류 원본: `infrastructure/krx/stock_catalog.py`; 중앙 보존 컬렉션 `stock_catalog`
- TOP20 지수 재시작 복구 outbox: `central_server/persistent_outbox.py`
- 서버: `central_server/realtime_collector.py`, `realtime_hub.py`, `market_ingest.py` (`ka10016/ka10001/ka10100`의 영속 저장 포함)
- API: `central_server/app.py`의 `/api/v1/realtime`, `/api/v1/market/snapshots/ranking`
- 클라이언트: `infrastructure/kiwoom_rest/central_realtime_worker.py`, `realtime_worker.py`, `realtime.py`
- 계산: `application/ranking_service.py`, `domain/ranking.py`
- UI/예약: `presentation/main_window.py`의 `MainWindow` 순위 갱신·구독 메서드
- 순위 QThread 생성·신호·현재 worker 수명: `presentation/ranking_worker_controller.py`
- 0B WebSocket QThread 생성·9개 신호 전달·구독 갱신·중지·factory 교체: `presentation/realtime_worker_controller.py`
- 분봉 보완 QThread 생성·신호 전달·강제 보완 문맥과 다음 단계 완료 연결: `presentation/minute_history_worker_controller.py`
- 일봉 기간 신고가 QThread 생성·형식 검증·수신/실패/완료 문맥 전달: `presentation/daily_high_worker_controller.py`
- 종목 기본정보 QThread 생성·형식 검증·수신/실패/완료 문맥 전달: `presentation/fundamentals_worker_controller.py`
- 역사적 신고가 QThread 생성·형식 검증·수신/실패/완료 신호 전달: `presentation/historical_high_worker_controller.py`
- NXT 거래 가능 여부 QThread 생성·형식 검증·수신/실패/완료 신호 전달: `presentation/nxt_eligibility_worker_controller.py`
- 신고가 목록 갱신 QThread 생성·형식 검증·성공/실패/종료 신호 전달: `presentation/new_high_worker_controller.py`
- KRX 상장종목 카탈로그 QThread 생성·형식 검증·자동/수동/후속 실행별 신호 연결: `presentation/krx_stock_catalog_worker_controller.py`
- 다음 조회·저우선순위 양보 시각, 부분 응답 정책, 순위 변경·동일 응답 계산: `application/ranking_schedule.py`
- 밀린 조회 1회 병합, 부분 응답 재시도 횟수, 모달 중 최신 응답 보류, 마지막 순위 상태 소유: `application/ranking_execution.py`
- 0B/0w 구독의 시간대별 종목 선택, 동일 구독 유지, 무중단 종목 교체, 장 종료 및 재시작 판단: `application/realtime_subscription.py`
- KRX/NXT 시간대별 구독 종목·세션 경계·보완조회 휴지·NAS 장애전환 중 TOP20 수집 중단 판단: `application/market_session_schedule.py`
- 장 마감 분봉·일봉 확정 대상·완료 봉 시각·최대 2회/5분 간격 재시도·미확정 판정: `application/market_data_finalization.py`
- 순위 후 분봉·일봉·기본정보·신고가/NXT 보완 대상과 worker 완료 순서 조정: `application/secondary_data_schedule.py`
- DB: 중앙 `central_dataset_snapshots`, 로컬 `stocks`와 화면 메모리 상태
- 테스트: `test_autonomous_top20.py`, `test_ranking_service.py`, `test_ranking_schedule.py`, `test_ranking_execution.py`, `test_ranking_worker_controller.py`, `test_realtime_worker_controller.py`, `test_minute_history_worker_controller.py`, `test_daily_high_worker_controller.py`, `test_fundamentals_worker_controller.py`, `test_historical_high_worker_controller.py`, `test_nxt_eligibility_worker_controller.py`, `test_new_high_worker_controller.py`, `test_krx_stock_catalog_worker_controller.py`, `test_market_session_schedule.py`, `test_realtime_subscription.py`, `test_market_data_finalization.py`, `test_secondary_data_schedule.py`, `test_realtime*.py`, `test_central_realtime_*.py`, `test_main_window.py`

## 거래대금과 TOP20 지수

- 실시간 종목 분봉 계산: `application/minute_trade_value.py`
- 30초 TOP20 코호트 예약·교체, 구간 기준값, 1분 마감과 종료 시 부분 기록: `application/top20_trade_value_collector.py`
- 표시 기간 계산: `application/trade_strength.py`
- 저장/시장지수/TOP20 통계: `infrastructure/persistence/minute_bar_repository.py` (매매일지 시장지수 보완 결과의 분봉·일봉 원자적 일괄 저장 포함)
- 중앙 실시간 봉: `central_server/minute_bars.py`, `market_ingest.py`, `database.py`
- TOP20 차트·전용 창·DB 보완 worker: `presentation/top20_trade_value.py`; worker 생성·저우선순위 실행·신호 수명은 `presentation/top20_market_repair_worker_controller.py`
- TOP20 데이터 공급·수집 결과 저장·통계 창 호출 등 메인 화면 연결: `presentation/main_window.py`의 관련 `MainWindow` 메서드
- 테스트: `test_minute_trade_value.py`, `test_top20_trade_value_collector.py`, `test_top20_market_repair_worker_controller.py`, `test_minute_bar_repository.py`, `test_trade_strength.py`, `test_central_minute_bars.py`, `test_central_market_ingest.py`, `test_main_window.py`의 화면 모드 독립 수집 회귀

## 과거 시장 재현과 시뮬레이션 데이터

- 보존 기준과 현재 수집 공백: `HISTORICAL_DATA_CONTRACT.md`
- 봉·시장 상태·후보군의 거래소·단위·실제/추정·완결 상태·기준시각/가용시각 공통 계약과 기존 문자열 어댑터: `domain/market_data_contract.py`
- 로컬·중앙 공통 메타데이터 행 변환: `infrastructure/market_data_metadata_codec.py`; 로컬 저장은 `persistence/market_data_metadata_*`, 로컬 분봉·일봉 의미 생성은 `persistence/local_bar_observations.py`, 중앙 저장은 `central_server/central_schema.py`, `central_server/database.py`
- 시각 범위별 가용 관측 수·완결성·고정주기 결측 구간 판정: `application/market_data_coverage.py`; 중앙 조회는 `central_server/database.py`와 `central_server/app.py`의 `/api/v1/market/coverage`
- 중앙 순위·TOP20·시장 상태·조회/실시간 분봉·일봉의 관측 의미 생성과 KST 시각 정규화: `central_server/market_observations.py`; 생산자 연결은 `market_ingest.py`, `autonomous_top20.py`, `realtime_collector.py`
- 시점별 순위 원본: `central_server/market_ingest.py`, `central_server/database.py`의 `central_dataset_snapshots`
- 종목 분봉·일봉: `infrastructure/persistence/minute_bar_repository.py`, `persistence/journal_bar_repository.py`, `central_server/minute_bars.py`, `central_server/database.py`
- TOP20 합계·시장 분해·구성 종목·30초 코호트: `application/top20_trade_value_collector.py`, `infrastructure/persistence/minute_bar_repository.py`
- 체결 당시 시장·뉴스·수급·호가 근거: `persistence/journal_snapshot_repository.py`, `persistence/journal_snapshot_service.py`, 필드별 관측 의미 `domain/trade_snapshot_observations.py`, 출처 판정 `domain/snapshot_provenance.py`
- 현재 테마 상태: `infrastructure/persistence/theme_repository.py`, `infrastructure/central_content_sync.py` (시점별 이력은 아직 없으므로 과거 순위와 직접 결합하지 않는다)
- 보존 회귀: `test_market_data_contract.py`, `test_market_data_coverage.py`, `test_central_server_database.py`의 시각별 순위 및 봉/메타데이터 원자적 롤백·범위 조회, `test_central_market_ingest.py`·`test_autonomous_top20.py`·`test_central_realtime_collector.py`의 메타데이터 연결, `test_minute_bar_repository.py`·`test_journal_database.py`의 로컬 봉/체결 문맥 의미·원자성·확정 상태 보존 테스트
- 나스닥·원유 선물 임시 지연 시세: `central_server/external_market_collector.py`, 월물 코드·롤 판단 `central_server/futures_roll.py`, 중앙 `central_external_bars`, 롤 상태 `central_documents.external_market_roll_state`, `test_external_market_collector.py`

과거 시뮬레이션 기능을 수정할 때는 먼저 `HISTORICAL_DATA_CONTRACT.md`에서 원본/파생값, 시점 기준, 누락 의미를 확인한다. 읽기 전용 시뮬레이션 조회 서비스가 생기기 전까지 UI가 저장소 여러 개를 직접 결합하지 않는다.

## 테마

- 파싱/매칭/미리보기: `domain/theme_parser.py`, `domain/theme_text_import.py`, `application/theme_matching.py`, `theme_preview.py`
- DB/프로필: `infrastructure/persistence/theme_repository.py`, `database.py`
- Excel/OCR/백업: `infrastructure/excel/theme_repository.py`, `infrastructure/ocr/paddle_theme_ocr.py`, `persistence/theme_backup.py`; OCR worker 생성·저우선순위 실행·취소·신호 수명은 `presentation/image_theme_ocr_worker_controller.py`
- 중앙 동기화: `infrastructure/central_theme_sync.py`, `central_content_sync.py`. 테마 변경은 변경 시각이 든 디스크 대기 표식을 남기고 실패 시 자동 재시도한다. 시작·재시도에서는 NAS `theme_metadata` 완료 시각과 비교하여 로컬이 최신일 때만 업로드하고 NAS가 최신이면 해당 스냅샷을 적용한다.
- 테마 가져오기·미리보기·편집·프로필 관리 UI: `presentation/theme_dialogs.py`
- 테마 색상 표시 보조: `presentation/theme_colors.py`
- TOP20 테마 빈도·거래대금 합계·상위 테마·제외 종목·정렬키 계산: `application/theme_ranking.py`
- 메인 화면 연결·가져오기 worker 조정: `presentation/main_window.py`의 관련 `MainWindow` 메서드
- 테스트: `test_theme_*.py`, `test_paddle_theme_ocr.py`, `test_image_theme_ocr_worker_controller.py`, `test_central_theme_sync.py`

## 뉴스와 AI

- 별도 프로세스: `news_process.py`
- 뉴스 목록·상세 UI: `presentation/stock_news_window.py`
- 네이버/DART/AI·필터·색상·바로가기 설정창: `presentation/news_settings_dialog.py`
- 뉴스 조회·DB 준비·AI 분석 작업 스레드와 공통 조회 주기: `presentation/news_workers.py`
- 뉴스 목록 행·AI 판단·상세 HTML 표시 계산: `presentation/news_view_model.py`
- 뉴스 자동분석 후보·수동 시작점·단건/묶음 선택 정책: `application/news_auto_analysis.py`
- 뉴스 worker 종료 정리와 AI 실행 가능 여부·일일 한도·진행 문구: `presentation/news_execution.py`
- 공급자: `infrastructure/naver_news.py`, `dart_disclosures.py`, `article_text.py`, `news_ai.py`
- 대상 종목 관점 AI 프롬프트·버전 캐시 키와 공급자 오류 상태: `infrastructure/news_ai.py`; 중앙 적용은 `central_server/ai_service.py`, 로컬 적용은 `presentation/news_workers.py`, 이전 계약 결과 제외는 `persistence/news_ai_repository.py`; NAS 자동분석의 새 identity 제한은 `central_server/news_service.py`, 앱 재시작 자동 후보 정책은 `presentation/stock_news_window.py`
- 사건 묶음/기본 판정: `application/news_grouping.py`, `news_analysis.py`
- 로컬 DB: `persistence/stock_news_repository.py`, `news_ai_repository.py`, `news_database.py`
- 중앙 서비스/API 클라이언트: `central_server/news_service.py`, `ai_service.py`, `infrastructure/central_news_client.py`, `central_ai_client.py`
- 중앙 보존/동기화: `central_content_client.py`, `central_content_sync.py`; 뉴스 프로세스의 60초 변경 감지는 `news_process.py`의 `_news_content_signature`가 뉴스/AI DB만 대상으로 하며 테마 변경은 `central_theme_sync.py`가 별도로 전송
- 테스트: `test_news_*.py`, `test_naver_news_config.py`, `test_article_text.py`, `test_central_news_*.py`, `test_central_ai_*.py`

## 매매일지

- 프로세스·매매일지 본창과 차트 데이터 연결, 일봉 및 시장지수 worker 실행 중 최신 대기 요청 1건 보관·종료 후 연속 실행: `journal_process.py`
- 매매일지→뉴스창 요청번호와 원자적 JSON 명령 기록: `presentation/process_control.py`의 `JsonCommandChannel`; 매매일지는 요청 내용과 결과 문구만 결정
- 체결 확인·기간 체결/비용·분봉/일봉·시장지수 보완 worker: `presentation/journal_workers.py`
- 선택 행의 셀 왼쪽 표시 delegate: `presentation/journal_delegates.py`
- 매매일지 기본 설정·공통 차트 설정 UI와 이동평균선 기본값: `presentation/journal_settings_dialogs.py`
- 본창/따로보기 공통 차트 렌더링·그리기 상태 신호: `presentation/journal_chart_widget.py`; 같은 종목·같은 봉 주기의 그림/봉 수 동기화와 다중 패널 배치: `presentation/detached_chart_window.py`
- `presentation/detached_chart_settings.py`의 예전 전용 색 키는 기존 사용자 설정 호환용으로 남아 있지만, 실제 차트 배경·선 설정은 본창의 공통 차트 설정을 사용한다.
- 분봉·일봉 렌더링, 확대·가로 스크롤·마우스 드래그 이동, 체결 꼬리표, 이동평균선, 차트 그리기 위젯: `presentation/journal_chart_widget.py`
  - 최대 6개 차트 패널·배치·비교 종목/지수·따로보기 이미지 저장: `presentation/detached_chart_window.py`
  - 따로보기 Qt 객체 종료 수명 회귀: `tests/unit/test_detached_chart_window.py`의 `tearDown`
  - 체결 묶음: `application/trade_history_service.py`, `trade_journal_summary.py`
  - 기간 체결·비용 조회, 과거 진입 연결용 회차 구성, 종목·손익·복기 필터, 묶음/개별 체결 선택 시 차트 종목·초점·날짜 범위 결정, 분봉 보완 기준일·상태·체결시각 범위 및 현재 선택 영향 판정: `application/trade_history_query_service.py`
- 수동 매매 묶음 합치기·선택 체결 분리·자동분류 복원의 검증, 수동 ID 발급과 저장 명령: `application/trade_group_edit_service.py`
- 비용: `application/trade_cost_service.py`
- 자동 복기/통계: `trade_review_analysis.py`, `trade_journal_statistics.py`
- 기본 유형/전략팩: `trade_setup_classification.py`, `generic_strategy_evaluator.py`, `strategy_pack.py`, `strategy_pack_extraction.py`, `personal_trade_rules.py`
- 전략팩 등록·강의 추가·규칙 검토·승인·버전 복원 UI: `presentation/strategy_pack_dialogs.py`
- 차트 계산: `trade_chart.py`(봉 집계·일봉 표시 대상·과거 250개 캐시 재사용), `minute_chart_service.py`, `market_index_chart_service.py`, `journal_chart_layout.py`(가격축·시간눈금·종가/체결 문구·레이블 배치)
- 로컬 DB 저장/조회: `persistence/journal_database.py`, `entry_snapshot_writer.py`, `journal_backup.py`
- 매매일지 테이블 생성·호환성 복구: `persistence/journal_schema.py`
- 체결 시점 순위·대금·테마·뉴스·수급·호가·시장 스냅샷과 장후 보완: `persistence/journal_snapshot_repository.py`, `entry_snapshot_writer.py`
- 매매 회차 스냅샷 조회·누락 뉴스 검색 범위·장후 뉴스 연결 조립: `persistence/journal_snapshot_service.py`
- 스냅샷 필드별 실시간 관측/장후 보완/누락 판정: `domain/snapshot_provenance.py`
- 매매 회차별 스냅샷 연결·매수 진입 자료 선택·판정 불가 항목 정리: `application/trade_snapshot_context.py`
- 회차별 자동분석 본문·진입 스냅샷·시장 상태 표시 문구: `presentation/trade_review_formatting.py`
- 기간 손익·비용 요약, 매매 묶음 표 15개 열·유형 덮어쓰기 표시, 매매유형 요약·회차 편집 행·연결 상태·전체 분석 문서 화면 모델: `presentation/trade_review_view_model.py`
- 기본 강의/사용자 전략팩별 복기 참고자료명·원칙 수 계산: `application/strategy_review_context.py`
- 기본분석·사용자 전략팩 후보 평가·표시방식 선택·회차별 수동 유형 적용: `application/trade_strategy_coordinator.py`
- 자동분석 전 일봉 준비·자동 판정 저장·과거 단일 수동 유형 이전·전략 초안 수 집계 조정: `application/trade_analysis_preparation_service.py`
- 회차별 분석 입력 구성·수동/자동 근거 분기·스냅샷 판정 범위 보정·분석기 실행: `application/trade_episode_analysis_service.py`
- 개인 원칙·구조화 원칙·전략팩·강의 추출 초안·버전 저장: `persistence/journal_strategy_settings_repository.py`
- 매매유형·체결·수동 묶음·복기·실제 비용 저장과 체결/비용 동기화 결과의 원자적 일괄 반영: `persistence/journal_trade_repository.py`
- 분봉·일봉·시장지수 봉, 분봉/관측 메타데이터/백필 상태의 원자적 결과 저장과 메인 DB 읽기 전용 가져오기: `persistence/journal_bar_repository.py`
- 중앙 동기화: 병합·업로드는 `infrastructure/central_journal_sync.py`의 `CentralJournalSyncService`; 중복 실행 차단·백그라운드 스레드·오류 격리는 같은 모듈의 `CentralJournalSyncRunner`
- 중앙 동기화 공통 배치·문서 envelope·로컬 테이블 확인: `infrastructure/central_sync_utils.py`
- 테스트: `test_journal_*.py`, `test_trade_*.py`, `test_strategy_pack*.py`, `test_strategy_review_context.py`, `test_trade_strategy_coordinator.py`, `test_trade_episode_analysis_service.py`, `test_trade_group_edit_service.py`, `test_trade_review_view_model.py`, `test_trade_chart.py`, `test_detached_chart_window.py`, `test_journal_detached_flow.py`, `test_snapshot_provenance.py`, `test_journal_snapshot_service.py`, `test_trade_snapshot_context.py`, `test_trade_review_formatting.py`, `test_journal_workers.py`

매매일지의 분석식만 고칠 때는 `journal_process.py`를 먼저 건드리지 않고 `application/trade_*`와 해당 단위 테스트를 수정한다. 기간 요약·비용/순손익/유형 표시는 `trade_review_view_model.py`, 일봉 준비·판정 저장·과거 유형 이전 순서는 `trade_analysis_preparation_service.py`, 실제 저장소 조회와 위젯·선택 상태·화면 배치·그리기·이미지 저장은 `journal_process.py`에서 다룬다.

## DB와 설정

  - 메인 스키마/초기값: `persistence/database.py`
  - 로컬 SQLite 공통 마이그레이션 실행기: `persistence/schema_migrations.py`
  - 로컬 SQLite 읽기 연결·쓰기 트랜잭션/종료 경계: `persistence/sqlite_connections.py`
  - 로컬 시장 관측 메타데이터 스키마·저장: `persistence/market_data_metadata_schema.py`, `persistence/market_data_metadata_repository.py`; 의미 계약은 `domain/market_data_contract.py`
  - 마이그레이션 회귀(기존 데이터 보존·멱등성·실패 롤백·신버전 거부): `tests/unit/test_schema_migrations.py`
  - 관측 시각·출처 round-trip/보수적 레거시 판정: `tests/unit/test_market_data_metadata_repository.py`
  - 세부 저장소: `persistence/*_repository.py`
  - 뉴스 스키마/마이그레이션 기준선: `persistence/news_schema.py`; 기존 메인 DB 이전은 `news_database.py`, 저장은 `stock_news_repository.py`, `news_ai_repository.py`
- 매매일지 스키마: `persistence/journal_schema.py`
  - 중앙 SQLite/PostgreSQL: `central_server/database.py`
  - 중앙 SQLite/PostgreSQL 공통 마이그레이션 원장·실행기: `central_server/schema_migrations.py`
  - 중앙 SQLite/PostgreSQL 공통 봉 열 계약·행 변환·조회 범위 보정과 문서/스냅샷 JSON 행 codec·문서 조회 조건: `central_server/database_codec.py`
  - 중앙/로컬 공통 관측 메타데이터 행 codec: `infrastructure/market_data_metadata_codec.py`
  - 중앙 SQLite/PostgreSQL 테이블·인덱스 방언별 실행 명세: `central_server/central_schema.py`
- 공통 설정과 메인 표 표시·순서 중앙 병합: `central_settings_sync.py`, `persistence/settings_repository.py`, `persistence/column_settings_repository.py`
- 뉴스·AI·매매일지 뉴스 연결·전체 테마 중앙 병합: `central_content_sync.py`, 즉시 테마 교체와 영속 재시도 dispatcher는 `central_theme_sync.py`
- NAS 뉴스·AI 운영 설정의 GET/PUT 단일 경계와 로컬 뉴스 설정 미러: `infrastructure/central_operational_settings.py`; NAS 연결 설정 UI와 뉴스 설정 UI가 이 경계를 함께 사용한다.
- 메인 순위표 열 표시·순서 편집 UI: `presentation/column_manager_dialog.py`
- Google Drive: `persistence/google_drive_sync.py`, `settings_backup.py`, `theme_backup.py`, `news_ai_backup.py`, `journal_backup.py`

## NAS API

- 라우트와 입력 검증: `central_server/app.py`
- 공개 버전/기능 문서: `central_server/contracts.py`
- 환경설정: `central_server/config.py`
- DB: `central_server/database.py`
- 상태 검사: `central_server/deployment_check.py`, `resource_usage.py`
- 배포: `deploy/synology/`
- 실제 PostgreSQL 경계 검사: `scripts/check_postgres_integration.py` (NAS 서버 컨테이너 안에서 실행)
- 인증 API·WebSocket·자원·스냅샷 운영 검사: `scripts/check_nas_operational.py` (개발 PC/다른 PC 공용)
- 클라이언트 계약: `infrastructure/central_*_client.py`, `kiwoom_rest/remote_client.py`
  - 테스트: 모든 `test_central_*.py`(스키마 버전은 `test_central_schema_migrations.py`), `test_remote_kiwoom_rest_client.py`

## 큰 UI 파일에서 먼저 찾을 클래스

- `main_window.py`: `MainWindow`
- `journal_process.py`: `JournalWindow`
- `stock_news_window.py`: `NewsCellMarkerDelegate`, `StockNewsWindow`
- `news_settings_dialog.py`: `NaverNewsSettingsDialog`
- `news_workers.py`: `NewsSearchWorker`, `NewsPrepareWorker`, `AINewsWorker`
- `news_view_model.py`: `NewsDisplayRow`와 뉴스 표시용 순수 함수
- `news_auto_analysis.py`: 자동/수동 AI 분석 후보 선택 순수 함수
- `news_execution.py`: worker 수명 정리와 AI 실행 경계 정책

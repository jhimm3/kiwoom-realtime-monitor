# JSON 문서 종류 지도

`central_documents`는 물리 테이블 하나지만 최소 **94개 코드상 문서 종류**를 확인했다. 이 중 **88개**는 범용 document 관측 registry에 등록되어 있고 나머지는 계좌·제어 등 전용 저장 경로에서 발견했다. 실제 DB의 DISTINCT collection 목록이나 문서 건수를 읽은 결과가 아니며, 동적으로 만들어지는 모든 문자열을 망라했다고 주장하지 않는다.

공통 키는 `(collection, owner, document_key)`이고 `document_json`에 도메인별 내용이 들어간다. 같은 `owner` 문자열이어도 다른 collection이면 다른 자료다. 최신 투영, 불변 이력, 완료 표식이 섞여 있으므로 collection을 빼고 연결하거나 일괄 삭제하면 안 된다.

| 문서 종류 | 무엇을 보존하는가 | 코드 근거(대표) |
|---|---|---|
| `account_entry_symbols_daily` | 계좌에서 당일 진입한 종목 목록 | [autonomous_top20.py:L524](../../src/kiwoom_monitor/central_server/autonomous_top20.py) · [autonomous_top20.py:L878](../../src/kiwoom_monitor/central_server/autonomous_top20.py) |
| `app_column_settings` | 앱 표의 열 설정 | [app.py:L2391](../../src/kiwoom_monitor/central_server/app.py) · [central_settings_sync.py:L36](../../src/kiwoom_monitor/infrastructure/central_settings_sync.py) |
| `app_settings` | 앱 일반 설정 | [app.py:L2391](../../src/kiwoom_monitor/central_server/app.py) · [central_settings_sync.py:L35](../../src/kiwoom_monitor/infrastructure/central_settings_sync.py) |
| `candidate_flow_capture` | 후보 편입 시점 수급 확보 근거 | [autonomous_top20.py:L1147](../../src/kiwoom_monitor/central_server/autonomous_top20.py) · [autonomous_top20.py:L1161](../../src/kiwoom_monitor/central_server/autonomous_top20.py) |
| `candidate_flow_finalization` | 후보 수급 장후 최종화 근거 | [autonomous_top20.py:L1208](../../src/kiwoom_monitor/central_server/autonomous_top20.py) · [autonomous_top20.py:L1237](../../src/kiwoom_monitor/central_server/autonomous_top20.py) |
| `condition_search_status` | 조건검색 수집 상태 | [app.py:L1899](../../src/kiwoom_monitor/central_server/app.py) · [market_events.py:L393](../../src/kiwoom_monitor/central_server/market_events.py) |
| `credential_profile_requests` | 프로필 생성 요청의 멱등 기록 | [database.py:L579](../../src/kiwoom_monitor/central_server/database.py) · [database.py:L597](../../src/kiwoom_monitor/central_server/database.py) |
| `credential_vault_state` | 암호화 자격증명 vault의 상태·revision 근거(비밀키 원문 아님) | [credential_store.py:L104](../../src/kiwoom_monitor/central_server/credential_store.py) · [credential_store.py:L108](../../src/kiwoom_monitor/central_server/credential_store.py) |
| `daily_bar_history_coverage` | 일봉 이력 범위와 준비 완료 근거 | [daily_bar_coverage.py:L10](../../src/kiwoom_monitor/application/daily_bar_coverage.py) |
| `execution_feedback_evidence` | 모의/실행: 실행 피드백 근거 | [forward_evaluation_repository.py:L94](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_feedback_improvement_proposals` | 모의/실행: 개선 제안 | [forward_evaluation_repository.py:L96](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_feedback_revalidation_receipts` | 모의/실행: 재검증 결과 영수증 | [forward_evaluation_repository.py:L99](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_feedback_revalidation_requests` | 모의/실행: 재검증 요청 | [forward_evaluation_repository.py:L98](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_feedback_reviews` | 모의/실행: 피드백 검토 | [forward_evaluation_repository.py:L95](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_feedback_strategy_versions` | 모의/실행: 검토한 전략 버전 | [forward_evaluation_repository.py:L97](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_forward_profiles` | 모의/실행: 전진 평가 설정 | [forward_evaluation_repository.py:L91](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_forward_reports` | 모의/실행: 전진 평가 보고서 | [forward_evaluation_repository.py:L92](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_mock_automation_admission_by_spec` | 모의/실행: 실행 명세별 입장 승인 참조 | [forward_evaluation_repository.py:L102](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_mock_automation_admissions` | 모의/실행: 모의 자동실행 입장 승인 | [forward_evaluation_repository.py:L101](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_mock_automation_approved_gates` | 모의/실행: 승인된 판단 게이트 참조 | [forward_evaluation_repository.py:L108](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_mock_automation_candidate_packages` | 모의/실행: 후보 실행 패키지 | [forward_evaluation_repository.py:L113](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_mock_automation_control` | 계좌별 모의 자동실행 제어 revision·승인 경계 | [database.py:L960](../../src/kiwoom_monitor/central_server/database.py) · [database.py:L2532](../../src/kiwoom_monitor/central_server/database.py) |
| `execution_mock_automation_current_recovery` | 모의/실행: 현재 복구 판단 투영 | [forward_evaluation_repository.py:L106](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_mock_automation_current_risk` | 모의/실행: 현재 위험 판단 투영 | [forward_evaluation_repository.py:L117](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_mock_automation_current_stop` | 모의/실행: 현재 중지 상태 투영 | [forward_evaluation_repository.py:L112](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_mock_automation_decision_gates` | 모의/실행: 실행 전 판단 게이트 이력 | [forward_evaluation_repository.py:L107](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_mock_automation_dispatch_by_intent` | 모의/실행: 주문 의도별 dispatch 참조 | [forward_evaluation_repository.py:L110](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_mock_automation_dispatch_receipts` | 모의/실행: dispatch 영수증 | [forward_evaluation_repository.py:L109](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_mock_automation_eligibility_policies` | 모의/실행: 실행 적격 정책 | [forward_evaluation_repository.py:L114](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_mock_automation_eligibility_receipts` | 모의/실행: 실행 적격 판정 영수증 | [forward_evaluation_repository.py:L115](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_mock_automation_lease_by_admission` | 모의/실행: 입장 승인별 임대 참조 | [forward_evaluation_repository.py:L104](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_mock_automation_lease_receipts` | 모의/실행: 임대 영수증 | [forward_evaluation_repository.py:L103](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_mock_automation_recovery_decisions` | 모의/실행: 복구 판단 이력 | [forward_evaluation_repository.py:L105](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_mock_automation_risk_snapshots` | 모의/실행: 위험 판단 snapshot 이력 | [forward_evaluation_repository.py:L116](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_mock_automation_runner_current` | 모의/실행: 실행기 재개 checkpoint | [mock_automation_runner.py:L356](../../src/kiwoom_monitor/central_server/mock_automation_runner.py) · [mock_automation_runner.py:L453](../../src/kiwoom_monitor/central_server/mock_automation_runner.py) |
| `execution_mock_automation_specs` | 모의/실행: 모의 자동실행 명세 | [forward_evaluation_repository.py:L100](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_mock_automation_stop_revisions` | 모의/실행: 중지 결정 이력 | [forward_evaluation_repository.py:L111](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `execution_strategy_stage_revisions` | 모의/실행: 전략 단계 변경 이력 | [forward_evaluation_repository.py:L93](../../src/kiwoom_monitor/infrastructure/persistence/forward_evaluation_repository.py) |
| `external_market_collection_status` | 외부시장 수집 상태 | [external_market_collector.py:L257](../../src/kiwoom_monitor/central_server/external_market_collector.py) |
| `external_market_roll_state` | 외부시장 계약 전환 진행 상태 | [external_market_collector.py:L266](../../src/kiwoom_monitor/central_server/external_market_collector.py) · [external_market_collector.py:L274](../../src/kiwoom_monitor/central_server/external_market_collector.py) |
| `historical_highs` | 일봉에서 계산한 기간별 신고가 결과 | [app.py:L2393](../../src/kiwoom_monitor/central_server/app.py) · [autonomous_top20.py:L1171](../../src/kiwoom_monitor/central_server/autonomous_top20.py) |
| `journal_backfill` | 매매일지: 차트/부가자료 보완 상태 | [app.py:L2385](../../src/kiwoom_monitor/central_server/app.py) · [central_journal_sync.py:L47](../../src/kiwoom_monitor/infrastructure/central_journal_sync.py) |
| `journal_costs` | 매매일지: 거래 비용 | [app.py:L2385](../../src/kiwoom_monitor/central_server/app.py) · [central_journal_sync.py:L42](../../src/kiwoom_monitor/infrastructure/central_journal_sync.py) |
| `journal_cycle_overrides` | 매매일지: 거래 회차 수동 보정 | [app.py:L2384](../../src/kiwoom_monitor/central_server/app.py) · [central_journal_sync.py:L38](../../src/kiwoom_monitor/infrastructure/central_journal_sync.py) |
| `journal_entry_snapshots` | 매매일지: 진입 당시 관측 | [app.py:L2384](../../src/kiwoom_monitor/central_server/app.py) · [central_journal_sync.py:L40](../../src/kiwoom_monitor/infrastructure/central_journal_sync.py) |
| `journal_fills` | 매매일지: 체결 | [app.py:L2383](../../src/kiwoom_monitor/central_server/app.py) · [central_journal_sync.py:L35](../../src/kiwoom_monitor/infrastructure/central_journal_sync.py) |
| `journal_group_overrides` | 매매일지: 거래 묶음 수동 보정 | [app.py:L2384](../../src/kiwoom_monitor/central_server/app.py) · [central_journal_sync.py:L39](../../src/kiwoom_monitor/infrastructure/central_journal_sync.py) |
| `journal_news_link` | 매매일지: 뉴스 연결 | [app.py:L2381](../../src/kiwoom_monitor/central_server/app.py) · [app.py:L2493](../../src/kiwoom_monitor/central_server/app.py) |
| `journal_reviews` | 매매일지: 거래 복기 | [app.py:L2383](../../src/kiwoom_monitor/central_server/app.py) · [central_journal_sync.py:L36](../../src/kiwoom_monitor/infrastructure/central_journal_sync.py) |
| `journal_settings` | 매매일지: 설정 | [app.py:L2383](../../src/kiwoom_monitor/central_server/app.py) · [central_journal_sync.py:L34](../../src/kiwoom_monitor/infrastructure/central_journal_sync.py) |
| `journal_setups` | 매매일지: 매매 분류 | [app.py:L2383](../../src/kiwoom_monitor/central_server/app.py) · [central_journal_sync.py:L37](../../src/kiwoom_monitor/infrastructure/central_journal_sync.py) |
| `journal_stocks` | 매매일지: 종목 정보 | [app.py:L2385](../../src/kiwoom_monitor/central_server/app.py) · [central_journal_sync.py:L46](../../src/kiwoom_monitor/infrastructure/central_journal_sync.py) |
| `journal_sync_states` | 매매일지: 동기화 상태 | [app.py:L2390](../../src/kiwoom_monitor/central_server/app.py) · [app.py:L2402](../../src/kiwoom_monitor/central_server/app.py) |
| `journal_v2_analysis_revisions` | 매매일지 v2: 분석 revision | [app.py:L2389](../../src/kiwoom_monitor/central_server/app.py) · [central_journal_sync.py:L63](../../src/kiwoom_monitor/infrastructure/central_journal_sync.py) |
| `journal_v2_costs` | 매매일지 v2: 거래 비용 | [app.py:L2388](../../src/kiwoom_monitor/central_server/app.py) · [central_journal_sync.py:L58](../../src/kiwoom_monitor/infrastructure/central_journal_sync.py) |
| `journal_v2_cycle_overrides` | 매매일지 v2: 거래 회차 수동 보정 | [app.py:L2387](../../src/kiwoom_monitor/central_server/app.py) · [central_journal_sync.py:L54](../../src/kiwoom_monitor/infrastructure/central_journal_sync.py) |
| `journal_v2_enrichment_tasks` | 매매일지 v2: 부가자료 보완 작업 | [app.py:L2389](../../src/kiwoom_monitor/central_server/app.py) · [central_journal_sync.py:L62](../../src/kiwoom_monitor/infrastructure/central_journal_sync.py) |
| `journal_v2_entry_snapshots` | 매매일지 v2: 진입 당시 관측 | [app.py:L2388](../../src/kiwoom_monitor/central_server/app.py) · [central_journal_sync.py:L56](../../src/kiwoom_monitor/infrastructure/central_journal_sync.py) |
| `journal_v2_fills` | 매매일지 v2: 체결 | [app.py:L2386](../../src/kiwoom_monitor/central_server/app.py) · [central_journal_sync.py:L51](../../src/kiwoom_monitor/infrastructure/central_journal_sync.py) |
| `journal_v2_group_overrides` | 매매일지 v2: 거래 묶음 수동 보정 | [app.py:L2387](../../src/kiwoom_monitor/central_server/app.py) · [central_journal_sync.py:L55](../../src/kiwoom_monitor/infrastructure/central_journal_sync.py) |
| `journal_v2_news_links` | 매매일지 v2: 뉴스 연결 | [app.py:L2381](../../src/kiwoom_monitor/central_server/app.py) · [app.py:L2475](../../src/kiwoom_monitor/central_server/app.py) |
| `journal_v2_research_links` | 매매일지 v2: 연구 연결 | [app.py:L2390](../../src/kiwoom_monitor/central_server/app.py) · [central_journal_sync.py:L64](../../src/kiwoom_monitor/infrastructure/central_journal_sync.py) |
| `journal_v2_reviews` | 매매일지 v2: 거래 복기 | [app.py:L2386](../../src/kiwoom_monitor/central_server/app.py) · [central_journal_sync.py:L52](../../src/kiwoom_monitor/infrastructure/central_journal_sync.py) |
| `journal_v2_setups` | 매매일지 v2: 매매 분류 | [app.py:L2386](../../src/kiwoom_monitor/central_server/app.py) · [central_journal_sync.py:L53](../../src/kiwoom_monitor/infrastructure/central_journal_sync.py) |
| `journal_v2_sync_states` | 매매일지 v2: 동기화 상태 | [app.py:L2390](../../src/kiwoom_monitor/central_server/app.py) · [app.py:L2402](../../src/kiwoom_monitor/central_server/app.py) |
| `krx_trading_day_observations` | 시장운영 수신으로 확인한 KRX 거래일 근거 | [autonomous_top20.py:L629](../../src/kiwoom_monitor/central_server/autonomous_top20.py) · [autonomous_top20.py:L605](../../src/kiwoom_monitor/central_server/autonomous_top20.py) |
| `market_data_coverage` | 분봉 장후 최종화 완료 근거 | [app.py:L2819](../../src/kiwoom_monitor/central_server/app.py) · [app.py:L2644](../../src/kiwoom_monitor/central_server/app.py) |
| `market_data_coverage_daily` | 일봉 장후 보완 완료 근거 | [app.py:L2661](../../src/kiwoom_monitor/central_server/app.py) · [autonomous_top20.py:L1385](../../src/kiwoom_monitor/central_server/autonomous_top20.py) |
| `market_data_coverage_intraday` | 편입 시점까지 분봉 확보 근거 | [autonomous_top20.py:L1104](../../src/kiwoom_monitor/central_server/autonomous_top20.py) · [autonomous_top20.py:L1135](../../src/kiwoom_monitor/central_server/autonomous_top20.py) |
| `market_event_sessions` | 시장 이벤트 수집 세션 표식 | [market_events.py:L298](../../src/kiwoom_monitor/central_server/market_events.py) · [market_events.py:L333](../../src/kiwoom_monitor/central_server/market_events.py) |
| `market_index_chart_coverage` | 시장지수 차트 자료 확보 근거 | [autonomous_top20.py:L1248](../../src/kiwoom_monitor/central_server/autonomous_top20.py) · [autonomous_top20.py:L1299](../../src/kiwoom_monitor/central_server/autonomous_top20.py) |
| `minute_trade_value_comparisons` | 조회/실시간 분봉 거래대금 비교 결과 | [app.py:L1876](../../src/kiwoom_monitor/central_server/app.py) · [market_ingest.py:L261](../../src/kiwoom_monitor/central_server/market_ingest.py) |
| `news_ai` | 종목별 뉴스 AI 결과 투영 | [ai_service.py:L136](../../src/kiwoom_monitor/central_server/ai_service.py) · [ai_service.py:L259](../../src/kiwoom_monitor/central_server/ai_service.py) |
| `news_ai_shared` | 동일 기사/입력에 공유하는 AI 결과 | [app.py:L2381](../../src/kiwoom_monitor/central_server/app.py) · [central_content_sync.py:L432](../../src/kiwoom_monitor/infrastructure/central_content_sync.py) |
| `news_article` | 뉴스 기사 현재 문서 | [app.py:L2381](../../src/kiwoom_monitor/central_server/app.py) · [news_service.py:L517](../../src/kiwoom_monitor/central_server/news_service.py) |
| `news_assessment` | 뉴스 규칙 판단의 현재 문서 | [app.py:L1760](../../src/kiwoom_monitor/central_server/app.py) · [news_jobs.py:L256](../../src/kiwoom_monitor/central_server/news_jobs.py) |
| `news_automation_settings` | 뉴스 자동 수집·분석 설정 | [news_service.py:L255](../../src/kiwoom_monitor/central_server/news_service.py) · [news_service.py:L205](../../src/kiwoom_monitor/central_server/news_service.py) |
| `news_original_publication` | 원문 확인 발행시각 근거 | [news_jobs.py:L245](../../src/kiwoom_monitor/central_server/news_jobs.py) · [news_jobs.py:L191](../../src/kiwoom_monitor/central_server/news_jobs.py) |
| `news_request_usage` | 뉴스 요청 사용량 문서 | [ai_service.py:L167](../../src/kiwoom_monitor/central_server/ai_service.py) · [ai_service.py:L261](../../src/kiwoom_monitor/central_server/ai_service.py) |
| `news_sync` | 종목 뉴스 동기화 진행 상태 | [app.py:L2381](../../src/kiwoom_monitor/central_server/app.py) · [news_service.py:L433](../../src/kiwoom_monitor/central_server/news_service.py) |
| `news_watchlist` | 뉴스 관심 대상 | [app.py:L2381](../../src/kiwoom_monitor/central_server/app.py) · [news_service.py:L219](../../src/kiwoom_monitor/central_server/news_service.py) |
| `real_account_event` | 실계좌 상태/이벤트 관측 문서 | [database.py:L690](../../src/kiwoom_monitor/central_server/database.py) · [database.py:L2689](../../src/kiwoom_monitor/central_server/database.py) |
| `real_account_recovery` | 실계좌 복구 근거 문서 | [database.py:L667](../../src/kiwoom_monitor/central_server/database.py) · [database.py:L2678](../../src/kiwoom_monitor/central_server/database.py) |
| `server_account_settings` | 검증된 계좌 범위별 설정과 revision | [database.py:L762](../../src/kiwoom_monitor/central_server/database.py) · [database.py:L829](../../src/kiwoom_monitor/central_server/database.py) |
| `server_market_profile_settings` | 서버 시장조회용 프로필 설정 | [database.py:L697](../../src/kiwoom_monitor/central_server/database.py) · [database.py:L756](../../src/kiwoom_monitor/central_server/database.py) |
| `server_operational_settings` | 중앙서버 운영 설정 | [app.py:L183](../../src/kiwoom_monitor/central_server/app.py) · [app.py:L1467](../../src/kiwoom_monitor/central_server/app.py) |
| `stock_catalog` | 종목명·시장 등 종목 목록 | [autonomous_top20.py:L441](../../src/kiwoom_monitor/central_server/autonomous_top20.py) · [autonomous_top20.py:L483](../../src/kiwoom_monitor/central_server/autonomous_top20.py) |
| `stock_fundamentals` | ka10001 종목 기본정보 최신 관측 | [app.py:L2392](../../src/kiwoom_monitor/central_server/app.py) · [app.py:L2354](../../src/kiwoom_monitor/central_server/app.py) |
| `stock_nxt_eligibility` | 날짜별 NXT 대상 여부 관측 | [app.py:L2392](../../src/kiwoom_monitor/central_server/app.py) · [app.py:L2612](../../src/kiwoom_monitor/central_server/app.py) |
| `stock_price_references` | 종목 가격 기준 정보 | [app.py:L2392](../../src/kiwoom_monitor/central_server/app.py) · [market_events.py:L520](../../src/kiwoom_monitor/central_server/market_events.py) |
| `theme_metadata` | 테마 프로필의 버전·갱신 근거 | [app.py:L2382](../../src/kiwoom_monitor/central_server/app.py) · [app.py:L2525](../../src/kiwoom_monitor/central_server/app.py) |
| `theme_profile` | 테마 프로필 정의 | [app.py:L2382](../../src/kiwoom_monitor/central_server/app.py) · [app.py:L2525](../../src/kiwoom_monitor/central_server/app.py) |
| `theme_stock` | 테마와 종목 구성 | [app.py:L2382](../../src/kiwoom_monitor/central_server/app.py) · [app.py:L2525](../../src/kiwoom_monitor/central_server/app.py) |
| `top20_daily_entrants` | 당일 한 번이라도 TOP20에 들어온 종목의 최초 편입 기록 | [autonomous_top20.py:L343](../../src/kiwoom_monitor/central_server/autonomous_top20.py) · [autonomous_top20.py:L511](../../src/kiwoom_monitor/central_server/autonomous_top20.py) |

## 자주 혼동하는 관계

- `ranking`: 키움 조회순위 응답 스냅샷(테이블은 `central_dataset_snapshots`). 보관 시간창은 07:55 이상 08:06 미만 KST이며 08:05분을 포함한다.
- `top20_membership`: 관측 시점의 TOP20 구성 이력(동일 snapshot 테이블). 장중 구성 이력은 별도로 유지한다.
- `top20_daily_entrants`: 하루에 한 번이라도 편입된 종목의 최초 편입 문서. 장후 보완 대상을 결정하며 매번 last_seen을 갱신하는 순위 이력이 아니다.
- `candidate_flow_capture`: 편입 준비의 수급 확보 상태. 주식 체결 때만 생기는 자료가 아니다.
- `daily_bar_history_coverage`, `market_data_coverage_daily`, `market_data_coverage_intraday`, `market_data_coverage`: 각각 필요한 범위·시점이 다르다. 봉 1개 존재만으로 모든 완료 상태를 대신할 수 없다.
- `news_article` / `news_ai`는 조회용 문서이고 기사·본문·분석 revision 테이블은 계보 이력이다. 한쪽을 지워도 다른 쪽이 자동 삭제되는 FK는 없다.
- 모의 자동실행의 `current_*`, `*_by_*`는 이력의 현재값/참조 투영이다. 이력과 별도 저장·재시도하는 경로가 있으므로 전부 한 COMMIT이라고 해석하면 안 된다.

## JSON 내부 필드의 범위

이 사전은 모든 **SQL 컬럼**과 알려진 collection을 설명한다. JSON 내부는 동적 문서이며 별도의 고정 SQL 스키마가 없다. 실제 저장된 모든 JSON key를 얻으려면 데이터 전수/표본 조회와 도메인 버전 검증이 필요하다. 이번 조사에서는 기사 원문·계좌 내용·비밀정보를 수집하지 않았다. 각 collection의 링크에서 작성/직렬화하는 문서 계약을 확인한다.

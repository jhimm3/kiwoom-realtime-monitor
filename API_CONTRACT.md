# NAS API 계약

`search_calls`는 연구 요청의 검색 동작 시도 건수다. 완료되지 않은 웹 동작은 안전한 URL 근거에 포함하지 않고 기존 web_actions 항목의 선택 status에 failed/in_progress/incomplete/unknown을 남긴다. 완료된 모델 텍스트·사용량은 검색 동작/출처 부재로 버리지 않는다. 일반 기사 AI 응답 경로와 인증·전송 오류는 바꾸지 않는다.

2026-10-17 후속 사용자 정정이 우선한다. 장전 `result`는 연구 자료이며 미확인 출처/시각/사실 분류를 이유로 값·evidence·참조를 지우거나 run을 실패시키지 않는다. 확인 부족은 기존 unknowns로 전달하고 모델 반환 분류와 구분한다. 근거 참조 배열은 빈 배열을 허용하며 unknowns의 개수로 연구 결과를 거절하지 않는다. JSON/수치/정의되지 않은 참조·시간 기반 과거 비교의 구조 검사는 유지한다. 기존 GET은 원래 failed run의 보존 raw_response를 읽어 현행 정책으로 처리할 수 있으면 result와 `result_recovered_from_raw_response=true`를 부가한다. 원 status/error/history와 저장 자료는 유지하며 새 AI 호출/운영 DB 쓰기가 없다. context의 input_quality_ok는 진단이며 연구 공급 차단 조건이 아니다. 아래의 미확인 제외 정책은 이전 기록이다.

2026-10-17 사용자 승인에 따라 장전 결과 검증 시 `verification=unverified`이고 웹 도구 반환 출처에도 없는 항목과 그 근거 참조만 분리한다. 제외된 ID·주소·사유는 기존 `result.unknowns`에 남기며 `raw_response`·과거 실패 run은 보존한다. `original_read`/`secondary_only`의 출처 검사와 확정 사실/수치의 남은 근거·시각/참조 검증은 유지한다. 기존 GET/설정/예약/claim·출력 스키마는 바꾸지 않는다.

2026-10-17 장전 run의 선택 `web_actions`는 웹 도구의 동작 종류(search/open_page/find_in_page/other)와 실제 반환된 안전한 URL 목록만 담는다. 같은 인증의 기존 GET에 부가하며 요청·설정 revision·예약/claim·결과 검증은 바뀌지 않는다. 검색어·프롬프트·계정/토큰·원시 provider 응답을 이 필드에 포함하지 않는다. URL이 출처 목록에 있다고 원문 내용 검증을 완료했다는 뜻은 아니며 기존 evidence 검증을 유지한다.

## 2026-10-16 00종합 가격 기준선 관측 (로컬·미배포)

기존 `leader_breakout/v1` 정책에 선택 `reference_level`을 추가한다. 정확한 키는 `type=prior_window_high`, `window_seconds=null 또는 명시 양의 정수`, `exclude_current_interval=true`다. 기존 필드가 없는 v1 직렬화/hash는 유지한다. native1분으로 증명할 수 없는 창을 반올림하지 않으며 null은 기준 미정이다. 기본 초안은 이 구조와 `observed_price_cross`/`completed_minute_close_cross` 이름만 명시하고 숫자는 넣지 않는다. 명시 환경 override와 기존 저장 원문은 보존한다.

기존 후보 조회 `quality`에 새 프로필의 선택 `document_baseline_observations` 목록(최대200)을 부가한다. 실제 가격/시각/원문 ref, reference의 창·고가·quality·대표 input_refs 최대3개/전체 input_count·input_refs_hash, 관측 상태와 `observed_price_cross`, 미정 사유를 담는다. 상태는 DATA_BLOCKED/WATCH/ABOVE_REFERENCE/CROSSED_UNCONFIRMED이며 `confirmation_verified=false`, `proposal=WAIT`, `order_authorized=false`다. native 기준 자료의 영속성은 `not_verified`로 표시한다. 0B FID14 일중 누적 대금과 설정할 비교 대금 기준을 구분한다. 주도주 적격은 명시 context의 현재 가용한 사실만 사용한다. 관측은 기존 shadow decision JSON의 `baseline_observation`에 남고 신규 후보/ENTER/알림을 만들지 않는다.

분봉 판단 연구 결과의 각 decision에도 같은 선택 `baseline_observation`을 부가한다. 완료 종가 교차 분석이며 fake0B/체결/손익이 아니다. 새 프로필은 `document_baseline_observation_only`로 실행 계약/진입 승인을 차단한다. 새 endpoint/인증·주문 승인 권한/DB 스키마는 추가하지 않는다. [호출 경로와 검증](docs/workstreams/realtime-entry.md#2026-10-16-00종합에-명시된-가격-기준선-연결-로컬).

## 2026-10-16 현재 0B 테마 H2의 시작가격 기준 (로컬)

기존 RAM Shadow context의 현재0B `leadership.facts`에 선택 진단 `price_return_basis=first_observed_second_last_received_price/v1`을 명시한다. 창 상승률의 시작 가격은 창 첫 관측 초의 마지막 가용 수신가격, 끝 가격은 창 마지막 가용 수신가격이다. 창 고가·거래대금·체결 수는 모든 가용 체결을 반영한다. 이는 초봉 객체나 저장 자료를 만드는 계약이 아니다. 기존 정책 창·임계값·만료·결측/공백·주문 미승인은 유지한다. 필드가 없는 과거0B/초봉/분봉 결과를 새 계산 기준으로 재표지하지 않는다. 필드는 기존 context 내용 hash에 포함되며 새 endpoint·인증·승인 권한은 추가하지 않는다. [대조 원장](docs/workstreams/second-trade-dependencies.md).

## 2026-10-16 현재 0B 테마 H2의 시작가격 기준 (로컬)

기존 RAM Shadow context의 현재0B `leadership.facts`에 선택 진단 `price_return_basis=first_observed_second_last_received_price/v1`을 명시한다. 창 상승률의 시작 가격은 창 첫 관측 초의 마지막 가용 수신가격, 끝 가격은 창 마지막 가용 수신가격이다. 창 고가·거래대금·체결 수는 모든 가용 체결을 반영한다. 이는 초봉 객체나 저장 자료를 만드는 계약이 아니다. 기존 정책 창·임계값·만료·결측/공백·주문 미승인은 유지한다. 필드가 없는 과거0B/초봉/분봉 결과를 새 계산 기준으로 재표지하지 않는다. 필드는 기존 context 내용 hash에 포함되며 새 endpoint·인증·승인 권한은 추가하지 않는다. [대조 원장](docs/workstreams/second-trade-dependencies.md).

## 2026-10-16 현재 0B 동적 비중의 RMS 진단 필드 (로컬)

기존 RAM Shadow 응답의 `position_allocation.facts`에는 현재0B 경로에서 선택 진단 필드 `volatility_basis=last_received_price_per_observed_second/v1`, `volatility_sample_count`를 제공한다. 표본 수는 창 안에서 실제 관측한 초별 마지막 수신가격의 수이며 체결 건수가 아니다. 무체결 초는 채우지 않는다. `observed_close_return_rms_bps`는 이 가격들에 기존 공식·최소 표본 수·반올림을 적용하고, `recent_turnover_won`은 모든 실제 체결을 합산한다. 과거 응답에 새 필드를 소급 삽입하거나 현재0B로 재표지하지 않는다. 새 endpoint·인증·주문 권한은 없다. [재현과 범위](docs/workstreams/second-trade-dependencies.md#2026-10-16-동적-비중-rms의-기존-표본-의미-복구).

## 2026-10-16 주도주 돌파 실행·캠페인 연결 (로컬 구현)

새 Family `leader_breakout/v1`은 기존 모의 자동운용 API/인증/승인/계좌 lease를 사용한다. 승인된 실제0B 원문과 현재 운영 설정의 명시 근거가 필요하며 Shadow 조회는 주문 권한이 아니다. `leader_breakout_execution/v1`의 미정 필드는 null로 남기고 차단한다. `leader_absolute_stop/v1`은 고정 절대가격·실제 FILL 수량/진입가·계좌/실행/규칙을 묶는 보호 proof다. 구형 bps proof는 보존한다.

판단 campaign `leader_breakout_decision_campaign/v1`은 명시 정책 목록과 frozen dataset/구현 hash를 묶고 판단/차단 사유를 수집한다. PnL/승인 후보는 만들지 않는다. 성과는 기존 limited-search/final에서 명시 체결·비용·평가 조건을 요구한다. 연구는 완료·보완 분봉, 실시간은0B이며 초봉을 요구하지 않는다. `final_candidate/v2`는 contexts를 비워 과거 연구 근거의 현재 주문 사용을 막는다. 기존 v1 의미/hash는 유지한다. 아래 성과 미지원 설명은 이전 상태다. [현재 구현](docs/workstreams/realtime-entry.md#2026-10-16-자동매매연구-캠페인windows-연결-구현).

## 2026-10-16 주도주 구조 돌파 — 기존 설정/후보 API의 새 정책

`shadow_candidate_config.policy_id=leader_breakout/v1`은 실제0B 입력을 쓰며 초봉/분봉 DB polling을 하지 않는다. 주도주/구조 context·이격·확인 관측 수/유지 시간·대금/신선도·구조 이탈/위험의 미정 필수 값은 null로 보존하고 BLOCKED 사유를 반환한다. 명시 이전 비교 설정은 별도 호환이며 새 미지정 기본값과 과거 암묵적5봉·3%·5%·10분 프로필은 새 draft다. 원래 저장 문서는 재작성하지 않는다. 기존 인증·revision 충돌·저장/적용 실패 응답은 유지한다.

새 후보는 `strategy_id=leader_breakout`, `strategy_version=v1`, `quantity=null`, `order_authorized=false`이고 기존 후보 저장/API를 쓴다. 새 monitor가 활성일 때 유효기간 안의 이전 monitor 후보는 SUPERSEDED, 끝난 episode는 INVALIDATED로 투영한다. 관찰 비활성화나 기존 Family로 전환해 새 정책의 현재 owner가 없어도 미만료 새 후보는 SUPERSEDED이며 ACTIVE로 복귀하지 않는다. EXPIRED 우선과 원본 불변 원장은 보존한다. Windows는 incremental 전달과 별도로 같은 인증 API의 최근 최대1,000건 상태를 읽어 기존 행을 갱신한다. 재확인은 신규 cursor/알림을 소비하지 않고 범위 밖·실패의 현재 상태는 미확인으로 표시한다. 후보에서 포지션/체결·주문 권한을 복원하지 않는다. 정책의 단일 비교 함수가 수행하는 HOLD/EXIT는 실제 보유 여부를 명시 입력한 순수 계산이며 현재 후보 callback은 보유/주문 owner를 대체하지 않는다.

연구 실행기는 별도 명시 `mode=leader_breakout`에서 frozen 완료 분봉을 읽어 가격·판단·사유를 반환한다. 실제 수신0B 교차와 분봉 종가 교차·대금 창의 해상도 차이를 보존하며 체결·비용·성과 캠페인/최종 후보 계약은 아직 지원하지 않는다. 기존 Family로 조용히 대체하지 않는다. [정책·검증·한계](docs/workstreams/realtime-entry.md#2026-10-16-실제-돌파-로직-구현--0b-관측과-분봉-연구).

## 2026-10-15 직접 검색 장전 시황 (ed9d 로컬 구현, NAS 미배포)

기존 인증의 `GET/PUT /api/v1/market/morning-brief/settings`, `GET /api/v1/market/morning-brief`, `POST /api/v1/market/morning-brief/run`을 추가한다. 설정 PUT은 expected_revision과 편집 프롬프트·공급자·모델·추론·07:55 기본 예약·TTL·관심 목록을 검증하며 revision 충돌409/잘못된 추가 필드422/미인증401이다. 생성 POST는202로 실행 소유를 반환하고 GET은 저장 결과만 읽는다.

분석 payload는 `morning-market-brief-v4`의 [엄격 JSON Schema](docs/workstreams/morning-market-brief-output.schema.json)를 따른다. 확정 종가는 trading_date/price_status/timestamp_precision/availability_basis로 표현하며 공식 완료 근거가 있으면 observed_at=null을 허용한다. 실시간/지연 시세의 관측 시각 요구는 유지한다. 사람용 evidence_summary, 시장 방향/참여/집중, 사실/재료/근거, proposed 관계와 UNCONFIRMED 가설, 장중 확인/반대/무효화 조건, 누락을 분리한다. 점수·확률·숫자 신뢰도·영속 ID·장중 확인 상태를 LLM 출력으로 받지 않는다. [원본 설계 대비 차이와 기존 C1 매핑](docs/workstreams/morning-market-brief-alignment.md).

조회는 run/context/history를 반환한다. context.available은 cutoff 이후 실제 완료 가용시각과 TTL, 검색/필수 지표 품질을 검사한 **조회 가용성**이며 정책의 news_context 가용성 선언이 아니다. `advisory_only=true`, `auto_trade_eligible=false`, `consumer_binding=not_connected`를 유지한다. 기존 v2/v3 실행은 history/run에 보존하되 현재 context는 legacy_schema로 제외한다. 이 API를 Market State/C1/최종 매매 판단에 실제 연결했다고 표시하지 않는다. 기존 공개 엔드포인트와 정책별 required_data_capabilities는 변경하지 않는다.

아래는 기존 정책·API 계약이다.

`paired-net-action-selection/v2`의 선택 `action_required_data_capabilities`는 JSON `[["ADD", ["news_context"]]]`처럼 행동과 정렬된 추가 자료 배열의 쌍이며 DTO는 불변 tuple이다. 행동 키는 HOLD/ADD/REDUCE/REENTER/EXIT 중 중복 없이 정렬된 1~5개이며 비교 요청에 존재해야 한다. 추가 자료는 아래 v2 허용값을 사용하고 공통 required_data_capabilities와 합친다. 생략/None은 기존 결과·ID와 출력 모양을 유지한다. 선택 action_inputs는 행동별 required/missing_data_capabilities·reasons·원래 valid_until을 제공하고 action_selection.action_input_rejections는 제외된 후보의 사유다. 후보 결측이 다른 후보를 차단하지 않으나 HOLD 기준선의 필수 자료 결측은 WAIT다. 타점 제약/TTL은 요구한 행동에 적용하며 유효한 R3 EXIT와 공통 하드 Risk는 전체 보호 우선이다. 선택한 행동의 TTL을 receipt에도 반영한다. **현재 thesis_ref 조건은 공통 또는 행동별 stock_thesis 요구가 있을 때 필수, 그 외 빈 값이다.** 테마 없는 시작 조건은 공통 자료 요구에 적용하고 행동별 추가 자료 결측은 해당 후보를 제외한다. 별도 선택 stock_thesis/상위 allocation의 테마 시작 요건은 유지한다.

상위 native 비중의 가중치0 소비자는 기존 Runtime의 typed ThemeMembership을 위험 소속으로 받아 사용 H1/H2 점수 구성요소의 시각/TTL을 검사한다. 미사용 leadership 창의 결측이 시장 점수의 lease를 줄이지 않는다. 미래 소속·상이한 중복·선택 종목 소속 결측은 차단한다. 모두 양수인 기존 가중치와 독립 owner 자료를 명시하지 않은 직접 호출 계약은 유지한다. 현재 A03 지원 계약/실제 실행 승인과 구분한다.

이하 정책별 계약 설명은 행동별 추가 필드 도입 전의 기록이며 현재 thesis_ref/행동별 조건은 위 계약을 따른다.

R4의 기존 `position-action-comparison-shadow/v1` 외피·endpoint/auth/revision을 유지한다. `parameters.selection_policy`의 기존 `paired-net-action-selection/v1` 필드·모드는 unchanged이며 별도 v2 `paired-net-action-selection/v2`는 `input_mode="owner_ram_risk"`와 명시적 `required_data_capabilities` JSON 배열을 요구한다. 배열은 중복 없는 정렬된 0~5개 값이며 허용값은 `sampled_price`, `stock_thesis`, `theme_context`, `news_context`, `turnover_delta`다. `thesis_ref`는 stock_thesis 요구 시에만 필수이며 미요구 시 빈 값이어야 한다. v2 선택 근거의 required/available/missing_data_capabilities는 JSON 배열로 제공한다. 현재 검증된 R4 생산자는 sampled_price와 기존 stock_thesis이며 나머지 자료를 요구하면 `required_data_capability_unavailable:<name>`로 차단한다. 미요구 자료는 공통 차단 조건이 아니다. 계좌/주문·native Risk·자금/비용·가용시각/TTL·scope는 생략할 수 없고 신선한 하드 보호가 우선한다. 모든 receipt는 실행 승인이 아니다.

`ram-shadow-selection/v1`의 enabled와 빈 theme_ids 조합은 native 계좌 관측을 명시한 v2 R4가 stock_thesis/theme_context를 요구하지 않고 상위 allocation/별도 stock_thesis 선택이 없을 때만 허용한다. 이 경우 mode는 `position_context`이고 기존 선택 저장·owner 수명·기록을 재사용한다. 계좌·현재 가격·Risk를 과거 저장 문서에서 복원하지 않는다. 테마 전략/v1과 상위 allocation의 시작 조건은 유지한다. 상위 비중 후보의 시장/테마/종목 점수는 null을 보존할 수 있으나 해당 context_weights가 양수이면 결측 차단이며, theme_ids는 테마 집중 위험 정보로 계속 필수다. [현재 설계](docs/AI_TRADING_SYSTEM_REDESIGN.md#현재-r4의-정책별-자료-요구조건).

기존 dynamic-allocation-native-models/v2 소비 receipt에 선택 input_bundle(dynamic-allocation-input-bundle/v1)을 추가하고 current-goal-binding/v2 goal에는 input_bundle_ref를 붙인다. 기존 v1에는 붙이지 않는다. 입력7그룹 checks, 원래 current_cut_ref/selection/model refs, original available_at/valid_until, model source/study/EV comparison/population/exit refs 및 EV outcome ref count/hash를 제공한다. status INPUTS_BOUND 또는 INPUTS_UNAVAILABLE이며 model_application_verifiedFalse/source_durabilitynot_verified/orderfalse다. 현재 EV estimator의 trained_predictor_usedFalse는 학습 모델 승인/평가를 완료했다는 뜻이 아니다.256KiB 초과 sentinel에는 bundle_ref/lease가 없고 이유만 제공한다. expired current 조회에서는 bundle/goals를 제거하며 기록된 원래 selected latest의 과거 lease는 재발행하지 않는다. endpoint/auth/revision 및 기존 amount/policy/model IDs는 unchanged다. [86단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#86-개발-마무리-범위-고정과-공통-입력원본-근거-묶음)

이하 단계 설명은 해당 시점의 기록이다.

execute_research/research_run_identity의 선택 position_study_context는 prepared original position-study-historical-context/v1 document다. 정확12-key(version/dataset_id/revision_ids_hash/decision_time/symbol/market_venue/ranking_window_truncated/theme_window_truncated/rankings/theme_snapshots/context_ref), COMBINED TOP20/cumulative source 범위·100/100·128KiB를 검사한다. None 기본 run spec/proof는 unchanged이고 명시 source spec에 position_study_context, 기존 POSITION_STUDY_CASE proof에 historical_context를 추가한다. source spec이 없이 event만 값을 붙이면 거절한다. v2 allocation 외피/endpoint/선택 revision/기한은 그대로이며 current-goal-binding/v2에 선택 historical_context_summary를 추가한다. refs/case coverage/시장 quality/관측 테마/truncated를 나타내고 current_context_support_verifiedFalse·source_durabilitynot_verified·orderfalse다. 기본 v1/미포함v2는 새 필드를 만들지 않는다. [85단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#85-기존-연구-입력의-과거-순위테마-맥락-연결)

이하 단계 설명은 해당 시점의 기록이다.

기존 dynamic-allocation-native-ev/v1 CLI 입력은 기본8-key를 유지하고 선택 include_source_records(bool)만 허용한다. 기본 False 결과는 동일하고 True의 성공 receipt에 native_source_publication을 추가한다. 이는 native-allocation-ev-source-publication/v1의 정확7-key(version/decision_time/selections/sources/source_receipt/outcomes/publication_ref)이며 원래 record/events를 포함한다. 별도 publish endpoint나 자동 호출은 없다. 기존 v2 row의 네 key를 유지하고 ev_receipt 내부 선택 proof만 재검증한다. 원래 receipt의 outcomes/source_receipt/decision cut과 같아야 한다. current-goal-binding/v2의 선택 allocation_lineage는 position-study-allocation-lineage-audit/v1, REPORTED_OVERLAP 또는 REPORTED_DISJOINT이며 independence_verifiedFalse/source_durabilitynot_verified/orderfalse다. 미포함 기존 v1/v2 외피/TTL/기본 출력은 유지하고 직접 실행 승인이나 R4 행동 EV를 반환하지 않는다. [84단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#84-상위-ev-원본-출처-재생과-r4-연구-중복-확인)

이하 단계 설명은 해당 시점의 기록이다.

현재 계좌 내부 기본 PositionAccountObservation의 필드/기본 serialization은 그대로다. 현재 등록 native owner가 정합한 경우에만 PositionRunnerAccountObservation.runner_state를 붙인다. 기존 v2 current-goal-binding에 선택 native_owner_facts(entry_price_won/owned_quantity/opened_at/holding_age_seconds/family/family_scope_matches/protection_pending/session_clock_pending/state_ref/fill_cursor/state_available_at)를 추가한다. 기존 공개 인증 endpoint/packet/revision 계약은 바꾸지 않는다. owner_state는 native-position-runner-state/v1·owner_ram/not_verified/orderfalse이며 64KiB 한도/정확 key/content hash/cut/원가·수량을 검사한다. source_continuation의 원래 code/family/parameters/session은 decoder 내부 정보이며 기존 게시 외피/ID를 바꾸지 않는다. family match를 실행·보호 모델 admission으로 사용하지 않는다. 상태/status 조회로 원래 관측 시각/TTL을 갱신하지 않는다. [83단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#83-native-runner-체결보호-상태의-동일-계좌-ram-관측)

이하 단계 설명은 해당 시점의 기록이다.

기존 POST /api/v1/research/ram-shadow/allocation-models의 인증/lock/revision은 유지한다. 명시 dynamic-allocation-native-models/v2도 기존 10-key 외피를 사용하며 모든 row가 symbol/ev_receipt/exit_receipt/position_study의 정확한 네 key를 가져야 한다. 기존 v1 row/10-key 계약은 변하지 않는다. prepare_dynamic_allocation_models의 선택 position_studies는 종목별 native-position-study-publication/v2를 묶는다. native 준비 helper는 source_record를 동일 read에서 요청하며 기본 loader 결과에는 붙이지 않는다. 게시 proof의 정확한 7-key는 version/cohort/selections/decision_time/comparison/sources/publication_ref다. 각 source는 원래 record/events이고 최초 native 재생/예산·원본 TTL을 확인한다. 보고된 PC source일 뿐 원본 DB/capture 인증이 아니며 PC_READONLY_REPLAY_REPORTED/not_verified/order false다. 현재 결합의 version은 position-study-current-goal-binding/v2, status EV_UNAVAILABLE이며 양의 행동/실행 승인을 반환하지 않는다. [82단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#82-v2-연구-근거의-공통-ram-게시와-현재-목표risk-결합)

이하 단계 설명은 해당 시점의 기록이다.

programmatic freeze_position_action_study_cohort는 typed goal cases 2..100, 명시 minimum_samples/evidence age/model_valid_until과 실제 source spec/manifest로 position-action-study-cohort/v2를 동결한다. execute_research/research_run_identity의 선택 position_study_cohort는 같은 사전 선언을 run spec에 고정한다. read_position_action_study_cohort는 원래 5-key endpoints와 같은 loader를 받아 native-position-study-cohort-comparison/v2를 반환한다. 빈/누락 selections는 EV_UNAVAILABLE이고 중복 run/endpoint/이름 바꾸기·다른 source 선언은 거절한다. 선언 256KiB·전체 10,000 events/4MiB case proof 상한과 완결 짝 전수/기한을 검사한다. COHORT_COMPARABLE의 결과도 current_goal_status EV_UNAVAILABLE/usable false/order false다. 기존 v1 endpoint/10-key packet에 v2를 섞지 않는다. HTTP/CLI v2 packet 및 현재 binding은 후속이다. [81단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#81-v2-native-짝-모집단-동결과-공통-통계)

이하 단계 설명은 해당 시점의 기록이다.

이번 v2는 기존 programmatic research API의 명시 opt-in 계약이다. execute_research/research_run_identity의 position_action_study/position_study_case는 typed PositionActionStudyPolicy/Case를 받아 한 TRAIN 또는 VALIDATION KRX 독립 native branch의 spec에 고정한다. 기존 position_target_plan/unfilled_action_plan과 혼합하지 않는다. read_position_action_study_case는 기존 readonly loader에서 한 run/endpoints를 빌려 native-position-study-case/v2를 반환한다. 실제 TTL/source/전략 상태·원래 goal/value replay·원본 ID 및 NATIVE_CASE_PROOF/R4 EV_UNAVAILABLE·주문 false를 포함한다. 64KiB study 의미/256KiB case proof/10,000 event 상한이다. 현재 reader는 held ADD/REDUCE/HOLD의 기존 지원 범위이며 EXIT/flat REENTER·현재 exact context와 cohort/HTTP v2 packet 게시·UI/예약 caller는 후속이다. 기존 dynamic-allocation-native-models/v1 endpoint/10-key shape는 그대로다. [80단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#80-v2-연구-규칙순간별-case-계약과-native-증명-첫-구현)

이하 단계 설명은 해당 시점의 기록이다.

인증 POST /api/v1/research/ram-shadow/allocation-models는 dynamic-allocation-native-models/v1의 exact 10-key JSON을 받는다. CentralContentClient.prepare_dynamic_allocation_models(selection_document, rows, expected_selection_revision=...)는 이미 준비된 native receipts에서 고정 패킷을 만들고 publish_dynamic_allocation_models(document)는 그대로 보낸다. applied 선택 revision·전체 selection hash/종목 coverage·cost/horizon/q/price·원래 TTL을 검사하며 4MiB/200종목/2,000 outcomes/2,000 실행·대기 bars/10,000 native event budget이다. 새 패킷은 최초 EV 짝/출구 replay를 검증하고 publication_ref/generation/duplicate·source_durability=not_verified/order_authorized=false를 응답한다. 원래 available_at/valid_until·source_validation은 기존 RAM 결과의 position_allocation_models 메타데이터에 있다. 동일 유효 패킷은 inert ACK, 만료/잘못된 내용·revision은 422, 비활성/미준비/close는 503, 미인증은 401이다. 기존 selection PUT의 409 계약은 그대로다. DB 경로/원본 증명·활성·주문 승인을 받지 않고 재시작으로 모델 슬롯을 복구하지 않는다. [78단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#78-준비된-native-모델의-공통-ram-게시와-상위하위-목표-연결)

이하 단계 설명은 해당 시점의 기록이다.

기존 preview CLI의 research DB/native selection/명시 TRAIN·VALIDATION flag에서 dynamic-allocation-native-exit/v1 문서(policy/decision_time/max_evidence_age_seconds)와 단일 run/start_event/end_event 선택을 읽는다. caller Capacity/stress/추가 필드·잘못된 배열을 거절한다. full_exit_evidence와 allocation_exit_horizon_seconds는 기존 고정 비중 preview의 optional 연구 계약이며 기존 supplied v1의 ID/shape를 보존한다. 새 HTTP route/실시간 근거 게시/활성·주문 승인 없음. [77단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#77-전체-보유-출구-capacity와-명시-스트레스-연구-근거)

기존 preview CLI의 --research-db/--position-outcome-selection/--position-outcome-role로 dynamic-allocation-native-ev/v1 문서(request/account/open_orders/horizon_seconds/population_ref/minimum_samples/max_evidence_age_seconds)를 명시 읽는다. caller EV/추가 필드는 거절한다. dynamic-position-allocation-shadow/v1 candidate의 optional ev_evidence와 policy의 allocation_ev_horizon_seconds는 가격·현재/검증 수량·cost/horizon·원래 TTL을 강제한다. 미선택 기존 v1 shape/scientific ID는 유지한다. 새 HTTP route/selection EV 게시/운영 승인 없음. [76단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#76-배분-ev의-native-연구-공급과-검증-크기-제한)

기존 RAM selection PUT/GET에 optional position_allocation(dynamic-position-allocation-ram/v1)을 추가한다. 64KiB/200종목·연구 policy/expected account run/binding/path/명시 손실 한도/비용만 받으며 EV/Capacity facts를 caller가 채울 수 없다. null로 해제하고 기존 research/ram-shadow GET은 선택한 경우에만 native 사실/unknown producer/만료 receipt를 노출한다. 읽기에서 DB/재계산·활성/주문을 수행하지 않는다. 새 HTTP route 없음. [75단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#75-동적-비중의-공통-ram-사실과-native-계좌-risk-연결)

기존 고정 preview CLI에 dynamic-position-allocation-shadow/v1 문서를 추가한다. 계좌 운용 정책/candidates/PositionAccountObservation/decision_time/비용/명시 연구 Risk disposition/ref를 받아 weight/notional·기존 bounded-notional-sizing/v1을 반환한다. source_durability=not_verified·shadow_only·order_authorized=false이고 새 HTTP route는 없다. native Risk JSON 승인은 현재 adapter가 받지 않는다. [74단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#74-실제-모의계좌-수락-준비-확인과-상위-동적-비중-계산-시작)

2026-10-14 POST `/api/v1/mock-automation/unfilled-population`은 기존 bearer 인증·Supervisor에만 연결된다. request.stream의 실제 1MiB 상한(413), JSON/typed 구조·추가 필드·bool revision/모델/시간 오류(400), 활성 profile/spec/run/control/STOP conflict(409/503)를 검사한다. 패킷은 mock-unfilled-native-population/v1, canonical UUID account_ref·credential_profile_id/spec_id/execution_run_id/expected_control_revision/root_intent_id/managed_intent_id, 원래 available_at, 4–200 typed outcomes, 같은 native 정책/모델의 완결 source_receipt, 내용 hash publication_ref다. DB 경로를 받을 수 없고 native source_durability/주문 승인 승격을 거부한다. 새 패킷은 현재 native RUNNING control 한 번을 확인한다. 동일 수신은 unchanged/inert이며 TTL은 그대로다. 현재 계좌/실행을 만들거나 resume하지 않는다. 기존 status에는 optional unfilled_population_source와 paired_unavailable_reason이 있다. PC의 readonly/replay 검증 보고이며 서버의 시장 원본/호가/체결 확률/취소 지연 승인이 아니다. PC_READONLY_REPLAY_REPORTED·source_durability=not_verified·order_authorized=false를 유지한다. native v1은 ACCEPTED 분봉 자료만 지원하고 현재 PARTIALLY_FILLED/CANCELLED/UNKNOWN에서는 EV 미확인(native_unfilled_model_state_unsupported)이며 이전 cohort를 재표지하지 않는다. 실제 00/04/REST·PG/NAS/장중 수락·부분체결/CANCELLED 모집단/취소 지연 보정은 남는다. [72단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#72-pc-native-원장-결과의-인증-게시와-기존-runner-ram-접수).

2026-10-14 execute_research/research_run_identity의 optional unfilled_action_plan은 2–200개 typed UnfilledResearchAction(SUBMIT/WAIT/REPRICE)을 받는다. 독립 개발 KRX 단일 종목/순서·유일 cut·256KiB/기존 claim을 요구하고 자동 목표 계획과 혼합하거나 final 실행에 쓸 수 없다. read_unfilled_action_outcomes는 기존 load_position_action_evidence의 readonly bounded snapshot/role/final guard와 공통 selection budget을 재사용하며 native baseline→terminal MARK를 같은 실행기로 재생한다. 결과는 simulation/minute/ACCEPTED 상태의 별도 WAIT/REPRICE DTO이고 기존 RAM API에 호환되지만 자동 게시 caller는 후속이다. HTTP/API 인증·계좌/원래 목표/UNKNOWN·취소 확정 전 새 주문 금지는 유지한다. 실시간 원장 입력 게시 caller·CANCELLED/부분체결 모집단 생성은 후속이며, 실제 호가 순서·취소 지연·부분체결 확률·시장 원본 lineage는 미검증이다. source_durability=not_verified와 주문 승인 false를 유지한다. 실제 00/04/REST·종목별 즉시 native 손절 producer/수락·PG/NAS/장중 운용 승인도 남는다. [71단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#71-기존-연구-실행기의-지정가-분기와-native-미체결-결과-읽기).

2026-10-14 내부 Runner의 offer_unfilled_reprice_outcomes는 root/managed ID·population ref·원래 available_at·typed WAIT/REPRICE tuple을 명시 접수한다. 기존 동결 명세가 supplied paired mock 자료를 선택한 경우만 가능하며 1MiB/10,000행·중복/범위/순서를 검사한다. 동일 접수는 no-op, DB/주문 권한은 없고 재시작 시 복구하지 않는다. 기존 status paired_ev의 UNAVAILABLE를 보존하며 새 비교의 EMPIRICAL_ESTIMATE, paired_input_ref/cost_model_available/judgment_input_ref를 optional 표시한다. EMPIRICAL은 미래 예측/운용 승인이나 native 출처 검증을 뜻하지 않는다. HTTP/인증/계좌 활성화/동결 명세 identity와 기존 주문 유형은 유지한다. [70단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#70-waitreprice-짝-입력동결-비용익절-판단의-공통-연결).

2026-10-14 기존 모의 운용 명세의 unfilled_execution_policy에 optional maximum_protective_cancel_retries(정수 0–3, 기본 0)·protective_cancel_retry_delay_seconds(OFF는 0, 활성은 1..maximum_management_seconds)를 추가했다. OFF 직렬화는 두 키를 생략해 기존 정책/명세 ID를 유지하며 활성은 새 명세/승인이 필요하다. 기존 Runner status의 optional protective_cancel_recovery는 WATCH/RETRY_READY/WAIT_RECONCILIATION/RESOLVED/ESCALATE·원주문/관리 주문/정책 ID·native 취소 수/계보 재시도 수·다음 시각/이유를 노출한다. order_authorized는 false이고 ESCALATE는 requires_operator_attention이다. HTTP/인증·원래 주문 유형·운용 활성화 계약은 유지한다. [69단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#69-취소-거절불확실-이후-bounded-보호-대응).

2026-10-14 기존 Runner status의 optional protection_exit는 원래 요청/가격/Risk ref와 고정 결정/workflow ID다. first_protection_exit_state는 PREPARED/BLOCKED/ORDER_SUBMITTED/NATIVE_RESTORED/LIQUIDATION_CONFIRMED/REVALIDATION_REQUIRED이며 first_protection_exit_reason/pending_intent_id 및 protection_flat_risk_snapshot_id를 표시한다. 제출 ACK/목적 복구는 청산 성공이 아니다. HTTP/인증·계좌 활성화/원래 주문 유형은 유지하며 protection claim은 내부 Python/native 저장 계약이다. [68단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#68-운영-risk-보호-요청의-첫-exit-생성과-native-claim-확인).

2026-10-14 기존 Runner status에 optional `unfilled_registration`(원주문·목적·원래 목표/기한·동결 정책/내용 ID)과 `unfilled_native_source`(DELIVERED/BLOCKED·실제 Risk/가격 ref·paired_ev UNAVAILABLE·registration ID)를 추가한다. 등록과 DELIVERED는 승인/취소 확정/체결 성공을 뜻하지 않는다. 기존 HTTP 경로·인증·계좌 활성화/주문 유형은 유지하며 Collector/Monitor getter 및 price callback은 내부 Python 계약이다. [67단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#67-기존-collector계좌-입력의-자동-공급과-원래-주문-관리-등록).

2026-10-14 기존 Runner의 `unfilled_input_delivery.state`에 WAITING/ABANDONED/CANCEL_REQUESTED/CANCEL_UNCONFIRMED/CANCEL_RESOLVED를 사용한다. CANCEL_REQUESTED는 보통 취소 ACK이며 실제 취소/청산 확정이 아니다. Runner 상태의 optional `cancel_confirmation_required`, `requires_operator_attention`, `after_cancel`, `cancel_assessment_ref`, `cancel_risk_snapshot_id`, `cancel_broker_as_of`는 실제 처리/재검증 참조다. 보호 latch 표시가 이 참조를 지우지 않는다. HTTP 경로/인증·주문 지원/계좌 활성화 정책은 유지하며 before-claim callback은 내부 Python seam이다. [66단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#66-목적별-자동-취소와-취소-직전-native-계좌risk-재검증).

2026-10-14 Runner 상태에 optional `unfilled_input_delivery` 진단을 추가했다. 상태는 PENDING/PROCESSED/BLOCKED/ERROR/CURRENT_INPUT_REQUIRED, pending은 RAM cut 존재 여부이며 `order_authorized=false`는 전달 진단이 실행 승인이라는 뜻이 아님을 나타낸다. PROCESSED는 입력 소비이며 체결/청산 성공이 아니다. 내부 typed 입력 ingress는 새 HTTP 주문/제어 API가 아니고 계좌 활성화·기존 인증/제어 계약을 바꾸지 않는다. 실제 authority는 기존 v3 gate/QUEUED 원장이다. [65단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#65-running-runner의-사건기한-소비와-고정-주문의-동일-native-실행).

2026-10-14 기존 모의 운용 명세 JSON/v1의 optional `unfilled_execution_policy`와 gate 조회의 병행 `mock_automation_replacement_gate/v3`를 추가했다. policy는 기존 동결 spec hash/새 admission에 포함하며 미선택 시 원래 JSON/hash와 OFF를 유지한다. v3는 root/parent/attempt·목적/정책 ID·고정 child/해시·실제 평가/짝 입력 참조를 포함한다. 기존 ENTER/EXIT v1/v2 승인과 공개 API 경로/인증·전송 지원을 바꾸지 않는다. 실제 제출 연결은 내부 idle 명시 dispatcher이며 새 주문 API/자동 활성화는 없다. [64단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#64-목적별-고정-대체-주문의-native-승인과-제출).

2026-10-14 자동 모의계좌의 기존 ACK된 Risk를 Runner의 보호 요청으로 자동 전달한다. 상태의 optional `native_risk_delivery`는 최근 전달 snapshot/reconciliation revision·전달 시각/trigger 여부이며, `protection_review.risk_trigger`는 원래 위험의 scope·source·판정/사유·signature다. Risk/계좌 원문이나 새로운 command API를 공개하지 않는다. EXIT_REQUIRED/RECONCILE_REQUIRED는 보호 검토 판정이며 실제 제출 승인이 아니고 order_authorized=false/transport_state=OFF다. 실제 취소/재주문은 사용자 순서 ②의 후속이다. [62단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#62-운영-risk의-자동-보호-요청-연결과-개발-순서-개정).

## 목적별 대체 주문·보호 요청의 내부 계약 (2026-10-14)

기존 runner 상태에 optional `protection_review`·`protection_review_required`를 제공한다. 새 HTTP command/자동 주문 API는 없다. 내부 명시 OFF 검토의 `unfilled_execution_preflight.receipt.replacement_gate`는 `mock-unfilled-replacement-gate/v1`의 목적/action·원래 승인 계보·고정 child ID/payload/hash·현재 Risk/control·차단 사유를 담는다. `VALIDATED_FOR_OFFLINE_REPLACEMENT`도 order_authorized=false/transport_state=OFF이며 실제 native 제출 승인이 아니다. 보호 요청은 bounded RAM 한 건과 기존 checkpoint로 전달되고 STOP을 재개하지 않는다. 명시 flat 해제도 재시작 후 새 native 검증을 요구한다. 운영 Risk producer 자동 전달·실제 취소/재주문·보호 주문/지연 수락은 후속이다. [60단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#60-목적별-고정-대체-주문-검토와-보호-요청의-우선-처리).

## Native 미체결 시도 검토의 내부 계약 (2026-10-14)

명시 idle `MockAutomationRunner.preview_unfilled_execution`은 기존 runtime의 닫힌 owner guard에서 native parent/child·QUEUED claim/SUBMISSION_STARTED·상세 보유·control/복구/현재 저장된 Risk를 검증한다. 기존 checkpoint의 optional `unfilled_execution_preflight`, version=`unfilled-native-attempt-plan/v1`로 복구하며 status는 항상 requires_revalidation=true/order_authorized=false다. 최대 64개 슬롯/child 이벤트 400개/계획 1MiB, 결정적 root+ordinal ID와 원래 anchor/기한·고정 child 내용·소모 예산을 보존한다. native_gate는 현재 assessment에 대한 기존 gate 결과이며 child submission 승인/ID 결합이 아니다. ADD/REDUCE를 ENTER/EXIT로 재표지하지 않는다. transport_state=OFF, 새 endpoint/CLI schema·approved gate/전송은 없다. [59단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#59-native-parent-child-시도-투영과-닫힌-owner-gate-검토).

## 미체결 영속 검토 계획의 내부 계약 (2026-10-14)

기존 idle/ready `MockAutomationRunner.preview_unfilled_management`는 native parent와 control을 확인하고 기존 checkpoint에 `unfilled-order-management-preflight/v1`을 저장·재검증한다. endpoint·CLI schema·실제 전송 권한은 추가하지 않는다. runner status의 optional `unfilled_management_preflight={receipt,requires_revalidation:true,order_authorized:false}`와 `unfilled_management_clock`는 복구/보호 기한 검토용이다. receipt는 1MiB 이하이며 단일 root의 첫 preview 예약만 담는다. 예약 슬롯과 실제 전송 횟수는 별개이고, native child 존재/공급 횟수 증가·control/anchor 변경은 차단한다. 현재 owner lease/Risk 실행 승인·다회 재시도·보호 자동 wake-up은 후속이다. [현재 경계](docs/AI_TRADING_SYSTEM_REDESIGN.md#58-미체결-검토-계획의-기존-checkpoint-복구와-실행-전-검증).

## R4 후 미체결 목적별 정책의 고정 입력 미리보기

기존 `scripts.preview_market_theme_shadow --input`은 추가 schema `unfilled-order-management-shadow/v1`을 지원한다. top-level 필드는 `schema/policy/record/broker_snapshot/target/account/open_orders/assessment`만 허용한다. 기존 목표/계좌/ExecutionRecord parser와 BrokerOrderSnapshot/BrokerFill 계약을 재사용하며 lifecycle/repository/transport를 생성하지 않는다. open_orders는 관리 record와 합쳐 최대 400건, broker fills는 최대 400건이다. 고정 입력의 공급된 연구 제약이며 native Risk 승인/운영 실행 허가가 아니다.

policy는 `version=unfilled-purpose-policy/v1`, `purpose=BUY_OPPORTUNITY|TAKE_PROFIT|PROTECTIVE_EXIT`, 원래 `anchor_intent_id/anchor_price_won/anchor_price_input_ref/anchor_available_at`, `valid_until/wait_until/protection_deadline`, 목적별 `maximum_adverse_slippage_bps/protective_slippage_bps`, `maximum_reprices/max_snapshot_age_seconds/minimum_net_advantage_won/uncertainty_multiplier/minimum_paired_samples/reprice_horizon_seconds`를 모두 명시한다. 현재 첫 범위는 기존 KRX mock LIMIT intent이며 기준 가격은 원래 intent.limit_price와 같고 anchor 가용시각은 intent 생성 이전이어야 한다. 시각은 timezone-aware, 수량·한도·횟수는 엄격한 정수, 점수/페널티는 유한 값이다. 기본 위험 임계값을 만들지 않는다.

assessment는 현재 타점 유효성·익절 추세 대기·신규 위험 허용의 bool/null, 보호 필요 bool, 타점/Risk ref·가용/만료, `market_status=TRADABLE|HALTED|NO_LIQUIDITY|UNKNOWN`, 후보 지정가·가격 ref/가용 시각·사용 재가격 횟수와 optional `reprice_evidence`를 받는다. 관리 target은 같은 계좌/주문 observation ref에 묶고 BUY는 기존 ADD/REENTER 목표, SELL은 REDUCE/EXIT 목표 계산을 재사용한다. 보호 전환에는 현재 EXIT 목표가 필요하며 익절의 일부 REDUCE 목표를 전량 보호 목표로 재표지하지 않는다.

reprice_evidence는 `version=unfilled-WAIT-vs-REPRICE/v1`, 원래 intent·현재 비교 잔량·후보 가격·고정 horizon·가용 시각·현재 비용 model ref·짝 표본 수·모집단/input ref와 비용 차감 순차이/표준오차를 명시한다. 별도 미체결 WAIT 대 REPRICE 요약이며 기존 R4 HOLD 대비 평균을 받지 않는다. 현재 비용 유효 구간·수량/가격/기간/cut·최소 표본을 확인하고 명시 페널티 차감 점수가 최소 개선액을 엄격히 넘을 때만 일반 REPRICE를 제안한다. 상태는 `SUPPLIED_PAIRED_UNFILLED_ESTIMATE`이며 native 자체 성과 검증·독립성/확률 보정·미래 체결 확률을 뜻하지 않는다. 자료가 없으면 `EV_UNAVAILABLE`다.

결과 action은 WAIT/CANCEL/REPRICE/ABANDON이며 CANCEL의 `after_cancel=REPRICE|ABANDON`은 실행 명령이 아니다. QUEUED/SUBMISSION_UNKNOWN/CANCEL_PENDING에서는 WAIT와 기존 대사 필요를 반환하고 대체 수량을 만들지 않는다. 원래 lifecycle의 snapshot 정규화와 filled/last_broker_as_of·동일 broker identity·최신 계좌 cut을 확인한 CANCELLED/remaining 0에서만 체결 세부 대사·계좌 전체 pending/예약·현재 목표를 재계산한다. 대체 BUY는 원래 미체결 예산을 늘리지 않고 SELL은 현재 실제 잔여 보유를 넘지 않는다. 취소 중 전량 FILLED면 원래 주문 계획을 종료하고 현재 포지션으로 돌아간다. 취소 거절/ACK 유실·빈 미체결 조회·로컬 expires_at을 취소 확정으로 취급하지 않는다.

매수 기회/위험/기한·가격/재시도 한도 위반은 살아 있는 주문의 CANCEL→ABANDON 또는 확정 취소 후 ABANDON이다. 이미 체결된 보유는 보존한다. 일반 익절은 추세/위험 여유의 제한된 WAIT와 확인된 개선의 REPRICE를 사용한다. 보호 매도는 일반 EV 대기를 적용하지 않고 별도 슬리피지 한도·명시 기한으로 처리하며 거래 중단/유동성·가격/상태 미확인·재시도 한도는 미해결 위험/운영 개입 필요를 표시하고 ABANDON하지 않는다. 현재 기존 세션 규칙을 사용하며 수동 장후 broker probe 허용을 자동 REPRICE 허가로 사용하지 않는다. 기존 취소/대사는 장 외에서도 기존 계약대로 유지된다.

출력에는 원래 lineage/cut/policy/입력과 action/사유·추가 확인 시각·target ref·대체 수량/가격·risk_path_required/운영 개입 필요·liquidation_confirmed를 포함한다. `order_authorized=false`, `execution_support=shadow_only`, `risk_authority=supplied_research_constraint_only`다. FILLED도 실제 계좌 0주·완전한 대사/전체 pending 소멸을 확인한 입력에서만 보호 해소로 표시한다. 새 API endpoint/DB/writer·브로커 취소/주문 전송·운영 timer 연결은 없다. 현재 금액/수량은 하위 기능이며 상위 동적 비중은 원래 Position·Risk·Capacity/Liquidity의 후속 범위다.

## R4 기존 타점·native Risk RAM 입력 (optional)

`parameters.selection_policy.input_mode=owner_ram_risk_and_thesis`와 명시 `thesis_ref`를 선택하면 같은 공통 Runtime의 기존 계좌 관측에 포함된 `MockAutomationRiskSnapshot`과 기존 R3 타점 결과를 사용한다. 기본/생략 `supplied_fixed`·빈 thesis_ref는 기존 결과/ID를 보존하며 native 모드는 새 계좌 조회·Risk DB read·위험 계산을 만들지 않는다. JSON에는 native Risk snapshot 본문을 받지 않고 런타임 owner RAM만 공급한다. 고정 입력 CLI/저장 복구는 정책을 검증하지만 실제 native 관측을 복원하거나 승인하지 않으므로 WAIT다.

기존 모니터가 Risk 저장 완료 후 동일 recovery의 계좌 RAM에 복사한다. 기존 generation/runtime revision·연결 해제·미확인 주문 무효화가 함께 적용된다. native Risk의 원래 내용 hash·계좌 ref/as_of·관측 가용 시각/최대 age를 확인하고 관측 이전보다 미래인 계좌 cut은 차단한다. 타점은 선택 thesis ref·종목/KRX·order_authorized=false·COMPLETE quality·가용/판단 시각/만료를 확인하며 여러 소비자의 동일 decision ID는 공유하고 서로 다른 현재 결과면 모호함으로 차단한다.

native 입력 receipt `selection_inputs`는 원래 Risk snapshot/account revision/reconciliation revision·관측 시각과 타점 decision ref/action·만료·사유를 기록한다. 권고의 `risk_authority=native_observation_not_execution_approval`이며 주문 권한은 false다. HOLD는 증액/재진입 허가로 변환하지 않고 NO_NEW_RISK, REDUCE는 REDUCE_ONLY, WAIT는 차단, 유효 EXIT는 보호 경로 필요를 우선 표시한다. native Risk의 미확인 주문/잔고·대사 불완전은 WAIT, 비용/일별 순손익 미확인은 증액 금지다. 임의 일손실/Capacity 한도나 자동 비중은 새로 만들지 않는다. 실제 승인된 운영 Risk gate·일손실·STOP·owner/control은 기존 실행 경로의 책임이다.

유효 타점 EXIT는 Risk/EV 결손에도 보호 경로 필요를 표시하되 새 수량/취소/주문을 만들지 않는다. 유효 native Risk에 연결된 명시 EXIT_REQUIRED 제약도 타점 WAIT보다 우선한다. 보호 표시와 실제 주문 허용·체결은 구분한다. live receipt는 기존 계좌/가격/정책 만료와 native Risk age·타점 만료 중 가장 이른 시각까지다. 계좌 이벤트는 기존 타점 결과를 재사용하고 H1/H2를 추가 계산하지 않는다. 현재 금액/수량 하위 기능과 원래 Position·Risk·Capacity/Liquidity의 후속 상위 동적 비중 정책을 유지한다.

## R4 주문 없는 행동 권고 (optional)

기존 비교 문서의 `parameters.selection_policy`를 생략하거나 null로 두면 기존 v1 결과/ID를 유지한다. 명시할 때는 `version=paired-net-action-selection/v1`, 음수가 아닌 정수 `minimum_net_advantage_won`, 유한한 음수가 아닌 `uncertainty_multiplier`, `risk_constraint=ALLOW|NO_NEW_RISK|REDUCE_ONLY|EXIT_REQUIRED|HALT_NEW_ORDERS`, 비어 있지 않은 `risk_input_ref/account_ref/symbol/account_input_ref/orders_input_ref`, timezone-aware `available_at/valid_until`을 모두 제공한다. CAP 수량 정책은 이 첫 범위에 없다. 계좌·종목·계좌/주문 input ref는 실제 비교 cut과 같아야 하고 미래/만료 제약은 WAIT/BLOCKED다. 공급된 연구 위험 제약이며 기존 native Risk 승인으로 취급하지 않는다.

optional `action_selection`은 `action/status/reasons/scores/risk_constraint/risk_input_ref/risk_path_required/target_decision_ref`와 `order_authorized=false`를 반환한다. 점수는 같은 HOLD 짝의 비용 차감 평균 차이에서 명시 multiplier×표준오차를 뺀 값이다. 신뢰구간·확률 보정·미래 예측값이 아니다. 후보별 짝 HOLD 근거 집합이 다르거나 허용 후보 자료가 부족하면 WAIT, 동일 최상 점수면 WAIT, 최소 개선값을 엄격히 넘는 행동이 없으면 HOLD다. NO_NEW_RISK/REDUCE_ONLY는 ADD·REENTER를 제외하고 HALT_NEW_ORDERS는 WAIT다. EXIT_REQUIRED는 EV 유무와 무관하게 EXIT/RISK_REQUIRED와 기존 보호 경로 필요를 표시한다. EXIT 목표가 없거나 수량 차단 상태면 target_decision_ref는 null이며 주문량·체결을 만들지 않는다.

정책/제약은 UTC 정규화한 비교 identity에 포함한다. 같은 parser·CLI·PC 선택/저장/복구·공통 Runtime/기존 선별 기록으로 소비하며 계좌/가격 갱신은 위험 ref를 새 cut으로 다시 붙이지 않는다. live receipt는 위험 제약 만료도 포함하고 화면은 만료 권고 사용 불가를 표시한다. 새 endpoint/DB/writer/주문 권한은 없다. 목표 금액·수량은 하위 계산 계약이며 상위 자동 비중조절은 원래 Position·Risk·Capacity/Liquidity 설계에 따른 후속 범위다.

## R4 optional 자동 목표 수량 정책

기존 position-target-shadow/v1·position-action-comparison-shadow/v1 요청에 optional target_policy를 허용한다. 없거나 null이면 기존 결과/ID를 유지한다. 정책이 있으면 target_quantity는 null이며 행동은 ADD/REDUCE/REENTER로 명시한다. version=bounded-notional-sizing/v1·target_notional_won·max_position_notional_won·max_position_quantity·cash_buffer_won·timezone-aware available_at/valid_until을 모두 제공하고 미지원 필드는 거절한다. 원화/수량은 음수가 아닌 정수이며 bool/실수는 허용하지 않는다.

결과의 optional target_sizing은 정책 ref/원래 정책 version·정규화된 parameters·희망 수량·제한 사유/상태다. target_policy_version은 정책 내용 전체의 고유 ref이고 target_quantity는 현재 수량 계산 결과다. 과거 자료의 policy/target을 이 계산 결과와 비교하며 기존 자료를 새 정책 성과로 바꾸지 않는다. 명시 목표·HOLD/EXIT/주문 권한 false는 유지한다. 기존 관찰 선택 API/revision·저장/복구와 공통 RAM 결과를 사용하고 live valid_until은 정책 만료도 포함한다. 새 endpoint·DB schema·주문/운영 권한 변경은 없다. [53단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#53-r4-자동-목표-수량-정책의-공통-shadow-계산-연결).

## R4 native 연구 행동 자료의 명시 CLI/PC 선택

명시 paper-position-target/v1 native 연구에는 ADD/REDUCE/REENTER와 flat HOLD를 추가로 지원한다. 원래 자동 목표 정책·원장 주문/FILL/분별 평가·현금/수량/비용·선행 청산을 검증하며 미표지 run의 기존 HOLD/EXIT 계약은 유지한다. sources의 optional position_action_execution은 원래 실행 version/source_policy_version/target_policy이며 입력 정책을 자동 변경하지 않는다. source 실행 모델 ref는 원래 code_hash도 포함한다. 기존 CLI/PC의 선택 형식/role/예산·HTTP endpoint/저장 계약은 유지한다. [54단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#54-r4-자동-목표의-native-addreducereenter-실행-자료-연결).

`preview_market_theme_shadow.py --input <비교 입력> --research-db <기존 원장> --position-outcome-selection <창 JSON> --position-outcome-role TRAIN|VALIDATION`이 기존 비교 v1에 저장된 HOLD/EXIT 창을 읽기 전용으로 전달한다. 입력 outcomes는 빈 배열이며 기존 thesis selector와 혼합하지 않는다. selection은 256 KiB·1–200창, 각 필드는 run_id/start_event_id/end_event_id/action/case_ref다. native v19+의 기존 development/final/원본 identity guard 뒤에 실행 원장을 모든 run 합계 10,000행 이내로 읽는다. 선택한 role의 실제 완결·가용 자료만 사용하며 미지원/미확인 창은 제외 사유를 남긴다.

기존 결과/ID는 유지하고 native 선택에서만 optional `position_outcome_source`에 원래 source ref/정책/모델·선택 role/종목·요청/수락 수/제외 사유·simulation 실행 종류와 mock 시나리오 scope를 제공한다. 정책/목표를 자동 수정하지 않으며 binding mismatch는 기존 EV_UNAVAILABLE다. 실제 broker 체결/원본 내구성/운영 승인은 아니다. 이번 단계는 CLI이며 HTTP API·기존 live/선별 기록 envelope와 주문 권한을 변경하지 않는다. [51단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#51-r4-기존-연구-원장의-완결-행동-자료-reader-연결).

PC 관찰 탭도 명시 DB/창/role로 같은 reader를 기존 worker에서 호출한다. 원래 비교 문서의 outcomes만 변경한 편집 입력이며 저장/적용은 기존 선택 API/revision을 사용한다. DB/선택 JSON 파일 경로는 서버에 보내지 않고 복구/GET/tick 때 원장을 재조회하지 않는다. 편집 상태의 source receipt는 HTTP 응답 필드를 추가하지 않으며 저장 값의 native source ref를 유지한다. 선택 변경/닫기/종료의 늦은 결과는 무시하고 실제 SQL 중단/worker 완료를 소유한다. 새 HTTP endpoint·주문 권한 변경은 없다. [52단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#52-r4-완결-행동-자료의-기존-pc-관찰-선택-연결).

## R4 기존 모의계좌 RAM 입력 선택

계좌 전체 대사 후속에서는 live 선택 계좌에만 기존 recovery 전후 bounded 원장 읽기를 연결한다(각 401행 조회, 400행 사용; 초과는 불완전). 새로운 broker TR/계좌 활성화·GET DB 조회는 없다. 현재 실행의 기존 reconcile 쓰기는 유지하고 다른 실행은 읽기 전용이다. 수동 주문은 브로커 원래 수량/방향/가격/venue의 확인된 잔량으로 전달하며 intent/체결을 합성하지 않는다. 확인된 native 경로의 목표 scope에는 `pending_quantity_basis=broker_remaining_quantity/v1`이 붙고 legacy 고정 입력은 기존 필드/ID를 유지한다.

RAM receipt의 optional `account_orders_complete`, `account_reconciliation_complete`는 조회 근거의 완전성이다. QUEUED/전송 미확인·취소 대기·누락/모호한 메타데이터·예산/조회 실패·실행 변경 때 false이며 수량 계산을 차단한다. true도 주문·원자적 broker snapshot·원본 내구성 승인은 아니다. `valid_until`은 계좌/가격/브로커 주문의 freshness와 비용 모델 유효기간 중 가장 이른 만료다. 계좌/실행 변경·scope 선택 변경·종료는 캐시를 무효화한다. 여러 테마는 한 source read/평가를 공유하고 GET은 복사만 한다. [대사 계약](docs/AI_TRADING_SYSTEM_REDESIGN.md#50-r4-계좌-전체-대사와-브로커-확인-잔량-계산).

기존 인증 selection PUT의 optional strict boolean `position_account_live`는 기본 false/OFF다. true는 기존 position_comparison 입력 문서가 필요하며 같은 목표·비용/행동 정책에 등록된 기존 mock owner의 RAM 계좌·Collector KRX 가격을 전달한다. 계좌를 boot/조회/활성화하지 않는다. false는 필드를 생략하고 기존 고정 입력 모드로 돌아간다. position_comparison null은 live flag도 해제하며 null+explicit true는 422다. 기존 private 선택 문서/revision·save/apply/재시작·기본 OFF를 유지한다.

RAM envelope의 optional position_comparison은 live 선택에서 `input_mode=owner_ram_snapshot, is_live_account=true, result=<비교 v1 또는 null>, quality={status, reasons}`다. result가 있으면 account_input_ref·price_input_ref·account_as_of·valid_until·unmapped_broker_order_ids를 제공한다. is_live_account는 owner RAM 경로 표시이며 신선도·계좌 전체 원장/대사·운영 승인 근거가 아니다. source 부재/무효화는 null/UNAVAILABLE, 잘못된 scope/미래 관측은 ERROR다. 고정 계좌/가격으로 fallback하지 않는다. 최초 읽기와 account 알림은 공통 Runtime 수명에서 수행하며 GET은 자료를 재조회/재계산하지 않는다.

현재 owner의 로컬 주문 원장은 실행별 범위이므로 open_orders_complete=false를 유지한다. 따라서 현재 전체 미체결·대사 근거가 없는 live 목표는 추가 수량과 EV가 차단된다. 계좌 보유/확인된 주문·원래 목표는 표시하며 None을 0으로 바꾸지 않는다. 저장 결과는 과거 cut이고 replay_eligible/order_authorized=false다. 새 DB 스키마·1초봉/계좌 polling·주문 API는 없다.


## R4 고정 포지션 비교의 공통 관찰 연결

인증 `GET/PUT /api/v1/research/ram-shadow/selection`의 optional nullable `position_comparison`은 기존 `position-action-comparison-shadow/v1` **입력 문서**를 받는다. 서버 파일 경로·계산 완료 결과는 받지 않는다. 생략은 유지, null은 해제이며 기존 expected_revision·save-before-apply·ACK 유실/재시작 계약을 사용한다. UTF-8 JSON 입력/계산 결과 각각 256 KiB 이하, mock/KRX만 지원한다. 활성화 시 비교 종목은 관찰 테마 중 하나에 포함돼야 한다. 기존 순수 계산의 계좌/cut·비용/목표·표본 검증을 저장 전에 실행하고 오류 시 422·기존 선택/Runtime 유지다.

공통 Runtime의 `ram-shadow-diagnostics/v1` envelope는 optional `position_comparison={input_mode: supplied_fixed_snapshot, is_live_account: false, result: <기존 비교 v1 결과>}`를 한 번 포함한다. 기존 contexts/results 및 목표/비교 ID를 덮지 않는다. 기본 인증 RAM 조회는 DB를 읽지 않으며 저장분은 기존 include_recorded 명시 조회를 사용한다. 결과는 선택 적용/재시작 준비에서 한 번 계산하고 틱·여러 테마·조회에서는 복사만 한다. 원래 decision_time을 보존하고 현재 계좌/실시간 판단으로 갱신하지 않는다. 기록 ON은 기존 선별 한 행/transaction에 같은 결과를 포함하며 R4 때문에 별도 주기·쓰기를 추가하지 않는다. 미선택 envelope는 기존 형식 그대로다.

고정 결과의 live envelope 포함은 실시간 계좌 연결·원본 확정·Replay 적격·주문 승인 근거가 아니다. source_durability/probability_calibration은 not_verified, execution_support=shadow_only, order_authorized=false다. 실제 행동 자료 reader·실시간 계좌 owner 입력 연결은 후속이다.


## 2026-10-15 TOP20 관측 변화의 RAM 전달 (로컬 미배포)

가격 후보의 `quality.coverage`는 판단 cut 당시 기대/평가/자료 확인 종목 수와 연속성·이전/현재 표본·시세 만료의 수를 추가 제공한다. 사유별 수는 중복될 수 있다. 종목 metrics의 reasons는 기존 source 승인·연속 시작/비교 이력·RAM retention/gap·소속 품질·시세 변환, 이전/현재 창 표본과 관측 부재를 구분한다. 자료 확인 수는 전체 코호트/후보 COMPLETE 승인과 같지 않으며 미래 소속의 종목/시세를 노출하지 않는다. 기존 TTL 투영은 원래 cut의 진단을 보존하되 PC는 만료 때 `시세자료 확인 만료`로 표시한다. 부분 자료를 후보로 승격하거나 기존 전원/표본/시점/신선도 기준을 완화하지 않는다.

선택 map의 `lookup_change`는 최근 완성 순위의 첫 종목 유지/교체다. Runtime의 `price_trade_leadership`은 `minute-price-trade-candidates/v1`, `primary_timeframe=1m`이며 조회순위와 독립적으로 후보를 선정한다. 같은 현재 TOP20/거래소의 완료된 직전1분과 그 앞1분을 비교한다. 각 구간 첫 시가→마지막 종가의 가격 bps, 실제 거래대금 합계(WON)를 사용하며 가격 양수·코호트 중앙값 초과, 대금 양수·중앙값 초과·동률 보존 상위 K를 모두 요구한다. `rank_price_trade_relation`은 조회순위 변경 중 후보 유지, 조회순위 유지 중 후보 교체를 구분한다. 이는 TOP20 내 관측이며 전체 시장 대장·순유입/수익성·주문 자격을 뜻하지 않는다.

`timeframes`는 1m/5m 비교를 제공한다. `five_minute_confirmation`은 현재1분 후보가 최근5분 누적 가격·대금 조건과 그5개 분 중3개 이상의 동일 후보 조건을 충족하면 CONFIRMED다. 현재5분만으로 지속 확인이 가능하며, 이전5분과의 변화 비교에는10분 이력이 필요하다. 5분 결측은 완전한1분 관측을 막지 않는다. 기존 분 마감2초 지연, 같은 거래일/venue, complete/actual·출처·effective_at·당시 available_at/updated_at을 검사한다. 최신 가용 revision의 partial 정정도 반영하며 결측/추정 대금을0이나 완료 봉으로 채우지 않는다. 분봉 만료는 완료 구간 끝+90초와 소속의 H1 TTL 중 빠른 값이다. H2 초 창/만료 설정을 사용하지 않는다.

초봉은 별도 `second_trade_auxiliary`다. 기존 `observed-price-trade-candidates/v1` 직접 호출/과거 저장값의 의미를 유지하되 Runtime 분봉의 필수 조건이나 만료 기준으로 사용하지 않는다. 초봉 보조에만 승인 source/retention/gap·두 창 표본·H2 창/신선도를 적용한다. 각 부가 변환/계산 실패는 해당 관측만 UNKNOWN/UNAVAILABLE이며 기존 H1/H2/보호 결과는 유지한다.

기존 Runtime이 유효 소속의 최대200(code,venue)에 대해 기존 Collector.load_live_minute_bars를 읽고 완료10분 범위만 공유 cache에 둔다. 같은 day/완료 경계/코호트/기존 분 저장·마감 ACK의 읽기를 합치며 별도 polling/수집/쓰기 owner는 없다. 한 소유 비동기 읽기가 대기해도 기존 판단/보호 처리는 계속한다. 완료 시 정확히 연결된 core 결과의 부가 관측만 원래 cut으로 갱신하고 `published_at`에 실제 반영시각을 둔다. 미래 자료는 제외하며 core 판단/ID를 재계산하지 않는다. 종료는 수락한 읽기를 기다리고 정리한다. 같은 스코프의2/8 소비자가 계산을 공유하고 상태 GET100회는 추가 DB/계산0이다.

기존 인증 `GET /api/v1/research/ram-shadow`의 `ram-shadow-diagnostics/v1` live envelope에 optional `market_observed_changes` map을 추가한다. 기존 Runtime이 가진 시장 프레임/H1 특징을 같은 회차에서 공유하며, map은 `market-observed-changes:<hash>`를 키로 `top20_observed_changes/v1`의 판단 cut·관측시각·원래 만료시각·venue/window·TOP20 패턴·후보 출현 사실·품질·단위/근거/한계를 담는다. 각 해당 results 행의 optional `market_observed_changes_ref`가 이를 참조한다. 기존 contexts의 v1 내용/revision ID, stock-thesis의 내용/decision ID와 주문 권한은 유지한다.

테마/뉴스/성숙 결과 결측은 독립 순위·대금 관측까지 막지 않는다. 해당 관측 실패는 UNKNOWN/UNAVAILABLE로 보고하며 기존 H1/H2 판단을 무효화하지 않는다. 조회순위 1위/상위 행의 대금 비중은 대금 순위나 전체 시장 점유율·실제 매수 자격이 아니다. 후보 유지·이탈·재등장은 제공된 당일 프레임 안의 관측이다. 원본 체결 무누락/영속 기억/전략 검증을 뜻하지 않는다.

조회는 계산·DB 접근 없이 복사한다. 만료 시 원래 revision ref/근거/시각은 보존하고 `projection=expired`, 패턴 UNKNOWN, 후보/변화는 빈 값, 분류 None, 관측 품질 STALE로 투영한다. 이 투영은 원본 판단 revision을 새로 생성하거나 lease를 연장하지 않는다. 실패 binding은 기존 결과에 묶인 이전 관측을 유지하며 새 cut의 관측을 잘못 연결하지 않는다. 관측 map 부재는 미확인으로 처리한다.

기존 선별 기록의 categorical signature·한 행·1MiB 예산·writer를 유지한다. 관측 자체의 변화는 새 기록 계기가 아니다. 선별 시 기존 기록에 optional map을 포함하며 예산 초과 시 이 map과 행의 해당 ref만 우선 생략한 뒤 기존 출처 map의 예산 처리를 적용한다. 기록 포함/부재는 Replay·현재 EV·주문 승인 근거가 아니며 `order_authorized=false`를 유지한다. PC 관찰 표에는 기존 시장 분류와 별도로 TOP20 변화/자료 상태를 표시하고 tooltip에 관측시각·후보 수·근거를 제공한다. 저장 관찰은 당시 값이며 live 만료 값은 미확인이다.

## 2026-10-13 RAM 출처의 녹화 입력 조회 힌트 (로컬 구현)

기존 `source_coverage[coverage_ref]`의 optional `capture_anchor`는 현재 입력 snapshot 시점에 수락돼 있던 Recorder high-water를 재사용한다. 현재 collector_inputs 녹화가 running이고 Collector capture epoch와 high-water trace ID가 모두 일치할 때만 `{trace_id, producer_component, input_id, collector_input_version, role=last_accepted_collector_input, verified=false}`를 담는다. 녹화 OFF/다른 epoch/없는 ID/입력 재생에서는 null이다. 구형 기록의 필드 부재도 미확인으로 처리한다.

snapshot에 고정한 힌트를 공유 결과/기존 선별 한 행으로 전달하고 반환 문서는 복사한다. 이후 Recorder 정지/세션 교체는 새 snapshot에 적용되며 이전 판단에는 당시 힌트가 남는다. 힌트는 마지막 수락 입력의 위치이고 최신 RAM 값·원본 전체·prefix·제외/거절된 입력의 완전성이나 내구 저장 완료를 뜻하지 않는다. 현 collector-input/v2는 0g를 포함하지 않으므로 상한가 참조까지 확인됐다고 해석하지 않는다. capture_lineage/source_durability·Replay/운영 승인 플래그는 여전히 미확인/false이며 별도 manifest/checksum/원본 대조가 필요하다. 녹화 시작·payload 재기록·manifest/DB 조회와 수집 요청은 추가하지 않는다. [45단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#45-ram-출처의-기존-녹화-입력-조회-힌트).

## 2026-10-13 RAM 판단의 공통 출처 근거 (로컬 구현)

기존 인증 `GET /api/v1/research/ram-shadow`의 live/recorded `ram-shadow-diagnostics/v1` envelope에 optional `source_coverage`와 `input_provenance` map을 추가했다. source_coverage는 회차별 `ram-coverage:<id>`를 키로 `ram-source-coverage/v1`의 Collector source_id/generation·입력 cut·승인 종목/거래소와 continuous_from·retention_since·Collector/effective/delivery gap·dropped_events를 공유한다. input_provenance는 기존 context_ref를 키로 coverage_ref·입력 연속성 상태·H2 창·테마 revision/구성원·입력 행 수·제공된 시장 frame/상한가 refs를 연결한다. 제공 refs는 최종 판단에 사용됐거나 원본 확정됐다는 뜻이 아니다.

실패한 binding의 이전 결과는 이전 근거를 유지한다. 취소 중 publish된 성공 결과에도 같은 cut의 근거를 연결하며, 직접 다른 경로에서 갱신된 결과/확인할 수 없는 입력에는 근거를 부여하지 않는다. 반환 map은 복사본이고 같은 결과/회차는 한 번만 나타난다. 기본 조회는 여전히 DB 0회, include_recorded만 기존 한 행을 명시 조회한다.

선별 기록은 기존 categorical transition·coalescing/interval·한 행·1MiB 예산을 유지한다. 새 근거가 기록 예산을 넘으면 두 optional map을 생략하고 기존 판단이 예산 내라면 저장한다. 이전 기록/생략된 map의 부재는 근거 미확인이며 복구/Replay 승인으로 사용하지 않는다. coverage_ref는 진단 상관 ID이고 native immutable revision 또는 체크섬 검증된 capture 영수증이 아니다. `raw_values_recorded=false`, `capture_lineage=unverified`, `source_durability=not_verified`, `replay_eligible=false`, `order_authorized=false`를 유지한다. [44단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#44-ram-판단의-공통-출처와-연속성-근거-연결).

## 2026-10-13 연구 가설 원본 대조 (로컬 구현)

인증 `GET /api/v1/research/ram-shadow/source-check`은 현재 선택의 `expected_revision`(필수, 0 이상)과 `lookback_seconds`(기본 20, 1–60)를 받는다. 명시 저장 연구 가설이 있어야 하며 관찰 활성화는 요구하지 않는다. 현재 선택 snapshot을 기존 owner lock에서 검증·대조하고 취소 시 native read를 drain한 뒤 잠금을 해제한다. 요청 중 선택 변경과 shutdown은 이 잠금을 기다린다. 기본 RAM 조회/틱에서는 호출하지 않는다.

`stock-thesis-source-check/v1`은 가설/대장 참조·selection_revision·과거 대장 as_of·요청 창·테마 status/조회 예산·canonical 초봉 expected/matched/unmatched refs·미확인 factor/hypothesis/기타 refs·조회 시간/행 수를 반환한다. 테마는 최대 100개 native snapshot에서 정확 ID/내용 hash/구성원을 비교하고, 초봉은 해당 거래일·거래소·구성원·as_of 가용시각의 최신 저장 값 최대 10,000행을 재해시한다. 창은 자정에서 절단하며 예산 초과 창을 일부 결과로 승인하지 않는다. 별도 두 읽기를 단일 DB snapshot으로 주장하지 않는다.

테마 `MATCHED`와 초봉 matched 수는 현재 보존 값의 대조 결과다. 초봉 이전 revision·수집 연속성·factor/hypothesis lineage·운영 승인은 미확인이며 `source_durability=not_verified`, `replay_eligible=false`, `order_authorized=false`를 항상 유지한다. 불일치를 저장 누락/수정/보존 만료 중 한 원인으로 단정하지 않는다. 보고서는 저장하거나 설정에 승인 상태로 반영하지 않는다.

미인증 401, 필수 query/창 범위 오류 422, 선택 없음·revision 충돌·직접 binding·종료 중 409, 불변 근거/대조 예산 오류 400을 반환한다. native DB 장애는 정상 보고서로 삼키지 않는다. PC client의 명시 `check_ram_shadow_sources`를 사용한다. 후속 관찰 화면은 수동 버튼으로만 이 호출을 실행하며 자동 polling은 대조를 호출하지 않는다. [원본 대조 범위](docs/AI_TRADING_SYSTEM_REDESIGN.md#42-연구-가설의-native-원본을-명시-대조) · [화면 연결](docs/AI_TRADING_SYSTEM_REDESIGN.md#43-관찰-화면의-수동-원본-대조와-결과-표시).

## 2026-10-14 작은 블록 본문의 공유 저장 (로컬 후보)

지연 저장에서 1MiB 이하 단일 블록 본문은 명시적 `blocks/shared-v1` descriptor로 기존 16MiB `.payloads` 묶음을 공유한다. schema 4·입력 codec/profile/논리 digest는 유지하며 한 root는 `bytes`와 하나의 `parts` 항목(index=0, bytes, sha256, 6자리 숫자 `.payloads` 이름, offset)을 갖는다. root/part checksum과 파일/offset/논리 크기 한도를 모두 검사한다. 복원은 기존 불변 `BlockPayloadReference`로 입력마다 독립 수행하고 원래 native 호출을 합치지 않는다. 구형 `blocks/v1` 읽기와 큰 입력의 분할 기록을 유지한다.

새 선택의 `payload_storage`는 `blocks_v1_shared_v1_and_chunk_bundle_v1`이며 새 descriptor를 모르는 구형 reader/등록기는 거부한다. 공개 block payload 다운로드의 기존 제한은 유지한다. NAS 설치 helper는 아직 갱신하지 않았고 운영 배포 전 새 등록·reader/Replay 검증과 승인된 helper 갱신이 필요하다. 실제 검증/배포 범위는 CURRENT_STATUS와 OPEN_ITEMS를 따른다.

## 2026-10-13 RAM Shadow 연구 가설 선택 확장 (로컬 구현)

인증 `GET/PUT /api/v1/research/ram-shadow/selection`은 기존 context 선택/expected_revision·saved/applied revision·lock/취소/종료 계약을 사용한다. PUT은 optional nullable `stock_thesis`를 추가로 받는다. 생략하면 현재 선택을 유지하고 null이면 명시 해제한다. GET은 선택이 없으면 필드를 생략한다. 기존 가설 없는 v1 설정/응답의 의미는 그대로다.

`stock_thesis`의 정확한 필드는 `evidence`와 `thesis_policy_version`이며 evidence는 기존 연구 조회의 `thesis`, `leadership`, `run_status`, `stored_identity` 네 키다. 한 가설/기존 정책 하나, UTF-8 JSON payload 1MiB 이하로 제한한다. PC 파일 경로 인자는 허용하지 않는다. 선택 가설의 theme는 theme_ids에 포함되고 venue·COMPLETE/불변 ID/시각·사용 가능한 연구 상태를 검증해야 한다. mode는 선택이 있으면 research_thesis, 없으면 기존 theme_context, 생성자 직접 binding이면 explicit_bindings다.

미인증 401·revision 충돌 409, 근거/가용시각/거래소/path/예산 오류 422, native 저장/적용 pending 503을 구분한다. ACK 유실은 실제 저장 revision만 공개하며 재시도는 적용만 진행한다. 재시작은 저장된 제공 연구 snapshot과 새 H2 상태를 사용하고 원본 확정/운영 승인/주문 권한은 복원하지 않는다. 자세한 현재 범위는 [41단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#41-저장된-연구-가설정책의-관찰-화면과-명시-설정-연결)를 따른다.

> 정리 기준(2026-10-11): API·필드·오류 계약을 다룬다. 날짜가 붙은 확장·검증 문단은 그 단계 당시의 구현·검증 범위이며, 당시의 배포 대기 표현을 현재 배포 상태로 해석하지 않는다. 최신 실행 소스와 배포 기록은 [현재 상태](docs/CURRENT_STATUS.md), 실제 잔여는 [남은 작업](docs/OPEN_ITEMS.md), 진단 운영·제어의 통합 계약은 [NAS 진단 운영](docs/NAS_RUNTIME_DIAGNOSTICS.md)을 따른다.

2026-10-10 로컬 블록 수집 후보: `POST /api/v1/diagnostics/trace`에 엄격한 불리언 `large_inputs`(기본값 false)를 추가했다. `store_inputs=true`와 지연 저장 시각 `persist_at`가 필요하다. 타입 오류는 422, 선행 조건 누락은 409이며 자식 제어를 되돌리고 master를 보존한다.
이 선택은 일봉·분봉 교체, 초봉, Shadow 체크포인트에 trace 스키마 4와 `large-store-input/v1`을 사용한다. 기존 스키마·옵션 필드를 유지하면서 capabilities에 `large_input_capture`를 추가한다. 타입별 계산 한도는 64MiB, 인코딩 블록은 1MiB, 인코딩 입력은 128MiB 다.
오프라인 원래 경로 재생 전에 모든 조각의 체크섬이 통과해야 한다. 공개 payload 다운로드는 블록 루트를 이어 붙이지 않는다. 구간 보고서는 검증한 블록 바이트와 기존 방식으로 보관한 디코딩 바이트를 구분한다. NAS 용량 검증은 미완료이며 배포·월요일 예약 완료를 뜻하지 않는다.

같은 로컬 후보는 엄격한 불리언 `account_inputs`(기본값 false)를 추가한다. `store_inputs=true`와 지연 저장 `persist_at`가 필요하며 타입·선행 조건 오류는 각각 422·409 다. capabilities는 스키마 4와 `native_replay_ready=false`인 `account_input_capture`를 제공한다.
계좌 복구·이벤트·스냅샷과 실행 lease·원장의 인수는 명시적인 타입별 코덱을 사용한다. 권한을 부여하는 owner 토큰만 세션 HMAC 별칭으로 바꾼다. UUID·revision·run ID·order ID는 원래 값을 유지한다. HMAC 키와 원본 토큰은 저장하지 않는다. 행위자 귀속은 실제 계좌 writer·heartbeat·start·stop과 VI 실시간·과거 수집 경로를 따른다.
계좌 수집을 선택하면 입력 승인 전에 `account-context/v2`를 수집한다. registry·binding, 초기 런타임 lease, 실행 intent·event·계좌 snapshot, settings·control·recovery·event 문서를 포함한다. 다음 execution-event 시퀀스는 관측된 빈 구간을 유지하고 트랜잭션 `RESTART`는 v3 seal과 함께 롤백된다. v1 캡슐은 원래 범위를 유지한다.
PostgreSQL은 `READ ONLY REPEATABLE READ`와 이름 있는 cursor를 사용하며 fetch·행·문서·전체 크기 및 읽기 시간을 제한한다. 컨텍스트는 복사 계산 32MiB·50,000행 한도와 전용 블록 프로필을 갖는다. 원래 producer가 시작되기 전에 원자적으로 승인하고 기존 지연 writer로 저장한다.
상태에는 체크섬·개수·시간만 노출한다. 전체 투영 값은 인증된 기록 파일 reader로 읽는다. 원본 권한 별칭은 입력 세션 키를 공유하며 원래 fingerprint·토큰·vault 데이터는 없다. 자식 갱신은 같은 control revision을 확인하고 준비 시간은 요청된 입력 수집 시간을 줄이지 않는다. v3 seal은 v1 부모를 확인한 뒤 같은 관리 트랜잭션에서 분리된 캡슐을 적용할 수 있다.
계좌·VI 스케줄러 연결과 예상 실패 실행은 실제 PostgreSQL 78개 테스트를 통과했다. 오프라인 CLI v3 seal/run은 고정된 캡슐을 읽고 명시적 native frontier·source clock을 확인한 뒤 DB lease를 얻는다. 블록을 이해하는 권한 등록과 v3 worker 연결은 로컬 구현되었으며 root helper 설치는 별도다.
실제 오프라인 CLI seal/run은 NAS PostgreSQL 107개 테스트를 통과했다. 같은 제어 입력 12개·초기 DB로 두 번 실행하여 내용·원래 경로 결과·복원 일치를 확인했다. 공개 `native_replay_ready`는 false를 유지한다. 이 API는 native Replay를 실행하거나 원본 상태 동등성을 보장하지 않는다. NAS 60분 검증·배포는 미완료다.

최신 context/v2 검증은 NAS 138개 테스트를 통과했다. 같은 제어 입력 17개·초기 DB를 두 번 재생했고 매회 COMMIT 14개·롤백 3개였다. 초기 원장의 중복 결과, 원본 원장·문서 내용, 다음 시퀀스를 보존했다. 인코딩 크기 8MiB 초과 Shadow 입력을 실제 PostgreSQL 호출 1회·COMMIT 1회로 복원하여 JSON 일치를 확인했다. seal 실패 시 행·시퀀스를 롤백하고 최종 복원은 소유 연결을 모두 비웠다. 이는 오프라인 검증이다. 최신 root helper·등록 bundle 경로와 60분 배포 검증은 남아 있다.

2026-10-09 기록 입력 trace 소스(`2026.10.09-trace-ram-main-v1`): 인증된 `GET /api/v1/diagnostics/capabilities`는 `trace_input_capture` 스키마 3, 관측 경로에 한정된 범위, 인과 REST·catalog·ranking·subscription·lifecycle·delivery-receipt 경계와 collector 이벤트 `0B`, `0w`, `0J`, `0U`를 알린다.
`POST /api/v1/diagnostics/trace`의 엄격한 불리언 선택 `store_inputs`, `collector_inputs`, `top20_inputs`는 기본값 false를 유지한다. 지연 저장 한도는 8GiB·5,000,000이벤트·1MiB/s이며 제한된 원시 staging과 프레임 RAM 블록에 입력을 보관한 뒤 속도를 제한하여 저장한다. 이는 설정상 논리 한도이며 RSS 보장이나 전체 시장 수집 범위의 증거가 아니다. NAS 활성 release는 당시 PC main 소스로 전환되지 않았다.

2026-09-29 NAS 진단 API v4(`2026.09.29-diagnostic-api-v4`)가 NAS에 배포됐다. 기존 Bearer 인증 아래 `GET /api/v1/diagnostics/capabilities`, `PUT /api/v1/diagnostics/control`, `GET /api/v1/diagnostics/snapshot`, `POST /api/v1/diagnostics/runs`, `GET /api/v1/diagnostics/runs/{run_id}`, `POST /api/v1/diagnostics/runs/{run_id}/cancel`, `GET /api/v1/diagnostics/reports`, `GET /api/v1/diagnostics/reports/{report_id}`, `GET /api/v1/diagnostics/history`를 제공한다. control은 `target=master|capture|workload`, `expected_revision` 필수, 기존 session/TTL/owner 규칙을 따른다. PostgreSQL snapshot은 고정 read-only query만 실행하며 `sections=postgres,activity,news_jobs,host,storage`에서 선택한다. query text·parameter·DSN은 반환하지 않는다. run은 `kind=measure|compare`, `seconds`와 선택 label/workload/request_id를 받아 202와 run ID를 반환하고, 동일 session/request_id의 같은 요청은 기존 run을 반환한다. 한 번에 한 run만 허용하며 409 충돌은 큐잉하지 않는다. 보고서는 기존 diagnostic-results JSON도 조회한다. 실제 검증은 [설계·검증 문서](docs/NAS_RUNTIME_DIAGNOSTICS.md#api-contract)에 기록하며, 관측 범위는 `opt_in_observed_calls_only`와 각 section의 scope에 한정된다.

2026-09-27 공통 DB 관측 pilot: 인증된 `GET /api/v1/diagnostics/db-calls?start=<epoch>&end=<epoch>&mode=summary|verbose|raw&limit=200&slow_ms=500`는 capture 중 공통 경계를 통과한 호출만 반환한다. 구간은 0초 초과·1,800초 이내, `limit`은 1~500, `slow_ms`는 1~30,000이다. 응답은 `coverage=opt_in_observed_calls_only`, capture 상태, 보존 범위와 drop 수, `(writer_family,writer_kind)`별 `writers`와 `readers` 집계 및 무등록 수를 포함한다. 기존 call record에 `access_mode`가 없으면 `write`로 취급한다. `raw`는 최대 `limit`개 호출별 ID·PID·접근 분류·단계 시간·outcome·오류 타입만 노출하며 SQL/parameter/DSN은 담지 않는다. 현재 이관 writer는 REST query cache 한 종류이며 cache reader는 별도 `read.query_cache/query_cache` 집계다. capture OFF/만료나 미이관 경로는 0건 활동으로 해석하지 않는다. 기존 `/diagnostics/market-bar-saves`와 `/diagnostics/writers`는 유지한다.

2026-09-28 READ 확장: summary 응답은 writer와 reader를 분리한다. 현재 관측한 reader kinds에는 `read.query_cache/query_cache`, `read.document_collection/document:<collection>`, `read.market_bars/minute_bar|daily_bar`, `read.observation_revisions/observation_revision|observation_revisions_after`, `read.shadow_monitor/monitor_state|candidate_events`, `read.dataset_snapshots/dataset:<kind>`, `read.market_data_metadata/metadata_range`, `read.news_history/history:<kind>`, `read.news_revision/article_revision|body_revision`, `read.news_source/source_cursor|source_diagnostics`, `read.news_publications/market_feed|stock_articles|confirmed_articles`가 포함된다. 이 목록은 접근 전수 인벤토리가 아니며 capture OFF·미이관 경로가 0건임을 의미하지 않는다.

2026-09-26 진단 상태 응답 추가: 인증된 `GET /api/v1/diagnostics/workloads`는
최상단 `diagnostic_tool`에 `enabled`, `expires_at`, `owner`, `session_id`를,
응답 최상위에 `control_revision`을 포함한다. `workloads`의 자식 상태와
`metrics_capture`는 같은 제어 파일 세대를 한 번 읽어 평가한다.
`workloads[*].effective`는 운영 설정과 진단 pause를 결합한 요청 상태이며,
실제 작업 drain 완료를 뜻하지 않는다. master OFF·만료·재시작 시 진단
override는 무효화된다. CLI 측정 보고서의 `state=aborted`는 중간 종료를 뜻하고,
`report <id>` 읽기는 master OFF에서도 허용된다. 보호 수신·주문·writer 제어는
현재 API 구현 범위에 포함되지 않는다.

상태 정리 기준: 2026-09-22. 아래 API·필드·실패 처리 계약은 유지한다. R0~R6 로컬 구현과 R7 기본 배포·HTTPS/WSS 연결은 완료 기록이 있으며, 최신 누적 빌드의 실행 반영과 장중·장시간 검증은 별도다. 단계 이름이 붙은 설명은 그 단계의 구현 범위를 나타낸다. 현재 구현·배포 상태는 [현재 상태](docs/CURRENT_STATUS.md), 미완료 구현과 운영 검증은 [남은 작업](docs/OPEN_ITEMS.md)을 따른다.

현재 계약 버전은 `api_version=v1`, `schema_version=1`이다. 모든 `/api/v1/*` HTTP 요청은 `Authorization: Bearer <NAS 접속 토큰>`이 필요하다. `/health`만 공개다. WebSocket은 같은 헤더 또는 `?token=`을 사용한다.

2026-09-26 진단 상태 응답은 `external_market`의 `runtime` 객체에 `operational_enabled`, `running`, `poll_seconds`, 누적 수집 시도·완료·성공 행 수와 마지막 실행 시각·행 수·오류를 추가한다. 이 항목의 `effective`는 운영 활성화, 살아 있는 수집 task, 진단 pause를 모두 반영한다. 계측값은 프로세스 수명 내 메모리 상태이며 수집 task의 실제 DB 반영 성공을 행 수만으로 보증하지 않는다.

자동 시장자료 조회에서 `GET /api/v1/market/snapshots/{kind}`의 `kind`는 `investor_flow`, `program_flow`, `new_high`, `market_index_chart`를 포함한다. `GET /api/v1/content/stock_fundamentals`, `stock_nxt_eligibility`, `historical_highs`는 NAS가 만든 종목 문서를 반환한다. `historical_highs.document.target`은 `price`, `first_year`, `last_year`, `occurred_on`, `evidence`를 가진다. 중앙 연결이 정상인데 자료가 비어 있으면 앱은 빈 저장 결과로 처리하고 Kiwoom TR을 대신 만들지 않는다.

중앙 `00` 실시간에서 확인한 실제 매수 체결은 `account_entry_symbols_daily` 내부 문서에 거래일·종목·환경·관측시각만 저장한다. 이 목록은 NAS의 시장자료 구독·보완 범위에만 사용하며 TOP20 membership, TOP20 지수 및 계좌별 매매일지 계약을 변경하지 않는다.

## 공통 오류

- `401`: 접속 토큰 없음/불일치
- `400`: 허용하지 않은 Kiwoom API·경로·연속조회 값 또는 잘못된 입력
- `404`: 지원하지 않는 스냅샷 종류/콘텐츠 컬렉션
- `405`: 전체 교체가 허용되지 않은 콘텐츠 컬렉션
- `429`: 중앙 AI 공급자가 호출 한도를 반환한 경우. 클라이언트는 즉시 연속 재요청하지 않는다.
- `502`/`503`/`504`: 상류 Kiwoom/뉴스/AI 처리 실패 또는 일시적 혼잡. JSON `detail`은 비밀값 없이 사용자에게 표시 가능한 원인을 담는다.
- `503`: 서버에 필요한 공급자 자격 증명이 없음

## 상태와 설정

### 시세 담당 역할 조회·변경 (구현 계약)

인증된 `PUT /api/v1/settings/market-profile` 요청은 market_profile_id,
expected_revision(0 이상 정수), expected_binding_revision(1 이상 정수) 세 필드만 받는다.
role revision/binding 충돌·후보 실행 미준비·실전 credential 후보 대기는 409, 잘못된 입력은 422,
실전 owner 없는 설치 또는 전환/복구 실패는 503이다. 성공은 GET과 같은 응답을 반환한다.
활성 실전 owner가 있는 설치에서만 변경을 적용하며 모의 profile은 대상이 될 수 없다.

전환은 기존 두 계좌 조회·시장 WS·물리 REST 작업을 drain하고 직전 snapshot/DB CAS를 확인한 뒤,
기존 중앙 broker와 계좌 broker 사이의 검증된 client만 교환한다. 물리 요청 잠금/토큰/호출 이력은
각 client를 따라간다. 기존 단일 시장 collector/집계/hub·우선순위 큐를 유지한다.
같은 담당 변경은 저장/연결을 다시 하지 않는다. 기본계좌/v2 암묵적 대상과 두 계좌 cursor/binding은 유지한다.
실시간 계좌 event의 신원은 현재 시세 담당을 따로 사용하며 기본계좌 신원을 대신 쓰지 않는다.

GET의 applied_revision은 담당 REST/계좌 routing과 collector 재연결 시작이 적용된 역할 revision이다.
전체 REG 승인/실시간 수신 준비 완료를 뜻하지 않으며 기존 upstream ready 계약은 별도다.
미준비/실패/owner 없는 설치는 null이다. 재시작도 저장된 담당을 먼저 배치하고
legacy 기본계좌는 별도 조회 context로 복원한다. 대상 복원 실패 시 다른 계좌로 자동 대체하지 않는다.
저장 전 검증/CAS 실패는 이전 routing/cursor를 복구한다. 저장 결과가 불명확하거나 저장 후 실패하면
MARKET_ROLE_RECOVERY_REQUIRED로 두 대상 조회/시장 연결을 차단하고 적용 revision을 null로 둔다.
이 상태는 기존 역할로 임의 복귀하지 않고 저장 상태/키 정상 확인 후 서버 재시작으로 복구한다.
HTTP 대기 취소는 소유된 전환 task를 취소하지 않으며 종료가 그 task의 완료를 기다린다.

아래는 R6b3a 저장 기반 및 R6b3b1 내부 보호 계약이다.

인증된 `GET /api/v1/settings/market-profile`은
`{"settings":{"market_profile_id":"nas-real-default","legacy_real_profile_id":"nas-real-default","revision":0},"applied_revision":null}`을 반환한다.
문서가 없으면 이 기본값을 읽기만 하고 저장하지 않는다. 저장 문서 손상은 409
`MARKET_PROFILE_SETTINGS_RECOVERY_REQUIRED`이며 기본값으로 덮지 않는다.
R6b3a 당시에는 applied_revision=null·GET만 제공했다. 실제 전환 PUT은 위 R6b3b2에서 연결했다.
일반 content 동기화로 역할 문서를 쓰는 경로도 열지 않는다.

내부 저장은 역할 expected_revision과 검증된 계좌 expected_binding_revision을 함께 확인한다.
활성 kiwoom_real/real profile·활성 registry·해당 계좌의 active_profile_id가 일치해야 한다.
monitor OFF는 시세 담당 자격을 없애지 않는다. legacy_real_profile_id는 nas-real-default로 고정한다.
같은 역할 저장은 revision/updated_at을 유지하며 충돌은 전체 rollback한다.
시세 담당 profile의 disable 또는 계좌 연결 해제는 MARKET_PROFILE_REQUIRED로 거절한다.
이미 완료한 credential replay는 이후 역할·계좌 설정을 변경하지 않는다.
실제 역할 변경 API/REST·WS 전환은 R6b3b2, 계좌별 REST·실시간 운영은 R6b3c에서 연결했다. 실제 복수 계좌의 장중 전환·수신 검증은 남은 작업으로 구분한다.

R6b3b1은 실제 전환 전에 사용할 서버 내부 독점 구간을 구현했다. 실전 credential 후보가
VALIDATING/READY/apply 상태이면 담당 전환은 PROFILE_BUSY로 거절하며 기다리지 않는다.
전환 구간 중에는 새 실전 credential 후보를 받지 않는다. 현재 역할/계좌 binding/활성 profile과
실행에 적용된 암호화 vault revision을 검증하고 저장 직전에 같은 snapshot을 재검증한다.
취소/예외는 구간을 해제하며 종료는 구간이 끝나기 전에 context를 닫지 않는다.
독점 구간 자체는 문서/REST/WS를 변경하지 않는다. R6b3b2 호출자가 구간 안에서 전환한다.

### 선택 계좌 조회·모의주문 (구현 계약)

R6b3c1은 실전 계좌의 read-only 복구 조회 기반을 연결했다. 서버 내부
RealCredentialOwner.read_account(profile_id, expected_binding_revision=...)는 admitted real scope와
현재 binding revision을 확인하고 계좌당 한 개의 소유 task로 조회한다. 호출자 취소는 실제 조회를 취소하지 않는다.
기존 중앙/계좌 broker를 사용하며 새 client·요청 한도·WS를 만들지 않는다.
키 변경/시세 담당 변경/종료는 여러 TR로 구성된 실제 조회 전체가 끝나기를 기다린다.
현재/미체결 ka10075, 체결 ka10076, 잔고 kt00018, 예수금 kt00001은
일반 v1 kiwoom/query에서 400 ACCOUNT_QUERY_SCOPE_REQUIRED로 차단한다.
이 단계는 새 공개 계좌 복구 조회 endpoint/계좌 운영 PUT을 추가하지 않는다.
R6b3c1 당시 자동 수집·실시간 적용은 후속이며 실전 monitor applied_revision은 null이었다.

R6b3c2a는 같은 계좌 설정 GET/HTTPS PUT에 real owner를 연결한다. 요청 필드는 기존 mock과 같으며
현재 active_profile_id를 유지하고 real mock_order_enabled는 반드시 false여야 한다(위반 422).
계좌별 monitor_enabled는 기존 broker의 30초 REST 복구 조회/실전 전용 저장을 켜고 끈다.
첫 자동 회차도 30초 뒤 시작하고 같은 계좌 수동 조회가 있으면 그 회차를 건너뛴다.
OFF/ON은 시장 collector/순위를 중지하거나 reconnect하지 않으며 일반 v1 계좌 조회 차단을 유지한다.
실전 GET/PUT은 monitor_status를 추가한다: mode=rest_poll, state=waiting|collecting|off|paused|unavailable,
poll_interval_seconds(기본 30), last_success_at(aware ISO|null), error_code(비밀 없는 코드|null).
applied_revision은 REST 수집 정책이 적용된 revision이다. 성공 자료를 받았다는 증거는 last_success_at이며,
WS/REG 승인·실제 주문 허용을 의미하지 않는다. 키 binding이 바뀌면 이전 성공 상태를 초기화한다.
설정 변경 대기 취소는 소유 task를 취소하지 않는다. 실제 조회/저장 완료 뒤 설정 CAS/ON-OFF를 적용한다.
낡은 설정은 409 ACCOUNT_SETTINGS_REVISION_CONFLICT다. 저장 결과 불명확/적용 복구 실패는
503 ACCOUNT_SETTINGS_RECOVERY_REQUIRED, 해당 계좌 수집 paused 및 applied_revision=null이다.
이 경우 시장 collector·역사 조회는 유지하고 임의 DB rollback 없이 정상 키/저장 상태 확인 후 서버 재시작으로 복구한다.
R6b3c2a 당시 계좌 WS 분배·비담당 WS는 후속이었다. 공개 snapshot 조회나 실전 주문 API는 추가하지 않는다.

R6b3c2b는 monitor_status에 realtime={source: shared_market|account_only,
state: ready|waiting|off|paused, last_event_at: aware ISO|null, dropped_events: int,
error_code: 안전한 코드|null}를 추가한다. 기존 mode=rest_poll/applied_revision 의미는 유지한다.
ready는 시장 연결의 승인 완료 또는 계좌 전용 REG 성공이며 last_event_at은 실제 이벤트 저장 성공이다.
시세 담당은 기존 00/04 시장 WS를 공유하고 비담당은 자신의 real 인증/계좌 전용 00/04 연결만 연다.
raw 9201 HMAC/현재 binding/scope를 검증하고, 다른 계좌/누락 신원/이전 monitor generation의 callback은 버린다.
비담당 체결·잔고도 기존 hub에 익명 scope로 한 번 전달한다. 매수 종목은 기존 NAS 시장 수집 범위에 합친다.
실전 연결 LOGIN/REG 성공을 확인한 뒤 REAL을 전달하며 PING은 그대로 응답한다.
계좌 전용 관측은 평일 08:00~20:00이다. 휴장일 달력/동시 토큰 실제 운용은 R7 실환경 항목이다.
00 접수/취소·04 변경·연결 상태는 0.5초 동안 합쳐 계좌 REST 복구를 깨우며 30초 backup을 유지한다.
계좌 이벤트 저장은 별도 소유 writer에서 진행하여 시장 수신이나 REST 조회 완료를 기다리지 않는다.
저장 실패는 pending event를 보존해 30초 간격으로 재시도한다. 미저장 이벤트가 있으면 살아 있는
키/역할/설정 전환은 commit하지 않고 이전 정책 복구를 시도한다. 오류는 raw exception 대신 안전한 코드다.
1000개 queue가 가득 차면 dropped_events와 REAL_ACCOUNT_EVENT_QUEUE_FULL을 기록하고 REST 복구를 요청한다.
메모리 대기는 DB outage 중 강제 종료/프로세스 재시작을 견디는 영속 outbox가 아니다.
실제 socket 종료가 실패하면 REAL_ACCOUNT_DRAIN_FAILED로 표시하고 새 연결/설정 commit을 차단한다.
저장 설정은 유지하고 해당 계좌 수집 paused/applied_revision=null로 둔다. 정상 키/종료 상태 확인 후 재시작으로 복구한다.
이 전달을 실전 주문 실행/reconcile이나 유일한 execution ledger로 해석하지 않는다.

실전 reader의 주문 조회는 stex_tp=0 통합, 잔고는 dmst_stex_tp=KRX/NXT를 각각 조회한다.
같은 종목의 같은 수량은 한 번만 사용하며 수량 충돌/계좌 테이블 누락/중복 주문 페이지/
연속 cursor 순환/서로 다른 거래소의 같은 주문번호는 실패로 처리하고 부분 결과를 반환하지 않는다.
정상 빈 list는 빈 자료이며 누락/null 테이블을 빈 계좌로 단정하지 않는다.
기존 mock reader는 KRX 조회·mock 환경 검사와 출력 필드 계약을 유지한다.
조회 결과는 정규화된 account/orders이며 원계좌번호·OAuth 토큰을 결과에 복사하지 않는다.
실전 결과를 기존 mock 실행 원장에 저장하지 않는다.

capability `multi_account_query_v3`는 명시 대상 조회 경로, `scoped_mock_orders_v2`는
실제 mock owner의 선택 계좌 주문 경로가 있음을 표시한다. 개별 계좌의 키/주문 허용 여부는 별도로 확인한다.
모의와 R6b2 실전 owner가 연결된 설치는 admitted profile별 client/broker로 계좌를 지정해 조회한다.
실전 owner는 vault·계좌 HMAC 검증·real 주 실행 환경을 갖춘 설치에 연결된다.
그 외 기존 설치의 실전 조회는 검증된 main profile만 사용하고 미연결 profile은 503 PROFILE_RUNTIME_NOT_READY다.

R6a 단계에서는 서버 내부의 실전 재연결 기반을 구현했다. collector의 pause/drain/resume와 이전 연결
generation 차단·실제 token/socket/저장/장 마감 완료, 계좌 query의 신규 BUSY/실제 작업 drain과
선택적 cursor 폐기를 제공했다. R6a 자체는 실제 kiwoom_real 인증 hook/입력 UI나
새 공개 endpoint/실전 지원 capability/응답 필드를 추가하지 않은 기반 단계다.
후속 R6b owner/API와 R6c1 입력·R6c2 planned reconnect/등록 확인/관측 간격 PC 표시까지 로컬 연결했다.

R6b1은 기존 credential activation 저장 계약을 실전계좌에도 적용한다. 검증된 scope의
binding·적용 원장·단일 active_profile_id는 같은 트랜잭션에서 확정된다. 중복 profile의
계좌 claim 실패는 전체 rollback하며 같은 profile 키 갱신은 운영 설정을 보존한다.
사용 중지는 과거 binding을 유지하고 계좌 설정을 OFF로 저장한다. 완료 replay는 이후
설정이나 새 활성 profile을 덮지 않는다. 최초 저장 monitor ON은 실제 runtime 시작을 뜻하지 않는다.
R6b1 단계 자체는 새 실전 hook/endpoint/capability나 실전 설정 PUT 지원을 추가하지 않았으며, 후속 연결 범위는 아래 R6b2/R6b3 계약을 따른다.

R6b2는 기존 credential prepare/apply/status API에 kiwoom_real hook을 연결한다. provider metadata의
supported는 실제 owner 연결 여부를 반영하고 v3 계좌 목록/조회는 검증된 실전 profile별로 분리한다.
R6b2 단계에서는 시세 담당으로 nas-real-default의 기존 client/broker/단일 collector를 유지했다. 이후 담당 변경은 위 역할 조회·변경 계약을 따른다. 추가 실전계좌의
과거 조회/키 갱신은 시장 WS를 재연결하지 않는다. v2 계좌 조회의 암묵적 대상은 여전히 기본 프로필이다.
사용 중지는 비시세 profile만 허용하며 시세 담당에는 MARKET_PROFILE_REQUIRED를 반환한다.
동일 계좌 키 갱신의 scope/run은 유지하고 계좌 변경·중복 연결은 준비에서 거절한다.
commit 후 실패는 RECOVERY_REQUIRED이며 기존 키를 다시 열지 않는다. 명시 복구의 후보 검증도
기존 broker queue/요청 잠금/한도를 쓰며 일반 조회는 계속 차단한다.
ACTIVE는 REST 인증/계좌 binding 적용이며 모든 WS REG 승인·연속 수신 완료를 뜻하지 않는다.
실전 monitor ON 저장만으로 계좌별 실시간 수집 완료를 주장하지 않으며 해당 GET의 applied_revision은
수집 운영 적용 전 None이다(사용 중지가 실제 적용된 비시세 계좌는 OFF revision 확인 가능).
시세 담당 전환/CAS/PUT은 R6b3b2, 실전 계좌 운영 PUT·추가 계좌 실시간 수집은 R6b3c, PC 입력/재연결 표시는 R6c에서 연결했다. 구현 완료와 실제 복수 계좌·토큰의 장중 운용 검증은 구분한다.

| Method / path | 요청 | 응답 |
| --- | --- | --- |
| `POST /api/v3/kiwoom/account-query` | 기존 v2 페이지 필드 + 필수 account_scope, credential_profile_id, expected_binding_revision | payload/batch_id/page_index/complete/next_key/context/provenance; v2 응답 형태 유지 |
| `POST /api/v2/mock/accounts/{account_ref}/orders` | 기존 v1 지정가 주문 필드 + 필수 account_scope, credential_profile_id, expected_binding_revision | 기존 주문/events + 검증된 context |
| `GET /api/v2/mock/accounts/{account_ref}/orders/{intent_id}` | query: environment=mock, credential_profile_id, expected_binding_revision; broker=kiwoom 기본 | 해당 계좌/current run 주문/events + context |
| `POST /api/v2/mock/accounts/{account_ref}/orders/{intent_id}/cancel` | account_scope, credential_profile_id, expected_binding_revision, 선택 quantity | 취소 결과의 주문/events + context |

모든 경로는 bearer 인증을 요구한다. account_scope는 정확히 broker/environment/account_ref이며
0이 아닌 UUID를 사용한다. POST 대상 필드는 필수, binding revision은 양의 정수이고 bool은 거절한다.
모의 path의 account_ref와 body scope도 같아야 한다. 잘못된 scope는 400, 대상/연결 버전 불일치는 409,
미준비 profile/비활성 계좌는 503, 다른 계좌 또는 run의 intent는 404 MOCK_ORDER_NOT_FOUND다.
잘못된 대상·cursor·intent는 공급자 요청 전에 거절한다. 새 경로의 HTTP 예외는 고정 오류 코드를 사용한다.

조회는 kt00007/kt00015만 허용한다. cursor는 계좌 bundle별 API/path/body/binding에 고정되고
연결 교체나 bundle 종료 뒤 재사용할 수 없다. 응답 직전에도 binding을 확인한다.
HTTP 대기자 취소는 실제 페이지 작업을 취소하지 않으며 bundle 종료가 실제 작업까지 기다린다.
계좌별 최대 32개 owned 작업·256개 열린 cursor이며 혼잡/종료는 503이다.
모의 페이지는 기존 공통 2개 read limiter와 계좌별 mock broker의 한도를 사용한다.

모의 주문은 기존 명시 주문 허용·KRX 지정가 정책을 유지한다. 주문 허용 OFF이면 신규 주문/취소는 503이다.
활성 binding이 있는 경우 주문 허용 OFF라도 해당 계좌/current run의 저장 주문 GET은 가능하다.
비활성 profile의 과거 기록 열람은 향후 UI/일지 DB 경로로 제공하며 이 실행 API로 다른 profile을 대신 선택하지 않는다.
새 scoped intent ID는 environment/account_ref/run/request_id로 분리한다. 동일 요청은 기존 상태를 반환하며
SUBMISSION_UNKNOWN도 자동 재전송하지 않는다. 기존 v1의 run/request_id ID 계산과 기본 nas-mock-default는 유지한다.
v2 계좌 조회의 legacy main profile도 화면 선택에 따라 바뀌지 않는다.

| Method / path | 요청 | 응답 핵심 | 역할 |
| --- | --- | --- | --- |
| `GET /health` | 없음 | `status`, `api_version`, `schema_version`, `server_time`, `server_build`, `operational` | 공개 생존 확인과 부분 업무 관측 |
| `GET /api/v1/capabilities` | 없음 | 버전 + `capabilities` boolean map | 서버가 제공하는 기능 협상 |
| `GET /api/v1/settings/operations` | 없음 | AI·뉴스·Shadow 설정과 revision/applied_revision/apply_status | 영속값과 실행 적용 상태 읽기 |
| `PUT /api/v1/settings/operations` | 변경 필드, 선택 expected_revision | 저장된 전체 구조와 적용 상태 | 부분 변경; 충돌 409, 저장/적용 실패 503 |

### 업무 상태 관측 — 2026-10-11 로컬 소스

`/health.status=ok`와 기존 `realtime_connection`은 보존한다. 추가 `operational`은
`coverage=partial`이며, 수집기 저장 실패 또는 후보 입력 누락/만료·평가 오류가 있으면
`state=degraded`, 그 외에는 `unknown`이다. 모든 업무가 정상이라는 판정은 제공하지 않는다.
수집기/후보 감시가 구성되지 않은 경우 각각 `realtime`/`candidate_inputs`는 null이다.

| 기존 owner에서 수집하는 값 | 의미와 범위 |
|---|---|
| `realtime.receive.last_received_at`, `age_seconds` | 이 프로세스가 승인한 0B 체결을 마지막으로 관측한 UTC 시각·monotonic 경과 시간. 전체 공급자 수신 시각은 아니다. |
| `realtime.persistence.last_success_at`, `last_success_by_kind` | 기존 native 저장 호출이 성공 반환한 시각. 프로세스 재시작·아직 저장 없음은 null/빈 map이며 DB의 과거 성공을 추측하지 않는다. |
| `pending_records`, `inflight_records`, `pending_by_kind`, `inflight`, `waiting_flushes` | 기존 버퍼·재시도 안의 저장 레코드 수와 실제 저장 중 레코드/flush 대기 수를 구분한다. 고유 입력 이벤트 수가 아니며 정상 봉·최신값 저장 주기의 대기도 포함한다. |
| `oldest_record_at`, `timed_records`, `record_time_basis` | 보유 레코드의 관측/최신 갱신 시각 중 가장 오래된 값과 시각을 아는 레코드 수. 최초 enqueue 시각·정확한 queue 대기 시간은 미계측이다. |
| `unresolved_failures`, 저장 `state` | 종류별 마지막 실패 시각을 같은 종류의 성공 ACK까지 보존한다. 다른 저장 성공으로 실패를 지우지 않는다. `observed`는 성공 관측 이력이며 대기열 완료나 전체 저장 정상의 뜻이 아니다. |
| `candidate_inputs.universe` | 봉 기반 후보 모니터의 조회 시각 후보군 available_at·나이·기존 max-age 기준의 fresh/stale/missing. 장외라도 시각을 임의 갱신하지 않는다. `scope=leader_breakout_0B`에서는 이 항목이 없으며 별도 `configuration_missing`과 평가 상태를 보존한다. |
| `candidate_inputs.last_evaluation`, `current_all_inputs_fresh` | 마지막 평가 결과/시각은 현재 신선도와 분리한다. 모든 판단 입력의 현재 최신 여부는 미계측이므로 null이다. |

응답은 기존 owner의 메모리만 읽고 DB 조회·버퍼 drain·업무/주문을 시작하지 않는다.
공개 필드에는 오류 원문·계좌/종목 식별자·키/토큰을 추가하지 않는다. 뉴스·외부시장·다른
writer와 최초 enqueue 대기 시간의 종합 관측은 후속 범위이며, 이 추가는 NAS 배포를 뜻하지 않는다.

R5d2a는 operations에 `external_market_enabled`/`external_market_auto_roll_enabled`(strict bool),
`external_market_poll_seconds`(strict int 60~86400), `external_market_roll_confirmations`(strict int 1~100)을 추가한다.
기존 revision CAS/부분 변경/인증과 revision/applied_revision/apply_status 계약을 유지한다.
상품 symbol이 없는 서버에서 ON은 저장 전 422 EXTERNAL_MARKET_SYMBOLS_REQUIRED다. 초기 OFF에도
symbol이 있으면 같은 수집기를 조립하며 새 설정은 재시작 없이 활성화된다. 저장 OFF는 ENV ON보다 우선한다.
갱신은 현재 수집 cycle의 실제 HTTP·봉·roll·status 저장까지 drain한 뒤 적용하며 요청 waiter 취소도
이를 취소하지 않는다. shutdown은 pending 갱신과 실제 수집 완료를 기다린다. 다른 중앙 서비스는 유지한다.
기존 봉/roll 이력/일봉 기준 cache를 초기화하지 않는다. 상품/활성 월물 수동 변경 필드는 이번 범위에 없다.
저장 전 실패는 기존 실행/버전을 유지하고 저장 후 실패는 503 OPERATIONAL_SETTINGS_APPLY_PENDING/
복구 필요로 공개한다. GET으로 확인 후 같은 revision을 명시 재저장할 수 있으며 자동 재적용하지 않는다.
PC는 구 NAS의 응답에 없는 네 필드를 보내지 않고 해당 controls를 비활성화한다. 새 endpoint/SQL 변경 없음.

R5d2b는 같은 operations에 `hot_cohort_condition_enabled`(strict bool),
`hot_cohort_condition_name`/`hot_cohort_condition_substring`(strict string, 최대 120자)을 추가한다.
정확한 이름이 있으면 우선하고 없으면 substring에 유일하게 일치하는 조건식을 선택한다.
ON에서 두 선택값이 비면 422 CONDITION_SELECTION_REQUIRED다. 조건검색 서비스가 조립되지 않은
서버에 조건 필드를 쓰면 저장 전 422 CONDITION_RUNTIME_NOT_READY다. 기존 인증/부분 PUT/CAS는 유지한다.
응답의 `condition_runtime_supported`와 `condition_status`는 현재 메모리 실행 상태다.
`condition_status`는 enabled/configured_exact_name/configured_substring, active(seq/name 또는 null),
policy_revision/active_policy_revision, coverage=KRX, apply_status를 가진다.
상태는 WAITING_CONNECTION/WAITING_LIST/WAITING_REGISTER/WAITING_CLEAR/ACTIVE/OFF/RECOVERY_REQUIRED다.
operations의 ACTIVE는 설정 전달 완료이며 실제 조건 등록 완료는 condition_status.ACTIVE로 확인한다.
나중에 등록/해제 실패가 확인되면 operations GET도 RECOVERY_REQUIRED를 반환한다.
같은 expected_revision으로 명시 재저장해 다시 시도하며 변경값이 없으면 영속 revision은 증가하지 않는다.

설정 PUT은 WebSocket 응답을 기다리지 않는다. 기존 단일 수신 loop가 변경을 감지해 CNSRLST와
CNSRREQ를 보내고 전체 초기 결과/페이지를 확인한 뒤 새 조건을 활성화하고 이전 조건을 CNSRCLR로 해제한다.
후보 초기 결과와 실시간 신호를 합쳐 최대 5,000건 보류하며 초기 결과 다음 실시간 신호 순서로 적용한다.
잘못된 seq/페이지 cursor/형식, 등록 실패/15초 응답 timeout/queue 초과는 부분 후보 적용을 막는다.
런타임 변경 뒤 seq 없는 초기 응답은 추측하지 않고 timeout/복구 필요로 처리한다.
실패한 후보/이전 조건 해제는 ACK를 확인하며 실패하면 명시 재저장 또는 실제 재연결이 필요하다.
미완료 요청이 있는 중간 변경은 이전 응답/후보 정리를 마친 뒤 최신 정책만 등록한다.
OFF는 신규 조건 신호를 무시하고 등록 해제를 요청하되 기존 코호트·VI·체결·hub·일자 보존 정책은 유지한다.
queue에 접수된 신호는 당시 조건식 seq/name을 보존한다. 조건 교체로 과거 접수 신호의 이름을 바꾸지 않는다.
접수 시각·거래일도 queue/잠금/저장 재시도 동안 고정한다. signal/metadata worker는 저장 실패를
완료로 소비하지 않고 같은 native revision/current 입력을 ACK까지 재시도한다. NXT 여부는 ACK 뒤
RAM과 구독에 반영한다. 정상 종료는 접수 signal→파생 metadata→실제 native 작업을 drain하며,
10초 경고 뒤에도 대기를 유지한다. 지속 DB 오류에서는 종료 성공을 반환하지 않는다.
`condition_status.cohort_collection`은 현재 process의 queue queued/unfinished, owned_tasks,
native_inflight, save_failures/worker_failures, last_error_types, dropped_inputs, state와
recovery_required/admission_closed를 제공한다. database_ack_only이며 restart_coverage는 unverified다.
기존 bounded queue의 overflow는 dropped_inputs로 드러나며 강제 종료 전 미확정 RAM 입력의
재시작 복구를 보장하지 않는다. 조건 REG의 apply_status와 별도 상태다.
`/api/v1/market/events?kind=cohort`의 condition.runtime도 저장 목록 진단에 현재 메모리 상태를 합쳐 반환한다.
PC는 지원 flag가 true이고 세 필드가 있을 때만 입력을 활성화/전송한다. 새 endpoint/SQL/연결 owner 없음.

R5d2c PC NAS 운영 화면은 기존 `news_query_set_enabled`, `news_query_set`,
`news_query_set_refresh_seconds`를 편집한다. 서버 필드/endpoint 의미를 변경하지 않는다.
검색어는 한 줄에 하나이며 앞뒤 공백/빈 줄/중복을 제거하고 구문 내부 공백은 유지한다.
정규화 후 최대 50개, UI에서 ON은 한 개 이상을 요구하고 OFF는 빈 목록도 허용한다.
수집 주기는 60~86400초다. 세 필드가 모두 있는 NAS에만 입력을 활성화/전송하며 기존 worker/
부분 PUT/expected_revision CAS를 사용한다. 변경 없는 값과 이 화면이 편집하지 않는 설정은 보내지 않는다.
등록 종목 뉴스 주기 `news_refresh_seconds` 및 선택 종목의 60초 freshness와 공통 검색 주기를 분리한다.
검색어 운영값은 PC 직접 뉴스 설정/키에 복사하지 않는다. NAS 화면은 폼을 스크롤하며 저장 버튼을 유지한다.

기존 QuerySetNewsCollector는 한 번 시작한 검색어의 owned 통신/페이지/저장을 완료한다.
수집 중 ON/OFF·검색어·주기가 바뀌면 이전 정책으로 다음 검색어를 시작하지 않고 다음 정기 loop가
새 정책을 사용한다. 정상 완료 source의 다음 조회 시각은 last_success + 현재 poll_seconds로
판정한다. 따라서 저장된 오래된 next_schedule_at 때문에 단축 주기가 밀리지 않으며 주기 연장도 적용된다.
실패 backoff/부분 페이지/명시 next_schedule_at=0은 기존 예약 의미를 유지한다.
기사/본문/분류 작업·source cursor·요청 사용량·일일 상한을 초기화하지 않는다. 제거한 검색어 cursor도
보존해 다시 추가하면 해당 위치를 재사용한다. 즉시 수집 요청/새 TR/SQL 테이블/범용 계층은 추가하지 않는다.

### 런타임 인증 공통 API (구현 계약)

R5d1 PC NAS 설정 메뉴는 네이버/DART/OpenAI/Gemini/Claude의 고정 global 키 관리 화면을 제공한다.
같은 HTTPS client/worker를 사용하되 client의 공급자는 생성 시 고정한다. 같은 공급자/NAS 대상의
재열기는 메모리 pending operation을 유지하고 다른 공급자/주소/토큰 변경은 별도 client를 사용한다.
입력 키는 password/빈 값으로 시작하고 전달 직전·종료 때 비우며 PC 설정/DB/미러/백업에 저장하지 않는다.
네이버는 client_id/client_secret, 나머지는 api_key만 전송한다. global 임의 프로필을 만들거나
계좌 설정을 조회하지 않는다. READY는 provider/profile/revision/operation ID/계좌 null을 확인하며
NAVER/DART는 VERIFIED, AI는 VERIFIED 또는 UNVERIFIED만 적용할 수 있다.
disable도 prepare 후 명시 apply하며 timeout은 같은 operation GET으로 확인한다. 자동 재적용하지 않는다.
ACTIVE receipt에 validation이 없어도 AI 키를 인증 성공으로 표시하지 않는다. 프로필 runtime_validation은
기존 R5c 상태를 그대로 읽고 실제 인증은 명시 분석으로 확인한다. 서버/API/DB 변경은 없다.

R5c는 `openai`/`gemini`/`claude`와 각각 고정 `nas-{provider}-default`를 지원한다.
keyless vault 설치에도 supported=true/revision=0/UNCONFIGURED로 등록하고 임의 global profile은
503 PROFILE_RUNTIME_NOT_READY다. replacement는 `{api_key}`, disable=true에서는 빈 객체이며 계좌 대상은 null이다.
prepare는 유료 분석/기사 HTTP를 실행하지 않고 READY의 validation=UNVERIFIED로 반환한다.
disable 후보의 validation=VERIFIED는 비활성화 준비 완료를 뜻하며 인증 성공을 뜻하지 않는다.
명시 apply 후 ACTIVE는 키 적용 완료이며 새 키 유효성의 검증 완료를 뜻하지 않는다.
profiles의 기존 validation은 vault 저장 당시 불변값이고 선택 추가 필드 runtime_validation은
활성 revision에서만 UNVERIFIED/VERIFIED/INVALID_CREDENTIAL/ACCESS_DENIED/RETRYABLE/REQUEST_FAILED/DISABLED를 반환한다.
AI 외 공급자·미활성/복구 필요 프로필에는 null이다. 재시작/키 교체 때 초기화되는 실행 상태로,
첫 실제 분석 성공은 VERIFIED, HTTP 401은 INVALID_CREDENTIAL, 403은 ACCESS_DENIED,
429/5xx/통신 실패는 RETRYABLE, 그 밖의 공급자 요청 실패는 REQUEST_FAILED다.
과거 성공 캐시 응답은 새 키를 검증하지 않으며 이전 revision의 늦은 실패도 새 상태에 반영하지 않는다.
AI 요청은 본문 준비 전에 공급자/model/key/revision을 고정한다. 교체는 해당 공급자의 접수·준비·
실행 대기·실제 분석·최종 저장을 drain하며 HTTP waiter 취소/서버 종료에도 실제 완료를 기다린다.
교체 중 해당 공급자 신규 분석은 재요청 안내 오류를 반환하고 다른 공급자 gate는 열어 둔다.
공유 실행 키에는 body hash/revision을 포함한다. 저장 성공 캐시의 품질 키는 바꾸지 않으며
키 교체만으로 재분석을 예약하거나 일일 사용량/상한을 초기화하지 않는다.
AI 응답의 선택 추가 credential_revision은 접수 context이고 새 results/사용량 JSON의 같은 필드는
실제 분석에 사용한 revision이다. 캐시 results는 과거 revision을 보존하며 구형 결과는 해당 필드가 없을 수 있다.
commit 이후 실패는 해당 공급자 키 부재/RECOVERY_REQUIRED로 두고 이전 ENV 키를 사용하지 않는다.
PC 공급자 입력 UI는 R5d1에서 연결했다. 실제 공급자 인증·분석 및 운영 검증은 별도이며 DB migration/AI 품질 규칙 변경은 없다.

R5b는 provider `dart`의 고정 `nas-dart-default`를 지원한다. keyless vault 설치에도 metadata
supported=true/revision=0/UNCONFIGURED로 공개한다. 임의 DART profile은 503 PROFILE_RUNTIME_NOT_READY다.
prepare는 replacement `{api_key}` 또는 disable=true+빈 객체이며 계좌 대상은 null이다.
기존 client의 회사코드 캐시를 건드리지 않는 당일 공시 검색 page_count=1로 검증한다.
000의 list 응답/013 조회 없음은 VERIFIED, 010/011/901은 FAILED+INVALID_CREDENTIAL,
020/800/900·timeout·HTTP 429/5xx는 FAILED+CREDENTIAL_VALIDATION_RETRYABLE다.
그 밖의 접근 제한/잘못된 응답은 FAILED+CREDENTIAL_VALIDATION_FAILED이며 원래 URL/메시지/키를 노출하지 않는다.
재시도 가능 실패를 검증 성공으로 처리하거나 자동 apply하지 않는다. 기존 operation 멱등성/TTL/HTTPS를 따른다.
apply는 종목 수집/최종 저장을 drain하고 cache Path/기사/cursor/작업/예산을 유지한다.
NAVER 공통 검색은 DART 교체 동안 계속하며 두 공급자 동시 교체도 각 pause를 독립적으로 유지한다.
키 적용/disable은 운영 dart_enabled를 켜거나 끄지 않는다. 운영 변경은 다음 접수 수집부터 적용한다.
commit 이후 실패는 DART None/RECOVERY_REQUIRED, disable ACTIVE는 tombstone 적용 완료다.
PC 공급자 입력 UI는 R5d1에서 연결했다. 실제 DART 키/IP·응답과 운영 검증은 별도이며 DB migration/분류 규칙 변경은 없다.

R5a는 provider `naver`의 고정 `nas-naver-default` profile을 지원한다. vault 설치가 정상이라면
키 없는 설치에도 metadata에서 `supported=true`, `revision=0`, `runtime=UNCONFIGURED`로 공개한다.
prepare의 replacement는 `{client_id, client_secret}`이며 `disable=true`에서는 빈 객체다.
검증은 기존 네이버 클라이언트의 검색 1건 요청으로 수행하고 모든 실제 HTTP 시도(legacy fallback 포함)를
기존 watchlist/shared 일일 예산에 포함한다. 성공은 VERIFIED, 인증/예산 실패는 FAILED로 반환하고 키를 노출하지 않는다.
계좌 대상은 READY/apply 모두 `target_account_ref=null`이다. NAVER 입력은 R5d1의 NAS 뉴스·AI 공급자 관리 화면에서 제공하며 계좌용 입력과 구분한다.
apply는 두 수집 경로의 실제 진행 작업/저장을 drain한 뒤 함께 교체하며 기사/cursor/작업/사용량을 유지한다.
pause 중 새 종목 조회는 저장 목록만 읽고 fresh 조회 완료 시각을 기록하지 않는다.
deadline 초과는 BUSY 후 실제 종료 뒤 FAILED/이전 연결 재개, commit 시작 이후 실패는 RECOVERY_REQUIRED/
NAVER 요청 차단으로 처리한다. disable ACTIVE는 키 부재 적용 완료이며 env 키를 재사용하지 않는다.
새 임의 NAVER profile은 `503 PROFILE_RUNTIME_NOT_READY`, 미연결 real provider는
`503 PROVIDER_RUNTIME_NOT_READY`다. DB migration/뉴스 분류 규칙 변경은 없다.

R4a PC 관리 화면은 아래 기존 API를 HTTPS로만 호출하며 redirect를 따르지 않는다.
capability와 provider/profile의 supported를 모두 확인한다. 빈 키 입력으로 시작하고 PC 키
설정/DB/미러에 NAS 키를 저장하지 않는다. prepare/status 응답의 operation/provider/profile/
expected revision/확인된 계좌를 검사하며 READY의 같은 revision/계좌만 apply한다.
서버 재시작 후 committed receipt의 revision은 expected revision+1로 확인한다.
apply 응답 유실은 동일 operation GET으로 확인하고 새 apply를 자동 전송하지 않는다.
operation ID를 못 받은 prepare는 메모리의 같은 request_id/프로필/revision을 유지한 재입력만 허용한다.
계좌 PUT 응답 유실은 설정 GET을 먼저 재확인한다. R6c1에서 같은 화면을 실전 프로필에도 확장했다.
NAS 설정에는 모의·실전 관리 버튼을 따로 제공하며 provider별 client/진행 요청을 공유하지 않는다.
실전은 `kiwoom_real` 프로필 생성/prepare/apply 및 `environment=real` 계좌 GET/PUT을 사용한다.
실전 PUT의 `mock_order_enabled`는 false만 허용하며 화면에서도 모의주문 토글을 숨긴다.
설정 revision 적용과 실제 `monitor_status.realtime.state=ready`를 분리해 표시한다.
상태 다시 확인은 계좌 설정 GET이며 새 인증/주문/분석을 실행하지 않는다. R6c2의 계획된
재연결 상태는 계좌 설정과 별도 프로토콜이며 아래 유한 대기 정책을 따른다.
키 관리 자체는 매매일지 기본 계좌·명령·시세 담당을 변경하지 않는다. R4b의 별도 계좌 선택을 따른다.

R6c2 `planned_reconnect_v1`은 기존 capabilities의 선택적 bool 필드다.
`/health`와 인증된 capabilities는 `realtime_connection`을 추가하며 collector가 없으면 null이다.
기존 WS ready/central_ready/상류 connection_opened/connection_failed는 선택적
`connection_status`를 추가한다. 상태 변경은 새 `connection_status` event의 payload로도 배포한다.
기존 클라이언트의 ready/subscription 접수 의미와 기존 이벤트/코드는 유지한다.

상태 필드는 `generation`, `phase`, `paused`, `shutdown`, `planned_reconnect`,
`remaining_seconds`, `started_at`, `outcome`, `observation_expected`, `previous_trade_at`,
`first_trade_at`, `gap_seconds`다. credential/계좌번호/account_ref는 포함하지 않는다.
planned reconnect는 담당 collector의 실제 drain 시작부터 최대 30초이며 같은 전환의
반복 drain/fence나 같은 generation 통지로 deadline을 갱신하지 않는다.
모든 REG 승인 후 READY에서 끝나며 재개 시작 자체의 실패/종료도 끝낸다. 대기 중 재시도할
상류 오류는 deadline 안에서만 유예한다. 정상 관측시간의 deadline 초과는
명시 connection_failed로 기존 장애/failover 정책에 복귀한다. 거래시간/구독 대기는
유예를 종료하고 waiting_market/waiting_subscription으로 알리며 거짓 장애를 만들지 않는다.
deadline은 표시/장애 판단 경계이고 실제 token/socket/저장 drain을 취소하거나 fence를 해제하지 않는다.

PC는 명시 active 상태와 유효한 int generation, 유한 0초 초과~30초 remaining만 받아들인다.
같은 generation의 통지는 로컬 deadline을 늘리지 않고 끝난/이전 generation으로 대기를 다시 시작하지 않는다.
서버 상태가 끊겨도 PC 자체 deadline에서 유예를 해제한다. 정상 상태 통지까지 기존 순위표는
비우지 않으며 계획된 상류 오류만 connection_failed 신호/즉시 로컬 전환을 유예한다.
실제 NAS transport 단절과 기존 일반 장애는 기존 정책을 따른다.

저장 자료 조회가 불가능한 TR에서 broker가 paused이고 유효한 계획된 전환이면
HTTP 503 detail `{code: REALTIME_RECONNECTING, connection_status: ...}`다.
원격 client는 이를 `CentralPlannedReconnect` 일반 API 대기 오류로 분리하며
`CentralServerUnavailable` transport 오류로 변환하지 않는다. 따라서 REST failover는 로컬 TR을 만들지 않는다.
NAS DB 우선 경로와 순위 우선순위는 유지한다. deadline 이후 오류는 기존 HTTP/장애 판단을 따른다.

gap_seconds는 교체 전 마지막 허용 0B 관측에서 새 REG 후 첫 허용 0B 관측까지의
monotonic 간격이다. UTC 관측시각과 함께 최신 상태·PC 상태 문구·PC/NAS 로그에 기록한다.
이전 관측이 없으면 null이며 0으로 추정하지 않는다. 종목별 누락량/거래소 지연이나
API와 NAS 수신 지연 차이를 뜻하지 않는다. 별도 DB 이력/outbox는 추가하지 않았다.

R4b의 인증 `GET /api/v3/kiwoom/accounts`는 `{accounts: [context]}`를 반환한다.
context는 기존 v3 응답과 같은 broker/environment/account_ref/credential_profile_id/
binding_revision/verified_at/verification_method이며 실제 mock admitted bundle과 main 조회 binding만 포함한다.
활성 자격 프로필에 사용자가 입력한 이름이 있으면 표시 전용 `display_label`을 추가한다. 이 값은 계좌
식별·조회 일치 판정에 사용하지 않으며 원문 계좌번호나 키움 자격정보를 포함하지 않는다.
capability `account_contexts_v3`는 이 목록 기능을 표시한다. 빈 목록은 현재 조회 가능한 연결 없음이고
저장된 옛 계좌를 삭제/병합하지 않는다. 이 API는 키움 TR·인증 갱신을 하지 않는다.
PC는 Qt worker로 목록을 읽고 선택 scope를 조회 worker 시작 시 같은 목록에서 다시 확인한다.
고정 scope/profile/binding 버전의 모든 페이지·날짜·비용을 검증하고 빈 결과도 완료 context로 저장한다.
조회 도중 UI 선택이 바뀌거나 context가 바뀌면 결과를 저장하지 않는다. 명시 선택 계좌는 PC 단일
프로필로 fallback하지 않으며 구 NAS의 v3 미지원도 기본 v2 계좌로 대체하지 않는다.
기존 context 없는 v2 기본 조회와 시세 fallback는 유지한다.
고정 mock client의 submit/GET/cancel은 기존 v2 scoped 경로만 쓴다. submit request_id는 호출자가
명시하고 자동 retry하지 않는다. 응답 미확인 client는 같은 ID/내용의 확인을 먼저 요구한다.
이 상태는 client 메모리 수명이며 외부 caller는 해결 전 client/ID를 유지해야 한다.
서버의 영속 intent/UNKNOWN 중복 방지 계약을 바꾸지 않는다. 자동주문 UI/실제 주문 검증은 범위 밖이다.

R3d는 인증된 `GET /api/v1/settings/accounts/{account_ref}?environment=mock|real&broker=kiwoom`
읽기를 추가한다. 응답은 `{settings: {scope, active_profile_id, monitor_enabled,
mock_order_enabled, revision}, applied_revision: int|null}`이다. 미등록/환경 불일치 계좌는
404 ACCOUNT_IDENTITY_UNVERIFIED, 잘못된 scope는 400, 저장 문서 손상은
409 ACCOUNT_SETTINGS_RECOVERY_REQUIRED다. R3e mock owner가 연결된 계좌는 실제 확인한 설정
revision을 반환하고 미연결 계좌는 null이다. 저장 토글만으로 실제 연결/주문 상태를 판단하지 않는다.
R3f는 실제 mock owner가 있는 서버에 같은 경로의 인증·HTTPS PUT을 추가한다.
요청은 `{expected_revision, active_profile_id, monitor_enabled, mock_order_enabled}` 전체 필드다.
profile 변경/선택은 허용하지 않으며 현재 active profile을 명시한다. scope query의 environment는 필수,
broker는 kiwoom 기본이다. 중복/알 수 없는 query·JSON 필드와 bool 아닌 토글을 거절한다.
응답은 GET과 같은 settings/applied_revision이다. 409는 낡은 revision 또는 준비 중 profile,
503 ACCOUNT_SETTINGS_APPLY_FAILED는 저장 전 실패 후 이전 연결 복원,
503 ACCOUNT_SETTINGS_RECOVERY_REQUIRED는 저장/실행 결과를 다시 확인해야 하는 상태다.
동일 설정도 실제 적용이 미확인되면 실행 연결을 다시 준비하며 설정 revision은 늘리지 않는다.
R3f 당시 실전 설정 변경은 503 PROFILE_RUNTIME_NOT_READY, mock owner 없는 구동은 PUT 405였다.
현재 R6b3c2a는 real/mock 중 하나의 owner만 있어도 PUT을 등록하며 요청 environment의 owner가 없으면
503 PROFILE_RUNTIME_NOT_READY다. 둘 다 없는 구동은 PUT 405를 유지한다.
이 collection은 일반 content API의 허용 목록에 포함하지 않는다.

`runtime_credentials_v1` capability는 공통 API가 존재한다는 뜻이다. 아래 GET의
`providers[].supported`와 해당 `profiles[].supported`가 참인 경우만 실행 중 변경할 수 있다.
R3e는 vault/HMAC registry가 있는 서버의 모의계좌 owner를 연결하며 R5a NAVER 지원은 위를 따른다. 미연결 공급자는
`503 PROVIDER_RUNTIME_NOT_READY`, 시세 담당 main mock profile은 `503 PROFILE_RUNTIME_NOT_READY`다.
R3f의 모의 `disable=true`는 저장된 검증 계좌/run을 대상으로 기존 prepare/apply를 사용한다.
새 인증 조회 없이 연결을 drain하고 빈 credentials의 명시 disabled tombstone을 저장한다.
DB activation receipt와 active_profile=None/토글 OFF는 같은 트랜잭션이며 기존 binding은 추가하지 않는다.
계좌 이력·미확정 주문은 유지한다. disabled ACTIVE는 비활성화 적용 완료이며 거래 연결 준비를 뜻하지 않는다.
재시작 시 tombstone은 env보다 우선하고 계좌 bootstrap/TR/WS를 시작하지 않는다.
monitor ON 모의계좌는 초기 read/lease가 확인된 후 ACTIVE를 표시한다. 신규 계좌 주문은 OFF다.
기본 `/api/v1/mock/orders`는 admitted nas-mock-default bundle만 사용하고 boot/교체 중에는 503이다.
provider는 kiwoom_real/kiwoom_mock/naver/dart/openai/gemini/claude 중 하나다.

| Method / path | 요청 | 응답 |
| --- | --- | --- |
| `GET /api/v1/settings/credentials` | 없음 | 공급자 지원 여부와 프로필 configured/source/revision/validation/runtime, 익명 account_ref. 키·토큰 없음 |
| `POST /api/v1/settings/credentials/{provider}/profiles` | UUID request_id, label(최대 120자) | 키 없는 draft profile UUID. 같은 request_id/내용은 같은 profile |
| `PUT /api/v1/settings/credentials/{provider}/profiles/{profile_id}` | expected_revision, label(1~120자) | 실전·모의계좌의 표시 이름만 변경. 계좌 신원·binding·인증키·설정·매매 이력은 유지 |
| `POST /api/v1/settings/credentials/{provider}/profiles/{profile_id}/prepare` | UUID request_id, expected_revision, replacement 또는 disable=true | 202 operation_id와 상태. 활성 키/binding 미변경 |
| `DELETE /api/v1/settings/credentials/{provider}/profiles/{profile_id}` | expected_revision | 연결 해제된 profile만 archived 처리. 계좌 신원·binding·activation·매매 이력은 보존하고 일반 목록에서 제외 |
| `GET /api/v1/settings/credential-operations/{operation_id}` | 없음 | 상태·safe error_code·revision·committed·계좌 미리보기. 비밀 없음 |
| `POST /api/v1/settings/credential-operations/{operation_id}/apply` | expected_revision, Kiwoom은 확인한 target_account_ref UUID | 202 적용 시작 또는 동일 작업 결과 |
| `DELETE /api/v1/settings/credential-operations/{operation_id}` | 없음 | 준비만 취소. 적용 시작 뒤 취소 불가 |

replacement는 공급자별 전체 필드다: Kiwoom app_key/secret_key, Naver client_id/client_secret,
DART api_key, AI api_key. 빈 값·토큰 필드·미지정 필드·중복 JSON 키는 허용하지 않는다.
request_id가 같고 내용이 다르면 409이며 적용 완료 재요청은 외부 적용을 다시 실행하지 않는다.
준비 TTL은 5분, 한 프로필당 한 작업·전체 진행 중 32개·보존 상태 최대 256개다.
취소·만료·HTTP 단절은 이미 시작한 실제 통신을 종료된 것으로 간주하지 않는다.
deadline 이후 BUSY는 실제 작업 종료를 기다리며 늦은 자동 적용을 하지 않는다.
준비만 했던 작업은 서버 재시작 후 410, 파일 commit 이후 작업은 복구 대상이다.

쓰기에는 HTTPS 또는 `CREDENTIAL_TRUSTED_PROXIES`에 명시한 IP가 전달한 단일
`X-Forwarded-Proto: https`가 필요하다. Uvicorn proxy_headers는 false이며 임의 헤더를 신뢰하지 않는다.
이 API에는 query를 허용하지 않는다. query는 access-log scope에서도 제거한다.
본문 상한은 16KiB이며 오류는 `detail: {code}`만 반환한다. 400 query, 409 충돌/준비 상태,
410 알 수 없는 작업, 413 본문 초과, 422 잘못된 입력, 426 HTTPS 필요, 503 저장소/미지원 상태다.
ACTIVE는 파일·DB commit과 실제 runtime revision 확인이 끝난 상태다. 파일 commit 이후 실패는
RECOVERY_REQUIRED이며 이전 키로 자동 rollback하지 않는다. DB 최종화는 binding과 같은 트랜잭션이다.

### 운영 설정 적용

운영 설정의 `expected_revision`이 현재 `revision`과 다르면 저장하지 않고 409를 반환한다.
미변경 요청은 revision을 증가시키지 않는다. 구 클라이언트의 revision 생략은 읽기/부분 쓰기
호환을 위해 허용하므로 구 클라이언트의 전체 덮어쓰기를 모두 방지하는 계약은 아니다.
저장 실패는 `OPERATIONAL_SETTINGS_SAVE_FAILED`이며 메모리와 실행 설정을 유지한다.
저장 후 적용 실패는 `OPERATIONAL_SETTINGS_APPLY_PENDING`; GET의
`apply_status=RECOVERY_REQUIRED`, `applied_revision<revision`으로 구분한다.
현재 revision을 읽고 빈 부분 PUT으로 재적용하거나 재시작 시 영속 설정을 다시 조립한다.
성공 시 `apply_status=ACTIVE`, `applied_revision=revision`이다.
| `GET /api/v1/diagnostics/resources` | 없음 | 프로세스/호스트 메모리, 디스크·DB 전체 크기, 뉴스/시장/연구/계좌/기타별 추정 용량·건수와 현재 보존 상태 | NAS 자원 진단 |
| `GET /api/v1/diagnostics/writers` | 없음 | 현재 계측 writer의 ID·source·table·durability·pause 지원 여부와 미계측 범위 | writer registry. 전수 인벤토리 아님 |
| `GET /api/v1/diagnostics/db-calls` | `start`, `end`, 선택 `mode`, `limit`, `slow_ms` | 공통 관측 pilot의 family/kind별 호출·성공/오류·단계시간, verbose 느린 호출, raw 제한 표본 | 인증·시간/출력 상한; 기존 writer 전체가 아님 |

운영 설정 `ai_provider`는 `none/openai/gemini/claude`, 뉴스 간격은 60~86,400초다. 이 API는 공급자 비밀키를 반환하거나 변경하지 않는다.

NAS 연결 설정과 뉴스 설정은 이 운영 설정 API를 함께 사용한다. 뉴스 설정에서 저장할 때는 먼저 GET으로 그 화면이 편집하지 않는 값을 보존한 뒤 AI 공급자·모델·일일 한도, DART 사용 여부와 NAS 처리 제외 언론사를 합쳐 PUT한다. `news_processing_excluded_providers`는 최대 100개의 언론사명 또는 원문 도메인이다. 일치하는 새 기사는 제목·링크와 source observation을 보존하지만 BODY/RULE/자동 AI 작업을 만들지 않는다. 이미 대기 중이거나 완료된 작업은 소급 삭제하지 않으며, 제외를 해제한 뒤 기사가 다시 관측되면 누락 BODY 작업을 멱등 예약한다.

Shadow 후보 설정은 `shadow_candidate_enabled`, 검증된 `shadow_candidate_config` 객체, `shadow_candidate_poll_seconds`, `shadow_candidate_universe_max_age_seconds`다. PUT 성공 시 NAS 프로세스를 재시작하지 않고 후보 감지 task만 교체하거나 중지한다. 잘못된 전략 조건은 422로 거부하고 현재 task와 저장 설정을 유지한다. `.env` 값은 DB 운영 설정이 아직 없을 때의 최초 기본값으로만 사용한다.

## Kiwoom 조회

### `POST /api/v2/kiwoom/account-query`

검증 계좌 전용 읽기 경계다. 현재 `kt00007`, `kt00015`와 `/api/dostk/acnt` 조합만 허용한다. 첫 요청은 `batch_id=""`, `page_index=0`, `next_key=""`로 시작한다. 응답은 `payload`, 서버가 발급한 `batch_id`, `page_index`, `complete`, `next_key`, 익명 `context`, `provenance`를 반환한다. `context`에는 `broker`, `environment`, 지속 UUID `account_ref`, `credential_profile_id`, `binding_revision`, 검증 시각·방법이 있으며 원문 계좌번호와 API 자격증명은 없다.

연속 페이지는 첫 요청과 같은 `api_id/path/body`, 서버가 반환한 `batch_id/next_key`, 정확히 증가한 `page_index`를 보내야 한다. 서버는 120초 세션 동안 이 값과 실제 인증 binding을 고정한다. 만료 cursor, 본문·순서·cursor 불일치, 조회 중 binding 변경은 `409`로 전체 batch를 무효화한다. 마지막 응답의 `complete=true` 전에는 어느 페이지도 완료된 계좌 수입으로 저장하지 않는다. 클라이언트의 20페이지 안전 상한에서 다음 페이지가 남으면 partial 오류다.

NAS 통신이 페이지 도중 끊기면 검증된 직접 API binding이 같은 `account_ref`일 때만 첫 페이지부터 다시 조회한다. 다른 계좌이면 전체 결과를 폐기한다. 구 NAS의 404/405/501은 capability 부족이며 v1 payload에 화면 선택 계좌를 붙여 v2처럼 저장하지 않는다. 계좌 API는 중앙·직접 병행검증 대상에서 제외한다. 일반 시세용 v1 조회와 페이지별 failover 계약은 그대로 유지된다.

2026-09-13 구현 상태 정정: 위 장애전환 계약에서 직접 adapter는 아직 저장 binding과 현재 자격을 fresh `ka00001`으로 대조하지 않는다. 따라서 같은 실제 계좌임을 검증하는 부분은 [A4b 계획](docs/archive/2026-09-22/reports/A4B_DIRECT_WEBSOCKET_SCOPE_REVIEW.md) 1~3단계의 미구현 보완이다.

### 구현 예정: `POST /api/v2/accounts/resolve`

인증된 HTTPS에서 `{environment, account_number}`를 받아 NAS의 기존 검증 registry와만 대조한다. 응답은 `{canonical_scope, matched_binding: {credential_profile_id, binding_revision, verified_at, verification_method}, matched_at}`이며 NAS가 저장한 검증 근거와 PC 로컬 자격의 검증 revision은 구별한다. 요청으로 NAS registry나 활성 profile/binding을 생성·교체하지 않는다. 미등록은 `ACCOUNT_NOT_REGISTERED`이며 local origin의 중앙 결합만 보류한다. PC 키 소유를 NAS가 독립 증명하는 API가 아니라 신뢰하는 단일 소유자 PC의 관측값을 대조하는 경계다.

예정 capability는 `account_identity_resolve_v2`다. HTTP·인증서 오류·redirect는 거절하고 raw를 repr/validation `input`/예외/로그/DB/캐시에 남기지 않는다. 임의 proxy 헤더로 TLS 판정을 우회할 수 없어야 한다. R7c2 기록에서 NAS HTTPS/WSS 연결과 앱의 HTTPS 주소 전환은 확인됐다. 이 전송 경계의 확인과 별개로 resolve·aliases 경로 및 이 capability는 **현재 미구현**이다.

먼저 만든 local origin의 결합은 별도 인증 HTTPS `POST /api/v2/accounts/aliases`로 구현할 계획이며 현재 미구현이다. 입력 `{environment, account_number, origin_scope}`를 서버에서 같은 계좌로 대조한 후 기존 alias 저장 계약을 적용하고 익명 AccountScopeAlias를 반환한다. client가 canonical ref를 선택하지 않는다. 같은 연결은 멱등이며 대상 교체·환경 교차·연쇄/순환은 거절한다. 두 신원 경계가 준비된 뒤 위 capability를 true로 제공한다. raw는 일반 콘텐츠에 포함하지 않는다. 직접 query context의 기존 scope는 origin으로 유지하고 선택 canonical_scope로 매핑을 표현해 alias 뒤에도 저장 key가 변하지 않게 한다.

### `POST /api/v1/kiwoom/query`

요청:

```json
{"api_id":"ka10001","path":"/api/dostk/stkinfo","body":{"stk_cd":"005930"},"cont_yn":"N","next_key":""}
```

응답:

```json
{"payload":{},"has_next":false,"next_key":"","cache_hit":false,"archive_hit":false}
```

허용 API와 경로는 `central_server/rest_broker.py`의 `READ_ONLY_ENDPOINTS`가 현재 구현 원본이다. 주요 조회는 `ka00198`, `ka10016`, `ka10001`, `ka10100`, `ka10054`, `ka10080`, `ka10081`, `ka10083`, `ka10094`, `ka10045`, `ka90008`, `ka20005`, `ka20006`, `kt00007`, `kt00015`다. `ka10054`는 VI 시작·재연결 누락 보완용 저우선순위 조회다. 주문 API는 허용하지 않는다. `cont_yn=Y`에는 `next_key`가 필수다.

2026-09-13 감사에서 같은 allowlist의 `ka00001`이 공개 v1 route에서도 원문 응답으로 전달될 수 있음을 확인했다. 위 신원 대조 구현 시 공개 전달만 명시 거절하고 내부 broker의 `ka00001`/limiter는 유지한다. 이는 해당 민감 TR의 호환 정책 변경이며 기존 시세 요청/응답 형태는 유지한다. 현재 코드에서 이미 차단된 것으로 해석하지 않는다.

클라이언트 의존: `RemoteKiwoomRestClient`는 `payload/has_next/next_key`를 직접 읽으며, 중앙 접속 실패와 정상 HTTP API 오류를 구분한다. `archive_hit`는 완료 coverage가 있는 중앙 분봉·일봉을 재사용했음을 나타내는 선택 필드다. 필드 제거·이름 변경은 스키마 버전 상승 없이 금지한다.

O1의 mock 계좌 대조는 일반 `/api/v1/kiwoom/query`와 분리된 내부 broker namespace를 사용한다. 확인된 조회 allowlist는 미체결 `ka10075`, 체결 `ka10076`, 계좌평가잔고 `kt00018`, 주문가능금액 `kt00001`이며 모두 `/api/dostk/acnt`다. 주문 `kt10000~kt10003`은 이 조회 broker, 장애전환 클라이언트, 병행검증 클라이언트에서 거부된다. 별도 mock transport만 `/api/dostk/ordr`로 한 번 전송하며 아래 인증 API는 `MOCK_ORDER_TRANSPORT_ENABLED=1`일 때만 사용 가능하다.

## 수동 모의주문

| Method / path | 요청 | 응답 핵심 |
| --- | --- | --- |
| `POST /api/v1/mock/orders` | `request_id`, 6자리 `symbol`, `side=BUY/SELL`, 양수 `quantity`, 양수 `limit_price`, 선택 `expires_seconds=10..600` | intent·broker ID, `policy_version`, 상태, 체결수량, 시각, `events[]` |
| `GET /api/v1/mock/orders/{intent_id}` | 없음 | 현재 원장 상태와 `events[]` |
| `POST /api/v1/mock/orders/{intent_id}/cancel` | 선택 `quantity`; 0은 남은 수량 전체 | 취소 요청 뒤 현재 원장 상태와 `events[]` |

검증된 다계좌 경로는 `/api/v2/mock/accounts/{account_ref}/orders...`를 사용한다. A5의 읽기 경계인
`GET /api/v2/mock/accounts/{account_ref}/execution-events`는 `credential_profile_id`,
`expected_binding_revision`, `environment=mock`, 선택 `after_sequence>=0`, `limit=1..1000`을 받는다.
응답은 검증된 익명 `context`, 수락 순서대로 정렬된 `events[]`, `next_cursor`, `has_more`다.
각 행은 `accepted_sequence`, 불변 `source_event_id`, intent의 `run_id/decision_id/symbol/venue/side`,
event의 상태·발생/수신시각·broker 주문/체결 ID·수량·가격을 함께 가진다. 커서는 계좌 전체 원장을
기준으로 하며 run이 바뀌어도 이어진다. 다른 계좌나 오래된 binding으로 읽을 수 없고 이 API는
Kiwoom TR 또는 주문을 만들지 않는다. capability는 `execution_event_read_v1`이다.

신규 주문은 인증된 수동 KRX 지정가만 받는다. `09:00~15:20`은 검증된 정규장 정책이며 `16:00~20:00`은 키움 모의투자의 새 KRX 애프터 지원 여부를 broker 응답으로 확인하는 `manual-mock-krx-after-limit-probe/v1`이다. probe 허용은 지원 확정이 아니며 접수·거절 결과를 원장과 응답의 `policy_version`으로 구분한다. NXT·시장가·15:20~16:00 신규 주문은 허용하지 않는다.

`request_id`는 같은 `MOCK_EXECUTION_RUN_ID` 안의 멱등키다. 같은 내용으로 다시 요청하면 기존 결과를 반환하고 broker에 재전송하지 않는다. 다른 종목·방향·수량·가격에 같은 ID를 재사용하면 400이다. 전송과 취소 전에 계좌 REST 복구를 먼저 수행한다. 응답 유실은 `SUBMISSION_UNKNOWN`, 취소 응답 유실은 `CANCEL_PENDING`으로 남기며 자동 재전송하지 않는다. 신규 수동 LIMIT 전송은 시행일별 공통 세션 정책의 KRX 정규장 연속매매 `09:00 <= KST < 15:20`와 위 `16:00~20:00` KRX 애프터 probe 경로를 구분한다. 장후종가·NXT·동시호가 등 허용되지 않은 신규 주문은 `PRECHECK_REJECTED` 상태와 `UNSUPPORTED|reason|venue|order_type|session|phase|schedule|profile` 근거를 `events[]`에 남긴다. 이 시간 gate는 조회·취소·broker 대조·재연결 복구·늦은 체결을 막지 않으며, 미체결 잔량은 broker 확인 없이 시각만으로 종료하거나 16시에 자동 재주문하지 않는다. 후보나 전략은 이 API를 자동 호출하지 않는다. O2-M 자동 운용의 READY 지원은 별도 `krx-regular/v1` 범위이며 수동 애프터 probe로 확대되지 않는다.

## 뉴스와 AI

| Method / path | 요청 핵심 | 응답 핵심 |
| --- | --- | --- |
| `POST /api/v1/news/search` | `stock_code`, `stock_name`, 선택 `since`, 자동분석 옵션 | `stock_code`, 최신순 `items[]`(최대 1000) |
| `POST /api/v1/news/analyze` | 종목, provider/model, `events[1..20]`, `article_count` | 중앙 AI 서비스 분석 문서. 공급자 429/5xx는 상태 코드를 보존한다. |
| `GET /api/v1/news/history/{kind}` | `kind=article/body/ai/event/membership`, 선택 `target`, `identity`, Unix 초 `as_of`, `limit` | `known`, `kind`, `revisions[]`. 서버 가용시각 역순의 불변 이력 |
| `GET /api/v1/news/historical-archive/search` | Bearer 인증, 6자리 `stock_code`, `limit=1..200`, 선택 서명된 `cursor` | 봉인된 PC archive의 `dataset_id`, `items[]`, `next_cursor`. 현재 뉴스 목록과 분리. 미설정·미봉인·진단 일시정지 시 503, 잘못된 cursor는 400 |
| `GET /api/v1/news/historical-archive/v2/search` | Bearer 인증, 6자리 `stock_code`, `limit=1..200`, 선택 서명된 `cursor` | v2 종목 목록. 본문은 목록에 포함하지 않으며 상세에서 정확 ID로 읽는다. v1 파일은 409 |
| `GET /api/v1/news/historical-archive/v2/market` | Bearer 인증, 선택 `source=flash/world`, `limit=1..200`, 선택 서명된 `cursor` | v2 시황 목록. source 생략은 전체 목록이며 동일 identity는 한 번 표시한다. 실패 항목은 article/body ID가 null일 수 있다 |
| `GET /api/v1/news/historical-archive/articles/{article_revision_id}` | Bearer 인증, 필수 `dataset_id`, 선택 정확한 `body_revision_id` | archive의 기사·본문·assessment 상태·정확한 사건 membership 이력. dataset 변경 409, ID 없음 404, 다른 기사의 BODY ID 400. 누락 판정 재계산 없음 |
| `POST /api/v1/news/historical-jobs/claim?stage=BODY\|RULE` | Bearer 인증. 과거 기사 대기 작업 한 건 원자 선점. `scope=pc_market`은 신규 PC 시황, `pc_search`는 신규 PC 네이버 검색만 선점한다. `scope=pc`와 기본 `all`은 기존 NAS 적재분을 포함한 네 과거 범위를 선점한다. NAS 일반 작업기는 이 네 범위의 BODY/RULE을 선점하지 않는다. `excluded_codes`는 쉼표로 구분한 6자리 코드(최대 500개) | `job`·`article`, RULE이면 기존 저장 `body`; 없으면 `job=null`. 완료된 작업은 다시 선점하지 않고 시도 번호로 결과 소유권 검증 |
| `POST /api/v1/news/historical-jobs/complete` | Bearer 인증. `job_key`, `attempts`, `stage`와 BODY 본문·원문시각 또는 RULE 평가·핵심문장·공급계약 결과; 실패 시 `error` | revision·작업 완료를 한 트랜잭션에 기록. 동일 시도 재전송은 `already_completed`; 만료·다른 시도는 409 |
| `POST /api/v1/news/historical-market-articles` | Bearer 인증. `source=flash\|world`, `target_date`, 결정적 `batch_id`, `processing_owner=nas\|pc`(기본 nas), 해당 날짜 기사 `items` 1~100건. 각 기사는 identity·document·targets를 포함 | 기존 `central_news_source_observations`와 BODY/RULE 원장에 저장. `pc` 범위의 BODY/RULE은 NAS 일반 작업기가 선점하지 않음. 같은 batch_id 재전송은 `already_imported`; 종목 연결 여부와 무관하게 시황 탭 소속 유지 |
| `GET /api/v1/news/sources` | 선택 `source_id`, `days=1..31`, `limit` | `scope=query_set`, `coverage=configured_query_set`, source별 cursor/checked/last success/최근 실행·관측과 raw/unique/distinct identity/duplicate/truncated/error/request/budget, 본문·규칙·target·job 요약 |

사건은 `identity`, `title`, `body`, `body_hash`, `articles[]`를 가진다. 본문 최대 길이는 2,000,000자다. AI 결과의 요약·판정·근거는 요청한 `stock_name`의 주가·실적·사업 영향 관점이어야 하며, 종목이 단순 나열됐거나 직접 근거가 없으면 `판단 자료 부족`으로 반환한다. `body_hash`는 본문뿐 아니라 대상 종목과 분석 계약 버전(`target-company-v2`)을 포함하므로 이전의 시장 전체 관점 결과는 새 자동분석의 완료 캐시로 재사용하지 않는다. 캐시 재사용 여부와 사용량 구조는 중앙 AI 서비스와 클라이언트 테스트가 보호한다.

규칙 관련성 판정은 새 기업 사건 없이 이미 발생한 종목·시장 가격 움직임만 설명하는 기사를 `시세 반영·시장 요약`으로 제외한다. 환율·유가·금리·실적 전망 자체의 급등락은 종목 주가 반응으로 취급하지 않으며, 같은 제목에 별도의 주가·지수 반응이 남아 있으면 시황 기사로 판정한다. `투자심리`는 인수합병, 일반 `증시 전망`은 기업 실적 전망으로 분류하지 않는다.

`news/search`는 기존 종목 owner 기사에 해당 종목 코드로 `confirmed` 연결된 N3 GLOBAL 기사를 합친다. unresolved·ambiguous·다른 종목은 제외하고, 같은 identity가 겹치면 기존 종목 owner 기사를 우선한다. GLOBAL의 같은 identity 판본은 중앙 수락 순서가 가장 최신인 confirmed 판본 하나만 사용한다. 합친 결과는 게시시각 내림차순, 같은 시각은 identity 오름차순이며 게시시각 결측·오류는 `since`가 없을 때 마지막에 둔다. `since`가 있으면 시각을 확인할 수 없거나 cutoff 이하인 행은 제외한다. 목록 병합은 NAVER 요청이나 BODY/RULE/AI 예약을 추가하지 않고, 자동 AI 후보는 기존 종목 owner 기사만 유지한다.

기사 이력의 `target`은 종목코드, `identity`는 기존 기사 identity다. 본문 이력의 `target`은 `article_revision_id`, AI 이력의 `target`은 분석 대상 종목코드다. `as_of`는 `available_at <= as_of`인 행만 반환하며 최초 원장 기록 전에는 `known=false`다. 이 API는 현재 화면용 `news_article/news_ai` projection을 변경하지 않는다.

단발성 과거뉴스 archive는 **v1/v2 로컬 reader·API·조회 화면 구현, 전체 운영 자료 게시 미완료** 상태다. `HISTORICAL_NEWS_ARCHIVE_PATH`가 비어 있으면 비활성이고, 절대경로의 `sealed` 파일과 완료된 projection이 있어야 켜진다. v2는 coverage 완료·봉인 검사 통과도 요구하며 용량 시험본은 거부한다. 기존 `capabilities.news_archive`는 PG 뉴스 기능으로 유지하고, 파일 schema에 따라 `historical_news_archive_v1` 또는 `historical_news_archive_v2`와 `historical_news_archive_dataset_id`를 공개한다. v1 목록을 v2 파일에 요청하면 409다. cursor는 dataset·목록 종류·종목/source 필터·정렬키에 묶인다. 상세의 기존 정확 ID 계약은 유지하며 v2는 저장된 대상별 판정과 입력 출처도 반환한다. 진단 pause는 진단 master 세션과 TTL 아래에서만 유효하고 목록·상세 모두 적용한다. PC 조회 창은 종목/시황 범위를 선택하며 NAS가 BODY/RULE/사건을 계산하거나 쓰기 작업을 예약하지 않는다. `as_of` 목록은 제공하지 않는다. 화면 구현과 작은 파일 검사는 전체 NAS 게시 증거가 아니다.

사건 이력의 `target`은 종목코드, `identity`는 영속 `event_id`다. 소속 이력은 `target=event_id`, 선택 `identity=article_revision_id`로 조회한다. `event` 행은 정확한 article/body revision과 규칙·점수 산식 버전, 역할·범위·확실성·신규성, 근거 span, 금액/상대방, 대상별 방향·직접성, `ai_required` 이유를 가진다. URL은 event ID가 아니며 `possible_related_event_ids`는 후보 관계일 뿐 과거 행을 병합하지 않는다.

`news/sources`는 NAVER 검색 결과의 설정된 query set 범위만 설명한다. 전수 시장 피드가 아니며 `truncated` 또는 `query_set_error` 구간은 누락 가능 구간이다. `days`와 선택 `source_id`는 summary의 기간·source 집계 및 본문/규칙/target 집계에 적용되고, `limit`은 `runs[]`와 `observations[]` 상세행 수만 제한한다. `unique_count`는 그 기간에 새로 추가된 immutable article revision 수이고, `distinct_identity_count`는 관측된 서로 다른 기사 identity 수이므로 실제 기사 수를 볼 때 둘을 구분한다. `job_queue_scope=all_news_jobs`는 전역 작업 대기열, `budget_scope=today_kst_all_news_sources`는 오늘 KST 전체 source 예산임을 뜻한다. 인증키·비밀은 응답과 저장 문서에 포함하지 않는다.

## 시장 데이터

| Method / path | query | 응답 |
| --- | --- | --- |
| `GET /api/v1/market/minute-bars` | `code`, `trading_date=YYYY-MM-DD`, 선택 `market=KRX/NXT/SOR/COMBINED` | 같은 식별자 + `bars[]`, `coverage.complete/markets`; COMBINED 행에 `source_market` 포함 |
| `GET /api/v1/market/recent-minute-bars` | `code`, `end_date=YYYY-MM-DD`, 선택 `market=KRX/NXT/SOR/COMBINED`, `trading_days` 1~5 | 마지막 실제 거래일 목록 + 정렬된 `bars[]` |
| `GET /api/v1/market/latest-market-caps` | 반복 `codes=6자리 종목코드`, 1~200개 | 종목별 마지막 저장 0B의 `market_cap_eok`, `observed_at`; 현재가·등락률은 반환하지 않음 |
| `GET /api/v1/market/trade-value-comparisons` | `code`, `trading_date=YYYY-MM-DD`, 선택 `limit` | SOR 실시간값·KRX/NXT 분봉 조회값·차이·차이율 `comparisons[]`; KRX+NXT 완전 비교만 집계한 `summary` |
| `GET /api/v1/market/events` | `kind=vi/cohort/upper_limit`, 선택 `code`, `limit` | 불변 `history[]`; cohort는 현재 projection `current[]`와 조건 선택 진단 `condition` 포함 |
| `GET /api/v1/market/daily-bars` | `code`, 선택 `market`, `limit` 1~5000 | 식별자 + `bars[]` |
| `GET /api/v1/market/coverage` | `kind`, `subject`, ISO `start/end`, 선택 `available_by`, 고정주기 자료만 `expected_seconds` | 상태·관측 수·가용 수·결측 구간·부재 의미 |
| `GET /api/v1/market/external-bars` | `instrument`, `timeframe=5m/1d`, `limit` 1~10000 | 공급원·계약코드가 포함된 `bars[]` |
| `GET /api/v1/market/snapshots/{kind}` | 선택 `subject`, `limit` 1~5000; 최신 TOP20 화면 조회만 `kind=top20_membership&limit=1&prefer_live=true` | `kind`, `subject`, `snapshots[]`; live TOP20에는 `persistence_state=pending|persisted` 포함 |
| `GET /api/v1/market/top20-statistics` | `start_date/end_date=YYYY-MM-DD`, 최대 367일, 선택 `basis=realtime/display` (기본 realtime) | NAS TOP20 `hourly[]`, 정규장 일별 `comparisons[]`; display는 품질·coverage·계산 기준 포함 |
| `GET /api/v1/market/top20-series` | `trading_date=YYYY-MM-DD`, 선택 `basis=display/realtime` (기본 display) | 08:00~19:59의 720분 `rows[]`, basis·coverage·generation·available_at; 미확인 값은 null |
| `GET /api/v1/research/observations` | offset 포함 ISO `start/end`, `kinds=ranking,top20_membership,minute_bar` 중 하나 이상, 선택 `subject`, `watermark`, `cursor`, `limit` 1~1000 | 고정 `manifest/watermark`, 순서가 붙은 `observations[]`, `next_cursor` |

스냅샷 `kind`는 `ranking`, `top20_membership`, `top20_index`, `market_state`, `investor_flow`, `program_flow`, `new_high`, `stock_fundamentals`, `nxt_eligibility`를 허용한다. `stock_fundamentals`와 `nxt_eligibility`는 시점 이력과 별도로 범용 콘텐츠의 `stock_fundamentals`, `stock_nxt_eligibility` 컬렉션에서 종목별 최신값도 조회할 수 있다. 봉의 중앙 저장 단위는 거래대금 백만원(`trade_value_million_won`)이다.

`prefer_live=true`는 최신 `top20_membership` 한 건에만 적용된다. 목표 회차와 일치하고 종목코드·종목명이 채워진 20행 전체를 검증한 뒤 PostgreSQL 저장과 동시에 공개하는 것이 아니라 저장 전에 메모리 projection으로 반환한다. 부분 응답과 이전 회차 응답은 live 결과가 될 수 없다. live projection이 없거나 서버가 재시작된 때는 같은 요청도 DB 저장본으로 되돌아간다. 연구·과거 조회는 이 옵션을 사용하지 않는다.

검증된 live projection을 게시할 때 동일한 메모리 종목 목록으로 KRX 0B 구독 요청도 즉시 갱신한다. `top20_membership`·당일 편입 원장·TOP20 지수의 DB 저장 완료를 기다리지 않는다. 구독 요청과 실제 수신 준비는 다르며, `subscription_ready`는 WebSocket `REG` 승인 뒤에만 공개한다.

NAS 연결 중 TOP20 차트는 `top20_index` 중앙 스냅샷을 읽고, 통계는 서버의 날짜별 저장 집계 `top20_statistics_day`를 합쳐 읽는다. 과거일에 집계가 없으면 처음 한 번 계산해 저장하고, 원본 TOP20·시장 일봉이 보완되면 해당 날짜 집계를 무효화한다. 당일은 원본의 새 분을 반영해 조회한다. PC 직접 연결은 앱 실행 중에만 TOP20 자료를 자동 수집하며 로컬 `monitor.sqlite3`의 날짜별 집계를 재사용한다. 0원 TOP20 행은 관측 표본과 일별 비교에서 제외한다. 정규장 TOP20 합계는 09:00부터 15:30 종가 단일가 체결분까지 포함한다. 과거 전체시장 분모는 `ka20006` 일봉의 코스피·코스닥 거래대금을 사용하므로 장중 `0J/0U` 최종 수신 전에 끝난 값으로 과거 통계를 고정하지 않는다.

새 NAS 표시 화면은 `top20-series?basis=display`와 `top20-statistics?basis=display`를 사용한다. 위의 기존 원장 통계와 0원 제외 규칙은 기본 `basis=realtime` 계약이다. display는 유효한 날짜별 `top20_index_reconciled`가 있으면 장후 계산본을 선택하고, 없으면 저장 실시간 원장의 품질을 판정한다. `ka10080` OHLCV 거래대금과 00/30초 구성 배분 모두 추정이므로 장후 확정값으로 표시하지 않는다. 미확인 분을 0원으로 채우지 않고 확인된 0원 분은 표본에 포함한다. 일별 합계는 정규장 391분이 모두 확인됐을 때 표시하고, 비율은 코스피·코스닥 분모도 모두 있을 때만 비교한다. 부분일은 확인 구간 합계와 coverage를 표시한다. 표시 통계는 별도 `top20_statistics_display_day` 캐시를 사용하며 원본·분모·계산본 쓰기와 같은 transaction에서 해당 날짜를 무효화한다. 연구 revision과 실시간 원장은 변경하지 않는다. 구 서버의 404/405/501만 저장 실시간 시계열로 호환하고 인증·서버 오류는 화면에 실패로 남긴다. 구 서버가 통계의 basis를 무시하면 업데이트 안내를 표시한다.

거래대금 비교의 `summary.complete_count`와 차이 통계는 `query_scope=KRX+NXT`인 분만 대상으로 한다. `partial_count`와 `scope_counts`는 KRX 또는 NXT 한쪽만 보완된 중간 자료를 따로 보여준다. `total_difference_percent`는 완전 비교 합계의 `(SOR-조회)/조회`, `average_difference_percent`는 분별 차이율 평균이며 `mean_absolute_difference_percent`와 `max_absolute_difference_percent`는 방향을 제거한 오차 크기다.

앱 표시용 `minute-bars`와 `recent-minute-bars`의 `bars[]`는 저장된 이력에 중앙 수집기의 미저장 RAM 분봉을 합친다. 현재 분·저장 중·실패 재시도 delta를 복사한 뒤 DB 봉과 operation marker, QUERY 완료 상태를 동일한 읽기 snapshot에서 조회하므로 저장 완료 응답 유실이나 동시 COMMIT 때 두 번 합산하지 않는다. 완료된 ka10080 봉을 RAM 값으로 덮어쓰지 않는다. 이 GET은 DB write·revision·Kiwoom TR·coverage 완료를 만들지 않으며 저장된 이력만 사용하는 일반 QueryStore/연구 조회는 기존 동작을 유지한다. NAS가 수신하지 못한 시간은 RAM 보완으로 복원하지 않는다.

NAS 클라이언트의 당일 분봉은 `GET /api/v1/market/minute-bars`, 오늘과 직전 거래일 분봉은 `GET /api/v1/market/recent-minute-bars`의 `COMBINED` 보기를 한 번 읽는다. 같은 분에 SOR가 있으면 SOR 한 벌을 사용하고, 없을 때만 KRX+NXT를 합친다. `minute-bars.coverage.complete`는 해당 거래일의 장후 분봉 수집이 끝났다는 중앙 완료 문서가 있을 때만 참이다. 매매일지는 행이 하나 이상 있다는 이유만으로 장 종료 확정하지 않으며 이 값을 받은 뒤에만 `after_close_confirmed`로 저장한다. 구 NAS가 COMBINED를 지원하지 않으면 앱은 기존 KRX·NXT 중앙 조회로 호환한다. 중앙 연결이 정상인데 저장 행이 없으면 빈 결과를 반환하며 앱이 `/api/v1/kiwoom/query`의 `ka10080`을 대신 발생시키지 않는다. 중앙 연결 자체가 끊기고 사용자가 로컬 장애전환을 켠 경우에만 PC 직접 조회가 허용된다. 범용 조회의 첫 페이지는 완료 coverage가 있는 `ka10080/ka10081` 아카이브와 `ka10001/ka10100` 최신 중앙 문서를 먼저 검사한다. 적합한 저장 자료가 있으면 broker queue와 Kiwoom TR을 사용하지 않고 `archive_hit=true`로 반환한다.

coverage 상태는 `complete/partial/missing`이다. `available_by`를 지정하면 그 시각까지 실제로 가용했던 관측과 그 전에 저장된 완료 근거만 센다. 응답의 `window_closed`는 요청한 시간 구간이 cutoff 전에 끝났는지, `session_finalized`는 별도 장후 완료 근거가 있는지를 나타내며 서로 대신하지 않는다. 기존 `explicit_complete`는 `session_finalized`와 같은 완료 근거의 호환 필드다. 분봉·일봉은 거래가 없으면 행 자체가 없을 수 있으므로 `expected_seconds`를 허용하지 않고, 별도 장후 완료 문서가 있을 때만 0개 행을 완전 수집으로 판정한다. 순위 후보군·TOP20 지수·시장 상태처럼 고정 주기를 가질 수 있는 자료에만 `expected_seconds`를 주어 `missing_intervals`를 계산한다. `absence_meaning`은 실제 무체결 가능성과 미수집을 구분하지 못하는 경우 이를 명시한다.

외부 시장 봉은 현재 임시 Yahoo 지연 시세 수집 결과다. `NASDAQ_FUTURES`와 `WTI_FUTURES`는 실제 월물 계약을 함께 저장하며 각 행의 `provider`, `contract`, `bar_time`을 제거하지 않는다. 서버는 전월물과 차월물을 동시에 수집하고 차월물 거래량 우위를 연속 확인하면 대표 월물을 앞으로만 교체한다. 월물 간 절대가격 차이는 등락률로 계산하지 않으며, 방향값은 선택된 월물 자체의 전일 종가 기준이다. 이 자료는 실시간 주문 판단용 시세가 아니다.

연구 관측의 첫 요청은 최대 24시간 범위에서 현재 커밋된 revision ID 집합을 새 immutable manifest로 확정한다. 다음 페이지는 첫 응답의 `watermark`와 `next_cursor`를 그대로 보내며 시작·종료·종류·대상도 동일해야 한다. 추출 도중 새 관측이 들어와도 이미 확정한 ID 집합에는 추가되지 않는다. manifest의 `revision_count`와 `revision_ids_hash`로 전체 페이지의 누락·중복을 검사한다. 현재 quality의 `recording_gap=unknown`은 D1 기록 자체만 고정했고 세션별 수집 공백 판정은 아직 하지 않았다는 뜻이다.

`minute_bar` 연구 payload는 KRX/NXT/SOR 원본을 합치지 않으며 `bar_start/end`, OHLCV, 백만원 거래대금, 단위, `window_closed`, `session_finalized`, `capture_quality`, `finalization_source`와 신규 revision의 `session`, `phase`, `schedule_version`, `session_support`를 포함한다. 실시간 flush revision은 형성 중 누적 전체 봉이고 `window_closed=false`다. 타이머 마감은 실제 `bar_end` 뒤의 처리 시각을 `available_at`으로 사용한다. `session_finalized`는 장후 조회처럼 거래 세션 전체가 끝난 뒤의 확인인지 따로 표시한다. strict 연구 입력은 KRX·실제값·시간상 마감·수집 완전 revision 중 명시한 버전 있는 session profile이 허용한 봉만 사용한다. SOR 원본은 통합 시세 연구가 명시적으로 요청할 때만 사용한다. 기존 payload의 필드 누락은 기존 `krx-regular/v1` reader 경계에서만 호환한다.

## 자동 모의운용 후보 게시

`POST /api/v1/research/mock-automation-candidates`는 PC의 완료된 final holdout 결과를 NAS에
게시하는 전용 Bearer 인증 경계다. 요청은 현재 검증된 mock 계좌의 `account_ref`,
`credential_profile_id`, `expected_binding_revision`과 다음 세 불변 문서를 받는다.

- `mock_automation_candidate_package/v1`: 기존 `final_candidate/v1` 문서와 그 hash, 등록 family,
  정규 parameters, execution/session model, scientific implementation hash, final batch/run/result,
  OOS 평가 근거를 담는다.
- `mock_automation_eligibility_policy/v1`: final 실행 시작 전에 동결한 최소 거래일·거래수·순손익과
  최대 drawdown 기준을 담는다. 미정 수치는 합격으로 취급하지 않는다.
- `mock_automation_eligibility_receipt/v1`: 위 정책과 결과를 결합해 `ELIGIBLE` 또는 `BLOCKED`와
  관측 수치·사유를 고정한다.

서버는 256 KiB 크기, version, 등록 family와 정규 필드, 모든 content hash와 계보, 현재 scientific
implementation hash, 현재 mock binding을 다시 검사한다. 같은 ID·같은 내용은 `unchanged`, 같은 ID에
다른 내용은 409다. 응답의 `orders_started`는 항상 false이며 게시만으로 runtime lease나 주문 transport를
호출하지 않는다. capability는 `mock_automation_candidate_publish_v1`이다. 이 세 문서는 아래 범용 콘텐츠
allowlist에 포함되지 않는다.

`GET /api/v1/research/mock-automation-candidates/{account_ref}`는 READY 명세 작성에 사용할 게시 후보를
읽는다. query의 `credential_profile_id`가 현재 검증된 mock binding에서 같은 `account_ref`를 가리켜야
한다. 응답의 `binding`은 profile ID, broker/environment/account_ref, binding revision, `verified_at`,
`verification_method`를 포함하고,
`candidates[]`의 각 행은 package, eligibility policy, account-scoped receipt의 완전한 묶음이다. 누락되거나
계보가 맞지 않는 저장 묶음은 409이며 다른 계좌 후보를 섞지 않는다. capability는
`mock_automation_candidate_read_v1`이다.

`POST /api/v1/research/mock-automation-specs`는 게시된 ELIGIBLE 후보를 READY 운용 명세로
동결하는 별도 Bearer 인증 경계다. 요청은 같은 mock 계좌의 `account_ref`,
`credential_profile_id`, `expected_binding_revision`, 중앙 shadow 후보의 `shadow_event_id`와 다음
문서를 받는다.

- `ForwardEvaluationSpec`: 등록 family/factor, 후보의 session, NAS data path, 평가기간과 미정값 없는
  forward 기준을 고정한다.
- `StrategyStageRevision` 3개: `DRAFT→EVALUATED→VALIDATED→SHADOW` 순서와 후보 package hash,
  eligibility receipt ID, 실제 shadow event ID를 각각 증거로 연결한다.
- `MockAutomationOperatingSpec`: candidate/final 계보, 현재 mock binding, forward profile ID,
  동시 전략·포지션·자금·일손실·자료 공백·장애 한도와 고정 운영 정책을 담는다.

서버는 요청 전체 256 KiB 상한, 후보·policy·receipt·final 계보, 등록 family/factor/session, 실제 저장된
shadow event의 monitor/config/전략/version/전이/시각, 현재 mock binding과 READY 판정을 모두 저장 전에
검사한다. 기존 stage 이력은 요청 chain의 정확한 prefix일 때만 이어 쓴다. 동일 내용 재게시는
`unchanged`이고 충돌하는 profile/stage/spec은 409다. 응답의 `orders_started`는 항상 false다.
`GET /api/v1/research/mock-automation-specs/{account_ref}`는 저장 명세와 게시 계보 기준 readiness를
조회하며 runtime 시작 상태는 아래 계좌 상태 API에서 따로 읽는다. capability는
`mock_automation_spec_publish_v1`이다. 현재 중앙 shadow 생성기는 breakout family만 실행하므로 다른
등록 family는 실제 대응 shadow event가 추가되기 전까지 이 경계를 통과할 수 없다.

## 자동 모의운용 제어

capability `mock_automation_runtime_v1`이 참인 서버는 저장된 READY 운용 명세에 한해 다음 Bearer 인증
경계를 제공한다. 시작 요청은 `account_ref`, `credential_profile_id`, `spec_id`, 현재
`expected_settings_revision`, `credential_revision`을 받는다. 서버는 기존 mock credential owner에서
flat 계좌를 수동 bundle에서 불변 자동 run bundle로 교체하고 admission·최신 broker 대사를 완료한 뒤
runner를 시작한다. 중간 실패는 자동 bundle의 신규 주문을 닫은 상태로 유지한다.

| Method / path | 의미 |
| --- | --- |
| `GET /api/v1/mock-automation/accounts/{account_ref}` | `credential_profile_id`의 control·runtime·runner·복원 오류 상태 조회 |
| `POST /api/v1/mock-automation/start` | 저장된 RUNNING/신규 control의 READY 명세를 계좌별 runner로 시작 |
| `POST /api/v1/mock-automation/stop` | `expected_control_revision` CAS로 신규 주문 판단 중지 |
| `POST /api/v1/mock-automation/resume` | 중지 뒤 새 broker 대사를 기록하고 같은 revision을 CAS로 재개 |

상태 조회의 `runner.latency`는 선택적 읽기 전용 진단 값이다. 최근 poll의 전체·관측 DB 조회·관측당 최대 처리 시간(ms), 처리한 관측/분봉 수, 분봉 `available_at`부터 처리 완료까지의 마지막·최대 경과 시간(ms), 시각 누락·미래 분봉 수를 포함한다. 주문 gate나 checkpoint 근거로 사용하지 않으며 NAS 프로세스가 재시작되면 초기화된다. 분봉이 없는 poll에서도 마지막 분봉 시각과 경과 시간은 유지되므로 화면에 처리 시각을 함께 표시한다.

중지·재개 요청은 account/profile/spec, 현재 `expected_control_revision`, 비어 있지 않은 `reason`을
포함한다. 재개는 재시작 뒤 수동 bundle에서도 안전하게 자동 bundle을 다시 만들 수 있도록 현재
`expected_settings_revision`과 `credential_revision`도 요구한다. STOP은 기존 주문을 임의 취소하거나
보유를 청산하지 않는다. NAS 재시작은 저장된 RUNNING
control만 복원하며 계좌 자격·설정·명세가 맞지 않으면 재시도 간격을 두고 fail-closed 상태를 노출한다.
후보 게시만으로 시작할 수 없으며 위 READY 명세 게시까지 성공해야 시작 요청의 `spec_id`가 생긴다.

## 범용 콘텐츠

계약상 검증 계좌의 일지 문서는 `journal_v2_fills`, `journal_v2_reviews`, `journal_v2_setups`, `journal_v2_cycle_overrides`, `journal_v2_group_overrides`, `journal_v2_entry_snapshots`, `journal_v2_costs`, `journal_v2_enrichment_tasks`, `journal_v2_analysis_revisions`, `journal_v2_research_links`에 저장하고 뉴스 연결은 `journal_v2_news_links`, 삭제 상태는 `journal_v2_sync_states`로 분리한다. 각 v2 문서는 origin/canonical broker·environment·UUID를 명시해야 하며 legacy 자료는 v1 전용이다. 현재 앱은 `journal_v2_sync=true`를 확인한다.

서버는 v1 `journal_sync_states`를 허용하고 신규 뉴스 연결은 독립 `journal_news_links_v2=true` capability로 협상한다. sync state도 `(collection, origin owner, document_key)`로 계좌별 분리하며 envelope owner/key, target namespace와 origin/canonical scope를 함께 검증한다. v2 문서는 kiwoom/real 또는 mock/UUID와 origin owner 및 scope 기반 hash key를 검증하며 alias 증명이 없는 canonical 변경은 거절한다. v1은 legacy owner/key만 허용한다. optional 404와 401/500/timeout 구별은 유지한다.

| Method / path | 의미 |
| --- | --- |
| `GET /api/v1/content/{collection}` | `owner`, `limit`, `offset`, `updated_after`로 증분 조회 |
| `POST /api/v1/content/{collection}` | `documents[]`를 `(collection, owner, key)` 기준 upsert |
| `PUT /api/v1/content/{collection}` | 테마 컬렉션 전체 스냅샷 교체 |

문서 항목은 `owner`, `key`, `document`로 구성된다. GET 결과에는 `updated_at`도 포함된다. `news_article`은 선택 `collector_id`, `collection_scope`를 보내 수집기별 최초 수신과 로컬 projection 동기화를 구분할 수 있다. 허용 컬렉션은 `central_server/app.py`의 `content_collections`가 계약 원본이다. 전체 교체는 `theme_profile`, `theme_stock`, `theme_metadata`만 가능하다.

`execution_feedback_evidence`는 서버 내부 `ForwardEvaluationRepository`가 사용하는 비공개 컬렉션이며 위 공개 콘텐츠 API allowlist 대상이 아니다. owner는 `strategy_ref`, key는 전체 문서 내용 hash인 `evidence_id`다. 계좌·기간·선택/PIT/최종노출 상태·체결 품질·실제 비용이 같은 불변 문서만 멱등 저장한다.

`execution_feedback_reviews`도 같은 비공개 저장 경계다. owner는 `strategy_ref`, key는 `review_id`다. 저장 전에 같은 owner의 원본 `execution_feedback_evidence`가 존재해야 하며, 서버 repository가 원본과 명시 표본 정책으로 다시 계산한 revision과 정확히 일치해야 한다. 이 문서는 사용자 복기나 전략을 수정하는 API가 아니며 아직 개선안을 실행 대기열에 넣지 않는다.

`execution_feedback_improvement_proposals`는 적격 review에서 파생한 비공개 검토 대기 제안이다. owner는 `strategy_ref`, key는 `proposal_id`다. 등록 Family와 factor, 정규화한 기준 전략, 명시 허용값 안에서 정확히 한 정수 파라미터만 달라야 한다. 저장 전에 원본 review의 계좌·전략·evidence와 평가 방향을 다시 대조한다. seed는 반환 순서만 바꾸며 제안 ID에는 영향을 주지 않는다. 저장만으로 전략 채택·재검증·주문이 실행되지 않는다.

`execution_feedback_strategy_versions`는 owner=`parent_strategy_ref`, key=`version_id`인 비공개 불변 문서다. 저장된 proposal과 family/factor/parameters/계좌/근거가 일치해야 하며 같은 proposal을 다른 채택 사유로 두 번 버전화하지 않는다. 내부 전략 구현 버전은 그대로 두고 이 envelope ID가 새 전략 버전 참조가 된다.

`execution_feedback_revalidation_requests`는 owner=`parent_strategy_ref`, key=`request_id`인 queue 전 불변 의도다. 전략 버전·proposal·campaign·template과 파생할 exact `experiment_id`/`job_id`를 고정하며 한 전략 버전은 다른 campaign에 다시 요청할 수 없다.

`execution_feedback_revalidation_receipts`는 owner=`campaign_id`, key=`receipt_id`다. 저장된 request와 실제 queue의 exact `experiment_id`와 `job_id`가 일치했음을 기록한다. 중앙 문서와 연구 SQLite 사이에 분산 트랜잭션을 만들지 않으며 모든 ID를 내용 기반으로 고정해 request 또는 job 저장 뒤 중단해도 재시도는 같은 연구 job을 재사용한다. final holdout template과 주문 활성화는 이 경로에서 거절한다.

`execution_mock_automation_specs`는 owner=`mock account_ref`, key=`spec_id`인 비공개 O2-M 운용 명세다. 최종 후보 package/result hash, 저장된 forward profile, 현재 최신 `ka00001` mock binding과 동시 전략/포지션·자금·손실·데이터/장애·후보 교체·중지·복구 한도를 내용 hash로 고정한다. 미정 필드는 `BLOCKED`이며 현재 실행 범위는 단일 전략·단일 포지션·KRX 정규장이다. 문서 저장은 runtime claim, 주문 transport 활성화 또는 주문 전송을 수행하지 않는다.

`execution_mock_automation_admissions`는 owner=`mock account_ref`, key=`admission_id`다. 명세의 final batch/run/candidate/result가 CR3 연구 원장의 단일 완료 행과 일치하고 최신 SHADOW revision 및 mock binding이 유지될 때만 기존 O1 lease claim 전에 저장한다. spec당 admission은 하나다. `execution_mock_automation_lease_receipts`는 같은 owner 아래 결정적 자동 `execution_run_id`가 account lease를 얻은 결과를 기록하며 `new_orders_enabled=false`만 허용한다. 다른 수동/자동 run이 lease를 보유하면 receipt는 생기지 않는다.

`execution_mock_automation_recovery_decisions`는 owner=`mock account_ref`, key=`decision_id`인 append-only 복구 판정이다. admission/lease/spec 계보, broker account/orders fingerprint, 관측·account 시각, 미체결/포지션/예약자금 수, 주문가능금액, 검증된 당일 손익과 데이터 공백·unknown·재접속·잔고 불일치 값을 기록한다. 종결 주문 이력은 미체결로 세지 않는다. 보유가 있으면서 신규 진입에만 필요한 근거가 미완결이면 `MANAGE_ONLY_ORDERS_DISABLED`로 기록할 수 있고, 대사·scope·잔고가 불명확하면 계속 `BLOCKED`다. 통과 상태도 주문 활성화를 뜻하지 않는다.

`execution_mock_automation_decision_gates`는 owner=`mock account_ref`, key=`gate_id`인 매 Decision 판정이다. gate v2는 최신 recovery 뒤에도 현재 binding과 lease, 영속 control revision, 서버 시각·평가기간, KRX 정규 연속장, Decision/account/order freshness, 계좌별 FIFO와 broker 비용이 완결된 당일 순손익, 데이터 경로·공백, 자금·손실·장애 한도와 O1의 비종결 intent를 다시 검사한다. ENTER와 EXIT를 구분하며 EXIT는 같은 run·symbol의 확인 보유에서 미체결 매도 수량을 뺀 범위만 허용한다. v1 기록은 읽기 호환으로 보존하지만 control revision이 없어 새 제출 승인 근거로 사용하지 않는다. 통과 상태 `APPROVED_FOR_SINGLE_SUBMISSION`은 해당 `strategy_decision_id`에서 결정한 한 LIMIT intent에만 유효하다.

`execution_mock_automation_dispatch_receipts`는 승인 gate와 결정적 O1 intent/order state 연결을 기록한다. 같은 spec/Decision은 같은 intent ID이며 기존 O1 intent가 있으면 broker에 다시 제출하거나 새 receipt를 만들지 않는다. `execution_mock_automation_control`은 계좌별 RUNNING/STOPPED와 단조 증가 revision을 저장하고 O1 intent 저장 트랜잭션이 이를 다시 확인한다. `execution_mock_automation_stop_revisions`는 중지 감사 이력이며, 재개는 명시 요청과 중지 이후 새 broker 대사를 모두 요구한다. 기존 주문 취소나 포지션 청산은 별도 O1 대조/명시 정책으로만 처리한다. 이 컬렉션들은 공개 주문 API가 아니다.

`execution_mock_automation_risk_snapshots`는 공개 API가 아닌 계좌별 불변 근거다. 자동 account bundle의 기존
broker 복구와 O1 상세 `FILL`, `kt00015` 실제 비용을 FIFO로 대조하며 KST 거래일, broker 조회 구간,
binding/account/reconciliation/event/cost revision을 함께 저장한다. `execution_mock_automation_current_risk`는
최신 revision만 가리킨다. 비용 누락, aggregate-only 체결, 잔고 불일치는 당일 손익 unknown이며 운영
recovery와 Decision gate는 current snapshot ID/revision이 일치할 때만 진행한다.

`theme_metadata`의 `owner=default,key=full` 항목에는 선택 필드 `effective_at`, `origin_device`를 함께 보낼 수 있다. `document`는 `ThemeBackupService.export_document()`의 전체 프로필 문서여야 한다. 서버는 클라이언트가 보낸 시각을 가용시각으로 신뢰하지 않고 실제 요청 수신·DB 수락 시각을 별도로 기록한다.

`app_settings`는 PC 전용 값을 제외한 공통 설정, `app_column_settings`는 메인 표의 표시 여부와 순서를 저장한다. `journal_news_link`는 owner=`group_id`, key=`stock_code|identity`인 legacy 연결이다. `journal_v2_news_links`는 owner=불변 origin UUID, key=`journal-news-link:v2:`+정규 scope/group/stock/identity JSON의 SHA-256이다. 삭제는 문서의 `is_deleted/updated_at` revision으로 전파하며 envelope 저장 시각만으로 tombstone을 취소하지 않는다. `linked_at`은 최초 연결시각을 유지한다.

`stock_fundamentals`와 `stock_nxt_eligibility`는 키움 조회가 NAS를 통과할 때 갱신되는 종목별 최신 문서다. `/api/v1/kiwoom/query`의 `ka10001` 보관 응답은 `observed_at`이 KST 당일인 문서만 재사용하며, 이전 거래일 문서는 새 Kiwoom 조회로 갱신한다. 과거 재현에는 같은 응답을 관측 시각별로 남긴 시장 스냅샷을 사용하고, 최신 문서를 과거 시점의 값으로 간주하지 않는다.

## 테마 이력

| Method / path | query | 응답 |
| --- | --- | --- |
| `GET /api/v1/themes/history` | 선택 `as_of` Unix 초, `limit` 1~1000 | `known`, `as_of`, 최신순 `snapshots[]` |

각 snapshot은 `snapshot_id`, 활성 프로필 이름인 `profile_id`, `content_hash`, `effective_at`, 서버의 `received_at`·`available_at`, `origin_device`, 직전 이력의 `revision_of`, 전체 `document`를 가진다. `as_of`를 주면 그 서버 가용시각까지 수락된 행만 반환한다. 첫 기록보다 이른 시각은 `known=false`, 빈 배열이며 현재 테마로 대신 채우지 않는다. 늦게 도착한 다른 PC 문서는 과거 편집시각을 보존하되 서버에 실제 도착한 이후부터만 조회된다.

## 실시간 WebSocket

### `WS /api/v1/realtime`

서버 연결 직후:

```json
{"type":"ready","schema_version":1}
```

계좌 이벤트 `order_execution`(00)과 `account_balance`(04)는 서버 시작 시 같은 자격으로 확인한 `ka00001` HMAC 지문과 FID 9201이 일치할 때만 발행한다. payload에는 `origin_scope`의 익명 UUID만 포함하며 9201 원문은 포함하지 않는다. 필드 누락·불일치 이벤트는 화면 계좌로 보정하지 않고 폐기한다.

클라이언트 명령:

```json
{"type":"subscribe","codes":["005930"],"nxt_codes":["005930"]}
{"type":"ping"}
```

서버 제어 응답은 `pong`, `subscribed`, `central_ready`, `connection_opened`다. `connection_opened.scope`는 해당 앱 요청의 준비 완료인 `client`와 NAS가 키움에 승인받은 전체 합집합인 `upstream`을 구분한다. 이전 서버처럼 `scope`가 없으면 클라이언트 요청 확인으로 호환한다. 데이터 이벤트는 중앙 수집기가 전달하는 `trade`, `order_execution`, `market_state`, `program_trade`, `stock_reference` 등이다. `stock_reference`는 `code`, `upper_limit_price`, `lower_limit_price`, `base_price`, `market`을 한 기준 묶음으로 전달한다. 인증 실패 close code는 `4401`이다.

중앙 키움 등록은 한 WebSocket 안에서 `0B`, `0w`, `0g`를 독립된 타입 그룹으로 등록하며, 전송 `data` 행은 최대 100개 item씩 나눈다. 모든 요청 종목의 0B를 먼저 보장하고 남는 KRX/NXT 상세와 0w 대상은 TOP20, 실제 보유·매수 편입, 나머지 앱 요청 순으로 선택한다. `0g`는 같은 중복 제거 종목의 가격제한·기준가를 전달한다. 화면의 구독 종목 수는 거래소별 item 수가 아니라 중복 제거된 종목코드 수다.

2026-10-12 로컬 구현의 일반 구독 변경은 유지 item/type/group을 재등록하지 않는다. 이번 변경의 REMOVE·REG 송신과 성공 응답이 모두 확인되면 `subscription_ready`에 선택적 `scope`와 `codes`를 전달한다. 최초 상류 승인·실제 재연결은 `connection_opened`를 유지하고, 같은 클라이언트 연결의 반복 구독 준비 완료는 `subscription_ready.scope=client`로 알린다. `central_ready`만으로 상류 승인이라고 판단하지 않는다. `codes_added`에는 선택적 `source_resets=[{"code":"005930","market":"NXT"}]`가 추가되며, venue만 재활성화되면 `codes=[]`일 수 있다. 유지 source의 기준점은 보존하고 활성화된 `(code, market)`만 초기화한다. 구형 서버의 기존 알림도 수신하지만 새 의미의 연속성 검증은 새 PC/NAS 조합에서 수행한다. 현재 로컬 검증과 운영 미반영은 [구독 계약](docs/INCREMENTAL_REALTIME_SUBSCRIPTION_DESIGN.md)을 따른다.

## 변경 규칙

R3c 로컬 소스의 `/health.mock_account_available`은 실제 모의 bundle 시작 실패 시 false다.
`mock_account_error`는 계좌 불일치의 `ACCOUNT_CONTEXT_MISMATCH` 또는 그 밖의
`MOCK_ACCOUNT_STARTUP_FAILED`만 공개한다. 공급자 원문 오류·키·토큰을 공개 health에 전달하지 않는다.
기존 `/api/v1/mock/orders`의 기본 계좌 계약은 유지하고, 한 응답의 주문과 events는 동일 gateway로 읽는다.

2026-10-11 로컬 소스에서 owner 경로의 시작 상태 판단은 `MockCredentialOwner.has_started_monitor()`와
`MockAccountMonitor.is_started`를 사용한다. 기존 task 설치/종료 정리와 profile 공개 경계의 의미를
보존하며 복구 완료·heartbeat/lease 정상·주문 허용을 뜻하지 않는다. 계좌 공개 목록은 기존
`account_bindings()`를 사용하며 monitor OFF에서도 승인된 query context의 binding은 유지한다.

1. 기존 필드와 경로는 삭제·의미 변경하지 않는다.
2. 선택 필드 추가를 우선한다.
3. 호환 불가능 변경은 새 `/api/v2`와 새 `schema_version`으로 병행 제공한다.
4. 변경 시 `test_public_api_route_contract_is_stable`과 대응 클라이언트 테스트를 함께 수정한다.

## D4 shadow 후보 조회

`GET /api/v1/research/candidates?after_sequence=0&limit=100`은 인증된 읽기 전용 API다. 응답은 `high_watermark`, 불변 `events[]`, `next_cursor`, `has_more`, 생성기의 `quality`를 반환한다. event에는 서버 `accepted_sequence`, `event_id`, 종목, 감지·만료 시각, 판단 기준가와 입력 근거가 있다. `status=ACTIVE/EXPIRED`는 조회 시각의 표시값이며 원장을 수정하지 않는다. 생성 기능이 꺼져 있어도 HTTP 200과 `quality.status=DISABLED`를 반환한다. 앱의 Shadow 창에서 감지 여부와 조건을 저장하면 같은 서버 실행 중 즉시 이 상태가 바뀐다. 이 경로는 주문이나 체결을 만들지 않는다.

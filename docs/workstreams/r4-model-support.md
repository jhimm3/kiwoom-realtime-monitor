# A03 모델 지원: v2 경계와 v3 조건부 Paper 비교

현재 단계: 이 채팅의 A03 개발·로컬 검증을 완료했다. 기존 owner의 현재 입력 연결(v3)과 B 첫 v4 원본/공통 정책/전체 재생/현재 정책 바인딩/실행 중 격리 Shadow의 조건부 값을 검증했다. 관련 회귀313건 뒤 마지막 정책 ID·시작 체결 시각 보강 범위21건을 동일 최종 소스로 재검증했다. 실제 native R4 선택·주문은 OFF이며 운영 반영/실제 계좌 수락과 A04/A05는 후속 통합 범위다. 최신 결과는 마지막 「A03 B 구현」을 따른다. 앞 절은 당시 기록이다.

## ① 왜 필요한가

과거 `COHORT_COMPARABLE`, 현재 계좌/Risk 연결, 현재 native 모델 적용은 서로 다른 판단이다. 기존 current-goal binding은 원본 재생과 계좌 연결을 확인한 뒤 항상 `EV_UNAVAILABLE`를 반환하지만, 지원되는 연구 범위와 현재 적용을 막는 경계를 별도 구조로 설명하지 않았다.

공통 기준 `50a70c9ce6cdf20915fde27f2d1f47becb82eadf`에서 시작했고 워크트리 HEAD와 깨끗한 시작 상태를 확인했다. 원본 main을 변경하거나 다른 병렬 작업 파일을 수정하지 않았다. 현재 재구성 R4의 A03 첫 경계이며 00 ③9.2의 미래 정책별 행동 가치나 00 ③R4 전체 완료를 뜻하지 않는다.

## ② 실제 매매에서 어떤 판단이 가능해지는가

기존 RAM 결과의 각 study goal에 `model_support` (`position-study-model-support/v1`)를 추가했다.

| 소비 범위 | 이번 판정 | 의미 |
|---|---|---|
| 과거 비교 | 원본이 `COHORT_COMPARABLE`이면 `SUPPORTED` | 선언된 분봉 Paper 짝 비교의 재생/통계 결과만 읽을 수 있음. 전략 승격이나 현재 EV 승인이 아님 |
| 선언 범위 | 원본 policy/action/horizon/수량/가격/비용/해상도/continuation와 case proof 참조 | 없는 초 단위 입력, 수량·가격 보간, 다른 미래 정책으로 적용 범위를 늘리지 않음 |
| 현재 상태 | `UNAVAILABLE` / `OUTSIDE_OBSERVED_CASES` / `EXACT_OBSERVED_CASE` | 기존 native 검증을 통과한 전체 StrategyState와 원본 case를 문자 그대로 대조. 절대 opened/cooldown 시각과 emitted keys도 보존 |
| 현재 목표 | `BLOCKED`, `usable=false` | 계좌/Risk, 목표 정책, 수량·가격·비용·미체결, 원래 lease의 일치 여부와 차단 이유를 함께 표시 |
| native 실행 | `UNVERIFIED` | 원본에 native 보호/미체결 후속 정책의 검증 근거가 없으므로 실행 동등성·재진입 허용을 승인하지 않음 |

`EXACT_OBSERVED_CASE`는 상태의 동일성만 뜻한다. 같은 Family·현재 정상 Risk·같은 상태가 있어도 현재 맥락, 모집단/상위 입력 독립성, 미래 정책 동등성의 증거를 대신하지 못한다. `equivalence_verified=false`, 현재 `EV_UNAVAILABLE/result=None/order_authorized=false`는 유지한다. A01의 행동별 required capabilities를 변경하거나 테마/뉴스를 새 공통 필수 자료로 추가하지 않았다.

소비자는 `historical_comparison.status=SUPPORTED` 또는 상위 allocation의 기존 `CALCULATED`를 현재 study EV/실행 허용으로 읽으면 안 된다. 상위 금액/하위 수량은 기존 주문 없는 계획 결과이며 이번 변경으로 실행에 연결하지 않았다. 이번 current-goal 경계의 결과는 항상 null이고 native gate의 승인 입력이 아니다.

## ③ 기존 기능과 연결

기존 `decode_position_action_study_publication`이 원본 case/source/비교를 재생한 결과와 기존 계좌/Risk/Runner 관측을 `position_study_current_goal_binding` 한 곳에서 소비한다. 상위 allocation → 기존 하위 goal → 현재 binding → 기존 Runtime 조회 경로를 유지한다. 새 함수·엔진·Manager·Collector·DB·writer·task·주문 연결은 없다.

핵심 소비 파일 수/호출 깊이는 변경 전후 동일하다. 추가 상태 대조는 기존 최대 100개 사전 선언 case 안에서 수행하고, 조회 결과에는 전체 과거 상태 대신 기존 evidence/proof 참조와 상태 이름만 남긴다. 원본 publication/comparison/goal ID·비교 수치·원래 available_at/expiry는 갱신하지 않는다. 기존 Runtime의 만료 시 goal 제거, 중복 게시의 무재생, 한 번의 계좌 RAM borrow, native 보호 우선순위를 그대로 사용한다.

## ④ 연구/Shadow/가짜 브로커/실제 모의계좌/배포 승인 상태

- 연구: 기존 fixture가 실제 PaperExecutionEngine/ResearchRepository로 생성하고 readonly reader로 재생한 v2 source/case를 검증한다. 실제 시장 모집단의 승격 근거는 아님.
- Shadow: 기존 공통 Runtime의 2/8 소비자, 계좌 상태, 게시/만료/중복 경계를 검증한다. 현재 모델 사용과 주문은 계속 OFF.
- 가짜 브로커/owner: 기존 native 테스트 원장을 사용한다. 동일 상태 시험도 격리 fixture이며 운영 owner의 정책 동등성 증거가 아니다.
- 실제 모의계좌 00/04/REST·잔고·청산/운용 승인: 미실행·미승인.
- NAS·운영 DB·원본 data·.env·테스트 앱 실행: 접근/연결/쓰기 없음. 커밋·push·병합·배포 없음.

## ⑤ 아직 안 되는 것과 다음 필수 작업

A03의 양의 현재 상태/Shadow 모델 지원 경로는 아직 구현하지 않았다. 원본 `exact-observed-context/v1`을 다른 날짜의 상태와 같다고 간주하거나, 현재 관측을 과거 native 정책 근거로 복사하지 않는다. 다음 A03 단계에서는 지원 선언의 생산자가 현재 필수 자료·상태·규모·모집단/독립성·미래 정책을 실제 검증할 범위를 정해야 한다. native 적용이 필요하면 기존 공통 Risk/목적별 미체결/쿨다운 계약을 재사용한 명시 연구 프로파일과 새 원본이 필요하며, 기존 v2를 재표지하지 않는다. 이 첫 경계의 진단 출력만으로 A03 완료 처리하지 않는다.

A04 순자산/주문가능금액과 A05 보호 권한은 기존 후속 순서로 남긴다. 다른 작업의 완료를 기다리며 proof 범위를 늘리지 않았다. 공통 설계·CURRENT_STATUS·OPEN_ITEMS·MODULE_MAP·로드맵은 직접 갱신하지 않았고 이 문서가 통합용 delta다. 새 시험 모듈은 없어 catalog/CI 신규 등록 제안도 없다.

통합 문서 제안: 기존 RAM 조회 계약의 `r4_action_comparison.goals[].model_support`를 추가 진단 필드로 설명하고, CURRENT_STATUS/OPEN_ITEMS에는 “A03 지원 범위/차단 설명 첫 경계 완료, 양의 현재 적용은 미완료”로 반영한다. 전체 설계의 새로운 상태 동등성/native 연구 프로파일 결정은 이 변경으로 확정하지 않는다.

## 수정 파일과 근거

- `src/kiwoom_monitor/central_server/market_theme_shadow_inputs.py`: 기존 binding 안의 지원 범위/원본 참조/현재 상태 대조·차단 설명.
- `tests/unit/test_position_study_publication.py`: 원본 비교/ID/TTL/자료 무변경, 현재 Risk 연결과 모델 승인 분리, 행동·lease 변경 차단 시험 2개 추가.
- `tests/unit/test_position_runner_state.py`: 2/8 Runtime의 상태 범위 분리 확인 및 동일 기록 상태도 native 미래 정책을 승인하지 않는 시험 1개 추가.
- `docs/workstreams/r4-model-support.md`: 이번 통합 delta.

읽은 정본: AGENTS, DEVELOPMENT_GUARDRAILS, MODULE_MAP, CURRENT_STATUS, OPEN_ITEMS, FUTURE_DEVELOPMENT_ROADMAP, AI_TRADING_INTEGRATION_ALIGNMENT_AUDIT_20261014, AI_TRADING_SYSTEM_REDESIGN의 A03 재검토, 종료 상태 문서, 00 ③9.2. 코드 근거는 PositionActionStudyPolicy/spec, native v2 case/cohort reader·publication decoder, Paper의 분봉 체결/보호 경로, 기존 Runner state/protection/unfilled 계약이다.

정본 문서가 참조하는 `data/diagnostics/20261015-capability-context-support/source-contract-audit.json`과 과거 controlled-reentry publication은 이 Git checkout에 없다. 이 세션에서 해당 과거 산출물을 재검증했다고 보고하지 않는다. 운영/원본 data를 열지 않고 격리 fixture의 실제 생성 원본과 현재 코드를 검증 범위로 삼았다.

## 검증과 실패 구분

최종 변경 소스에서 관련 **24건 / 149.736초 / 종료 코드 0** 통과. `test_position_study_publication` 10건, `test_position_runner_state` 12건, 기존 A01의 행동별 news 요구와 보호 우선순위 2건이다. 원본 reader의 4borrow/SELECT16/COMMIT0·SQLite bytes 보존, v2 통계/원본 변조 거절, 현재 정책/규모/lease 불일치, 2/8 Runtime의 단일 계좌 borrow·hot SQLite 0·중복 무재생·만료 제거, native owner 변경/대사/보호 우선순위를 포함한다. 기존 HTTP 검사는 `httpx.AsyncClient`의 `ASGITransport`로 격리 app의 인증·revision·중복/해제를 확인했다. httpx2 설치/버전 변경/테스트 로딩 생략은 없다.

3개 변경 Python 파일의 AST/compile과 `git diff --check` 통과. 아래는 해당 파일의 UTF-8 LF SHA256이며 전체 source gate의 대체 근거가 아니다. 최종 검사 시작 뒤 실행 코드/시험은 변경하지 않았다.

| 파일 | LF SHA256 |
|---|---|
| market_theme_shadow_inputs.py | `b9d704efe7aa3f90c03304f96d4e7855a6692c51c76de3a429351192a59a2c0f` |
| test_position_study_publication.py | `e1319110b581d758fe68c3e0a93f6e77153eb2869200e814baf6c5c0cca262ea` |
| test_position_runner_state.py | `8312c0fd037e20597c85b6420d590b8ee0152dcd74336e6e4cdc3e22aaf3a27b` |

전체 all-local, 동일 LF 전체 source gate, 외부 CI/PG, 실제 계좌·장중 수락은 미실행이다. 최종 통합 후 필요한 회귀/Windows all-local 한 번과 외부/운용 승인은 통합 담당 범위로 남긴다.

초기 샌드박스 묶음은 fixture setUp에서 `sqlite3.OperationalError: unable to open database file`와 sandbox AppData Temp의 WinError5가 발생했고 중단했다. 새 단독 시험의 통과를 해당 실패의 해결로 세지 않았다. 사용자 문맥 `desktop-ts9efil/pc-1`, checkout 밖 `C:/Users/pc-1/AppData/Local/Temp/kiwoom-regression/r4-model-support-e2a0-*`에서 현재 worktree import와 독립 SQLite 생성/정리를 확인한 뒤 관련 검사를 실행했다. 샌드박스 실패 자료는 성공으로 재분류하지 않는다.

동일 상태의 첫 새 시험은 과거 entry 1,020원과 현재 fixture 체결/Risk entry 1,000원이 달라 native validator가 상태를 반환하지 않아 실패했다. 기존 validator를 완화하지 않고 fixture의 체결과 비용으로 Risk를 다시 생성해 같은 근거로 맞춘 뒤 해당 시험 1건을 통과시켰다. 실제 매매 근거를 변경한 것은 아니다.

모델: 현재 chat 최신 turn_context에서 `gpt-6.1-sol/high` 확인. 기존 원본 계약의 범위를 설명하고 문자 그대로 상태를 비교하는 첫 단계는 Sol High로 수행했다. 새로운 상태 동등성·통계적 적용 범위·native 미래 정책 승인은 결정하지 않았고 모델 전환도 하지 않았다.

## A03 후속 설계 결정 — 2026-10-15

이 절은 위 첫 구현 이후의 결정이다. 현재 chat의 최신 실행 기록에서 `gpt-6-astra/high`를 확인했다. 앞 절의 24건은 첫 구현의 실행 근거이며 아래 새 계약을 구현하거나 검증한 결과가 아니다. 공통 기준 HEAD는 계속 `50a70c9ce6cdf20915fde27f2d1f47becb82eadf`다.

### 2026-10-16 후속 사용자 기준과 현재 구현의 한계

연구의 목적은 일봉·5분봉·1분봉 등 필요한 자료로 유효한 규칙을 발견·검증하고 자동매매에 적용할 방법을 찾는 것이다. 실시간 현재가·체결 변화·실행은 기존0B를 사용하되 필요한 기존 봉 계산은 유지한다. 아래 exact 셀·분봉 종가/현재 기준가격·관측 시각 일치·정수초 제한은 기존의 좁은 모의 비교 계약이며 일반적인 연구 결과의 실시간 활용 요건으로 승격하지 않는다. 다른 해상도나 현재 가격 변화만으로 규칙 적용을 일괄 차단하지 않는다. 이 문서에 좁은 비교 구현이 있다는 이유로 일반화된 규칙 발견·자동매매 적용이 완성됐다고 하지 않는다.

초봉 전환의 로컬 변경에서 Runtime의 확정 분봉 요청 제거와0B snapshot의 support 조기 차단이 발견됐고, 조사 후 사용자 승인으로 그 연결을 복구했다. [복구와 관련334건 재검증](second-trade-dependencies.md#2026-10-16-승인된-분봉-연결-복구와-관련-재검증)을 따른다. 아래 기존 exact 비교 조건과 검증 기록은 보존하며 연구 수치를 실제 계좌/주문 권한으로 자동 승격하거나 새로운 일반화 규칙을 임의로 구현하지 않는다. 현재0B와 분봉의 공존은 허용하지만 좁은 모의 비교의 양의 결과가 일반 실시간 적용 규칙의 완성을 뜻하지 않는다.

### 1. 선택: 조건부 Paper Shadow를 먼저, native 미래 정책은 별도 지원

전체 설계의 대안 A를 먼저 구현하고 B를 후속으로 연결한다. 과거 v2 비교를 현재 native EV로 승격하지 않는다. 새 모델은 사전에 선언한 상태 표현과 표본 규칙으로 **현재 입력에 대응하는 Paper 가정의 행동별 조건부 추정**을 낼 수 있다. 이 값의 존재가 native 보호·미체결·재진입 정책과의 동등성이나 실제 계좌 운용 승인을 뜻하지 않는다.

| 경계 | 양의 판정에 필요한 근거 | 허용 소비 |
|---|---|---|
| historical comparison | 기존 원본 재생과 짝 비교 유효성 | 과거 결과 조회 |
| conditional paper shadow | 새 지원 명세, 원본에서 재계산한 상태/자료 셀, 해당 셀의 전체 사전 선언 짝 표본, 정확한 규모/비용/기간, 현재 가용 입력과 lease | 해당 Paper 프로파일을 명시한 조건부 연구 비교 표시 |
| native model applicability | 위 조건과 더불어 아래 5절의 미래 정책/실행 지원 증거 | 후속 native 판단 연결의 후보; 이번 단계에서는 연결하지 않음 |
| order authority | 기존 owner의 현재 Risk·대사·보호·주문 수명·별도 운용 승인 | 기존 주문 경계에서만 결정 |

기존 `current_goal_binding.status=EV_UNAVAILABLE`, `result=None`, `order_authorized=false`는 이번 A 구현에서 유지한다. 양의 Paper 결과는 새 `conditional_shadow` 하위 결과로만 반환하고 상위 allocation이나 native selection의 EV DTO에 자동 변환하지 않는다. 기존 `historical_comparison`/`current_state`/`current_goal`/`native_execution`의 뜻을 바꾸지 않는다. 새 외피는 `position-study-model-support/v2`로 구분한다.

### 2. 기존 v2 보존과 새 원본 경계

`position-action-study/v2`, `exact-observed-context/v1`, `single-target-then-registered-family/v1`은 그대로 읽는다. 전체 StrategyState의 문자 그대로 일치는 기존 진단으로 유지한다. 한 case의 상태가 같다는 이유로 서로 다른 상태가 섞인 기존 cohort 전체 평균을 현재 조건부 값으로 사용하지 않는다.

새 생성 원본은 `position-action-study/v3`로 명시적으로 구분한다. v2 DTO의 허용 문자열만 늘리거나 읽을 때 새 필드를 기본값으로 채워 v3로 승격하지 않는다. 새 원본은 기존 study spec/case proof/cohort 경로에 다음 내용을 함께 보관한다.

- `support_spec`: 고정된 상태 표현 버전, 행동별 필수 자료, 정확한 규모/비용/기간, 표본 선택·최소 표본·불확실성 계산 규칙, 미래 정책/실행 프로파일. 정규화한 내용의 hash를 study identity에 포함한다.
- `support_observation`: 원래 의사결정 cut에서 실제로 가용했던 입력과 원본 참조, source/venue/resolution/available_at, 상태 표현의 재계산 가능한 원재료. 결과 수익이나 미래 관측을 입력으로 넣지 않는다.
- `support_population`: 개발/평가 구분, 선언된 모든 case와 pair, 거래일과 창, 셀 구성, 학습/평가 cutoff와 자료 계보. 현재 상황을 본 뒤 유리한 과거 case만 고르지 않는다.

hash 일치만으로 실제 사전 등록이나 통계적 독립성이 증명되었다고 표시하지 않는다. 기존 freeze/reader가 확인하는 내부 일관성과 실제 사전 등록·평가 근거를 분리한다. 생산자가 없는 필드나 caller의 `supported=true`로 양의 판정을 만들 수 없다.

### 3. 첫 상태 표현과 양의 판정 규칙

첫 구현 범위는 **보유 중 HOLD 대 REDUCE 한 쌍, 기존 분봉 Paper 프로파일**로 제한한다. ADD/REENTER/EXIT 지원은 이 구현에서 자동 상속하지 않는다. 특히 REENTER의 native 쿨다운/보호 해제는 새 상태 표현의 책임이 아니다.

새 고정 표현 `held-minute-relative-state/v1`은 전략 상태의 비시간 필드를 보존하고, 시간 필드는 원래 cut에 대한 정수 시간 차이로 나타낸다. `opened_at`은 보유 경과 시간, `cooldown_until`/`candidate_expires_at`은 cut까지의 부호 있는 차이로 표현하며 null과 0을 구분한다. candidate key와 emitted key는 그대로 보존하고 임의로 지우거나 날짜 부분을 치환하지 않는다. 원래 절대 시각과 원본 상태 hash도 proof에 남긴다. 세션 프로파일과 세션 내 cut 위치를 별도 셀 키에 포함한다. 이는 **새 통계 모델의 입력 표현**이며 상태 머신의 native 실행 동등성 선언이 아니다.

셀 키에는 위 상태 표현뿐 아니라 symbol, venue, Family/policy/parameters, source continuation, horizon, initial/target quantity, reference price, cost model, 행동별 필수 관측의 실제 값과 관측 방식이 포함된다. 참조 ID와 available_at은 계보/가용성 검사에 쓰고 관측의 경제적 값 대신 비교하지 않는다. 첫 버전은 값의 허용 오차, 가격/수량 배율 보간, 종목 간 전이, 근접 셀 선택을 지원하지 않는다. 보유 상태·자료·세션에 대한 이 표현이 놓치는 변수가 있을 수 있다는 모델 가정은 결과에 명시하며 native 동등성으로 올리지 않는다.

양의 결과는 다음을 모두 만족할 때만 만든다.

1. reader가 v3 원본에서 표현을 재계산하고 선언/hash와 비교한다. 원본·현재 모두 가용 시각, 해상도, 완결성, source/venue, 비용·규모·기간을 검사한다.
2. 관측 결과를 보지 않고 선언된 셀 규칙으로 cohort를 구성한다. 현재 입력은 정확히 한 셀에 대응해야 한다. 다른 셀의 평균이나 가장 유리한 셀을 가져오지 않는다.
3. 해당 셀에 선언된 모든 pair를 읽는다. 누락·unsupported·censored pair를 조용히 버리고 분모를 줄이지 않는다. 셀에 선언된 최소 pair/거래일 요건을 만족하지 못하면 결과는 null이다.
4. pair별 HOLD 대비 REDUCE의 비용 반영 순변화를 재계산한다. 평균은 명시된 동일 가중 pair 평균을 사용한다. 불확실성은 거래일을 cluster 단위로 한 평균의 표준오차로 산출하고 식/버전을 고정한다. 거래일이 2개 미만이면 표준오차를 0으로 채우지 않고 미지원으로 처리한다. 2개라는 계산 가능 하한은 운영에 충분한 표본 수를 뜻하지 않는다. 최소 지원 표본/거래일 수는 원본 명세에 명시해야 하며 validator가 임의 기본값을 넣지 않는다.
5. 현재 계좌/Risk/owned position의 연결과 현재 정책/목표 규모·비용·pending 상태 및 lease를 기존 경계에서 확인한다. 첫 프로파일은 pending 없음만 지원한다. 하드 보호는 이 계산을 기다리지 않는다.

통계식은 pair 수 N, 거래일 수 G, pair 차이 d_i와 전체 평균 m에 대해 `SE = sqrt(G/(G-1) * sum_g(sum_{i in g}(d_i-m))^2 / N^2)`로 고정한다. 원화·bps 변환과 보수적 반올림은 기존 금액 계약을 따른다. 일내 상관을 분리한 계산일 뿐 날짜 간 독립성이나 확률 보정을 입증하지 않는다. source overlap 검사와 `independence_verified=false`, 미검증 calibration은 별도로 보고한다. 승률·확률·정규분포 신뢰구간을 새로 합성하지 않는다.

`conditional_shadow` 결과는 상태(`UNAVAILABLE`/`OUT_OF_SUPPORT`/`SUPPORTED_UNDER_PAPER_ASSUMPTIONS`), 차단 이유, 모델/셀/원본/pair 참조, 사용 표본·거래일 수, 평균/표준오차, 원래 value basis, scope, 현재 입력 참조와 expiry를 가진다. 순가치의 수수료 포함 여부는 원본 `value_basis` 그대로 표시한다. `usable_for_native_selection=false`, `order_authorized=false`를 유지한다. 결과를 현재 매매의 검증된 기대수익으로 표시하지 않는다.

### 4. 자료·계보·상위 비중 연결

필수 자료는 A01의 해당 행동 요구와 새 모델 표현이 실제 소비하는 자료, 미래 정책의 시작 상태 요구를 합친다. 계좌/미해결 주문/비용/자금/Risk의 공통 경계는 별도 유지한다. 사용하지 않는 테마·뉴스를 새 공통 필수 조건으로 추가하지 않는다. 선언만 있고 실제 생산자가 없는 자료는 unavailable이다.

첫 프로파일의 역사 입력은 완결된 1분봉이다. 현재 0B 표본 하나를 완결 봉이나 전체 tick으로 재표지하지 않는다. 현재 owner가 같은 관측 의미의 입력을 제공하지 못하면 이 프로파일만 미지원이며, 보호 경로나 다른 초 단위 전략을 기다리게 하지 않는다. 지연·gap·재접속·continuity·available_at은 주기 이름과 독립적으로 검사한다. 현재 native RAM 입력만으로 이 첫 프로파일이 양의 판정을 받는다고 약속하지 않는다.

상위 allocation에서 선택한 목표와 같은 outcome을 사용한 하위 비교를 서로 독립된 승인 근거로 재사용하지 않는다. 기존 `audit_position_study_ev_lineage`의 run/revision/window/outcome 검사를 보존한다. 겹친 연구 결과는 overlap 표시와 함께 연구 조회는 가능하지만, 독립 검증을 통과한 현재 EV로 변환하지 않는다. disjoint도 독립성 확정이 아니다. 이번 A에서는 하위 조건부 결과를 상위 allocation으로 되먹이는 경로를 추가하지 않는다.

A04의 순자산 분모 수정은 이 연구 계약과 별도다. 새 연구의 초기 현금·보유 평가액·자금 조건은 명시적인 Paper portfolio이며 현재 주문가능금액을 순자산으로 간주하지 않는다. A05 보호 상태의 권한은 기존 owner에 남기고 STOPPED/보호 상태의 실행 허용을 모델 지원으로 우회하지 않는다.

### 5. native 미래 정책 프로파일 B의 책임

native 적용을 위한 후속 원본은 Family/parameters/code hash만 일치시켜서는 부족하다. 시작 시점 Risk limits, 보호 목적/우선순위, 세션 경계, 목적별 미체결 WAIT/REPRICE/cancel 정책, 쿨다운과 재진입 admission, 종료 horizon/잔여 포지션 평가 방식을 명시해야 한다. 정책 판단과 체결 가정의 지원 상태를 분리한다.

기존 `mock_automation_protection_trigger`와 `evaluate_unfilled_order_management_shadow` 등 공통 순수 판단을 연구 실행에서 재사용한다. native runner를 연구에 기동하거나 별도 Risk/Execution 엔진을 복제하지 않는다. 연구 adapter가 만드는 account/order/event는 simulated임을 보존하고 기존 PaperExecutionEngine이 체결 가정을 담당한다. 실제 브로커의 cancel ACK·부분 체결·queue/latency를 분봉 high/low로 검증했다고 보고하지 않는다.

처음 지원하는 native 프로파일은 기록·재생 가능한 상태만 명시적으로 열고 pending/partial/UNKNOWN/reconnect 등 원본에 없는 전이는 미지원으로 반환한다. 원본 실행 중 발생한 미지원 전이를 결과에서 누락하지 않는다. 보호가 발생하지 않은 과거 창만으로 보호 정책 동등성을 승인하지 않는다. REENTER는 과거 완전 청산뿐 아니라 기존 owner의 현재 admission 계약을 따르며, 연구 결과가 cooldown을 해제하지 않는다.

B의 정책 판단 재사용과 상태 전이 재현이 끝나도 브로커 실행 적합성과 실제 모의계좌 운용 승인은 별도다. 현재 A03 작업에서 B 구현이나 실제 주문을 승인한 것으로 해석하지 않는다.

### 6. 다음 Sol 구현 경계와 통합 제안

다음 한 단계는 **v3의 HOLD/REDUCE 조건부 Paper 지원을 실제 생성 원본으로 재생하는 좁은 종단 경로**다. DTO만 추가하거나 항상 차단하는 진단 필드를 늘리는 것으로 완료하지 않는다. 기존 연구 실행/reader를 이용한 격리 원본에서 정확한 셀의 양의 결과와 미지원 결과를 함께 검증한다. 첫 단계의 native current-goal 차단은 유지한다.

| 파일/소유 경계 | 다음 변경 |
|---|---|
| `application/position_target.py` | v2와 구분되는 v3 불변 support 명세/검증, 고정 표현과 cohort 셀 규칙. 범용 registry/새 Manager 없음 |
| `application/research_execution.py` | 기존 case 관측에서 support 원재료/proof 기록. 기존 Paper 체결/전략 동작 유지 |
| `application/research_evaluation.py` | 버전별 원본 재생, 셀 검증, 해당 셀 전체 pair 재계산, 일별 cluster 불확실성, 계보/지원 범위 반환 |
| `central_server/market_theme_shadow_inputs.py` | 기존 publication→goal binding 안에서 검증된 결과만 conditional_shadow로 투영 |
| 기존 R4 테스트 | 기존 v2 불변 회귀 + v3 원본 생성/readonly 재생 + 현재 입력/TTL/정책 mismatch |
| 공통 `scripts/run_research.py` 통합 delta | 기존 spec 구성/observe 호출에 v3 support를 전달하는 최소 hook. 다른 담당 범위이므로 이 작업에서 직접 수정하지 않고 필요한 인자와 호출 위치를 통합 담당에 제시 |

공통 hook 전에는 기존 spec/engine/repository API를 호출하는 격리 fixture로 생산·저장·재생을 검증할 수 있다. 이 범위의 통과를 CLI/운영 생산자 연결 완료로 보고하지 않는다. 구현 과정에서 소유 파일 밖의 loader/엄격 버전 검사가 새로 확인되면 그 최소 변경을 이 문서에 추가하고, 허용 범위의 구현·검증은 계속한다. 다른 작업의 완료를 기다리며 전체 연구 파이프라인을 재설계하지 않는다.

완료 검사: (a) v2 ID/값/TTL/차단 불변, (b) v3 실제 원본의 양의 동일 셀 비교, (c) 한 case 일치/다른 셀 전체 평균 전이 거절, (d) 사후 pair 누락·변조·미래 입력 거절, (e) 규모·비용·세션·시각·자료 의미 mismatch 거절, (f) 같은 날 여러 pair를 독립 일수로 세지 않음 및 SE 수식 직접 계산 대조, (g) 현재 Risk/상태/lease 변경과 하드 보호 우선, (h) 새 결과에서 native EV/주문 권한으로의 유출 없음. 이미 검증한 관련 회귀를 재사용하고 변경된 경계에 필요한 검사만 추가한다.

### 7. 이 설계 단계의 상태

새 상태 표현, 표본/불확실성, 원본 버전, 조건부 결과 소비, native 후속 정책의 책임 경계를 위와 같이 확정했다. 기존 세 Python 변경 파일은 이번 설계 단계에서 수정하지 않았으며 새 계약에 대한 테스트는 아직 실행하지 않았다. 실제 계좌·NAS·배포·커밋·push도 수행하지 않았다.

이제 남은 것은 정해진 경계의 일반 구현과 검증이므로 GPT-6.1 Sol High로 복귀한다. 이 설계 문서 자체를 A03 양의 지원 구현 완료나 R4 종료로 집계하지 않는다.

## A03 v3 조건부 Paper 지원 구현 — 2026-10-15

### ① 필요한 이유와 이번 완료 경계

첫 구현은 과거 지원과 현재 차단의 설명이었다. 이번에는 새 원본의 사전 선언 상태 셀과 현재 Paper 관측이 일치하고 표본 요건을 만족할 때 실제 양의 조건부 비교를 반환하는 경로를 구현했다. `PositionStudySupportSpec`과 별도 `PositionActionConditionalStudyPolicy`가 v3 계약을 담당한다. v2의 DTO 외피/정책 문자열/정확 상태 진단/짝 비교 계산을 그대로 유지한다.

### ② 실제 가능한 판단

보유 중 HOLD/REDUCE에 한정하여 기존 분봉 Paper 가정 아래의 조건부 순변화 평균과 거래일별 cluster 표준오차를 볼 수 있다. 모델과 현재 입력의 Family·정책·세션·상태 표현·정확 가격/수량/비용/관측 값이 맞아야 한다. 같은 상태 case 하나나 다른 셀의 전체 평균을 가져오지 않는다. 완결된 1분 관측과 경제적 시작 portfolio, 원래 candidate/emitted keys를 보존하여 비교한다. 규모/가격 보간이나 종목 간 전이는 없다.

같은 날의 case 수를 거래일 수로 세지 않는다. 별도 거래일이 2개 미만이거나 선언한 최소 표본을 못 채우면 통계 결과는 null이다. 최소 2일은 계산 가능 조건이며 모델 품질·독립성·운용 적격성을 입증하지 않는다. 독립성/확률 보정은 계속 미검증으로 표시한다. 원래 value basis와 측정 수수료 차감 규칙을 함께 기록한다.

### ③ 기존 기능 연결과 설계 보완

기존 `position_action_study_spec` → `observe_position_study_case` → 기존 event writer/repository → 원본 readonly reader/publication decoder → 조건부 비교 경로를 사용한다. 기존 strict 원본 검증 뒤 proof의 support 관측을 재계산한다. 새 collector/DB/writer/owner/Manager/전략 엔진은 없다. 첫 구현에 없던 고정 표현/셀 통계 함수는 관측 정규화와 통계 책임이며 단순 위임 계층이 아니다.

사전 설계 6절에서는 공통 `scripts/run_research.py` hook 추가를 예상했지만 실제 호출 경로를 확인한 결과 **추가 변경이 필요 없었다**. 기존 진입점이 typed study를 spec과 engine에 그대로 전달하고, 기존 loader가 소유한 `research_evaluation.py`의 버전별 파싱을 사용한다. 기존 진입점으로 생성한 v3 원본도 격리 테스트에서 재생한다. 공통 스크립트/다른 담당 전략·feature 파일을 수정하지 않았다.

새 셀에는 미래 자금 경로에 영향을 주는 경제적 시작 portfolio와 OHLC/거래량/거래대금, 봉 가용 지연도 포함한다. 날짜를 제거한 보유 경과만으로 다른 자금/관측을 전이하지 않는다. 원래 절대 시각·revision 참조는 support observation의 계보로 유지한다. 후보/emitted key를 그대로 비교하므로 등록 전략의 다른 날짜 사례가 지원되지 않을 수 있다. 이를 임의 정규화로 넓히지 않았다.

v3 게시 시각도 별도로 보존하여 게시 전 시점에 모델을 적용하지 않는다. 현재 결과의 expiry는 원래 모델/현재 목표/현재 관측의 가장 이른 만료다. 조건부 결과의 숫자와 pair 참조는 분리 복사하여 소비자가 수정해도 준비된 모델을 바꾸지 않는다.

### ④ 연구·Shadow·가짜 owner·운용 상태

- 연구: 실제 Paper 주문/체결, 비용, 기존 report와 repository로 생성한 격리 v3 원본을 readonly 재생한다. 양의 셀 시험은 명시적으로 통제한 초기 candidate 상태와 서로 다른 두 거래일의 실제 Paper 가치 변화이며 등록 전략의 실제 시장 모집단 승격이 아니다.
- Shadow: `evaluate_position_study_conditional_shadow`의 양의 연구 비교가 구현됐다. 기존 current-goal binding은 선택적 현재 관측을 받으면 owner/Risk/상태/보호/lease를 먼저 확인한 뒤 `conditional_shadow`에만 투영한다. 기존 Runtime은 완결 분봉/경제적 portfolio를 제공하는 현재 support 생산자를 연결하지 않았으므로 기본 조회는 해당 결측을 표시한다. 현재 0B 가격 표본을 완결 봉으로 간주하지 않는다.
- native 선택/주문: 기존 `EV_UNAVAILABLE/result=None/order_authorized=false` 유지. 조건부 결과의 `usable_for_native_selection=false`도 유지한다. 상위 allocation/native EV DTO로 되먹이지 않는다. 기존 owner와 하드 보호가 우선한다.
- 실제 모의계좌·브로커 실행 적합성·NAS·배포: 미실행/미승인. 원본 data/.env/운영 DB 접근·쓰기, 커밋·push·병합 없음.

### ⑤ 남은 필수 작업

이번 좁은 v3 생성/재생/조건부 비교는 A03 전체 완료가 아니다. 현재 Runtime에서 동일 관측 의미의 support 원재료를 기존 owner 경계로 제공할 생산자 연결과, 설계 5절의 native 미래 Risk/보호/목적별 미체결/쿨다운 프로파일 B가 남는다. ADD/REENTER/EXIT로 지원을 확장하거나 보호 권한을 연구 모델에 옮기지 않는다. A04·A05는 기존 후속 순서로 유지한다.

공통 문서 통합 delta: “v3 HOLD/REDUCE 조건부 Paper 지원 구현, native current EV/주문 차단 및 현재 Runtime support 생산자 미연결”로 상태를 반영한다. 기존 v2의 현재 EV 미지원 표현을 삭제하지 않는다. 공통 API 계약에는 v3 원본/version과 `position-study-model-support/v2.conditional_shadow`가 native EV와 다른 연구 결과임을 명시해야 한다.

### 변경 파일과 검증 범위

- `application/position_target.py`: 별도 v3 DTO, 고정 관측/상태 셀, v3 cohort identity.
- `application/research_execution.py`: 기존 관측 event 안에 v3 원재료/proof 기록.
- `application/research_evaluation.py`: v2/v3 파싱·원본 재계산, 셀별 짝/일별 불확실성, 현재 Paper 비교.
- `central_server/market_theme_shadow_inputs.py`: 기존 binding 안의 owner 검증 후 조건부 외피. 기존 hot 경로에 새 I/O 없음.
- `tests/unit/test_position_study_publication.py`: 기존 모듈에 v3 실제 원본·기존 진입점·통계·누락·변조·현재 범위/보호 검증 6건 추가. 첫 단계의 runner 테스트는 이번 단계에서 수정하지 않았다.

검증 결과는 [r4-v3-verification.json](r4-v3-verification.json)에 저장한다. 현재 checkout import, 사용자 Temp 격리, 실행 전후 같은 LF 소스 지문과 AST/compile을 함께 확인한다. 전체 Windows all-local/외부 CI/PG/실제 계좌·장중 수락은 이번 검증 범위가 아니다.

최종 변경 소스에서 **55건 / 295.514초 / 종료 코드 0 / 실패·오류·skip 0**을 확인했다. publication 16건(신규 v3 6 포함), runner state 12건, cohort 15건, native case 10건, A01 행동별 자료/보호 우선 2건이다. 실행 전후 변경 Python 6파일의 LF SHA256이 동일하고 모두 AST/compile 통과했다. 기존 ASGI 인증/게시/revision/중복/clear, 2/8 RAM 공유·단일 계좌 borrow·만료, 원본 reader의 readonly/COMMIT 없음, 실제 가짜 owner의 경합/보호/대사를 포함한다. `git diff --check`도 통과했다. 테스트 소스는 최종 묶음 시작 이후 수정하지 않았다.

임시 자료는 `C:/Users/pc-1/AppData/Local/Temp/kiwoom-regression/r4-v3-e2a0-20261015-final`에서 격리했다. 실패 묶음의 경로 `.../r4-v3-e2a0-20261015-02`는 보존 대상으로 구분한다. HEAD는 `50a70c9ce6cdf20915fde27f2d1f47becb82eadf`이며 로컬 미커밋 변경이다. 원격 main/CI/배포 상태를 이 로컬 검증으로 갱신하지 않는다.

초기 좁은 5건은 통과했다. 새 확장 검사 묶음에서는 4건 통과/1건 실패/1건 오류가 있었다. 순변화가 같은 이유는 fixture의 가격 변화가 REDUCE 체결 전에 발생했기 때문이며, 계좌 관측 fixture에는 필수 reasons 인자가 빠졌다. 검증 조건이나 계산을 완화하지 않고 실제 Paper 체결 뒤의 가격 변화와 필수 인자를 수정했다. 두 실패 항목은 재검증해 통과했고 해당 실패 묶음은 성공으로 재분류하지 않는다. 최종 묶음은 아래 결과와 별도로 구분한다.

모델: 최신 chat 실행 기록 `gpt-6.1-sol/high`에서 진행했다. 확정한 설계의 구현·관련 회귀 범위로 충분하며 새 모델 handoff는 없다.

## A03 현재 입력 연결 — 2026-10-15

### 요청 범위와 연결 결과

사용자가 이 채팅에서 현재 입력 연결과 관련 검증을 지정하고 주문 OFF를 유지하도록 요청했다. 기존 Collector RAM 스냅샷과 계좌 owner 관측을 현재 조건부 비교에 연결했다. `MarketThemeShadowRuntime.start()`로 실제 격리 worker를 RUNNING 상태에서 실행하고 `latest_results.position_allocation.r4_action_comparison.goals[].model_support.conditional_shadow`의 지원 수치를 확인했다. 운영 앱/실제 모의계좌의 장중 수락과 구분한다.

현재 입력이 선언한 v3 셀과 같으면 `SUPPORTED_UNDER_PAPER_ASSUMPTIONS`와 측정 평균/불확실성이 나온다. 현재가 표본을 봉으로 간주하거나 과거 Paper portfolio를 현재 계좌 근거로 복사하지 않는다. native 선택/매매 EV는 계속 null이며 `usable_for_native_selection=false`, `order_authorized=false`다.

### 기존 owner 재사용과 공통 모듈 변경 delta

처음의 소유 파일 안에서 getter 인자만 전달해서는 완결 분봉과 실제 매입 비용이 만들어지지 않았다. 이번 사용자 요청에 필요한 공통 owner 변경을 다음 최소 책임으로 한정했다. 기존 계층/주문 writer/저장 스키마/네트워크 요청을 추가하지 않는다. 다른 담당의 theme/strategy/feature 파일은 수정하지 않았다.

| 기존 경계 | 변경과 이유 |
|---|---|
| `central_server/minute_bars.py` | 기존 누적기의 OHLCV를 그대로 복사해 승인 KRX source마다 최근 완결 관측 한 벌을 유지. 1분+2초 전에는 공개하지 않고, 반복 조회가 available_at을 갱신하지 않음. drain으로 기존 저장 값이 제거되어도 사본은 유지. 늦은 틱은 RAM 지원만 partial로 낮추며 기존 저장용 open_windows/dirty/operation ID/누적 값을 바꾸지 않음 |
| `central_server/realtime_collector.py` | 기존 단일 `ram_observation_snapshot`에 opt-in closed-minute 사본을 포함. 기존 기본 스냅샷 외피는 그대로. 새 Collector/등록/REST/DB 읽기/체결 재집계 없음 |
| `central_server/mock_account_monitor.py` | 완료된 계좌 복구가 이미 보유한 fill/cost cache를 읽는 `position_study_portfolio`. 기존 account epoch 전후 일치를 확인하고 추가 계좌/원장 조회 없이 정확한 첫 진입 portfolio만 반환 |
| `central_server/mock_automation_supervisor.py` | 기존 등록 owner에서 복사한 StrategyState와 같은 계좌/Risk cut의 portfolio를 새 관측 subtype에 추가. 기존 관측 DTO 및 runner-state/v1 필드는 변경하지 않음. owner 교체/epoch 변경 시 폐기 |
| `central_server/market_theme_shadow_monitor.py` | v3 study가 있는 경우에만 같은 Collector 스냅샷에서 완결 분봉을 요청. 기존 dispatch/worker/단일 계좌 borrow/2·8 공유 흐름 유지 |
| `application/position_target.py` | 첫 진입 portfolio의 원본 체결·수량·매입금액·비용 revision/hash·scope를 검증해 복사하는 순수 projection. 기존 Risk의 exact-fill 중복 판정과 digest를 재사용하고 Risk/비용 정책을 다시 계산하지 않음 |
| `central_server/market_theme_shadow_inputs.py` | 승인·venue·봉 완결·gap·세션·시점·원본 ref와 owner portfolio proof를 결합하고 기존 binding에 전달. 별도 engine/owner/쓰기 없음 |
| `application/research_evaluation.py` | 실제 owner account.as_of와 검증된 broker 주문 관측을 현재 목표 재생에 전달하도록 지원. Paper용 가상 현재 시각으로 계좌 cut을 대체하거나 broker 관측 None과 확인된 빈 tuple을 같게 취급하지 않음 |

새 DTO는 `PositionStudyRunnerAccountObservation`으로 필요한 경우에만 경제적 근거를 전달한다. 기존 base/runner 관측을 조용히 필드 확장하지 않았다. source/portfolio/state 참조가 조건부 결과에 함께 남는다. 수치가 있는 조건부 결과의 가장 이른 만료는 binding과 기존 receipt에 전달해, getter 조회만으로 오래된 goal을 남기지 않는다.

### 현재 지원 범위와 차단

첫 producer는 account-wide ledger에 **검증된 BUY fill 한 건**만 있고 현재 보유가 그 수량/가격과 일치하며 미체결이 없는 경우다. 원래 실제 비용과 Risk cost revision까지 같아야 한다. 매입 원가·수수료는 그 원본에서 가져오고 실현손익 0은 청산이 없는 해당 이력으로 확인한다. 다중 체결/부분 청산/타 run 보유/미해결 주문/미확인 비용을 추정해서 채우지 않는다. event cache는 2,000건 이내, cost record는 이 첫 프로파일의 한 건으로 제한한다.

v3 첫 모델은 정수초 상대 시각과 정확한 상태/가격/규모/관측 셀을 선언했다. 미세초 시각을 반올림하거나 candidate/emitted key를 제거하여 지원 범위를 넓히지 않는다. 정수초 밖의 현재 cut은 `current_study_timestamp_precision_outside_declared_profile`로 명시한다. 따라서 일반 장중 입력마다 수치가 나온다는 뜻은 아니며, 선언 범위를 벗어나면 null/미지원 사유가 나오는 것이 정상이다. 더 넓은 상태/시각 표현은 별도 모델 선언·원본 검증 대상이다.

늦은 틱·partial/gap·승인 취소·SOR/NXT·시점/가격 불일치·원본 만료·owner/비용 근거 유실·보호 대기는 현재 조건부 수치를 승인하지 않는다. 정상 Risk나 Family 일치만으로 native 실행 지원을 승인하지 않고 보호/쿨다운 권한은 기존 owner에 남긴다.

### 검증 근거와 실패 구분

단독 종단 검사에서 2/8 소비자가 같은 Collector snapshot/계좌 borrow 한 번으로 조건부 수치를 받았다. 실행 중인 격리 Shadow worker, 비용/owner 유실과 보호 대기, 늦은 틱, 조회 100회 무계산·무계좌 읽기, 원래 만료에 따른 goals 제거도 확인했다. 분봉 사본의 분리 복사/소스 범위/고정 available_at/drain 이후 유지와 기존 저장 상태 보존을 검사했다. 새 scope/precision 검사를 포함한 최종 묶음 결과는 [r4-current-input-verification.json](r4-current-input-verification.json)에 기록한다.

최종 관련 **161건 / 386.712초 / 종료 코드 0 / 실패·오류·skip 0** 통과. 현재 입력 5건·분봉 2건의 신규 검사와 기존 v3/v2/R4 원본/게시/owner 상태/보호, Collector34, 계좌 monitor17, supervisor4, 기존 RAM worker/상위 비중/모델 게시 회귀를 포함한다. 변경 Python 12파일의 실행 전후 LF 지문이 같고 AST/compile·`git diff --check`도 통과했다. 첫 단계의 55건 보고서를 현재 지문으로 재표지하지 않고 새 실행 기록을 남겼다.

기존 0/2/8 소비자 대조 probe에서 모두 DB connections4/COMMIT4, snapshot/minute/finalization/second writer 각1, minute/second add 각80으로 동일했고 RAM reads는 0/1/1이었다. 이는 기존 저장 경로와 공유 조회 회귀의 근거이며 장중 성능 수락이나 새 v3 전체 시장 표본의 지원 비율 측정은 아니다. 새 현재 입력 종단 검사는 별도로 snapshot1/account borrow1과 hot DB 연결0을 확인했다.

검사는 `C:/Users/pc-1/AppData/Local/Temp/kiwoom-regression/r4-current-e2a0-20261015-final`에 격리하고 현재 worktree import를 확인했다. 완료된 테스트 프로세스의 종료 후 빈 성공 임시 디렉터리만 정리하고 초기 실패 자료는 보존한다. HEAD `50a70c9ce6cdf20915fde27f2d1f47becb82eadf`, 로컬 미커밋이다. 전체 Windows all-local/외부 CI/실제 PG/NAS/계좌 장중 acceptance는 이 실행에 포함하지 않았다.

종단 fixture 초기 실패는 후보 package의 implementation_hash/candidate hash 불일치, 허용되지 않는 가중치0 조합, 연구 완료 시각이 비교 cut보다 늦음, 상위 Paper 창에서 실제 목표가 청산이 발생함, 서로 다른 상위/하위 가격, 시장 맥락 TTL 만료, 전역 예산과 종목 cap의 혼동으로 구분했다. 각 기존 계약/원본 이벤트/반환 reasons를 확인해 fixture를 바로잡았고 검증을 완화하지 않았다. 실패 자료 `r4-current-e2a0-20261015-01`~`10`은 성공으로 재분류하지 않는다.

실제 연결 수정이 필요한 원인은 현재 목표가 원래 owner account.as_of와 확인된 broker 주문 tuple을 포함하는데 기존 Paper 재생이 대체 계좌 cut과 broker=None으로 계산한 점이었다. 실제 account/broker cut을 전달하고 원래 목표 ID/전체 내용 재생 검사를 보존했다. 관련 정합성 판정은 고정 원본/현재 입력과 같은 검증 조건으로 재확인한다.

### 완료 경계와 이 채팅의 잔여

이번 요청의 **A03 현재 입력 생산자 → 실행 중 Shadow 조건부 결과 연결**은 이 범위의 구현과 관련 검증으로 종료한다. 이 채팅의 A03 전체가 끝난 것은 아니다. 기존 Risk/보호/목적별 미체결/쿨다운을 사용하는 native 미래 정책 프로파일 B의 생성·재생·모델 적용 검증이 남는다. 실제 행동 선택/주문 연결에는 그 근거 및 후속 A04/A05·모의계좌/운용 승인이 필요하다.

전체 CURRENT_STATUS/OPEN_ITEMS/MODULE_MAP/API 계약의 통합 delta는 “현재 입력 producer 연결 완료(첫 진입/정수초/정확 셀), native 미래 정책 미지원, 매매 EV·주문 OFF”다. 이번에 새 시험 모듈은 만들지 않았다. 공통 모듈 변경은 위 표로 통합 담당이 검토할 수 있게 남긴다.

실제 계좌 접속/브로커 전송·운영 DB/.env·NAS·GUI 앱 실행·커밋·push·병합·배포는 수행하지 않았다. 현 단계의 운영 acceptance나 현재 실행 중인 사용자 앱의 코드 reload를 확인했다고 보고하지 않는다. 모델은 현재 채팅 실행 기록 `gpt-6.1-sol/high`로 확인했고 이번 연결·검증 범위에서 모델 전환은 필요하지 않았다.

## A03 B 조사 — native 미래 정책의 새 원본 계약

2026-10-15 사용자의 후속 「다음」에 따라 B를 조사했다. 최신 `turn_context`에서 `gpt-6.1-sol/high`와 turn `01a13c96-8402-73f3-87ad-a2c23e57560f`를 확인했다. HEAD는 계속 `50a70c9ce6cdf20915fde27f2d1f47becb82eadf`다. 이번 조사에서 실행 코드·시험은 수정하지 않았고 위 161건을 B의 검증으로 재분류하지 않는다.

### ASTRA 설계 재검토 필요

**권고 모델·추론**
- GPT-6 Astra High (`gpt-6-astra`, `high`). 기존 A03 설계 5절의 재사용 원칙은 유지한다. 아래 미확정 계약을 결정한 뒤 일반 구현·검증은 Sol High로 복귀한다.

**현재 문제와 확인된 호출 경로**
- `application/research_execution.py::process_position_target`의 현재 REDUCE는 `PendingOrder`에 limit 가격/limit 실행 버전을 넣지 않는다. `_pending_bar_rejection` 뒤 `_fill_pending`이 다음 적격 분봉에서 전량 체결한다. 기존 `process_unfilled_action`은 별도의 LIMIT 연구 지시이며, 목적별 native 판단을 자동 호출하지 않는다.
- `application/mock_automation_execution.py`의 native 검증·재전송은 LIMIT 계약을 사용한다. `application/order_lifecycle.py::evaluate_unfilled_order_management_shadow`는 원래 가격 anchor, 실제 주문 상태/잔량, 대사된 취소·체결, 현재 목표·Risk·판단을 사용한다. 일반 재호가는 별도 paired unfilled EV에 의존하고, 보호 목적은 EV보다 앞선다. 최초 REDUCE 자체가 미체결 상태를 생성하므로, 시작 상태에 pending이 없다는 사실만으로 미래 미체결 정책을 제외할 수 없다.
- `central_server/mock_automation_runner.py::_poll_session_clock`는 active 주문이 있으면 WAITING_ORDER/취소 요청 후 실제 recovery·체결·Risk를 확인한다. `application/mock_automation_session_clock.py`의 순수 assessment만 같아도 이 전이 순서를 증명하지 못한다.
- `application/mock_automation_risk.py::mock_automation_protection_trigger`는 account/run/binding/data-path 및 현재 Risk 절단면을 검사한다. `mock_automation_stop_loss_trigger`는 ACK된 entry basis와 Collector 가격 참조를 요구한다. 현재 Paper `_apply_intrabar_risk`는 분봉 low/high로 즉시 RISK_FILL을 만들므로, 이것을 native 보호 receipt로 재표지할 수 없다.
- native `_apply_new_fills`는 실제 완전 청산 체결시각으로 cooldown을 시작한다. Paper `_sell_fill`은 RISK_FILL이면 bar_end, 일반 FILL이면 bar_start를 사용한다. 연구가 owner admission을 대신하거나 cooldown을 해제해서는 안 된다.
- 현재 `position_study_state`는 Risk limits/보호/시계/등록 정책을 복사하지만, v3 source는 위 미래 native LIMIT·취소·ACK·재호가 정책을 생성·재생한 증거를 갖고 있지 않다. `research_evaluation.py`의 v3 reader는 단일 target 주문/전량 fill과 기존 Paper cash/share 경로를 검증한다.

**기존 설계를 그대로 유지할 경우**
- v3의 현재 조건부 Paper 값과 ID·원래 lease를 보존할 수 있다.
- 공통 순수 함수의 결과를 단순 부가 기록하는 것만으로는 보호·미체결이 실제 portfolio 미래 경로에 반영되지 않는다. 반대로 기존 Paper 경로를 native로 바꾸면 이미 선언된 v2/v3 가치 의미와 reader 계약이 바뀐다.
- 보호 없는 창만 지원하고 실제 보호·미체결 전이를 결과에서 빼면 지원 모집단을 사후 축소한다. 기존 설계의 금지 사항이므로 이 방식으로 B 완료나 native 적용을 선언하지 않는다.

**가능한 대안**
- A: 별도 버전의 좁은 native 정책 연구 원본. 기존 Paper LIMIT 체결 가정 위에 공통 Risk·시계·목적별 미체결 판단을 연결하고, simulated account/order/event와 native ACK 근거를 구분한다. 미지원 전이는 선언된 전체 pair의 실패 근거로 남긴다. 새로운 reader는 그 판단·상태·수량·현금 전이를 원본으로 재생한다.
- B: 공통 판단의 관측 trace만 먼저 만든다. 재사용 함수와 policy 판단 검증에는 유용하지만 Paper 미래 가치가 native 정책 아래의 가치라는 증거는 아니며, native 모델 적용은 계속 미지원이다. 이 대안을 B 완료로 보고할 수 없다.
- 실제 브로커 queue/부분 체결/cancel ACK를 분봉으로 증명하는 대안은 채택하지 않는다. native runner 기동, 새 execution 엔진 복제, 실제 계좌 접속·주문은 이번 범위가 아니다.

**현재 추천안**
- A를 기본으로 하되 v2/v3를 유지하고 새 원본을 분리한다. 첫 버전의 가격·quantity·시간 절단면, simulated Risk/cost 근거, 최초 REDUCE LIMIT anchor와 후속 미체결 정책을 명시적으로 확정해야 한다. native 정책 판단의 지원과 분봉 체결 가정의 지원을 별도로 반환하며, 현재 주문은 OFF로 유지한다.

**Astra가 판단해야 할 핵심 질문**
1. 첫 B 프로파일이 실제로 실행·검증할 전이와 미지원으로 남길 전이는 무엇인가? 최초 REDUCE LIMIT, 보호 발생, 시계 청산, horizon 잔여 포지션·주문을 포함한 사전 선언 grammar와 새 버전 경계를 정한다.
2. 순수 판단에 필요한 simulated Risk/주문/가격/취소 근거를 어떤 계약으로 공급하고 재생할 것인가? native ACK 출처를 위조하지 않으면서 공통 판단을 재사용하는 adapter와 기존 Paper writer/reader의 책임을 정한다.
3. 일반 재호가의 별도 EV 입력은 첫 B에서 미지원인가, 사전에 동결된 외부 근거인가? 미래 결과를 그 미래 판단의 입력으로 돌려 쓰는 순환 및 pair 사후 누락을 막고, 현재 native owner 정책과의 일치 gate를 정한다.

위 판단은 단순 함수 추가나 파일 크기 때문에 요청하는 것이 아니다. 실행 가정·정책 전이·원본 계보와 현재 적용의 경계가 구현 방식에 따라 달라지고, 기존 원칙만으로 양의 native 지원을 신뢰성 있게 승인할 수 없어 전환한다. 이 문서의 조사 delta까지 저장한 뒤 HANDOFF를 출력하고, 전환이 확인되기 전 구현·시험·추가 문서화를 진행하지 않는다.

## A03 B 설계 확정 — 2026-10-15

현재 채팅 최신 실행 기록은 `gpt-6-astra/high`, turn `01a13c9d-55d7-73e3-a3e5-c03ec0810304`다. 위 조사 뒤 사용자의 「다음」에 따라 세 미확정 계약을 결정했다. 이번 변경은 이 문서뿐이며 실행 코드 변경이나 B 실행 시험의 통과를 뜻하지 않는다. 기존 161건은 앞선 현재 입력 연결의 근거다.

### 1. 결정과 완료 범위

**별도 v4 원본에서 native 공통 판단을 실제 Paper 미래 경로에 적용한다.** 첫 프로파일은 `native-policy-paper/v1`이며 HOLD/REDUCE, 같은 KST 거래일의 KRX 연속장, 단일 보유 종목, 검증된 최초 BUY 한 건, 시작 미체결 없음으로 한정한다. v3의 정확 셀·정수초·비용·전체 사전 pair·일별 cluster 통계를 재사용한다. 가격·시각을 반올림하거나 유사 셀로 확대하지 않는다.

일반 재호가 EV는 첫 프로파일에서 **없음**으로 동결한다. 실제 native owner의 동결 정책도 `allow_supplied_paired_mock_evidence=false`, protective cancel retries=0이어야 정책 일치로 인정한다. 나중에 EV가 들어올 수 있는 owner에 현재 입력이 없다는 이유만으로 이 프로파일을 맞추지 않는다. WAIT/완료/보호 우선은 공통 함수로 판단하고, CANCEL/REPRICE/취소 ACK/부분 체결/UNKNOWN/reconnect/새 진입이 필요해지면 전체 선언 비교를 미지원으로 남긴다. 이는 조용한 창만 사후 골라 쓰라는 뜻이 아니다.

B 완료 기준은 **새 원본 생성 → 원본 재생 → 정책 조건부 수치 → 현재 owner 정책과 비교 → 실행 중 주문 OFF Shadow**의 좁은 종단 경로다. 보호 발생과 세션 청산을 포함한 양의 원본 및 미지원 전이의 전체 pair 차단을 함께 검증해야 한다. 실제 브로커 실행 동등성이나 native R4 주문 승인은 이 완료 기준에 포함하지 않는다. B 이후 이 프로파일을 임의로 넓히며 이 채팅의 종료를 미루지 않는다. A04/A05와 실제 모의계좌/운용 수락은 기존 후속 범위다.

### 2. REDUCE 개입과 native 후속 정책의 구분

추가로 확인한 native `_intent_for_decision`은 ENTER면 BUY, 그 외 허용된 EXIT면 SELL LIMIT을 만들고, runner `_register_unfilled_execution`은 target_reached 단독 EXIT만 TAKE_PROFIT, 나머지 EXIT는 PROTECTIVE_EXIT 및 목표 0으로 등록한다. 따라서 현재 코드에는 **R4 REDUCE의 잔여 목표를 보존하는 native 주문 목적 매핑이 없다.** 이를 EXIT/목표0으로 바꾸거나 native 등록 함수를 그대로 호출하면 다른 실험이 된다.

- v4의 최초 REDUCE는 `position-target-intervention/v1`이라는 명시적인 연구 개입이다. 기존 목표 계산이 정한 감소 수량만 SELL LIMIT, 가격은 원래 현재 참조가격, 잔여 목표는 원래 REDUCE 목표로 고정한다. HOLD는 그 개입 주문만 생략한다. 두 분기 모두 이후 같은 등록 Family와 아래 공통 정책을 따른다.
- 이 연구 개입의 미체결 목적은 **실험 명세에서 명시한 TAKE_PROFIT 규칙 사용**이다. native가 REDUCE를 그렇게 등록한다는 주장이 아니다. 원래 결정 사유를 target_reached로 위조하지 않는다. 원본에 `origin=position_target_intervention`, 원래 goal ref와 잔여 목표, `native_action_mapping_verified=false`를 보존한다.
- 이후 실제 Family의 target_reached 단독 청산은 기존 native 목적 매핑을 사용하고, 손절/일일 손실/보유 만료/세션 청산은 보호 목적·목표0이다. 최초 REDUCE 개입과 이후 완전 청산은 별도 intent로 구분한다.
- 현재 Shadow의 v4 수치는 이 개입·정책·체결 가정에 조건부다. **native REDUCE 실행 매핑은 계속 미검증**이며 원래 `EV_UNAVAILABLE/result=None/order_authorized=false`의 native 선택 경계는 열지 않는다. 수치는 명시적인 `conditional_shadow`에만 둔다.

### 3. v4 동결 명세와 계보

`PositionActionStudyPolicy`의 새 v4 타입을 기존 파일에 추가한다. v2/v3의 직렬화에 새 기본 필드를 섞지 않는다. study/case/cohort/comparison/publication의 v4 경로는 엄격하게 분기하고, 기존 원본을 버전만 바꾸어 승인하지 않는다.

새 프로파일에는 다음을 원본 실행 전에 동결한다.

| 내용 | 고정 계약 |
|---|---|
| 실험 | HOLD/REDUCE·원래 sizing/goal·원래 cost·동일 초기 경제 상태·정확 셀·60~3,600초의 정수 분 horizon |
| 시작 Risk | 최초 BUY 한 건, 초기 실현손익0, 보유 원가/실제 비용의 출처; 다른 보유·이전 청산·미해결 체결 없음 |
| 후속 정책 | Family/parameters/세션 정책, 5개 native Risk limits, 전체 `MockUnfilledExecutionPolicy.to_dict()`, 정책 구현 지문 |
| 개입 | 위 2절의 LIMIT anchor/수량/잔여 목표·실험 목적, native action mapping 미검증 |
| 입력 | 원래 available_at을 가진 완결 분봉·원래 Family 입력; 손절은 이용 가능한 close에서 판단. intrabar low/high 손절은 사용하지 않음 |
| 실행 | 기존 LIMIT의 다음 적격 분봉 open·slippage·전량/volume 가정, 아래 ACK 가용 시각과 취소 미지원 규칙 |
| 미체결 | 일반 reprice evidence 없음, WAIT/완료만 실행 가능; CANCEL/REPRICE/ACK race/부분 체결은 미지원 |
| 종료 | 고정 horizon에서 보유·현금·체결 비용 평가, 미체결 노출 별도 기록; 강제 체결/청산/취소 없음 |

공통 Risk·FIFO/비용·미체결·시계 함수가 기존 연구 implementation hash 목록 밖에 있으므로 **v4 전용 policy implementation ref**가 필요하다. 기존 scientific code_hash와 함께 사용하고, 고정 목록의 상대 경로+UTF-8 LF 소스 hash를 묶는다. 목록은 실제 호출하는 `mock_automation_risk.py`, `order_lifecycle.py`, `mock_automation_session_clock.py`, 관련 `domain/order_contract.py`/`execution_activation.py`와 기존 research implementation identity다. helper가 다른 기존 파일의 정책을 호출하면 그 의존성도 목록에 넣는다. production hot loop에서 파일을 읽지 않고 연구 준비·owner 등록 시 한 번 계산한다. 함수 이름만 같다는 이유로 현재 구현을 승인하지 않는다.

### 4. 공통 판단 재사용과 출처 경계

새 native runner/계좌 owner/Risk 엔진/브로커 gateway를 연구에서 기동하지 않는다. 기존 `PaperExecutionEngine`의 선택 프로파일, 기존 Family, 이벤트 writer/reader를 사용한다.

1. `mock_automation_risk.py`에서 **출처와 무관한 수치 계산/판단 부분만** 작은 순수 함수로 추출한다. 기존 native wrapper의 account/run/binding/data-path 검사, ACK 출처, source tag, receipt/hash와 검증은 그대로 남긴다. Paper adapter는 같은 계산을 호출하고 `paper-simulated-policy/v1` 출처의 별도 결과를 만든다. `nas/direct`, `collector-trade:` 또는 `acknowledged_account_monitor`를 연구 근거에 부여하지 않는다.
2. 일일 손익은 Paper portfolio의 정수 `realized_pnl_won`을 그대로 native Risk로 복사하지 않는다. native는 날짜/종목/side별 비용 배분과 Fraction FIFO 후 정수화를 사용한다. 기존 builder 안의 이 수치 부분을 그대로 추출해 native와 연구가 함께 호출한다. 연구 비용은 이미 발생한 Paper fill의 동결 수수료/세금으로 구성하고 `simulated_cost`임을 보존한다. 아직 발생하지 않은 fill/미래 일계 비용을 당겨 쓰지 않는다. native 비용 검증/조회·Risk 저장 transaction은 이동하지 않는다.
3. 손절 threshold 판단 역시 공통 순수 부분을 사용하되, native의 실제 Collector 가격·ACK된 entry 검증은 native wrapper에 남긴다. 연구는 원래 분봉 close와 available_at을 가진 simulated 가격을 사용한다. 공통 함수 추출 전후 native receipt·서명·사유·시각이 동일한 회귀가 필요하다.
4. `session_clock_assessment`/`clock_exit_decision`과 `evaluate_unfilled_order_management_shadow`를 직접 재사용한다. 후자는 기존 typed account/order/broker DTO의 **연구 메모리 투영**을 받는다. 외부 envelope와 모든 입력 ref가 simulation임을 나타내고 실제 recovery/ACK receipt나 native repository에 저장하지 않는다.
5. 원래 LIMIT anchor와 목적별 deadline 계산은 `order_lifecycle.py`의 좁은 순수 helper로 공유할 수 있다. native caller는 기존 목적 매핑을 그대로 넘기고, 연구 개입은 위에 명시한 실험 목적을 넘긴다. 전략 판단/프로세스 수명/owner 권한을 새 계층으로 옮기지 않는다.

공통 모듈을 건드리는 이유는 Paper에서 native 수치·정책을 복제하거나 실제 ACK 출처를 위조하지 않기 위해서다. 이것이 A03 통합 delta이며 다른 작업 영역의 전략/feature 로직은 수정하지 않는다.

### 5. 실행 시각과 전이 grammar

첫 프로파일은 **실제 관측 도착 및 명시된 기한**으로 진행한다. 독립 시계의 due/intent expiry/management wait·validity/protection deadline을 다음 봉까지 임의로 미루지 않는다. 기존 owner에 새 timer를 만들지 않고 오프라인 입력 큐에 결정론적인 clock cut을 넣는다.

- source bar의 `bar_start/bar_end/available_at`은 수정하지 않는다. 분봉에서 가정한 fill의 발생시각은 기존 open, 그 사실의 가용 시각은 해당 bar의 원래 available_at이다. policy는 ACK 가용 시각 전의 fill·현금·잔고를 사용하지 않는다. source 시각과 simulated receipt 시각을 모두 기록한다.
- 동일 available_at에서는 먼저 해당 cut에 도착한 modeled fill을 반영하고, 공통 Risk/보호 → 시계 → 기존 pending의 미체결 판단 → 허용된 Family 판단 순서로 진행한다. Family는 기존 native와 같이 active TAKE_PROFIT 주문이 있어도 HOLD/EXIT 판단을 갱신할 수 있으나 주문을 중복 생성하지 않는다. 모든 순서는 프로파일에 동결하며 실제 async owner의 전송 지연까지 같다고 주장하지 않는다.
- 다음 open이 지났으나 관련 bar가 아직 도착하지 않은 사이에 cancel/expiry/보호 선점이 필요하면 **체결·취소 경쟁 미지원**이다. 기존 Paper의 즉시 취소로 그 pending을 지우지 않는다. 관측이 나중에 왔다고 이전 시계 결정을 다시 써서 성공 처리하지 않는다.
- v4 미래 구간에서는 기존 `_apply_intrabar_risk`의 즉시 RISK_FILL을 사용하지 않는다. 지원된 보호/Family/시계 EXIT는 실제 Paper SELL LIMIT 주문을 만들고, 기존 전량 LIMIT fill·비용·현금 반영을 거친다. 기존 v2/v3 엔진 분기는 그대로 유지한다.

| 현재 상태/공통 판단 | 첫 프로파일 처리 |
|---|---|
| 시작 open·미체결 없음, HOLD 개입 | 주문 없이 후속 정책 시작 |
| 시작 open·미체결 없음, REDUCE 개입 | 원래 감소 수량의 LIMIT 1건; pending 동안 보유 수량을 줄이지 않음 |
| pending, 공통 결과 PROPOSED/WAIT | 같은 주문·수량·anchor 유지. 매 관측/기한에 다시 판단 |
| 다음 적격 open이 LIMIT을 만족하고 volume/비용/원본이 충분함 | 기존 Paper 전량 fill. 이후 현금·잔여 보유·공통 Risk 재계산 |
| LIMIT 미도달 | pending을 보존. 자동 취소/목표 달성으로 처리하지 않음 |
| 미체결 없음, 공통 Risk EXIT_REQUIRED 또는 유효한 Family/시계 EXIT | 전체 실제 보유 수량의 보호/청산 LIMIT. 실제 modeled fill 전까지 open |
| 보호/시계/Family 선점이 기존 pending의 취소를 요구함 | 판단과 원래 pending을 기록하고 UNSUPPORTED. 추가 fill/청산 결과를 만들지 않음 |
| CANCEL/REPRICE, BLOCKED, 정책 만료, stale/gap, RECONCILE_REQUIRED | 원래 판단/사유와 마지막 상태를 기록하고 UNSUPPORTED. 특히 BLOCKED를 조용한 WAIT로 바꾸지 않음 |
| 전량 청산 fill | 기존 체결 발생시각 기준 cooldown. 원래 emitted keys와 상태 전이 보존 |
| cooldown 만료·Family 상태 갱신 | 기존 Family로 계산. ENTER/REENTER 주문이 필요하면 owner admission 미지원으로 해당 case 전체 차단 |
| partial/UNKNOWN/reconnect/복구/취소 ACK | 첫 프로파일 미지원; 모델에서 값을 추정하거나 실제 계좌 상태로 보완하지 않음 |

이 grammar는 취소 경로를 새로 구현하지 않는 결정이다. 따라서 CANCEL/REPRICE를 지원하기 위해 별도 simulated broker 상태 머신이나 임의의 cancel latency를 추가할 필요가 없다. 추후 범위 확장은 새 프로파일·원본으로 한다.

### 6. horizon과 값의 의미

시작 원본 분봉의 종료시각을 `T0`, 선언된 horizon을 더한 시각을 `T1`로 둔다. 마지막 완결 봉은 T1에서 끝나고 그 가격/기존 open fill을 사용할 수 있게 된 뒤 reader가 값을 확정한다. **T1에서 시작하는 다음 봉의 fill은 포함하지 않는다.** 최초 주문은 원래 decision cut(available_at)에만 가능하다. bar_end와 available_at 사이에는 사후 주문을 만들지 않는다.

값은 `terminal_cash + terminal_owned_quantity * terminal_close - initial_cash - initial_owned_quantity * initial_close`이며 실제 modeled fill의 수수료·세금은 현금에서 반영한다. 기존 paired 비교처럼 gross/fee 분해도 재현하되 entry 비용의 이중 차감은 금지한다. terminal 양도세나 청산 비용을 임의로 가산하지 않는다. 이는 청산 완료 값이 아닌 고정 기간의 mark-to-market 실험이다.

정상 WAIT 중인 LIMIT이 horizon에 남는 것은 실험의 가능한 결과다. **REDUCE가 체결되지 않은 경우도 포함한다.** 요청 목표와 terminal 실제 수량/미체결 잔량을 분리하며, 기존 v3 reader의 '주문 한 건·fill 한 건·목표 달성' 조건을 v4에 적용하지 않는다. pending 매도는 현금이나 매도 완료로 세지 않는다. terminal BLOCKED/UNKNOWN/자료 공백이나 시계의 필수 조치를 무시한 상태는 정상 WAIT가 아니므로 미지원이다. 비용/가격 유효성과 terminal 상태를 원본으로 재검증한다.

T1 이전/동일 시점에 판단이 필요했으나 근거가 T1 이후에야 도착하는 경계는 임의로 성공시키지 않는다. 기록된 clock/input cut으로 의무 동작을 검증할 수 없으면 미지원이다. 이미 지원된 구간의 값을 미지원 이후까지 forward-fill하지 않는다.

### 7. 원본 writer와 재생 reader

새 writer/table/DB/프로세스 없이 기존 `ExecutionEvent.reason`의 버전된 proof와 기존 execution writer를 사용한다. v4에는 시작 case, 사용한 원래 market/Family 입력의 ref·payload·가용 시각, clock cut, 공통 정책 판단, 주문/체결·Risk·경제 상태, terminal 또는 UNSUPPORTED 사건을 남긴다. 순서와 예산(기존 run 10,000 events/게시 4 MiB)을 검사한다. 예산 부족은 실패이며 입력을 조용히 잘라 내지 않는다.

reader는 기존 readonly repository의 원본 spec/manifest/development 검사를 거친 뒤, 동결된 시작 상태와 입력으로 **동일 v4 Paper 실행 및 공통 판단을 다시 실행**해 모든 사건과 terminal 현금/보유/비용을 대조한다. 생산자가 계산한 decision/result hash만 검사하는 것으로 대체하지 않는다. Family 재생에 실제 필요한 전체 입력을 기록·검증하고, 입력 부족 때 HOLD라고 가정하지 않는다. 기본 보고서의 독립 연구 적격성 검사도 우회하지 않는다.

기존 정책 관측 metadata를 검증 후 제거하는 v3 방식으로 v4의 정책/미지원 사건을 지우면 안 된다. 사전 선언한 모든 pair가 있고 그 전이가 지원돼야 비교 수치를 낸다. 한 case가 CANCEL/부분 체결/시계 공백 등으로 실패하면 원래 case ref·단계·사유를 비교 결과에 남기고 전체 수치를 null로 한다. 같은 셀 안의 성공 case만 평균 내거나 최소 표본 수를 낮추지 않는다. 결정론적 재생 통과와 경험적 모델의 보정/독립성은 별개다.

### 8. 현재 owner 연결과 출력

현재 입력은 기존 Collector snapshot·계좌 borrow·첫 BUY 비용 proof를 그대로 재사용한다. 여기에 동결 owner의 **비밀 없는 정책 사본**을 같은 owner epoch에서 한 번 복사한다. 새 계좌 조회/DB 읽기는 없다.

구체적 최소 연결은 기존 runner에 `position_study_policy(observation, runner_state)` 같은 좁은 읽기 메서드를 두고 supervisor의 기존 enrichment에서 호출하는 것이다. 사본은 account/run/binding·원래 account/Risk/state ref, 5개 Risk limits, 동결 unfilled 정책 전체 및 등록 시 계산한 policy implementation ref를 포함한다. 같은 owner/상태 epoch가 전후 일치해야 한다. credential ID/키는 필요 없으며 내보내지 않는다. 기존 observation subtype에 optional 사본을 추가하고, 기존 `native-position-runner-state/v1`의 strict 내용/ID를 바꾸지 않는다. 별도 owner/collector/task를 생성하지 않는다.

v4의 현재 적용은 기존 정확 셀·목표·규모·비용·계좌/Risk·Family·original lease·보호/시계 pending guard에 더해 동결 정책 사본의 정확한 일치를 요구한다. 현재 정책 사본이 없으면 연구 profile 값을 복사해 현재 값으로 삼지 않고 `current_native_policy_observation_unavailable`로 반환한다. native policy id·source hash·일일 손익/현재 초기 근거·allow-supplied/retry 설정이 다르면 범위 밖이다.

출력은 다음 세 범위를 구분한다.

- `conditional_shadow`: v4 전체 원본 재생과 현재 exact cell/정책 일치 시 `SUPPORTED_UNDER_NATIVE_POLICY_PAPER_ASSUMPTIONS`, 조건부 paired 값·불확실성·원본 refs·원래 expiry.
- `policy_support`: 실제 사용한 공통 정책과 동결 설정, 지원한 전이, sampled price/ACK 가정, 초기 REDUCE의 연구 개입 및 native action mapping 미검증을 명시.
- `native_execution`: UNVERIFIED, `equivalence_verified=false`. 분봉 close는 틱 중간 손절을 증명하지 않으며, Paper LIMIT 전량/ACK 가정은 브로커 queue·부분 체결·취소를 증명하지 않는다. native 선택의 `EV_UNAVAILABLE/result=None`, `usable_for_native_selection=false`, 주문 OFF를 유지한다.

TTL을 새로 늘리지 않는다. v3와 v4 값/평균을 섞지 않고 모델 교체 시 원래 선택/게시 검증을 거친다. 조회100회 무재계산, 2/8 Shadow의 snapshot/계좌 borrow 공유 및 hot DB0을 유지한다.

### 9. Sol 구현 순서·공통 변경 범위

1. `position_target.py`: 새 v4 study/profile·strict 버전·scope/identity. 기존 v2/v3 직렬화·계약 회귀.
2. `mock_automation_risk.py`와 `order_lifecycle.py`: 위 수치/정책 생성의 최소 순수 부분 공유. native 출력·검증·시각·원장 수명 보존 회귀를 먼저 통과시킨다. 전략/feature 파일은 수정하지 않는다.
3. `research_execution.py`: 기존 엔진 안의 opt-in B 상태/clock 입력·공통 판단 적용·LIMIT/terminal/UNSUPPORTED proof. 기존 LIMIT fill/비용/현금 helper를 재사용한다. 별도 ExecutionEngine/Manager를 추가하지 않는다.
4. `research_evaluation.py`: v4 전체 원본 재생과 완결 pair/terminal 가치·정확 셀, publication strict 분기. 기존 v3 reader를 완화하지 않는다.
5. `scripts/run_research.py`: v4의 실제 opt-in 인자/spec/clock·policy 호출·기존 writer 최소 hook. 공통 소유 파일이므로 이 문서의 delta를 따라 제한적으로 수정한다. fixture 전용 API에서 끝내지 않는다.
6. 기존 runner/supervisor의 읽기 사본과 `market_theme_shadow_inputs.py`/monitor의 v4 current binding. 실제 native 주문/제어·등록 목적은 변경하지 않는다.
7. 기존 연구/원본/runner/Shadow 시험 모듈 안에서 아래 acceptance를 수행하고, 최종 동일 소스 지문·범위별 검증 JSON을 남긴다. 공통 상태·로드맵·CI manifest를 임의로 갱신하지 않는다.

새 계층 없이 기존 연구 호출과 writer/reader 안에 둔다. 필요한 직접 공통 호출은 policy/FIFO 계산이라는 책임 때문에 추가하며, 단순 전달 전용 파일은 만들지 않는다. 검증 중 명확한 필드·함수 선택이나 기존 계약에 맞춘 adapter 세부 구현은 Sol 판단 범위이며 재차 Astra 전환할 사유가 아니다.

### 10. 실행해야 할 acceptance

- 기존 v2/v3의 source ID/값/TTL/기본 엔진 동작 및 current input 회귀. 코드 변경으로 scientific implementation hash가 달라지는 것은 기존 규칙대로 새 원본을 생성하며, 이전 실제 원본을 재표지하지 않는다.
- 2개 이상 사전 선언 거래일의 HOLD/REDUCE 원본을 실제 엔진/기존 writer로 생성하고 readonly reader로 재생. v4 조건부 수치와 cluster SE를 직접 계산한 기대값에 대조한다.
- **양의 보호 사례 필수:** 정상 경로 외에 미체결 없는 손절·일일 손실·시계 청산이 실제 Paper LIMIT/FILL·잔고/현금/값을 바꾸는 원본을 검증한다. 정책 한도를 바꾸면 원본 identity와 결과가 달라져야 한다. 보호 없는 창만 통과시켜 B 완료로 보고하지 않는다.
- LIMIT 미도달→공통 WAIT→이후 fill과 horizon까지 WAIT 두 경우. 후자는 미체결 REDUCE를 전체 pair에 포함하고 목표 달성으로 표시하지 않아야 한다.
- pending 중 보호/취소·expiry, CANCEL/REPRICE, BLOCKED, 부분 체결/UNKNOWN/reconnect, 입력 gap·시각 역전·기한 사이 ACK 공백, REENTER 필요를 각각 원래 실패 사건으로 보존하고 전체 비교 null을 확인한다. 미지원 case 제거/재서명 후 승인도 거절한다.
- close는 손절 미도달인데 low만 손절에 닿은 봉에서 legacy intrabar RISK_FILL이 v4로 유출되지 않아야 한다. 결과의 sampled-input 가정과 native 실행 미검증 표시도 검사한다.
- FIFO 비용 배분/정수화 경계, source/ref/goal/clock/terminal·판단 변조, 사건 누락·중복·순서 변경을 재생에서 거절한다. native wrapper의 ACK/source/scope 검사와 기존 보호 우선 동작은 그대로여야 한다.
- 현재 owner 정책 일치의 양의 Shadow 및 정책/비용/owner/보호/lease 변경 차단. 실행 중 격리 worker의 조건부 v4 수치, 2/8 공유, 조회 무재계산, 주문 OFF를 확인한다. 실제 계좌·앱 reload·배포 검증으로 과장하지 않는다.

위 계약으로 세 설계 질문은 해소됐다. 남은 것은 이 계약을 따르는 구현·회귀·현재 연결 검증이므로 **GPT-6.1 Sol High로 하향 handoff**한다. Astra에서 실행 코드나 시험을 이어서 변경하지 않는다.

## A03 B 구현 — v4 원본·현재 정책 연결

확정된 B 계약을 기존 엔진/원장/reader/Shadow 경로에 구현했다. 작업 시작 시 현재 실행 기록에서 `gpt-6.1-sol/high`를 확인했고 구현·원인 축소·검증 단계에서 같은 모델로 진행했다. 새 아키텍처 판단이나 모델 전환은 필요하지 않았다. HEAD는 `50a70c9ce6cdf20915fde27f2d1f47becb82eadf`, 로컬 미커밋이다.

### 구현 결과와 지원 범위

- v4 study/profile/case/cohort/publication을 v2/v3와 분리했다. 초기 REDUCE는 원래 감소 수량·잔여 목표의 명시 연구 LIMIT 개입이다. 이후 공통 Risk/Fraction FIFO/손절·세션 시계·목적별 미체결 판단이 실제 Paper 주문·체결·현금/잔여 보유에 반영된다. 기존 v2/v3의 intrabar RISK_FILL은 v4 미래 경로에 적용하지 않는다.
- 같은 날·단일 보유·최초 BUY 한 건·시작 미체결 없음·60~3,600초가 첫 프로파일이다. 일반 재호가 EV/취소 retry는 OFF다. 정상 LIMIT WAIT와 전량 fill, 미체결 없는 보호/시계 청산, terminal의 정상 미체결 노출을 기록한다. 취소 ACK·부분 체결·UNKNOWN·재진입·자료/정책 공백은 원래 판단과 마지막 상태를 남기고 전체 pair 비교를 차단한다. 미지원 case를 빼서 평균을 내지 않는다.
- `execute_research`의 opt-in 경로에서도 v4를 생성한다. 각 case의 거래일은 독립 계좌 episode로 고정하며 다른 거래일 입력은 warmup 용도로만 사용한다. 이전 날짜의 모의 매매 손익이 첫 진입 실험에 섞이지 않게 했다. prepared 입력 전체를 256 KiB 한도 안에서 원본 spec에 동결하고 해시·전체 revision 목록을 검증한다. 큰 입력을 조용히 잘라내지 않는다. 기존 게시 4 MiB/사건 예산도 보존한다.
- reader가 동결 입력·시작 상태로 같은 엔진과 Family·공통 정책을 다시 실행하여 모든 정책·시계·체결 사건과 terminal 수량/현금/비용을 대조한다. 처음 BUY의 실제 입력 분봉/시각/가용 시각/가격/비용과 시작 미해결 주문도 대조한다. producer가 제시한 통계·결정 hash만 확인하는 경로가 아니다.
- 기존 Collector RAM/첫 BUY 원장·비용 proof/계좌 borrow를 재사용하고, 기존 runner가 현재 owner epoch에 묶인 비밀 없는 동결 정책 사본을 제공한다. 정책 값과 canonical 정책 ID·구현 지문, 원래 account/Risk/state 참조가 같아야 한다. 소스 파일을 읽을 수 없으면 정책 관측만 미지원으로 두며 기존 native runner 시작을 막지 않는다.
- 실행 중인 격리 Shadow의 v4 결과는 `SUPPORTED_UNDER_NATIVE_POLICY_PAPER_ASSUMPTIONS`다. 원래 native R4 결과는 `EV_UNAVAILABLE/result=None`, native 실행 동등성 false, 주문 OFF다. 연구의 REDUCE를 native EXIT/목표0으로 바꾸거나 현재 native REDUCE 주문 경로가 구현됐다고 표시하지 않는다. 틱 중간 손절·브로커 queue/ACK/부분 체결을 분봉으로 증명하지 않는다.

### 변경 파일과 연결 비용

| 기존 파일 | 이번 B 책임 |
|---|---|
| `application/position_target.py` | 별도 v4 명세·정책 지문·관측 subtype. v2/v3 직렬화에 기본 profile 필드를 섞지 않음 |
| `application/mock_automation_risk.py` | 기존 FIFO/비용·보호·가격 threshold의 수치 부분 공유. native 출처/scope/ACK/receipt 검증과 저장 책임 유지 |
| `application/order_lifecycle.py` | 원래 LIMIT anchor/deadline의 순수 정책 생성 공유 |
| `application/research_execution.py` | 기존 Paper 안의 선택 v4 상태·clock·공통 판단·LIMIT/terminal/미지원 proof |
| `application/research_evaluation.py` | v4 전체 재생·가치/짝/셀 검증·엄격한 현재 정책 ID와 시작 체결 가용 시각 검사 |
| `scripts/run_research.py` | 실제 opt-in 생산·입력 동결·독립 거래일 episode·기존 writer 호출 |
| `central_server/mock_automation_runner.py`, `mock_automation_supervisor.py` | 동결 정책 사본의 같은 owner epoch 확인. 기존 native 목적 등록/주문 제어는 유지 |
| `central_server/market_theme_shadow_inputs.py`, `market_theme_shadow_monitor.py` | 기존 단일 입력 조회의 v4 선택·현재 정책 바인딩·조건부 결과 |
| `tests/unit/test_position_study_publication.py` | 기존 fixture/모듈에 v4 원본·실제 연구 진입점·정책/시각 변조·실행 중 Shadow 검사 추가 |

원본 생성·재생의 핵심 소비 파일은 기존 명세/진입점/엔진/repository/evaluation 5개에서 공통 Risk/미체결/시계 3개를 더 읽는 8개가 된다. 실행 경로의 한 예는 `run_research → process_bar → _native_bar → _native_assess → _native_unfilled → evaluate_unfilled_order_management_shadow`다. 기존 fill/현금 경로보다 판단 단계가 추가됐지만, 실제 독립 clock·pending 상태와 기존 정책을 재사용하는 책임이며 전달 전용 계층이 아니다. 새 Manager/엔진/owner/Collector/DB writer/table/task/시험 모듈은 없다. 앞선 현재 입력 단계의 분봉·Collector·계좌 변경도 그대로 포함하며 B에서 저장·수집을 다시 만들지 않았다.

### 검증 기록과 실패 구분

첫 관련 회귀는 **313건 / 408.547초 / 실패·오류·skip 0**, 변경 Python 16개 AST/compile·실행 전후 동일 LF 지문으로 통과했다. [원본/공통 native/연구/계좌·Shadow 회귀](r4-native-policy-verification.json). 이 보고서는 뒤의 정책 ID/시작 체결 시각 보강 전 지문이며 최종 소스 전체 검사로 재표지하지 않는다.

마지막 보강은 `research_evaluation.py`와 기존 study 시험 파일뿐이다. 정책의 숫자 값이 같아도 canonical ID가 다른 원본은 현재 정책과 같다고 승인하지 않는다. 또한 시작 BUY의 received_at을 case cut 뒤로 옮긴 원본이 통과하는 현상을 직접 재현한 뒤, 원래 분봉·fill 가용 시각·가격/비용·미해결 주문을 검증하도록 최소 수정했다. 위 두 보강에 직접 영향받는 NativePolicyStudy/CurrentStudyRam/ConditionalStudy 묶음의 최종 실행·16파일 지문은 [최종 현재 정책/시작 체결 검증](r4-native-policy-current-verification.json)에 따로 남긴다. 중간 정책 ID 단독 보강 기록은 `r4-native-policy-policy-id-verification.json`이다. 중복 검사 건수를 합산하지 않는다.

HEAD의 원래 Risk 코드를 메모리에 읽어 현재 함수와 고정 입력으로 직접 대조했다. Risk snapshot66·보호396·손절396, **858항목의 전체 출력/ID/사유가 일치**했다. DB 읽기·브로커 호출은 0이다. [native 출력 대조](r4-native-policy-differential.json). 이 대조가 실제 브로커 수락이나 미지원 취소/부분 체결 검증을 뜻하지는 않는다.

새 원본에서는 일일 손실 한도가 달라지면 policy/source ID와 실제 LIMIT 청산·가치가 달라지는 것을 확인했다. Fraction FIFO 반올림(-21.8→-22)으로 보호가 발생하는 경계, 양의 sampled 손절/독립 시계 청산, LIMIT 미도달과 정상 terminal WAIT, pending 보호/주문 만료/재진입의 전체 비교 차단, partial/UNKNOWN/Family 입력 누락·미래 시작 체결과 미해결 시작 주문 변조를 포함한다. 실행 중 격리 8개 Shadow는 snapshot1/계좌 borrow1/hot SQLite0으로 조건부 값을 공유하고, 조회100회가 재계산·계좌 재조회하지 않았다. owner 정책 변경은 조건부 결과를 차단하며 원래 lease·보호 권한·주문 OFF를 유지한다.

초기 실패는 시험 import 경로 누락, fixture의 분봉 index와 실제 발생시각 차이, 등록된 손절/세션 정책과 다른 기대값, 차단된 cohort를 publication으로 만들려던 fixture 오류, native 실행 미검증 필드의 기존 형식과 다른 기대, 비용의 floor/반올림 경계 설정 및 HOLD source에서 REDUCE 주문을 찾던 fixture 오류로 구분했다. 원본 이벤트·기존 guard·실제 비용 함수를 확인해 고쳤고 publication/Risk 검증을 완화하지 않았다. 별도 native 출력 대조의 첫 실패는 Windows cp949/UTF-8 디코딩이 섞인 임시 대조 도구 문제였으며 UTF-8과 명시적인 Unicode side로 바로잡아 재대조했다. 시작 체결의 미래 확인은 실제 reader 경계 결함으로 재현·최소 수정·같은 변조 회귀를 추가한 경우다.

검사는 사용자 AppData의 `kiwoom-regression/r4-b-*`에 격리했다. 실패 실행을 통과로 재분류하지 않고, 성공 프로세스 종료 후 빈 소유 임시 경로만 정리한다. 전체 Windows all-local/외부 CI/PG/NAS/실제 모의계좌·장중 수락은 이번 범위가 아니다. 운영 DB·원본 data·.env·GUI 앱 reload·실제 계좌 전송·커밋/push/병합/배포는 수행하지 않았다. 공통 CURRENT_STATUS/OPEN_ITEMS/MODULE_MAP/로드맵/CI manifest는 수정하지 않았고 이 문서가 통합 delta다.

### 이 채팅의 완료 경계

최종 보강 후 **21건 / 84.950초 / 실패·오류·skip 0**, Python16파일 AST/compile과 실행 전후 동일 LF 지문으로 통과했다. 앞선 313건 보고서와 달라진 소스는 `research_evaluation.py`/기존 study 시험 2개뿐이며, 해당 경계와 실행 중 현재 owner·v3/v4 회귀를 재확인했다. 최종 기록을 만든 뒤 실행 코드·시험을 더 수정하지 않았다. `git diff --check`도 통과했다. 두 묶음은 겹치므로 고유334건으로 합산하지 않는다.

**이 채팅에 맡긴 A03 현재 입력 연결과 첫 B 프로파일의 구현·로컬 검증은 여기서 종료한다.** 지원되는 원본·현재 상태에서는 조건부 수치가 나오며, 지원 밖 전이/정책·시각·owner·비용·원래 lease 불일치는 차단한다. 실제 운영 중인 사용자 앱/NAS의 코드 reload·현재 계좌 양의 결과를 확인한 것은 아니다. native REDUCE 실제 주문 매핑·취소/부분 체결·브로커 실행 수락을 이번 구현으로 승인하지 않는다. 후속 통합 담당은 A04/A05와 운영 반영/실제 모의계좌 수락을 기존 순서대로 진행한다. 이 첫 프로파일 종료 뒤 별도 기능 확장을 자동 추가하지 않는다. 모델 라우팅 변경 없음.

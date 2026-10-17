# ② Trading·타점: 주도주 돌파 방향과 기존 RAM 비교 실험

## 2026-10-16 00종합에 명시된 가격 기준선 연결 (로컬)

사용자 요청에 따라 [00종합 §6·7·10](../archive/2026-09-22/references/00_전체통합_AI_Trading_Intelligence.md)의 `prior_window_high`, 현재 구간 제외, 실제 가격 교차를 기존 `leader_breakout/v1` 관측·분봉 판단 연구·기록·API·Windows 화면에 연결했다. 주도주 선정이나 완성된 진입 전략으로 승격한 것은 아니다.

- 정책의 선택 `reference_level`은 `type=prior_window_high`, `exclude_current_interval=true`, `window_seconds=null`이다. 실시간 관측 방식은 `observed_price_cross`, 분봉 연구 방식은 `completed_minute_close_cross`로 명시한다. 직전5봉 등 숫자 기본값을 넣지 않는다. 기준 구간·신선도·확인·대금·구조 무효화·하드 위험 및 주도주 선정 근거는 미정 상태를 표시한다. 기존 실행 계약의 buffer/TTL을 00종합에 정해진 추가 필수 조건처럼 표시하지 않는다.
- 현재 가격과 교차는 승인된 KRX 0B에서 받는다. 가격 구조의 기준 고가는 필요한 기존 완료 분봉을 사용하며 현재 구간을 제외한다. 명시한 창이 완전한 분 단위일 때만 native 분봉으로 계산하고, null/부분 분·누락 자료를 임의 보간하거나 반올림하지 않는다. 실시간 가용시각을 지키며 연구는 최종 query 보완 분봉 우선과 원래 수신 근거 보존을 따른다. 이 구현의 연구 입력 해상도는1분이다.
- 기존 CandidateMonitor worker가 native 분봉 reader와 분/완료 commit cache를 소유한다. 0B callback에는 DB 읽기가 없다. 최대200종목의 이전/현재 가격 각2개와 압축된 기준선 근거만 보존하고, 전체0B 스트림 복제·초봉 생산/저장·새 thread/수집기/스키마는 없다. 창 null이면 분봉 조회도 하지 않는다. source 단절·중단·역순 입력은 교차 연결을 끊는다.
- 주도주 context 없이도 가격 관측 사실은 기록한다. TOP20 순위/H2 점수로 주도주 적격을 만들지 않으며 현재 가용한 명시 context가 있을 때만 그 사실을 표시한다. 관측은 기존 shadow decision JSON에 부가하고, 별도 기준선 표에 가격·고가·교차·미정 사유를 보여준다. 후보 ENTER/알림과 구분하며 새 프로필의 `document_baseline_observation_only`는 실행 준비를 승인하지 않는다. 체결·수량·손익을 만들지 않는다. 기존 명시 v1 비교 정책은 원래 해석/hash와 보호 경계를 유지한다.

검증: 첫447건의 오래된 빈 초안 기대값 실패3건을 끝까지 수집한 뒤 보완했고, 연결8모듈449건과 최종 표시·00 미정 항목 관련126건이 실패/오류/skip0으로 통과했다. 각 실행 중 제품/대상 시험 지문 변경0을 확인했다. 현재 Collector 0B→설정 교체→후보 API·기존 저장, 고정 분봉 요청→연구 실행→결과 표시, 종료/캐시/200종목 한도를 확인했다. 실제 Qt 화면도 격리 합성 자료로 렌더링했다. 예시의120초는 명시한 시험 가설이며 제품 기본값이 아니다. 증거는 `data/diagnostics/second-trade-transition-20261016/document-breakout-acceptance.json` 및 `document-breakout-*.json/log/png`다. 현재 로컬 미커밋·미배포이며 all-local/CI·NAS/장중 수락, 미정 매매 규칙의 확정은 남는다. 운영 DB·보존 자료·실계좌·주문 송신은 건드리지 않았다.

## 2026-10-16 자동매매·연구 캠페인·Windows 연결 구현

로컬 소스에서 `leader_breakout/v1`을 기존 Family/모의 Runner/연구 campaign/독립 final·후보 게시에 연결했다. 아래 설계 인계 절은 이 구현의 계약이며 이전 ‘후보·단발 판단만 연결’ 설명은 당시 기록이다. 운용 시작·배포와는 별개다.

- 실시간: Collector의 승인된 KRX 0B 원문→app listener→기존 Supervisor→계좌별 Runner의 bounded FIFO→공통 kernel→기존 recovery/Risk/lease/승인/dispatch. Shadow를 꺼도 자동운용 입력을 끊지 않는다. 분봉·초봉 DB polling이나 Shadow 후보를 주문 신호로 쓰지 않는다.
- 명시 `leader_breakout_execution/v1`은 수량/자본/LIMIT/세션 처리/전량 청산/위험 문서를 검증하며 숫자 기본값은 없다. 규칙/context/실행 계약 hash, 운영 설정 revision·정책 지문·0B 승인/원문을 보존한다. 주문 접수 트랜잭션에서도 증거·현재 근거·시각·수량·가격·방향을 재검증한다.
- 포지션 근거를 진입 intent에 먼저 보존하고 실제 FILL만 포지션을 연다. 재시작 시 고정 진입 context/절대 하드 경계를 복구한다. 진입 근거 만료/교체가 보유 보호를 해제하지 않는다. `leader_absolute_stop/v1` proof는 기존 native 보호에 연결하고 구형 bps proof는 유지한다. 미정 ADD/REDUCE/REENTER는 허용하지 않는다.
- 연구: 최종 query 보완 분봉 우선, 원래 receipt/source/value_kind 보존, 완료 종가 분석 cut. 필요한 대금 창과 관측 상태만 유지하며 결측을0으로 대체하지 않는다. 성과는 명시 체결/비용 계약과 다음 거래 가능 봉 시가 모의를 사용한다. 고저 내부 순서·가짜0B 틱·숨은 목표/보유시간/cooldown은 만들지 않는다. 명시 세션 종료도 종가 판단→다음 봉 실행을 따른다.
- 판단 campaign `leader_breakout_decision_campaign/v1`은 유한 정책 목록을 기존 작업/lease/취소/재개에 저장한다. null 정책은 BLOCKED 사유를 수집하고 PnL/성과 후보를 만들지 않는다. 성과 campaign은 기존 limited search에서 새 Family와 명시 탐색 범위·목표·평가·실행 계약을 사용한다. 무관한 rank ablation은 거절한다.
- `final_candidate/v2`는 규칙·실행 계약을 묶고 연구 contexts를 현재 주문 권한으로 복사하지 않는다. 구형 v1 해석/hash는 유지한다. 독립 final/OOS·eligibility·실제0B Shadow·forward/admission을 거치며 미정 기준은 차단한다.
- Windows 연구 정책 수·background 등록/복원/예산/결과, 후보 게시·자동운용 안내를 수정했다. 성과 계약 미정을 필드별로 표시한다. 새 Runtime/Manager/수집기/주문 owner/DB 스키마는 추가하지 않았다.

검증 원장은 `data/diagnostics/leader-breakout-20261016/execution-campaign/`다. 최종 인접306건(`scope-final`)과 새 돌파71+서버API89=160건(`final-acceptance`)은 실패/오류/skip0이며 제품/관련 스크립트/시험·API 기준의 범위 지문413/424파일이 실행 전후 같다. 프로세스 종료·임시 자료 정리도 확인했다. 이전1,081건은 시험이 통과했지만 별도 역사 뉴스3파일 동시 변경으로 전체 저장소 동일 소스 수락에서 제외한다. 오류 수집·보완·재검사와 원본 API 기준을 보존했다. 로컬 build는 `2026.10.16-leader-execution-campaign-v3`이며 all-local·PG/NAS·장중 수락을 뜻하지 않는다. 주도주/구조 producer와 미정 조건은 미정이다. 계좌 RUN/주문 송신·배포·기존 DB 변환은 하지 않았다. 앱 전체 초봉 전환은 [별도 미완료](second-trade-dependencies.md)다.

## 2026-10-16 자동매매·캠페인 연결 계약 결정 — 구현 인계

상태: Astra High에서 현재 호출 경로를 대조해 연결 구조를 결정했다. **아래는 후속 구현 계약이며 구현·테스트·운영 전환 완료 기록이 아니다.** 사용자 요청은 새 돌파를 자동매매와 연구 캠페인에 실제 연결하되 실시간은 0B, 연구는 확정·보완 분봉으로 사용하고 미정 조건은 채우지 않는 것이다. 앞서 완료한 후보/단발 판단 연구에서 멈추지 않고 아래 전체 경로를 구현한다.

### 결정과 선택 근거

- 새 `leader_breakout/v1`을 기존 Family 선택 경계에 정식 등록하되 **판단 능력과 실행·성과 능력을 명시적으로 구분**한다. 기존 Family의 `evaluate_bar`/`StrategyEvaluation`에 맞추려고 수량, 손절률, 목표, 최대 보유시간, cooldown을 만들어 넣지 않는다.
- 기존 `ResearchRepository`의 작업·campaign lease/claim/heartbeat/취소/복구와 `MockAutomationSupervisor`/`MockAutomationRunner`의 계좌·주문 소유권을 재사용한다. 새 주문 runner, 수집기, scheduler, 범용 plugin framework를 만들지 않는다. 순수 adapter와 형식 검증은 기존 책임 파일에 둔다.
- 구형 `limited_search/v2`와 `final_candidate/v1`, 과거 Family config/state/hash는 그대로 해석한다. 새 JSON 의미가 필요한 경계에는 **명시적인 새 version**을 사용한다. 종전 자료를 새 전략으로 재표지하거나 원문을 rewrite하지 않는다.
- 결과적으로 **미정인 정책도 판단 연구 campaign에서 차단 사유를 수집**할 수 있다. 반면 성과 연구·후보 게시·자동주문은 각각 필요한 계약이 모두 명시되고 기존 검증/승인이 통과해야 실행된다. 단순 BLOCKED stub만 연결하고 자동매매 완료라고 보고해서는 안 된다. 명시적 fixture로 실제 판단→모의 주문 경계 및 연구→성과→게시를 끝까지 검증해야 한다.

### 1. 규칙·관측 근거·실행 계약

`application/leader_breakout.py`의 순수 판단을 정본으로 유지한다. `research_families.py`는 새 Family의 strict parser, 0B/1m 입력 능력, 판단/성과 지원 여부와 adapter를 등록한다. 기존 definition의 callable 타입을 모든 Family가 무조건 같은 숫자 config를 갖는 계약으로 사용하지 않는다. dispatch는 Family ID/명시 능력을 기준으로 하고 속성 오류를 잡아 구형 전략으로 재시도하지 않는다.

| 문서/상태 | 역할과 필수 조건 |
| --- | --- |
| 규칙 정책 | 기존 `LeaderBreakoutPolicy`의 명시 규칙들. null을 기본값으로 변환하지 않는다. 0B와 1m 확인·대금 기준은 각 mode별로 검증한다 |
| context 근거 | 종목별 주도주/구조 판단, 원래 ref, 가용시각/만료, 기준가격·무효화·하드 경계. 연구 자료와 현재 실시간 자료는 각각 보존한다. 선정/구조 producer는 임의 구현하지 않으며 명시 입력만 허용한다 |
| 실행 계약 | 별도 version을 가진 엄격한 typed document. sizing/자본 한도, 주문 방식, session/장 종료 처리, 위험 profile의 실제 문서와 hash, 청산 수량 범위를 명시한다. `hard_risk_profile_ref` 문자열만 있는 것은 실행 준비 완료가 아니다. 계약 전체 또는 필수 항목 null이면 해당 실행은 BLOCKED다 |
| 연구 실행 모델 | 기존 검증된 SimulationExecutionConfig/비용 출처를 명시적으로 선택한다. 새 Family의 청산 관측은 완료 분봉 종가라는 것을 별도로 명시한다. null이면 판단 연구만 가능하다. 기존 수치 default나 저가/고가 내부 순서를 자동 적용하지 않는다 |
| 관측 state | 교차·확인 횟수·episode·source generation. 계좌 포지션과 분리하며 신호로 체결 상태를 만들지 않는다 |
| 포지션 근거 | 승인된 진입 의도와 연결된 규칙/실행 hash·진입 context·하드 경계. 주문 제출 전 영속적으로 연결하고 실제 FILL 후 포지션에 귀속한다. 수량/평균 진입가는 실제 FILL·계좌 대조가 소유한다 |

새 실행 계약의 sizing에는 현재 구현에서 지원하는 **명시적** 방식만 허용한다. 고정 수량 방식이 선택됐다면 수량/자본 한도까지 사용자가 제공해야 하며 1주나 임의 비중을 기본으로 넣지 않는다. 지원하지 않는 방식과 ADD/REDUCE/REENTER는 해당 행동을 차단하고 사유를 표시한다. 최초 진입을 지원했다는 이유로 재진입·추가매수도 승인된 것으로 보지 않는다. 기존 목표수익률·보유시간·cooldown은 새 정책의 숨은 조건으로 사용하지 않는다.

현재 `policy.fingerprint`는 contexts까지 포함한다. 이 기존 지문의 의미를 바꾸지 않는다. 새 연결용 문서에서만 `rules_hash`(contexts를 제외한 canonical 규칙), `context_hash`(실제 사용 근거), `execution_contract_hash`, 입력/구현 hash를 구분한다. hash마다 version/domain을 고정한다. 연구에 사용한 과거 contexts는 연구 증거에 남기며 현재 0B 주문 context로 복사하지 않는다.

실시간 context는 기존 인증된 운영 설정 경로로 명시 입력하고 revision·전체 지문을 기록한다. 새 package의 규칙에 선언된 leader/structure rule ref와 일치해야 한다. 아직 producer가 없으면 `context_unavailable`로 차단하며 TOP20 순위나 직전5봉 고가를 대입하지 않는다. 규칙 변경은 새 승인 대상이고, 같은 규칙의 새 관측 근거는 원문/hash/가용시각을 주문 판단마다 검증한다. 과거 Shadow 근거는 검증 증거일 뿐 오늘 주문할 수 있는 유효 신호가 아니다.

### 2. 실시간 자동매매 연결

실제 경로는 `Collector._publish_parsed`의 승인된 KRX 0B → app의 기존 listener 연결 → 현재 Supervisor → 계좌별 기존 Runner → 순수 kernel → 기존 recovery/Risk/admission/dispatch다. CandidateMonitor의 persisted 후보를 polling해 주문하거나 분봉/초봉을 중간 신호로 사용하지 않는다. Shadow와 Runner는 **동일 kernel·규칙**을 쓰며 각자의 관측 상태와 실행 권한을 소유한다.

1. listener는 실제 payload와 수신 KST·원래 source/generation/승인 상태를 전달한다. price-only callback에 사라진 대금·시각 정보를 추정해 붙이지 않는다. Shadow enable/disable이 승인된 자동운용의 입력 수명을 대신 소유하지 않는다.
2. callback에서 DB/REST/주문을 기다리지 않는다. 기존 runner wake/iteration lock 안에서 순서대로 처리할 bounded FIFO에 전달한다. 최신가격 하나로 합쳐 교차를 잃지 않는다. overflow/재연결/미승인 source/역순은 관측 연속성을 끊고 신규 진입을 차단한다. 누락한 틱을 분봉/초봉으로 복구하지 않는다.
3. dequeue 때 공통 server clock으로 신선도를 다시 검사한다. 수신 순간이 신선했다고 지연된 주문까지 유효하다고 보지 않는다. 같은 envelope 재전달과 재시작 후 이미 수락한 episode/intent의 중복 주문을 기존 영속 claim·주문 ID 경계에서 막는다.
4. 승인 규칙, 현재 context, 실행 계약, 실제 포지션/Risk, 계좌 binding/generation을 같은 판단 cut에 묶는다. 신규 진입 조건과 이미 가진 포지션의 보호 조건을 구분한다. 주도주 근거가 만료되거나 대금이 줄어도 기존 하드 위험 처리를 해제하지 않는다.
5. hard stop은 진입 근거에 고정된 **절대 가격**으로 검증한다. `mock_automation_risk.py`의 기존 bps proof를 가짜 bps로 환산하지 않고 새 discriminator/proof를 추가한다. 청산 수량은 소유한 실제 잔고에서 검증하며 계좌 revision·symbol·price/source·규칙/position-context hash를 proof에 묶는다. 구형 bps proof 검증은 유지한다.
6. 진입 context가 만료/교체되어도 실제 보유 포지션의 고정 하드 경계는 사라지지 않는다. 보호에 필요한 freshness/가격은 계속 검사한다. 새 context가 하드 경계를 느슨하게 바꾸지 못하며, 명시 승인된 조정 정책이 없으면 진입 당시 근거를 유지한다. 포지션 근거를 복구하지 못하면 신규 주문을 차단하고 기존 reconcile/protection 상태로 보낸다.
7. partial FILL, ACK 유실, 취소/정정 대기, 보호 청산, STOP/재시작은 기존 owner와 원장을 따른다. 신호만으로 open/flat을 바꾸거나 active intent를 건너뛰지 않는다. pending 진입 중 새 신호는 중복 주문이 아니며 pending 청산 중 EXIT는 기존 보호·취소 절차로 들어간다.

현재 제품의 자동 실행 소비자는 MockAutomationRunner다. 새 전략 연결 구현/회귀와 실제 모의 운용 시작은 별개이며 이 요청으로 계좌의 RUN 상태를 바꾸지 않는다. 실계좌 조회 owner가 있다는 이유로 실전 자동주문 경로가 이미 존재한다고 보고하거나 신규 실전 송신 경로를 추가하지 않는다. 최종 보고에는 실제 지원 환경을 명시한다.

### 3. 연구·성과와 campaign

연구 입력 정본은 현재 `evaluate_leader_breakout_minutes`의 최종 query 우선·완료 분봉 선택이다. 이를 단발/성과/campaign에서 공유하는 순수 iterator 또는 step adapter로 정리한다. 기존 `research_replay`의 actual-only/as-available 필터를 새 Family에 그대로 적용해 보완 분봉을 누락시키지 않는다. 원래 source/receipt/revision과 `final_completed_minute` 분석 기준을 함께 기록한다. 분봉의 분석 cut을 쓴다는 이유로 뉴스/주도주 context의 가용시각도 과거로 옮기지는 않는다.

- **판단 campaign:** `leader_breakout_decision_campaign/v1` typed spec를 `research_queue.py`에 둔다. 명시된 유한 policy 문서 목록, frozen dataset id/hash, session, 구현 hash, resource budget을 식별자에 포함한다. null 정책도 허용하고 판단·차단 사유를 수집한다. 자동 수익 순위/최적 후보/승인 가능한 성과는 만들지 않는다.
- **성과 campaign:** 기존 `ExperimentSpec`/limited search가 등록된 새 Family를 실행하도록 연결한다. 명시 execution/비용·평가/split/objective/허용 탐색값을 모두 검증한 경우에만 수행한다. 임의 탐색 범위, null을 숫자로 채우는 가설, 오래된 baseline config 상속은 금지한다. 부족한 조건은 실행 전 structured BLOCKED로 남긴다.
- 판단 spec와 기존 성과 spec의 dispatch는 version으로 한다. 기존 campaign/search jobs·events·결과 JSON 저장과 lease를 재사용한다. `_campaign_job_spec`, `_campaign_source_spec`, `_campaign_completion_reason`, budget revision, enqueue, worker 재시작/취소/복구, 새 frozen input 수락 모두 같은 typed decode를 사용한다. 모르는 version은 실패하고 구형으로 fallback하지 않는다. 이 목적만으로 새 DB/table/별도 scheduler는 필요하지 않다.
- 판단 작업의 프로세스 완료와 전략 준비 상태를 구분한다. `completed` 작업에 `readiness=BLOCKED`, missing 목록과 판단 결과를 저장할 수 있다. `net_pnl` 등 미산출 성과는 null/없음이며 0원 성공 실험이나 ELIGIBLE 카드로 넣지 않는다. 완료 판정은 실제 완료한 policy 문서 수와 결과 지문으로 한다. `generate_trials(ExperimentSpec)`를 판단 spec에 호출하지 않는다.
- 자동 가설/feedback은 새 Family의 **명시된 숫자 탐색값만** 변경한다. nested policy가 있으면 등록된 경로를 통해 변경하고 context, 위험, sizing, provenance를 탐색 변수로 삼지 않는다. 미정 항목을 생성해 정책 완성을 가장하지 않는다. 명시 탐색값이 없으면 고정 정책 평가만 하며 자동 가설은 `unconfigured`로 표시한다.
- 성과 실행은 기존 PaperExecutionEngine의 현금·비용·부분 체결 원장을 재사용하되 새 Family에는 구형 `_apply_intrabar_risk`의 bps target/stop/max_hold/cooldown을 호출하지 않는다. close 관측 → kernel 제안 → 명시한 체결 모델의 다음 허용 분봉 순서다. 같은 분의 close로 판단해 그 분 open/low에 체결시키지 않는다. 분 내부의 순서/0B/초봉을 합성하지 않는다.
- 연구 포지션도 simulated FILL만 소유한다. close 기준 EXIT/HOLD를 실제 simulated position과 고정 진입 근거로 평가하고 데이터 gap/세션/청산 미완료를 보고한다. close 관측 결과를 실시간 하드스톱 체결 보장으로 해석하지 않는다. 기존 session 종료·미체결 처리도 사용자가 명시한 계약 범위에서만 적용한다.
- 새 결과가 기존 독립 평가/재현, cost stress, final holdout, forward/feedback을 통과하도록 Family adapter를 동일하게 연결한다. 판단-only 결과는 이 성과 승격 경로에 넣지 않는다. 기존 성과 eligibility 기준을 낮추거나 가짜 trade/PnL을 채우지 않는다.

### 4. 후보 게시·승인·화면

새 성과 package의 candidate spec는 `final_candidate/v2`로 구분하고 family, canonical 규칙, execution 계약, simulation 모델/비용, session, implementation hash를 봉인한다. 연구 context/data 증거는 final 결과에 연결하고 실제 live context와 혼동하지 않는다. 기존 v1 validator/자료는 유지하며 새 type의 입력/의미는 기존 API에서 명시적으로 검증하고 계약에 기록한다.

`mock_automation_candidate.py` → `mock_automation_specification.py` → admission/forward profile/repository → Supervisor/Runner 전체가 v2를 받아야 한다. Shadow의 quantity=None는 정상 판단 자료이며 1주로 조작하지 않는다. 새 Family의 Shadow 검증은 실제0B input, rules hash, context 원문/hash·유효시각, episode, 단조 시각과 정책을 검증한다. 주문 수량은 별도 승인된 실행 계약에서만 산정한다. 기존 Family의 양수 quantity 검증은 유지한다. v2에서도 OOS/성과 eligibility와 EVALUATED→VALIDATED→SHADOW·binding·계좌·권한·risk·lease 검사는 생략하지 않는다.

Windows에서는 연구 모드/Family 선택, campaign 등록·재개·상태/결과, 허용 가설 입력, candidate 게시와 자동운용 세 화면을 함께 수정한다. 판단 연구/성과 검증/주문 준비 상태를 구분하고 미정 필드와 차단 사유를 보여준다. 기존 비교 정책은 명시 선택·과거 재현으로 남긴다. 저장/복원·서버 적용 revision까지 검증하고 로컬 편집을 실제 승인/적용으로 표시하지 않는다. 구형 선택으로 자동 전환하지 않는다.

### 5. 구현 순서와 완료 검사

1. 기존 파일의 typed config/capability, versioned 규칙·실행/근거 identity와 순수 0B/분봉 step adapter를 연결한다. 기존 canonical 결과/ID가 바뀌지 않는 회귀를 확보한다.
2. 같은 campaign owner에 판단 spec·성과 Family 등록, 완료/취소/재시작·저장·새 입력 수락·UI를 연결한다. null 정책 판단 campaign과 명시 계약 성과 campaign을 둘 다 실제 실행한다.
3. 새 final spec·Shadow 증거·eligibility/forward/admission을 연결하고 0B Runner·position-context/protection proof·기존 dispatch까지 구현한다. 자동 주문은 stub broker로 검증하고 사용자 계좌를 시작하지 않는다.
4. UI→설정/요청→worker/runner→저장/복구→API/UI와 독립 재현·최종 후보 게시까지 회귀한다. 마지막에 현재 문서·API·module map·OPEN_ITEMS를 실제 구현 상태로 갱신한다.

반드시 포함할 검사는 null/미지원 계약 주문0·가짜PnL0, 미래 context 차단, late query 분봉 보존, 종가→다음 봉 체결, 동일 kernel의 mode별 결과/근거, 실제0B 처리 중 minute/second reader 호출0, 잘못된 승인/역순/중복/queue overflow·재연결·stale 대기, context 교체/만료 중 보유 하드 경계, pending/partial fill/ACK 유실/restart 중복 주문0, 위조 Shadow/규칙/위험/계좌 증거 거절, campaign 동일 입력 재등록 no-op·취소/재개·owner fencing, 구형 v1 결과/승인 회귀다.

한 기능을 추적하는 주요 실행 경로는 기존 owner를 유지한다. 새 연결로 추가되는 단계는 규칙/근거/실행 typed 검증과 순수 adapter이며 단순 위임 Manager를 추가하지 않는다. 전수 회귀는 동일 소스로 끝까지 실행해 오류를 모은 뒤 수정한다. 운영 배포/실계좌 자동주문/기존 데이터 변환/시장·테마 전체 초봉 제거는 이 구현 완료와 구분한다.

이 계약의 구조 판단은 완료됐다. 다음은 GPT-6.1 Sol High의 구현·검증 단계다. 현재 요청 전체는 아직 미완료이며, 새 기능의 실행 준비 여부는 실제 테스트와 명시 정책 입력으로만 판정한다.

## 2026-10-16 Windows 선택창과 연결 경로 재대조

앞선 구현은 새 후보/분봉 판단 연결까지였으며 전체 전략·자동주문 교체 완료가 아니었다. Windows에는 서버의 과거 프로필이 그대로 선택되고, 이미 표시한 후보의 현재 상태를 갱신하지 않는 경로가 남아 있었다. 이를 실제 호출과 Qt/HTTP 회귀로 확인했다.

| 연결 | 확인·수정한 현재 경계 |
| --- | --- |
| Windows 진입 | MainWindow→CandidateMonitorDialog. 테스트 앱의 run_test_app_with_data.py는 같은 작업폴더의 SOURCE_ROOT를 쓴다. 이미 생성된 Python 창에는 코드 자동 재로딩이 없다 |
| 선택·설정 | 처음부터 새 leader_breakout/v1 선택·null 초안을 표시한다. 서버가 구형 프로필을 반환해도 구형 수치를 상속하지 않는다. 기존 비교는 저장 프로필이 있을 때 명시 선택한다. 서버 저장 정책/켜짐과 편집 정책을 구분하며 expected_revision을 보존한다. 화면 선택·초안 초기화는 서버를 바꾸지 않는다 |
| 저장→판단 | 기존 PC operations client→인증 router/저장·적용 state→현재 CandidateMonitor 교체 경로다. 이번 HTTP 회귀는 실제 Windows 창이 만든 payload를 보내 Collector0B→kernel→기존 writer/후보 API·교체 fence까지 통과했다. 미정 필수 조건은 BLOCKED이며 기존 Family로 대체하지 않는다 |
| 후보 표시 | 행별 실제 strategy ID로 새0B/기존 비교를 구분한다. 같은 event ID의 상태도 갱신한다. 기존 incremental 전달을 따라잡은 회차에 최근 최대1,000건 상태를 같은 인증 API로 재확인한다. 이 읽기는 원래 cursor·신규 알림을 소비하지 않는다. 범위 밖/실패로 확인 못한 ACTIVE는 현재 미확인으로 표시한다. 비활성화/구형 전환 후 현재 새 정책 owner가 없는 후보도 API가 SUPERSEDED로 투영해 재활성 표시를 막는다. 불변 DB 원장은 보존한다 |
| 시장·테마 탭 | PRICE_ONLY/IMMEDIATE_EXIT/REDUCE_ON_RISK/CONFIRM_THEN_EXIT는 evaluate_entry_thesis의 저장 연구 가설 보유 행동 비교이며 새 진입 정책이 아니다. 선택명·그룹명을 구분하고 새0B 설정으로 이동하는 버튼을 연결했다. 기존 시장/테마 초봉 의존성이 남았다고 표시한다 |
| 분봉 연구 | 명시 mode=leader_breakout→frozen loader→분봉 adapter→같은 kernel→새 결과표다. final 보완 분봉을 사용하며 기존 Paper/두 Family의 손익을 새 결과로 사용하지 않는다 |
| 자동 가설·캠페인·성과 | research_families/search/hypotheses/queue/forward_evaluation/feedback_strategy_revision 및 research_evaluation의 재현은 기존 Family registry를 쓴다. 새 정책은 성과/체결 계약이 미정이므로 등록되지 않았고 자동 fallback도 없다. 이 경로의 새 정책 전환은 미완료다 |
| 주문·체결 | mock_automation_specification의 후보 matching·등록→MockAutomationRunner의 frozen Family 실행은 기존 경로다. 새0B 후보가 strategy/monitor/수량 계약상 기존 주문 증거로 사용되지 않는 것을 검사했다. 후보 게시·자동운용 화면도 새 정책 주문 미연결을 명시한다. 실제 FILL·Risk·보유 수량의 소유자는 보존한다 |
| 독립 옛 함수 | observed_window_high_breakout/v1은 정의/시험만 있고 제품 caller는 없다. 새 주도주 판단으로 연결하지 않는다 |

관련8모듈432건이 모두 통과한 뒤 후보 상태 갱신과 모의운용 화면 경계를 추가 확인했다. 추가3모듈121건의 첫 실행을 끝까지 마쳐 fixture 오류2건(상태 재확인을 신규 페이지로 센 테스트, 필수 계좌 식별자가 없는 화면 fixture)을 수집했다. 보완 후 동일121건은 실패/오류/skip0·종료0·전체 자식 종료 확인·임시 자료 정리로 재통과했다. 기록은 `data/diagnostics/leader-breakout-20261016/connection-audit/{first,final-ui,ui-recheck}.json/log`에 보존한다. 현재 소스 all-local·NAS 수락 결과가 아니다. 사용자 설정/네트워크 없이 실제 Qt 창을 렌더링해 선택창 배치도 확인했다.

이어 비활성화/구형 정책 전환의 후보 투영을 보완한 최종4모듈210건(새 판단·UI49, 후보 알림34, 모의운용 화면38, 서버 API89)이 실패/오류/skip0·native 종료0·프로세스 트리 전체 종료·임시 자료 정리로 통과했다(`api-status-final.json/log`). 같은 HTTP 시나리오에서 UI 저장 payload→실제0B→후보→구조 이탈→새 정책 교체→비활성화→기존 비교 전환을 확인하고 원본 후보 페이지가 불변임을 대조했다. Catalog는 unit462/integration27로 통과했다. 전수 all-local·실제 NAS/장중 수락으로 확대하지 않는다.

새0B 후보·분봉 판단과 실제 자동주문 지원은 다르다. 주도주/구조 근거 producer와 체결·비용·비중 계약은 미완료다. 운영 소스의 SERVER_BUILD는 `2026.10.16-kst-leader-breakout-v2`로 올렸으며 기존 의존성 runtime은 유지한다. NAS 배포·운영 설정 변경·실행 앱 재시작·자료 변환은 수행하지 않았다. 기능 변경 때 전체 연결 확인·동반 수정과 미완료 보고는 [저장소 정본 지침](../../AGENTS.md#기능-변경의-연결-경로-확인동반-수정)에 명시했다.

## 2026-10-16 실제 돌파 로직 구현 — 0B 관측과 분봉 연구

`application/leader_breakout.py`에 새 `leader_breakout/v1` 판단을 구현하고 실제 Collector·기존 CandidateMonitor·후보 API 및 연구 실행기·화면에 연결했다. 원래 완료봉 Family 두 개와 독립 초봉 함수는 과거 비교용으로 보존하며 새 로직에 재사용하지 않는다. 아래 이전 ‘로직 미완료’ 문단은 이 구현 전의 기록이다.

### 판단과 미정 조건

- 주도주 선정법·구조 기준가격을 임의로 계산하지 않는다. `contexts`에 종목, 원래 근거 ref, 근거 가용시각/만료, `leader_eligible`, `leader_rule_ref`, `reference_price`, `structure_rule_ref`, `invalidation_price`, `hard_stop_price`가 필요하다. 해당 cut의 근거가 없으면 BLOCKED다. TOP20 순위·5봉 고가로 대신하지 않는다.
- 이전 관측가격이 명시 이격을 포함한 기준 이하이고 새 가격이 초과일 때 교차를 시작한다. 첫 가격이 이미 높은 것만으로 돌파를 만들지 않는다. `breakout_buffer_bps`, `minimum_confirmation_observations`, `persistence_seconds`가 모두 명시돼야 한다. null을0bps·1회·0초로 간주하지 않는다. 관측 수는 입력 관측 수이며 모든 거래소 개별 체결 횟수가 아니다.
- 신선도·후보 유효시간·최소 대금과 그 기준·구조 이탈 규칙·위험 profile도 명시해야 한다. 기본값은 null이다. 같은 episode는 한 번만 발행하고 source/generation·거래일·context 변경·역순/중복·자료 부족을 구분한다.
- 순수 함수에 실제 보유 여부를 전달한 경우 명시 hard-stop 우선, 구조 이탈이면 EXIT, 구조 유지면 HOLD이며 대금 감소만으로 청산하지 않는다. **현재 live 연결은 주문 없는 진입 후보 관찰**이다. 실제 포지션·체결·Risk owner를 이 함수로 바꾸거나 후보 상태를 보유로 만들지 않았다.
- 손절3%·목표5%·10분 종료·1주·100만원을 넣지 않았다. 후보 수량은 null이고 `order_authorized=false`다. FID20의 초 해상도를 0.2초 확인이나 거래소 전체 틱 순서의 증거로 확대하지 않는다.

### 실제 연결

| 경로 | 현재 구현 |
| --- | --- |
| 실시간 | Collector의 실제0B payload·기존 키움 보정 KST 수신시각·source generation/승인→app의 현재 monitor→`CandidateMonitor.observe_trade`→새 kernel. 초봉 snapshot·분봉 polling·REST/DB 읽기 없음. callback에서 DB를 쓰지 않고 기존 worker가 transition을 기록 |
| 대금 | 명시한 live `0B_session_cumulative`는 FID14 백만원 값을 원으로 변환. 연구의 `completed_minute_window`는 명시 lookback의 완료 분봉 대금을 합산. 서로 같은 창이라고 주장하지 않으며 basis와 설정을 구분 |
| 연구 | 기존 `research_process`의 명시 `mode=leader_breakout`→기존 frozen loader/hash·세션·자원·취소 검사→분봉 adapter→같은 kernel. 최종 query 값 우선, 원래 수신시각/source·대금 추정 여부 보존. 분 종료 cut의 종가만 사용하며 빠진 분을 넘겨 교차하거나 초 안 경로/가짜 틱/체결을 만들지 않음 |
| 기본값 | 새 미지정 설정은 null인 새 정책. 저장된 과거의 암묵적5봉·3%·5%·10분 기본 프로필도 환경에서 명시한 비교 설정이 아니면 새 draft로 해석. 원래 DB 문서는 재작성하지 않음. 명시한 과거 비교 설정·실험은 보존 |
| API/화면 | 기존 인증·revision 충돌·실패 경계 유지. 새 정책 JSON·미정 사유를 편집/표시. 이전 정책/episode 후보는 SUPERSEDED/INVALIDATED로 투영. 새 분봉 결과 표는 가격·판단·사유를 표시하며 손익으로 표시하지 않음 |

[연구 요청 템플릿](../examples/leader-breakout-research-request.json)의 `dataset`, `session_profile`은 실제 자료에 맞게 명시해야 하며 모든 전략 수치는 null이다. 이 mode는 **돌파 판단 연구**다. 체결/비용/비중 규칙이 미정인 상태에서 기존 BUY→SELL 모의로 수익률을 만들지 않는다. 자동 가설·성과 캠페인·최종 후보 게시에는 새 정책의 실행/성과 계약이 아직 없으므로 기존 Family로 자동 대체하지 않는다. result/cancel 파일로 원본 요청·frozen 자료를 덮어쓰는 것도 거부한다.

새 Runtime/Manager/collector/table/주문 owner를 추가하지 않았다. 새 파일은 독립 정책·불변 DTO·0B/분봉 adapter이며 수명/저장은 기존 CandidateMonitor가 담당한다. live 핵심은 Collector→app 현재 owner 참조→CandidateMonitor→순수 판단의4파일, 연구는 기존 실행기/loader→같은 판단 파일이다. scientific hash에 새 파일을 포함하며 과거 결과를 현재 검증으로 재표지하지 않는다.

### 검증과 남은 범위

관련8모듈425건에서424건 통과·고정 Collector fixture의 신규 callback 메서드 누락1건을 확인했다. fixture에 실제 수신 인터페이스를 추가한 뒤 **새 로직37건+서버 API 전체89건=126건이 실패/오류/skip0, 종료0·자식 종료 확인·임시 경로 정리로 재통과**했다(`api-final.json/log`). Catalog도 unit462/integration27로 통과했고 새 시험을 core에 등록하며 순서 지문을 함께 갱신했다. 전수 all-local·NAS/Linux·장중 운용/성능·배포 수락은 이번 검증 범위가 아니다.

최초 관련8모듈412건을 끝까지 실행해 실패3·오류1을 수집했다. 새 기본값을 전제하지 않은 옛 health 시험/계약 fixture를 확인해 v1 fixture만 명시 비교 설정으로 고정하고 새 기본값·원래 저장 문서 보존을 별도 검사한다. API 기준 파일을 다시 쓰지 않았다. 이후236건에서 수집한 옛 monitor mock의 신규 투영 메서드 미설정2사례도 명시 legacy 반환값으로 보완했다. 최초 기록과 최종 관련 검사 결과는 `data/diagnostics/leader-breakout-20261016/`의 각 JSON/log에 보존한다.

주도주 선정·구조 생성의 실제 기준/producer, 새 정책의 체결·비용·비중/성과 검증, Add/Reduce/Reenter의 추가 정의는 미완료다. 다른 기존 시장/테마·배분의 초봉 소비와 집계/저장 중단도 별도로 남는다. 새 돌파는 그 입력을 읽지 않는다. 현재 작업폴더 구현이며 운영 NAS 배포·DB 변경·재시작은 하지 않았다.

## 2026-10-16 사용자 정정 — 연구와 Shadow의 방향

후속 사용자 확정: **실시간 자동매매는 0B 체결 관측, 연구·백테스트는 분봉**을 사용한다. 초 단위 창을 새 필수 입력으로 정하는 제안은 진행하지 않는다. 아래 독립 RAM 함수와 기존 초봉 소비는 과거 구현 기록이며 다음 전략의 기본 경로가 아니다. [현재 연결 전체 조사와 전환 대상](second-trade-dependencies.md).

사용자는 00 AI 인텔리전스가 미완성임을 명시하면서 기존 돌파·눌림 연구와 Shadow가 자신의 주도주 돌파 구상과 다르므로 방향을 수정하라고 요청했다. [00 원문](../archive/2026-09-22/references/00_전체통합_AI_Trading_Intelligence.md)과 [Trading 초안 2·10절](../archive/2026-09-22/references/02_Trading_타점_Intelligence_개발명세.md)은 구상과 미정 항목의 출처이며 완성된 규칙으로 승격하지 않는다. 아래 10/15 구현 기록은 독립 가격 실험의 기록으로 유지한다.

| 구분 | 현재 의미 | 다음 개발의 기준으로 사용할 범위 |
| --- | --- | --- |
| 사용자 구상 | 주도주 후보의 관측·거래·맥락 → 가격 구조 접근·확인 → Entry/Hold/Exit, 이후 Add/Reduce/Reenter | 방향. 구체적인 조건·검증 기준은 아직 미완료 |
| 완료봉 돌파 v1 | 5봉 고가·순위 체류·손절3%/목표5%/10분 등의 기존 기본값을 가진 비교 실험 | 과거 결과 재현과 개별 가설 비교. 사용자 요구·최종 전략으로 사용하지 않음 |
| 눌림 재가속 v1 | 별도 완료봉 비교 Family | 초기 중심 전략으로 확대하지 않음 |
| observed_window_high_breakout/v1 | 명시 설정을 받는 초 단위 가격·대금 독립 함수 | 실행 경로 미연결. 사용자 주도주 판단의 완성본으로 연결하지 않음 |

수정한 것은 연구/Shadow 화면의 비교 실험 표기와 개발 우선순위다. 알고리즘 교체·새 전략 완성·기존 연구 무효화·운영 설정 변경·배포를 수행한 것이 아니다. 기존 Family ID·파라미터·결과는 보존한다. 가격 단독 돌파에 주도주라는 이름만 붙이거나 미정 조건을 기존 기본값으로 채우지 않는다.

남은 순서는 (1) 사용자 구상의 확정/미정 의미와 기존 입력 대응표, (2) 같은 판단 정의를 소비할 연구/Shadow의 현재 경로 대조, (3) 필요한 관측 해상도와 명시 연구 가설, (4) 최소 판단 구현·동일 사례 비교다. 초봉을 사용하지 않겠다는 의향을 반영해 초 단위 창을 필수로 정하지 않는다. 원문 예시 숫자, 모든 지표의 AND, 완료봉 기본값을 사용자 승인 규칙으로 간주하지 않는다.

### 현재 초봉 연결의 소스 확인

- `central_server/realtime_collector.py`: 같은 0B 수신에서 분봉과 `SecondTradeAccumulator`를 각각 갱신하고 `save_second_trade_bars`로 저장한다. 별도 초봉 TR 요청이 아니다. 현재가/거래대금/분봉에 필요한 0B 수신과 파생 초봉 저장은 별도 책임이다.
- `central_server/market_theme_shadow_monitor.py::_snapshot/_attach_ready_market_minutes`와 `market_theme_shadow_inputs.py`: 공통 RAM snapshot의 초 단위 행을 시장 가격·거래대금 보조 관찰과 테마 leadership 입력으로 변환한다. 보존 DB를 읽는 preview/연구 adapter에도 `load_second_trade_bars` 경로가 있다.
- 기존 완료봉 `CandidateMonitor`와 `research_families.py`의 돌파·눌림은 분봉/순위 입력이다. 새 독립 `evaluate_realtime_breakout`은 정의만 있고 현재 실행 소비자는 없다.

후속으로 현재 NAS release/status와 배포 소스도 읽기 전용 대조했다. 초봉 관련 26개 제품·도구 파일의 참조 내용은 로컬과 동일하다. 개별 RAM 선택의 현재 활성 여부는 조회하지 않았다. 초봉 집계·저장 중단·기존 초봉 삭제·소비자 전환은 아직 하지 않았다. 실시간 소비자는 0B, 연구 소비자는 분봉으로 전환한다.

화면 표기 변경의 관련 회귀: 기존 4개 모듈75건이 실패/오류/skip0·native 종료0·자식 종료 확인·임시 경로 정리로 통과했다. `data/diagnostics/trading-direction-20261016/focused-result.json`과 `focused.log`에 보존했다. 전체 회귀·NAS 테스트·배포 결과가 아니다.

작성: 2026-10-15 KST. 공통 기준/현재 HEAD: `50a70c9ce6cdf20915fde27f2d1f47becb82eadf`.
시작 시 변경 없음, 격리 워크트리 `d3aa`. 독립 개발 종료 시 원본 main 변경·커밋·push·배포 없음. 이후 사용자 요청에 따른 원본 반영 상태는 아래와 같다.
최신 실행 기록에서 모델 `gpt-6.1-sol`, 추론 `high`를 확인했으며 모델 전환은 없다.

## 사용자 병합 요청 후 원본 main 작업폴더 반영

2026-10-15 사용자의 “여기껀 합쳐” 요청으로 `C:/Users/pc-1/Documents/ChatGPT/kiwoom-realtime-monitor`의 깨끗한 main 작업폴더에 이 작업의 코드·검사·보고서 3개만 반영했다. 반영 전 HEAD가 공통 기준 `50a70c9ce6cdf20915fde27f2d1f47becb82eadf`이고 기존 코드/검사 내용이 그 기준과 같은지 확인했다. 다른 병렬 워크트리의 파일은 가져오지 않았다.

원본 경로의 실제 Python module/test import를 확인한 뒤 같은 관련 검사 **54건 통과 / 10.487초 / 종료 코드 0**을 확인했다. fixture는 사용자 세션 `LOCALAPPDATA/Temp/kiwoom-regression/realtime-entry-main-integrated-20261015`에 격리했다. 원본 data·운영 DB·계좌·NAS는 사용하지 않았다.

이는 **원본 main 작업폴더 반영이며 Git 커밋/merge commit은 아직 없다**. HEAD는 기준 커밋 그대로다. 기존 공통 Runtime 연결·전체 병렬 통합·all-local·LF source gate·CI·push·배포는 이 반영에 포함하지 않았다. 해당 전체 검증을 완료해야 커밋/push할 수 있다는 기존 규칙은 유지한다.

## 왜 필요한가

완료봉 Family의 연구 기준선을 유지하면서, 검증 대상으로 삼을 장중 후보를 다음 분봉 완료 전에 명시적으로 판단하기 위해 필요하다. 분봉 성과가 초 단위 진입 성과를 검증했다는 가정 없이 기존 0B RAM 입력으로 첫 정책을 따로 정의했다.

## 실제 매매에서 가능해지는 판단

현재 관측가격이 직전 초 단위 창 고가를 넘고 최근 창 거래대금이 명시 문턱 이상인지 판단한다. 결과는 신규 후보, 조건 부족에 따른 대기, 이미 생긴 후보의 가격 구조 무효화, 자료 부족/공백에 따른 차단으로 나뉜다. 후보이지 매수 허가·EV·목표 비중·주문이 아니다. 거래대금 감소만으로 보유 EXIT를 생성하지 않는다.

## 기존 기능과 연결

기존 `SecondTradeAccumulator.observation_snapshot` 행과 H2 `second_trade_points_from_rows` 변환을 재사용한다. 정규 연속매매 시간 판정도 기존 함수를 사용한다. 테마 구성원·H1 시장 점수·H2 대장 점수·EntryThesis를 이 가격 정책의 필수 입력으로 강제하지 않는다.

현재 코드의 연결 범위는 다음과 같다.

| 기존 기능 | 이번 범위 |
|---|---|
| `evaluate_breakout_bar` → `compute_rolling_high_breakout` → 공통 `evaluate_factor_entry_bar` | 변경 없음. 완료봉·순위·보유/청산 계약 유지 |
| `evaluate_pullback_reacceleration_bar` → 기존 눌림 Factor → 같은 공통 평가 | 변경 없음. 실시간 눌림 정책까지 완료했다고 주장하지 않음 |
| Collector → 기존 RAM snapshot → H2/EntryThesis → 공통 Runtime | 읽기 대조만 수행. 기존 source/generation/승인/연속성 메타데이터와 초 관측 행 사용 |
| 새 `evaluate_realtime_breakout` | snapshot 값을 받는 순수 함수까지 구현. Runtime·설정·registry 자동 연결 없음 |

완료봉 경로의 이해 파일 수·호출 깊이는 그대로다. 새 실시간 규칙은 기존 `breakout_strategy.py` 한 파일에 있으며 기존 H2 변환/시각 판정 helper를 호출한다. 입력 소유자는 기존 Collector/Accumulator이고 새 owner·relay·engine·task·writer·DB 경계는 없다.

## 연구·Shadow·가짜 브로커·실제 모의계좌·배포 승인

| 단계 | 상태 |
|---|---|
| 순수 정책/격리 fixture | 구현 및 관련 검사 완료 |
| 기존 0B RAM 집계기 행과 DTO 호환 | 격리 검사 완료. 실시간 native 수신 수락은 아님 |
| 과거 연구 성과·실제 초 해상도 전진 평가 | 미실행. 수익성·확률·파라미터 적합성 미검증 |
| 공통 Shadow Runtime의 자동 실행 | 미연결. 아래 통합 제안만 제공 |
| 가짜 브로커·실제 모의계좌 00/04/REST·체결/잔고 | 이 정책에서 미연결·미실행 |
| 주문·운용·NAS 배포 승인 | 없음. 모든 결과 `order_authorized=False` |

## 아직 안 되는 것과 다음 필수 작업

통합 담당이 공통 Runtime의 기존 수명/동시성 경계에서 명시 선택·상태 슬롯·신선도/만료/중복 처리를 연결해야 한다. 이후 원래 초 관측 출처/연속성 범위의 주문 없는 전진 평가가 필요하다. 실시간 눌림 재가속·보유/증감/재진입·EV·자동 비중·주문은 이번 정책에 포함하지 않았다. 기존 hard Risk/손절은 이 후보·EV·LLM·다음 봉을 기다리게 해서는 안 된다. 미체결/UNKNOWN·취소 확정 전 재주문 금지 계약도 그대로다.

이 채팅에 배정된 **첫 독립 정책·관련 검사·연결 제안은 완료**했다. 아래 공통 파일의 실제 연결은 통합 담당 범위다. 연결 작업을 이 채팅에서 계속하려면 원래 수정 분담을 변경하는 것이므로 현재 완료 범위와 구분한다.

## ② 개발 명세와의 대응 범위

기준은 [② Trading·타점 Intelligence — Codex 개발 명세](../archive/2026-09-22/references/02_Trading_타점_Intelligence_개발명세.md) 및 같은 내용을 포함한 00 종합과 후속 확정 지시다. 강의 기반 복기 분류기를 이번 정책의 원문으로 사용한 것이 아니다. 아카이브 표시는 새 작업 권한이 아니며 최초 배정의 독립 구현 범위만 수행했다.

| 명세 항목 | 이번 구현과 한계 |
|---|---|
| 5장: 1초 자료의 의미·공백·가용 시각 | 기존 수신 초 집계와 연속성 메타데이터를 소비. 전 체결·호가 순서·초 안의 경로를 복원하지 않음 |
| 6장: 특징·완결된 최근 60초·상대 거래대금 | 기존 점/행 변환을 재사용. 이번 W초 관측 버킷 대금은 `turnover_rolling_60s` 또는 동일 시간대 상대 대금 구현이 아님. 현재 초 버킷은 cut까지 받은 부분 상태일 수 있음 |
| 7~9장: 주도주·시나리오·시간대 | 이번 가격 정책의 필수 자료에 시장/테마/뉴스를 강제하지 않음. 주도주 종합 판단·장전 시나리오·시간대별 성과 가설은 미구현 |
| 10장: 최초 돌파 기준선 | 직전 창 고가·현 창 제외·현재 관측가격 초과·대금/신선도·episode를 구현. 연속된 두 가격의 실제 crossing 전이, 최소 관측횟수, 유지시간 확인은 미포함. 문서의 null에 검증값을 채웠다고 주장하지 않음 |
| 11~14장: 행동·대금 감소·무효화·재진입 | 신규 후보/대기/고정 기준 무효화만 구현. 대금 감소만으로 EXIT하지 않음. 보유 Risk/청산/조건부 관찰·ADD/REDUCE/REENTER 정책 구현이 아님 |
| 16~17장: 상태/입출력 | 불변 정책 state/result와 출처·원래 시간·유효기한 제공. 공통 Runtime의 상태 수명·기록/재생 연결은 미구현 |
| 20장 T2 이후 | 첫 돌파 기준선의 독립 계산 부분 완료. T2 전체 기록·재생/차트 대조 및 후속 Shadow·운용 승인은 미완료 |

## 독립 계산 후속 보완 — 재현 후 수정

2026-10-15 후속 점검에서 아래 세 건을 각각 실패/오류로 먼저 재현한 뒤 같은 조건의 검사로 수정했다. 기존 완료봉 함수와 공통 Runtime은 수정하지 않았다.

1. **미래 내용이 과거 판단을 차단:** future `observed_at` 또는 future `available_at`인 행의 high/close 모순을 정보 cut 필터보다 먼저 검사해 과거 정상 후보가 DATA_BLOCKED로 바뀌었다. 먼저 가용 시각/관측창을 제한하고 그 안의 payload만 검사한다. 현재 가용 범위 안의 형식 오류는 계속 차단한다.
2. **고정 후보의 불필요한 이전 창 의존:** 후보 생성 때 기준 고가/원본 refs를 이미 고정했어도 이후 이전 창에 관측가격이 없으면 현재 가격의 구조 이탈을 DATA_BLOCKED로 가렸다. 새 후보에만 이전 창을 요구하고 기존 후보는 고정 기준·신선한 현재 가격으로 유지/무효화를 판단한다. 실제 수신 공백·보존/승인 부족을 우회하지 않으며 해당 상황은 계속 차단한다.
3. **공통 Runtime의 전달 공백 generation 거절:** 기존 `_snapshot`은 소비자 전달 누락이 있으면 `generation`을 `1:delivery-2` 같은 문자열로 바꾼다. 첫 함수의 정수 전용 검증은 이 원래 입력을 ValueError로 거절했다. 원래 정수와 길이 제한된 불투명 문자열을 그대로 보존하고 source/generation identity와 gap warmup을 함께 소비한다. 문자열을 정수로 바꿔 전달 공백 fence를 지우지 않는다.

## 정책과 결과의 정확한 계약

- 정책 ID: `observed_window_high_breakout/v1`. 등록된 완료봉 `krx_bar_close_breakout/v1`과 별개다.
- 설정 DTO: `RealtimeBreakoutPolicy(window_seconds, buffer_bps, minimum_trade_value_won, max_data_age_seconds)`. 기본 추천값 없음. 전부 명시한 연구 설정이며 성과 검증값이 아니다. W는 1~30초, 최근 창 최소 거래대금은 양의 원화 정수다.
- 평가: `evaluate_realtime_breakout(snapshot, *, symbol, policy, state=None) -> RealtimeBreakoutEvaluation`.
- snapshot은 기존 `ram_observation_snapshot`의 `decision_time`, `source_id`, `generation`, `durable=False`, `approved_sources`, `continuous_from`, `retention_since`, `gap_at`, `rows`를 소비한다. datetime 또는 timezone 있는 ISO 문자열 경계를 지원한다. generation은 Collector의 비음수 정수 또는 공통 Runtime이 붙인 전달 공백 fence를 포함한 1~128자 불투명 문자열이며 원래 타입/내용을 보존한다. 외부 IO 없음.
- 고가 창 `(cut-2W, cut-W]`, 최근 창 `(cut-W, cut]`. 현 창의 고가를 돌파 기준에 넣지 않는다. 후보 조건은 `latest.close * 10000 > reference_high * (10000 + buffer_bps)` 및 `sum(current.trade_value_won) >= minimum_trade_value_won`이다. 문턱 동률은 가격 불충족/대금 충족이다.
- 필수 자료는 `approved_krx_0b`, `ram_second_price_trade_value`, `ram_window_continuity` 세 capability다. 누락 시 `missing_data_capabilities`와 구체적 사유를 반환한다. 다른 정책에 이 목록을 강제하지 않는다.
- 두 창의 승인·보존·연속 시작이 확인돼야 한다. reconnect/gap 뒤 2W warmup 전은 DATA_BLOCKED다. 이전 candidate는 해제하고 새 source/generation의 별도 episode를 사용한다. 오래된 cut은 source가 달라도 이전 상태를 되감지 않는다.
- 데이터의 `observed_at`과 `available_at`을 각각 cut 이하로 필터링한다. 마지막 관측·가용 시각 모두 freshness를 만족해야 한다. 방금 수신한 오래된 관측도 차단한다. `valid_until = min(observed_at, available_at) + max_data_age_seconds`이며 조회/후속 cut만으로 유효기한을 연장하지 않는다. 원래 시간과 source/generation·RAM 참조를 결과에 남긴다.
- 정보 cut 안의 초 행이 변환되지 않거나 중복 초·high/close 모순이면 DATA_BLOCKED다. 대상 종목 KRX 유효창 행은 최대 61개로 제한한다. 현재 window의 무체결 초를 합성하지 않으며 관측 없는 초 하나만으로 broker 손실을 주장하지 않는다. 새 후보에는 이전/최근 창 관측이 모두 필요하다. 고정된 기존 후보는 이전 창의 새 print가 없어도 원래 고가/refs와 현재 관측을 사용하지만, 현재 관측 자체가 없거나 연속성이 부족하면 차단한다.

| 평가 상태 | proposal / 신규 알림 | 상태 의미 |
|---|---|---|
| WATCH | WAIT / false | 가격/대금/세션 조건 대기 |
| CANDIDATE | ENTER / 최초에만 true | 고정 기준·원본 참조·episode ID 유지. 주문 허가 아님 |
| INVALIDATED | WAIT / false | 후보가 생긴 뒤 현재 관측가격이 고정 `reference_high` 이하로 하락 |
| DATA_BLOCKED | WAIT / false | 승인/연속성/보존/가격/시간·형식 조건 부족. 가격 실패와 구분 |

후보 후 대금 부족 또는 buffer 안으로의 약한 후퇴는 평가 WATCH/WAIT이지만 `state.status=CANDIDATE`로 원래 episode를 보존한다. 다음 정상 관측에서 같은 후보를 새 후보로 재발행하지 않는다. 이때도 `last_cut`은 갱신해 오래된 callback을 거절한다. 구조가 무효화되면 INVALIDATED로 끝나며 다음 적격 cut의 후보는 새 episode다. DATA_BLOCKED는 활성 episode를 해제한다. out-of-order만 예외로 이전 상태를 그대로 보존한다.

`RealtimeBreakoutState`는 policy 설정 hash·종목·source/generation·last_cut과 고정 기준 원본 refs를 가진 불변 DTO다. 다른 종목/설정의 상태를 혼용하면 ValueError다. 결과의 decision ID는 전체 명시 내용의 결정론적 hash이며 입력 snapshot을 수정하지 않는다.

## 공통 Runtime/registry 통합 제안 — 미구현

수정 대상은 기존 `MarketThemeShadowRuntime._codes/_snapshot/_dispatch/latest_results`와 같은 owner의 선택 검증·설정·공통 정책 등록 경계다. 별도 Shadow/worker/engine 수명을 만들지 않는다.

1. 선택한 정책 ID와 `RealtimeBreakoutPolicy` 네 파라미터를 기존 설정 snapshot에 명시한다. `ResearchFamilyDefinition.evaluate_bar`는 분봉 연구 계약이므로 여기에 초 정책을 위장 등록하지 않는다. 공통 정책 등록의 입력 종류를 `collector_ram_snapshot`, 출력 타입을 `RealtimeBreakoutEvaluation`, state 타입을 `RealtimeBreakoutState`로 정확히 선언한다. 기존 Family/Factor v1 등록은 유지한다.
2. 기존 `_codes`가 선택 종목을 테마 구성원과 독립적으로 포함하도록 한다. 기존 owner의 RAM 보존 요청은 필요한 최댓값 `2 * window_seconds`(최대 60초)로 합친다. 하나의 `_snapshot` cut에서 모든 선택의 승인/연속성/보존 메타데이터를 읽는다.
3. 기존 coalescing/drain의 `_dispatch`에서 아래 순수 호출을 정책/종목 슬롯별 한 번 수행한다. 같은 source/generation/cut의 중복 전달과 status/조회는 이미 계산된 결과를 재사용한다. 0B callback마다 H1/전체시장/EV를 재계산하거나 DB/REST를 조회하지 않는다. 대상 행만 선별하는 작업도 가능한 한 공통 cut에서 공유한다.

```python
evaluation = evaluate_realtime_breakout(
    snapshot, symbol=selected_symbol, policy=selected_realtime_policy,
    state=previous_realtime_state,
)
next_state = evaluation.state
```

4. 슬롯 key는 `(policy_id, parameter_hash, symbol)`로 두고 재연결 generation/선택 교체/종료는 기존 Runtime fence/drain으로 처리한다. candidate 발행은 `new_candidate`와 episode ID를 사용하고 ENTER 문자열을 매번 새로운 신호로 해석하지 않는다. `valid_until` 경과 및 source 변경/실패 때 기존 latest 투영에서 stale 후보를 제거한다. cutoff를 현재 시간으로 덮어써 lease를 갱신하지 않는다.
5. EntryThesis가 필요한 테마별 정책은 기존 builder/검증을 그대로 사용한다. 이 가격 정책의 자료 부족을 테마/뉴스 부재로 바꾸지 않는다. 결과를 향후 ③에 후보로 전달해도 EV·자동 비중·Risk·실행 승인을 생략하지 않는다.

이번에는 연결 파일을 수정하지 않았다. 신규 모듈이 없고 기존 `test_breakout_strategy`가 `tests/regression_profiles.json`의 desktop-and-server/core에 이미 등록돼 있다. 통합 담당은 공통 registry 연결과 전체 catalog/CI/source gate를 별도로 확인해야 한다. 코드 변경으로 scientific source hash가 바뀔 수 있으므로 기존 연구 패키지를 자동 재표지하거나 현재 지원 증거로 사용하지 않는다.

### 연결 위치와 인수 검사 — 통합 담당에게 남기는 작업

| 현재 파일/함수 | 필요한 연결 및 확인 조건 |
|---|---|
| `central_server/market_theme_shadow_monitor.py::validate_ram_shadow_selection` | 명시 정책 ID/파라미터/종목의 선택 검증 추가. 기존 선택 JSON과 기본 OFF·기존 정책을 보존하고 임의 기본 문턱을 만들지 않음 |
| 같은 파일 `MarketThemeShadowRuntime.__init__/_codes` | 독립 정책 슬롯 및 선택 종목 합집합·2W 보존을 기존 owner에 반영. 기존 전체 200종목/60초 상한과 계좌·다른 정책의 scope를 유지 |
| 같은 파일 `_snapshot/_dispatch` | cut 한 번을 공유해 순수 함수를 호출. 원래 generation 및 소비자 gap fence를 그대로 전달. 중복 사건/조회 100회에 snapshot read/evaluation/DB/EV 추가 호출 0을 계수로 확인 |
| 같은 파일 `latest_results/_close_owned` | 원래 valid_until 경과·선택 교체·source/generation 변경·종료 뒤 후보 제거. 늦게 완료된 이전 세대 결과는 게시하지 않음. 재연결/전달 누락 뒤 2W warmup 전 새 후보 0 |
| `central_server/app.py::prepare_ram_bindings/apply_ram_selection` | 테마 없이 선택 종목을 준비하고 기존 operational lock과 close/drain으로 적용·복원. 새 task/collector/account owner를 만들지 않으며 선택 교체 실패와 종료 취소를 검증 |
| `central_server/operational_settings_routes.py::put_ram_shadow_settings` | 기존 인증·revision 충돌·크기/입력 검증·실패 응답을 유지. 선택 추가를 주문 권한 복원/계좌 연결 신호로 사용하지 않음 |
| `tests/unit/test_candidate_monitor.py`의 `RamShadowSelectionTests` 및 RAM Runtime 검사 | 테마/뉴스 없는 가격 정책의 정상 후보, 필요한 자료만 결측 차단, 동일 episode 1회, 두 정책의 상태/실패 격리, 단일 snapshot 공유, 만료/교체/종료·late completion fence를 검증 |

신규 입력 adapter를 만들 필요는 없다. 이미 생성된 snapshot을 그대로 evaluator에 전달한다. 기록/화면이 필요하면 기존 latest/선별 기록 경계에서 새 result의 명시 필드를 전달하며 기존 v1 응답에 필드를 강제하지 않는다. HTTP 검증이 필요한 통합 단계는 현재 지원 ASGI/HTTP 경로를 사용하며 httpx2나 의존성 우회를 추가하지 않는다. 이 표는 연결 제안이며 해당 파일을 수정하거나 이 통합 검사를 실행한 결과가 아니다.

## 검증과 실행 한계

- 후속 보완 후 최종 사용자 세션 실행: **54건 통과 / 14.686초 / 종료 코드 0**. `test_breakout_strategy` 전체, `test_theme_leadership` 전체, `test_entry_thesis.EntryThesisTests`, `test_entry_thesis.StockThesisShadowTests`, `test_research_factors` 전체. 신규 정책 총18건 포함. 이전 첫 구현 51건 통과 기록과 구분한다.
- 최초 후보/중복·고정 기준 유지/원본 refs·무효화·대금 대기/회복·문턱 동률·관측창 경계·future availability·신선 receipt/낡은 관측·원래 expiry·gap/reconnect/승인·다른 venue·관측 없는 초·형식/중복/예산·정책 scope·역순 cut을 검증했다.
- 기존 `SecondTradeAccumulator`와 실제 `TradeTick` 타입을 격리 생성해 canonical RAM 행을 직접 평가했다. 평가 전후 accumulator snapshot 동일. 반복 100회에서 신규 후보 재발행 없음, 입력 불변, file/SQLite/H2 호출 차단 확인. 운영 성능/지연 실측이 아니다.
- 기준 커밋의 `breakout_strategy.py`를 별도 Python 모듈로 읽기 로드해 동일 완료봉 fixture의 전체 evaluation/기본 설정을 현재 코드와 대조했다. 값과 기존 ID가 동일했다. v1의 모든 가능한 입력에 대한 동치 증명은 아니다.
- 기존 완료봉 함수/class 23개가 기준 커밋과 AST 동일함을 확인했고 변경 Python 두 파일의 구문 검사와 `git diff --check`가 통과했다.
- 원본 `.venv/Scripts/python.exe`를 실행기로만 재사용했고 실제 import는 이 워크트리 `src`임을 확인했다. PYTHONPATH는 워크트리 `src;tests/unit` 우선. 최종 fixture는 사용자 세션 `LOCALAPPDATA/Temp/kiwoom-regression/realtime-entry-d3aa-closeout-20261015`에 격리했다. 원본 data·계좌·NAS·.env 접근/실행 없음.
- 기본 샌드박스의 넓은 실행은 ERROR 후 중단했고 전체 통과로 집계하지 않았다. 원인 확인용 좁은 실행에서 `sqlite3.OperationalError: unable to open database file`, cleanup `PermissionError: [WinError 5]`를 확인했다. 경로는 샌드박스 `AppData/Local/Packages/sandbox.{95dc1ae5-7272-44c5-b6da-bf2b7f84d6b1}/AC/Temp/kiwoom-regression/realtime-entry-d3aa-final-20261015`였다. 실패 fixture는 보존했다. 사용자 세션의 별도 경로 실행만 통과로 집계하며 이 결과로 샌드박스 ACL/다른 자료 접근 문제가 해결됐다고 판정하지 않는다.
- 전수 Windows all-local·LF source gate·외부 CI·실제 PostgreSQL·실제 수신·공통 Runtime 통합·브로커 전송·운용/배포 승인은 미실행이다. 최종 통합 뒤 담당자가 수행한다.

해상도는 **수신된 0B로 만들어진 초 단위 집계**다. 모든 거래소 개별 체결·호가 순서·초 안의 전체 경로를 관측했다는 증거가 아니며 0B 미제공/전송 누락/수집 중단/지연을 서로 대체하지 않는다. 과거 1분·5분봉과 일봉은 이 정책의 초 단위 성과·확률·최적 문턱을 검증하지 못한다. KRX 정규 연속매매만 첫 범위이고 NXT/SOR·장외·동시호가는 지원하지 않는다.

## 수정 파일

- `src/kiwoom_monitor/application/breakout_strategy.py`: 기존 함수를 보존하고 명시 실시간 정책·state/result DTO·순수 evaluator 추가.
- `tests/unit/test_breakout_strategy.py`: 같은 소유 시험 모듈에 신규 총18건 추가.
- `docs/workstreams/realtime-entry.md`: 이 보고서. 공통 설계 문서는 독점 수정 범위 밖이므로 통합 제안만 기록.

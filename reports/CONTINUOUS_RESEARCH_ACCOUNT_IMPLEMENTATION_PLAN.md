# Sol 구현 계획 — 연속 연구와 계좌별 매매일지

기준일: 2026-09-13, O2-M 설계 재검토 갱신 2026-09-16. **단계별 구현 상태를 포함한 실행 계획**. [기존 설계 결정](CONTINUOUS_RESEARCH_ACCOUNT_SCOPE_REVIEW.md)과 [O2-M 최신 결정](O2M_DESIGN_REVIEW_DECISIONS_20260916.md)을 함께 읽는다. [A4b 재검토 결정](A4B_DIRECT_WEBSOCKET_SCOPE_REVIEW.md)은 당시 계좌 경계 완료 판정의 한계를 정정한 기록이다. CR3/CR4/A5와 O2-Ma~Md의 기본 구현은 존재하지만, 이번 감사로 **O2-Md 안전 완료 판정은 보완 필요로 정정**했다. 다음은 O2-M0 안전 보완이며 이후 Me1 후보 게시 → Me2 실제 위험 근거/계좌 owner → Me3 runner/UI → V1 순서다. 삭제 범위는 24시간 이상 지난 검증된 미완성 staging뿐이다. 새 날짜 확장은 CR3 평가 구간 정책과 함께 연결한다. 실제 NAS 전체 규모 성능 V1은 후속이다. 재검토 문서 작성만으로 코드·NAS·주문 상태가 바뀌지는 않는다.

현재 R2의 등록 두 Family/유한 탐색은 재사용한다. 이 계획이 끝나기 전 “24시간 자동 연구 완료”, “계좌 분리 완료”, “자동 모의운영 완료”로 보고하지 않는다.

2026-09-13 추가 영향 감사: 9월 14일 KRX 애프터 신설에 대한 [시간제도 변경 감사와 Sol 작업 순서](KRX_NXT_SESSION_CHANGE_20260914_REVIEW.md)를 별도 작성했다. A4의 실시간 연결과 CR1~CR4/A5/O2-M은 이 문서의 시행일·거래소·session profile 계약을 함께 적용해야 한다. 새 저녁 봉이 기존 연구/후보에 자동 유입되지 않도록 보호하며, 계좌 경계 오류 보완은 계속 남아 있다. 시간제도 구현은 아직 시작하지 않았다.

## 1. 실행 순서와 변경 경계

권장 순서: **CR0 → A1 → A2 → A3 → A4 → CR1 → CR2 → CR3 → CR4 → A5 → O2-M → V1**. O2-M은 자동 모의운영까지 연결할 때의 후속 단계이며 과거 연속 연구는 CR4까지로 먼저 사용할 수 있다.

CR1은 CR0 이후 계좌 작업과 독립적으로 진행할 수 있다. CR2를 CR3 이전에 개발하더라도 자동 최종평가·후속 가설 생성은 OFF로 둔다. A5는 A1~A4와 CR3가 모두 필요하다. 여러 단계 동시 대규모 패치보다 한 단계의 테스트·문서 갱신을 마친 뒤 다음으로 간다.

| 단계 | 목적 | 대상 |
| --- | --- | --- |
| CR0 | 기존 연구 결과·중단 처리의 정확성 확보 | PC 연구 |
| A1 | 실제 계좌와 안정된 식별자 연결 | PC+NAS |
| A2 | 계좌별 DB·계산 분리, 기존 자료 보존 | PC 일지 |
| A3 | 계좌 조회·failover의 출처 보장 | PC+NAS |
| A4 | 실시간·UI·동기화·백업까지 계좌 분리 완성 | PC+NAS |
| CR1 | 여러 날짜의 재현 가능한 데이터 묶음과 자원 검증 | PC 중심 |
| CR2 | 재시작 가능한 지속 캠페인 | PC 연구 |
| CR3 | 개발/최종 검증과 다양한 종목·기간 평가 분리 | PC 연구 |
| CR4 | 자동 가설 생성과 간단한 연구 화면 | PC 연구/UI |
| A5 | 모의 원장·일지의 피드백 연결 | PC+NAS 읽기 |
| O2-M | 검증 후보의 정책 내 자동 모의운영 | 기존 NAS O1/O2 |
| V1 | 전체 수명·연속 운전 확인 | PC+NAS |

NAS 수집 기능이나 주문 transport를 새로 만들지 않는다. 모든 중앙 조회는 기존 broker 경로다. 서버 변경 단계는 API_CONTRACT/DB_SCHEMA와 세 build 식별자를 함께 갱신하되 배포는 별도 검증 단계로 기록한다.

## 2. 공통 입출력 계약

아래 이름은 설계상 명칭이다. 기존 응집도 높은 모듈에 dataclass/함수를 두고 같은 뜻의 Manager/Provider/Service를 여러 개 만들지 않는다.

### 연구 계약

| 객체 | 입력/보존 필드 | 불변 규칙 |
| --- | --- | --- |
| ResearchSpec/v2 | bundle hash, 전체 baseline, Family/Factor/implementation hash, execution/cost, 실제 split/종목 partition, warmup/purge/state 정책, 선택·적격 정책 | 파일 경로/CPU/slice/owner 제외. overlay 적용 후 실제 행동 변화는 evidence key 변화. baseline/변형 표현은 cycle 계보와 구분 |
| CampaignRevision | 목적, baseline refs, 허용 Family/Factor/변형·값 범위, dataset selector, 개발/최종 정책, seed | 수정 시 새 revision. 기존 cycle의 요청은 변하지 않음 |
| Cycle | campaign revision, cycle sequence, 입력 manifest, 동결 trial 계획/선택 근거/parent refs | (campaign, revision, sequence) 유일. 생성과 enqueue 원자적 |
| LogicalTrial | evidence key, 변형 적용 후 최종 전략/실제 평가 입력의 key, 결과 ref, EVALUATED/INELIGIBLE 여부 | attempt 수와 독립. baseline과 동일한 변형은 중복 제거 |
| TrialAttempt | attempt_id, trial_id, owner/generation, 시작/heartbeat/종료, 상태·실패 이유·진단 ref | INTERRUPTED/YIELDED는 실험 성공 아님. 성공 결과 1개만 참조 |
| ExecutionPolicy | concurrency=1, cpu target, RSS/디스크 한도, slice, 재시도/backoff | 변경해도 연구 evidence ID 유지 |
| DevelopmentEvidence | 허용된 개발 partition의 지표·품질·실패 이유·표본·계보 | FINAL_OOS raw/점수 참조 불가 |
| FinalEvaluation | 잠근 candidate batch, window ID, 정책, input hash, access/exposure 기록, 보고서 | 재시도 외 동일 창 재선택 금지 |

현재 DB v8 이후 migration을 사용한다. 신규 핵심 저장 책임은 campaigns, campaign_cycles, trial_attempts, research_hypotheses에 한정하고 상세 명세는 JSON/hash로 저장할 수 있다. 기존 search_jobs/events/runs/trials/cards를 활용한다. 기존 v1 자료는 읽기/감사 가능하게 유지한다.

### 계좌 계약

AccountScope: broker, environment(real/mock), account_ref(UUID). legacy는 unknown/legacy-unassigned이며 migration·읽기 전용이고 신규 자동 수입에는 사용할 수 없다. 연구 simulation은 별도 run/origin이다.

AccountBinding: credential_profile_id, scope, binding_revision, verified_at, verification_method=ka00001, 보호된 로컬 비교 정보. 사용자 별칭은 식별자가 아니다.

AccountQueryContext/v2: scope, binding_revision, transport(nas/direct), query_session_id, verified_at, received_at. 요청/반환은 다음 의미를 갖는다.

- GET /api/v2/journal/accounts → 인증 사용자에게 허용된 검증 계좌의 ref/환경/마스킹 표시/사용 가능한 프로필.
- POST /api/v2/journal/account-query → expected scope/binding, allowlist api_id/body, query_session_id/cursor를 받아 실제 context + payload/has_next/cursor 반환.
- 최초 계좌 등록/직접 프로필 결합은 별도 인증된 binding 경계. 사용자 요청 ref를 그대로 신뢰하지 않음.
- cursor는 scope, binding, transport, API/body hash, expiry에 묶임. 기존 next_key를 다른 transport에 전달하지 않음.
- account-query는 조회만 허용. 주문 API는 기존 O1 외 경로로 열리지 않음.
- 오류: ACCOUNT_CONTEXT_MISMATCH, ACCOUNT_IDENTITY_UNVERIFIED, ACCOUNT_QUERY_RESTART_REQUIRED, CAPABILITY_REQUIRED, IDENTITY_RECOVERY_REQUIRED. 일반 네트워크 실패와 구별.
- 체결 시간 품질: exact_execution / order_time_proxy / unknown. 원본 TR/event, 거래일, broker 주문/체결 ID와 계보를 보존.

## 3. CR0 — 유한 연구의 정확성부터 복구

**진행 상태 (2026-09-13): CR0a와 CR0b 정확성·수명 경계 완료.** `limited_search/v2`가 전체 과학 입력을 identity에 고정하고 운영 예산을 분리하며 TRAIN/VALIDATION만 선택에 쓴다. DB v9는 중단 attempt를 결과와 분리하고 owner token·generation·heartbeat fencing을 적용한다. slice는 진행 중 trial을 완료하며 GUI 예약은 취소할 수 있다. `historical_simulation` 의미도 고정했다. 짧은 계산 batch의 CPU 양보와 실제 RSS 검사는 기존 replay 계측 후 CR1/V1에서 완료한다.

**목적:** 현재 F01~F07을 그대로 자동화하지 않도록 동일성·선택·중단 경계를 먼저 고친다. 두 작은 변경 묶음으로 나눈다: CR0a 식별/선택/생성, CR0b attempt/수명/임대.

**수정 대상:** application/research_search.py, research_queue.py, research_evaluation.py; research_process.py; infrastructure/persistence/research_repository.py; presentation/research_dialog.py; scripts/run_research.py의 run 명세/hash 경계.

**입출력:**

- 정규화한 전체 request로 v2 spec/evidence key를 만든 뒤 완료 캐시를 조회한다. v1 identity로 완료 반환하지 않는다.
- 선택 지표는 명시된 개발 fold만 사용한다. 봉인된 최종 구간이 있다는 이유로 정상 개발 후보를 INELIGIBLE로 만들지 않는다.
- 전체 Cartesian list 생성 전 distinct 값 개수의 합/곱과 baseline/무거래/비용/ablation 후보 수를 검사하고 iterator로 생성한다.
- slice 만료는 새 trial 시작을 막는다. 사용자 stop/프로세스 손실은 attempt INTERRUPTED; 동일 trial 재시도 가능.
- DB 완료를 원본으로 결과 파일을 원자 교체한다. 게시 전 충돌 시 DB에서 복원하고 완료 trial을 재계산하지 않는다.
- claim/renew/finish/result commit에 owner token+generation 조건을 적용한다. GUI 예약은 소유 QTimer/실행 세대 또는 영속 desired_state 확인으로 취소 가능하게 한다.
- v2의 실행 의미는 historical_simulation으로 고정한다. v1 replay/simulation은 기존 호환 입력으로 읽되 모두 과거 paper 모형이라는 사실을 표시한다.

**완료 기준:** 설정을 바꾸면 해당 결과가 계산되고, 시간 만료로 미완료 trial을 건너뛰지 않는다. 중지한 창이 다시 worker를 시작하지 않는다. 동시 claim 성공은 하나, 오래된 worker 쓰기는 거절된다. 운영 예산 변경으로 같은 연구를 새 가설처럼 세지 않는다.

**테스트:**

- test_research_search/process/repository/queue/dialog/evaluation 중심 회귀.
- baseline/비용/실제 fold/구현 hash 변경은 새 ID, CPU/slice/경로 변경은 같은 ID.
- v1 완료 job이 있어도 새 spec 결과를 반환. v1 누락값을 추측하지 않음.
- trial 중단→재시작→결과 1개·attempt 2개. 모두 중단된 작업이 completed가 되지 않음.
- OOS 손익을 극단적으로 바꿔도 개발 점수/후속 선택 동일.
- product 한도 초과 시 generator 소비 0, 경계값 후보 수/중복 제거 정확.
- 예약 후 체크 해제/취소/종료, lease 탈취 후 finish, 트랜잭션 직전/직후 충돌.

**문서:** RESEARCH_REQUEST_FORMAT, 현재 아키텍처, DB_SCHEMA, 감사 F01~F07의 해결 범위를 갱신한다. 이 단계 통과만으로 장기 자원 제한이 검증됐다고 쓰지 않는다.

## 4. A1 — 계좌 신원과 지속 registry

**진행 상태 (2026-09-13): A1a~A1b 계좌 신원 기반 구현.** `AccountScope`·`AccountBinding`·`AccountScopeAlias`, 환경별 `ka00001` reader, 보호 키 HMAC 지문, 중앙 DB v17 registry/binding revision과 v18 불변 alias, 최초 등록 명령을 추가했다. Windows 직접 프로필은 검증 성공 뒤 최신 binding revision을 별도 DPAPI 파일에 mirror할 수 있다. alias는 중앙에 기록된 실제 binding과 같은 broker/environment만 허용하고 origin 보존, 멱등 재시도, 대상 변경·연쇄·순환 거절을 강제한다. 기능 플래그를 켠 mock 계좌 모니터는 시작 전에 등록 UUID를 재검증한다. 계좌별 신규 일지 수입·조회 적용은 A2 이후이며 현재 레거시 일지에는 alias를 소급 적용하지 않는다.

**목적:** 별칭·App Key와 무관하게 NAS/직접/다른 PC가 같은 계좌를 확인할 기반.

**수정 대상:** domain의 계좌 context 값 객체(기존 order_contract와 중복 정의 방지), infrastructure/kiwoom_rest client/settings/client_factory, central_server의 account registry·schema·broker/config. 독립 registry 저장 책임은 기존 execution 또는 중앙 DB 계좌 영역에 두며 범용 관리 계층을 추가하지 않는다.

**입출력:**

- 해당 환경 client로 ka00001 조회 → 전체 계좌값 검증 → 사용자 범위 registry UUID 및 binding 반환.
- NAS HMAC 지문/UUID와 보호 키는 별도 보호 백업. 로컬 확인 지문은 OS 보호 저장소, 서버 ref는 검증된 mirror로 보관.
- raw acctNo 응답은 REST 캐시·관측 저장·일반 로그·일지 백업에 쓰지 않는다.
- 이미 등록한 직접 프로필은 NAS 장애 중 재확인할 수 있어야 함. 처음 보는 계좌는 기존 ref로 추정 연결하지 않음.
- 완전 로컬 신규 scope는 중앙과 연결할 때 검증된 alias로 조정. origin_scope/ID는 불변, canonical_scope는 alias로 해석. 연쇄·순환·환경 간 alias 금지. 실제 체결 ID로 canonical 중복을 제거하되 상세 ID 없는 충돌은 미확인. 메모·뉴스 참조를 재발급하지 않음.
- 현재 NAS는 기본 프로필+별도 mock 구성. registry에 등록돼 있어도 활성 검증 프로필이 있어야 신규 수입 가능. 화면의 일지 계좌 선택으로 공통 시세 키/WS를 바꾸지 않음.

**완료 기준:** 키 교체·별칭 변경·새 PC에서도 같은 실제 계좌는 같은 중앙 ref. 다른 계좌/환경은 분리. 빈 값·잘못된 binding·보호 키 유실은 검증 실패를 명시.

**테스트:** fake ka00001의 정상/빈/누락/다른 계좌, 동일 계좌 자격 교체, 환경 분리, registry 동시 등록 유일성, 보호 백업 복원·키 미복원, alias 후 canonical 중복·계좌별 참조 보존·순환/환경 변경 거절, secret/raw 로그 누출 없음. 실전 5/모의 1 limiter가 독립이며 신원 확인도 우회하지 않음.

**문서/운영:** API_CONTRACT에 새 capability/인증 범위, DB_SCHEMA에 registry·alias·보호 백업. 실제 계좌 확인은 읽기 전용으로 하고 원문을 결과 보고서에 넣지 않는다.

## 5. A2 — 단일 DB 계좌 scope와 계산 분리

**진행 상태 (2026-09-13): A2 완료.** v5는 체결·비용을, v6는 진입 스냅샷·복기·수동 묶음·유형·자동보완·분석 revision·연구 링크를 origin/canonical 계좌 scope로 분리한다. 기존 자료는 fill/execution/group/revision/link 키, 사용자 복기·유형·뉴스 JSON을 그대로 유지한 `legacy-unassigned`로 이전된다. 신규 `fill:v2`·`snapshot:v2` 식별자, 명시 scope 쓰기, canonical scope별 FIFO·비용·lookback, 계좌 간 수동 병합 거절, 계좌별 스냅샷·보완·분석 조회와 migration rollback을 확인했다. 자동 계좌 수입은 A3 전까지 legacy이며 계좌 선택·중앙 v2 동기화는 A4 대상이다.

**목적:** 계좌 분리를 PK뿐 아니라 FIFO·비용·편집·분석까지 적용하고 기존 복기를 보존.

**수정 대상:** persistence/journal_schema.py(v5 체결·비용, v6 파생 자료), journal_trade_repository.py, journal_snapshot_repository.py와 관련 review/group 저장소; application/trade_history_service.py의 TradeFill, trade_cost_service.py, trade_journal_summary.py, trade_history_query_service.py, trade_group_edit_service.py, journal_snapshot_service.py, journal_enrichment.py.

**입출력:**

- 신규 계좌 자료의 모든 쓰기/조회/그룹 함수는 명시 AccountScope를 요구한다. 기본 scope를 화면 전역값에서 가져오지 않는다.
- 기존 v4는 legacy-unassigned로 migration. 기존 group_id/fill_key/execution_key/메모/유형/뉴스 refs를 보존한다.
- 새 key는 version+scope+원천 식별자의 충돌 없는 canonical 인코딩/hash. origin_scope 컬럼과 key의 일치를 저장 시 검증. 계좌 필터/FIFO는 검증된 canonical_scope를 사용하며 alias 대조 전 겹치는 미확인 체결은 합산하지 않음.
- 비용 키에 scope 포함. 매수 원가 lookback과 FIFO는 같은 scope에서만 계산. 수동 병합에 다른 scope가 있으면 거절.
- 일지의 기계 작성 결과는 사용자 review를 덮어쓰지 않는다. 계좌 변경으로 enrichment가 다른 scope를 갱신하지 않음.
- 과거 ord_tm에는 order_time_proxy 품질. 실제 체결 ID 없는 동일초 행은 정밀 사건으로 주장하지 않는다.

**완료 기준:** real A/real B/mock A에 주문번호·종목·시간이 같아도 모두 보존되고 비용/FIFO/snapshot이 독립. legacy는 그대로 읽고 기본 계좌 합계에 섞이지 않는다. migration 중 실패 시 원본과 version이 함께 복구.

**테스트:** 이전 DB fixture+news DB 링크+수동 JSON 참조의 값/키/시각 전후 비교, 재실행 멱등, migration rollback, scope 누락 쓰기 거절, 계좌별 FIFO/비용/합치기/이전 매수 조회, 계좌별 같은 종목 snapshot.

**문서:** DB_SCHEMA, MODULE_MAP(실제 추가한 책임만), D8의 선행조건. 신규 자동 수입은 A3/A4 완료 전 OFF.

## 6. A3 — 계좌 조회·페이지·failover 계약

**진행 상태 (2026-09-13 재검토): NAS 조회 세션 구현, 직접 계좌 검증 미완료.** `/api/v2/kiwoom/account-query`는 실제 main 자격의 검증 context와 binding revision, API·본문·cursor·페이지 순서를 고정한다. 완료 batch와 체결/비용 scope 불일치 검사도 구현돼 있다. 그러나 직접 adapter는 저장 binding만 붙이고 현재 자격을 `ka00001`으로 재확인하지 않으며 factory는 같은 환경의 단일 binding을 선택한다. 따라서 NAS와 직접 계좌의 실제 일치까지 완료했다는 이전 판정은 정정한다. [A4b 재검토](A4B_DIRECT_WEBSOCKET_SCOPE_REVIEW.md)의 1~3단계로 보완한다. 현재 NAS 세션과 일반 시세 QueryClient는 재사용한다.

**목적:** 계좌 없는 payload에 화면 이름을 붙이는 우회를 없앤다.

**수정 대상:** 기존 client_factory/remote_client/failover_client/validation_client, central_server/app.py/rest_broker.py; trade_history_service/trade_cost_service 및 journal_workers. 계좌 전용 query adapter는 context/pagination을 소유하므로 허용하며 시세 QueryClient 전체 반환형은 바꾸지 않는다.

**입출력:**

- v2 account-query는 실제 인증 프로필의 context와 페이지 cursor를 반환.
- 조회 batch 전체의 scope/binding/transport를 고정. 중간 context 변경·cursor 만료·잘못된 페이지는 완료로 저장하지 않음.
- NAS A→직접 A는 미완료 batch 폐기 후 첫 페이지 재시작; NAS A→직접 B는 불일치 실패.
- 전체 페이지 완료 상태와 provenance를 반환한다. 현재 페이지 상한에 도달했는데 has_next이면 partial로 남기고 완료라고 표시하지 않음.
- 체결/비용 계좌 불일치는 hard error. 보통 비용 정산 지연은 체결을 보존하되 비용 미확인.
- 구 NAS는 capability 부족으로 v2 수입을 중단; v1 수입에 선택 계좌를 끼워 넣지 않음.

**완료 기준:** 페이지·binding·worker 완료 순서가 바뀌어도 다른 계좌 행은 0개. 계좌 조회 오류가 공통 시세 failover를 깨뜨리지 않음.

**테스트:** NAS A/direct B 0행, A/A 재시작 후 정확히 1회 저장, 2페이지 자격 교체·잘못된 cursor/body·20페이지 초과, 비용만 다른 scope, 병행검증 다른 계좌 제외, v1 NAS fallback의 legacy 제한. 기존 시세 client/failover 회귀 유지.

## 7. A4 — 실시간·UI·중앙 동기화·백업 완성

**진행 상태 (2026-09-13 재검토): A4a 중앙 scope·계좌 선택 기반 구현/배포, A4b 경계 보완 필요.** 중앙 00·04의 신원 대조·익명 envelope·entry snapshot과 일지 계좌 선택은 구현됐다. 뉴스 DB v2, 일지 DB v7 원장과 v2 sync 코드도 있으나, 실제 메인 명령 relay의 scope 유실, 묶음 편집의 legacy 저장, 서버 컬렉션 누락 404, 구 NAS capability 오판, 삭제 부활과 v1→verified 행 덮어쓰기가 재현됐다. 뉴스 연결·구형 수입 방어 완료 판정은 철회한다. 원본 참조와 전체 DB 백업은 보존하며 [재검토 문서](A4B_DIRECT_WEBSOCKET_SCOPE_REVIEW.md)의 0a→0b→0c를 우선한다. 직접 REST/WS·로컬 보호 verifier·HTTPS 대조는 이후 1~4단계다.

**목적:** 모든 유입 경로와 다른 PC/구 앱까지 계좌 분리 적용.

**수정 대상:** infrastructure/kiwoom_rest/realtime.py와 직접/중앙 worker, central_server/realtime_collector 및 mock_account_monitor, presentation/main_window.py의 체결 snapshot 연결부, journal_process.py/관련 view model, central_journal_sync.py, central_content_sync.py, stock_news_repository.py, journal_backup.py.

**입출력:**

- 00/04의 9201을 binding과 확인한 뒤 raw 제거+scope envelope. 불일치는 격리하며 현재 화면 계좌로 보정하지 않음.
- 일지 상단의 real A/real B/mock/legacy 선택. 후착 worker는 원래 context에 저장하고 화면 갱신은 현재 선택과 맞을 때만 수행.
- journal_v2_* 컬렉션/key/tombstone을 명시 allowlist에 추가. v1은 legacy만 송수신.
- 뉴스 문서 자체는 공통. 계좌별 journal link는 v2 경계 또는 구 앱이 수신하지 않는 내부 namespace로 분리.
- v1→legacy 수입에 원본 collection/key/revision을 남겨 재유입·삭제 부활 방지.
- 전체 DB 백업/복원은 모든 scope 보존. 구 앱의 새 schema 열기는 명시적으로 거절. 일반 설정/Drive에 보호된 binding 비밀을 포함하지 않음.

**완료 기준:** 구 PC+신 PC 동시 사용에도 신규 계좌 행이 구 컬렉션으로 내려가지 않음. 계좌 변경 중 실시간·snapshot·뉴스 연결 정확. A1~A4를 모두 통과한 뒤 계좌 수입 활성화.

**테스트:** 9201 불일치·필드 누락, 계좌 변경 중 후착 이벤트, 다른 scope snapshot 결합 차단, v1/v2 양방향 sync, 동일 행 두 PC 수입, 계좌별 tombstone, 삭제한 legacy 재유입, 뉴스 링크 보존, backup restore+schema version rollback.

직접 연결은 기존 설계에 허용된 인증된 HTTPS 계좌 대조로 확정했다. NAS는 이미 broker 검증을 마친 registry만 조회하고 PC 요청으로 NAS binding을 발급/교체하지 않는다. PC는 DPAPI 로컬 verifier로 현재 자격을 재확인하며 미등록 origin은 중앙 결합을 보류한다. 상세 입출력·완료 기준·테스트·배포 순서는 [A4b 재검토 결정](A4B_DIRECT_WEBSOCKET_SCOPE_REVIEW.md)을 따른다. 새 계층이나 사용자 UUID 수동 선택은 추가하지 않는다.

## 8. CR1 — 다기간 데이터와 계산 규모

**2026-09-16 CR1a 구현:** 명시 KST `--dates` 선택과 `--reuse-days`로 기존 일별 export를 재사용하는
`research_dataset_bundle/v1` index/검증 reader를 추가했다. 일별 ordinal/ID/파일은 변경하지 않고
동일 source revision의 중복은 고유 개수만 세며 충돌·잘린 테마·누락/변조·혼합 입력 계약은 거부한다.
**2026-09-16 CR1b 실행 연결 구현:** 통합 입력 reader와 CLI/앱 프로세스가 bundle을 받아
UTC 가용시각/ingest/ID 순으로 고유 revision을 반영한다. 같은 시각의 후속 정정도 자기 순서부터 보인다.
원본 ordinal/파일은 유지하며 하루 bundle의 identity/결과는 기존 하루 export와 같다.
한 실행 엔진의 현금/보유를 유지하고 pending/Factor는 기존 명시 profile의 세션 정책을 따른다.
요청 profile 누락/불일치는 실행 DB 생성 전에 거절한다. 테마는 시작 전 구성과 기간 중 변경을 당시 시점으로 선택한다.
**2026-09-16 CR1b 자원 구현:** CLI/PC 연구 프로세스에 512MiB 기본 RSS/입력 preflight와 CPU 50% 목표를
적용했다. 50ms 계산 batch의 CPU 시간/IO 대기를 고려해 양보하며 같은 예산을 trial 안에서 적용한다.
bundle 증분 cursor는 revision을 한 번 변환하고 무효 정정까지 기존 latest 선택과 일치시킨다.
자원 중단은 완료/실패 trial이 아닌 기존 INTERRUPTED attempt로 남겨 수동 재시도할 수 있다.
1/5/20일(1종목·30분/일) 실행의 전후 논리 hash/CPU/시간/RSS/파일 증가를 측정했고,
20종목·390분/일의 같은 기간 입력 크기도 실제 임시 파일로 측정했다. 큰 20일 입력은 기본 예산에서
RESOURCE_BLOCKED로 사전 차단된다. **CR1 최소 기능/fixture 검증 완료, 다음은 CR2**.
실제 NAS 전체기간 처리량, 최고 부하, 순간 할당 초과/OS quota, 장시간 복구는 V1의 후속 운영 검증이다.
실제 NAS 수집/API/DB/build/배포/주문은 변경하지 않았다.

**목적:** 사용자가 날짜별 export를 반복하지 않고 여러 거래일을 재현하도록 한다.

**수정 대상:** scripts/export_research_dataset.py, infrastructure/research_data_source.py, domain/research_contract.py의 별도 bundle 모델, scripts/run_research.py와 application/research_replay.py. 중앙 24시간 export 계약은 유지한다. 필요한 기존 theme pagination/coverage 보완만 별도 최소 패치.

**입출력:**

- 거래일 selector + 필요 capability + 동결 시점 → 자식 일별 manifest/파일을 재사용하는 bundle.
- manifest에 자식 hash/revision membership/범위/수집품질·세션 predecessor·sidecar 기준을 포함.
- 읽기는 시간순이고 동일 source revision은 한 번만 적용. 누락 세션/잘린 sidecar는 알려진 0으로 채우지 않음.
- 독립 기간과 연속 포지션의 상태 경계를 명시. 여러 날짜를 합치면 기존 runner가 하루 끝마다 임의 청산하지 않도록 확인.
- 실제 처리시간/RSS를 측정. 전체 재스캔 병목 확인 시 정렬 인덱스/증분 cursor, 결과 batch 저장으로 좁혀 수정.
- CPU 양보는 짧은 batch마다 수행; memory 한도는 입력 preflight와 실행 중 RSS로 확인.

**완료 기준:** 같은 bundle·spec은 같은 논리 결과. 동일 하루 bundle은 기존 단일 export와 결과 일치. 여러 거래일의 보유·현금·장 시작/끝이 명세대로 이어짐. 큰 자료는 명시 한도 안에서 실행하거나 이유 있는 RESOURCE_BLOCKED.

**테스트:** export/data_source/replay/process/execution 회귀, 겹치는 child 중복/충돌, 중간 파일 누락·변조·truncated theme·휴장일, 늦은 정정·당시 snapshot, lookback/warmup, NXT/초자료 미지원 거절. 1/5/20 거래일 규모의 동일 fixture로 처리시간·최대 RSS·파일 증가량 측정. 최적화 전후 decision/fill/logical hash 비교.

**문서:** HISTORICAL_DATA_CONTRACT, RESEARCH_REQUEST_FORMAT에 bundle/capability, 현재 지원 범위. 현 NAS 저장량/coverage 보고는 별도 읽기 검증으로 사실만 남긴다.

## 9. CR2 — 지속 캠페인과 복구 가능한 작업 선택

**2026-09-16 CR2a 원장 구현:** 기존 queue 정책과 PC ResearchRepository v10에 캠페인 설정 revision/
desired_state/job/cycle을 추가했다. 등록/기존 experiment·job 생성, cycle/sequence/owner generation
예약은 단일 트랜잭션으로 처리한다. lease 만료/중단은 attempt로 남기며 완료된 search job을 복구해
새 실행으로 중복 집계하지 않는다. 동결 입력·등록 작업 우선순위·실패 backoff/상한·자원 차단·100개
완료 후 101번째 등록을 검증했다. GUI/연속 worker/자동 startup은 아래 **CR2b1 구현**이다.
디스크/참조 보관 정책, 새 자료의 관련성 selector와 반복 worker 종료 backoff도 후속이다.
등록된 유한 job 목록 완료는 전체 미탐색 가설 공간 완료를 뜻하지 않는다.

**2026-09-16 CR2b1 실행 연결:** 저장된 spec/context/input_path를 기존 request 검증기와 유한 실행기에
연결했다. 원래 JSON 변경/삭제와 독립적으로 실행하며 데이터·implementation hash를 검증한다.
trial commit은 기존 search lease와 campaign owner/generation/live lease/RUNNING 의도를 한 트랜잭션에서 확인한다.
GUI에 등록/시작·재개/일시정지/중지를 추가하고 마지막 캠페인의 DB 실행 의도를 앱 시작에 복원한다.
창 숨김/앱 종료는 worker만 중단하며 재개는 완료 trial을 재사용한다. 등록된 전체 조합 완료와 실험
횟수 예산 소진은 구분한다. 후자는 NEEDS_ATTENTION/예산 확대 필요이며 예산을 자동 확대하지 않는다.
실제 PC 사용자 DB/NAS/장시간 실행 검증은 하지 않았으며 임시 fixture worker/Qt 회귀 범위다.
[실행 계약과 후속 범위](CR2B1_CAMPAIGN_WORKER_AND_CONTROLS.md)를 따른다.

**2026-09-16 CR2b2 예산 확대 구현:** 로컬 연구 v11에 job별 불변 운영 예산 revision을 추가하고
cycle에 해당 budget_revision을 고정했다. 원래 동결 spec/science identity와 기존 trial/card/report는 보존한다.
일시정지·live worker 종료·expected revision 확인 후 횟수/회차 시간/메모리/CPU 예산을 저장한다.
완료 job 재개와 search owner/lease 취득은 기존 start_search_job의 한 트랜잭션이다.
일반 유한 완료 캐시는 그대로이며 캠페인만 증가분을 실행한다. 2→4 뒤 기존 2개/보고서 유지와 새 2개,
동시 edit/claim·오래된 예산·쓰기 실패 rollback·backlog 상한·v10 행 보존을 테스트했다.
GUI의 실험 예산/재시도는 저장된 실험 선택과 예산 편집/명시 retry를 연결한다.
[구현 계약과 검증](CR2B2_CAMPAIGN_OPERATING_BUDGETS.md)을 따른다.

**2026-09-16 CR2c1 작업자 복구 구현:** 연구 v12는 worker 현재 상태와 세대별 실행 attempt를 추가한다.
원자 claim/30초 lease/약 1초 heartbeat와 trial 시작·commit의 worker fence를 기존 실행기에 연결했다.
시작 실패/예상하지 못한 종료/lease 만료는 한 번만 기록하고 기존 campaign policy의 backoff/상한을
worker 독립 실패 횟수에 적용한다. 기본 30/60초 대기 뒤 연속 3회 실패하면 자동 재시도를 멈춘다.
앱 재시작은 대기/격리를 유지하며 명시 시작/재개만 초기화한다. 60초 생존 갱신은 연속 실패를 초기화한다.
사용자 숨김/일시정지/앱 종료는 예상 종료이며 실패로 세지 않는다.
[실행 계약과 검증](CR2C1_PERSISTENT_WORKER_RECOVERY.md)을 따른다.

**2026-09-16 CR2c2a 같은 범위 준비 입력 자동 등록:** 로컬 연구 v13은 source/acceptance 원장을 추가한다.
GUI의 새 자료 폴더는 일시정지 후 기준 실험/상위 폴더/ON·OFF를 저장하며 실제 파일 읽기는 worker에 둔다.
worker는 같은 기간/종목 범위/kinds/세션/universe/order 계약의 직접 하위 완성 입력만 등록한다.
기존 평가 날짜를 자동 이동하지 않으며 현재 두 Family와 시장 보고가 쓰는 근거 fingerprint로 중복/무관 watermark를 차단한다.
기준 job의 전략/평가/파라미터와 현재 운영 예산을 복사한다. 기존 job은 변경하지 않는다.
source 초기화·등록은 worker/RUNNING fence를 확인하며 job/budget/acceptance는 원자 처리한다.
source 오류는 독립 backoff/격리로 처리하고 백로그 포화는 정상 대기로 구분한다.
[계약과 검증](CR2C2A_PREPARED_INPUT_DISCOVERY.md)을 따른다.

**2026-09-16 CR2c2b NAS 자동 준비 구현:** 연구 v14는 source에 NAS opt-in/config 경로/remote signature를 추가한다.
worker가 기존 앱 설정에서 인증을 읽어 기존 DB export/테마 이력 API만 사용한다. 토큰을 연구 DB에 복사하지 않는다.
1행 probe와 테마 signature가 같으면 전체 download를 생략한다. probe watermark를 이어 읽고 streaming 저장한다.
일별 bundle은 기존 날짜/경계를 유지한다. 임시 reader/scope/fingerprint 검증 뒤 새 폴더를 게시한다.
등록 뒤 fenced signature ack를 남겨 backlog/실패 때 자료를 놓치지 않는다. known backlog 포화는 다운로드를 생략한다.
source guard/10초 요청 timeout/전체 encoded byte cap과 기존 오류 정책을 적용한다.
[구현 계약과 검증](CR2C2B_NAS_INPUT_PREPARATION.md)을 따른다.

**2026-09-16 CR2c3a 보관 준비 구현:** 새 임시/완성 입력만 ownership sidecar를 작성한다.
완료 포함 전체 캠페인 입력 참조와 겹치는 폴더를 보호하는 읽기 전용 용량 목록을 기존 worker에 연결했다.
기존 무표시 자료/링크/손상 파일은 보호한다. bounded inventory의 부분 용량은 전체 용량으로 취급하지 않는다.
이 DB snapshot은 외부 매매일지 참조/후속 등록 race를 보장하지 않으므로 삭제 권한은 항상 false다.
[계약과 검증](CR2C3A_STORAGE_OWNERSHIP_INVENTORY.md)을 따른다.

**2026-09-16 CR2c3b 폴더 cap/준비 원장 구현:** 연구 v15는 source의 storage_cap_bytes 기본 0과
storage_operations를 추가한다. UI는 선택 폴더 GB 상한을 저장하고 worker는 signature 변경 뒤
기존 lease로 준비를 직렬화한다. 자료·테마·manifest·표시의 전체 byte 예산을 차감하고
완성 게시 전 용량과 owner/의도를 확인한다. 임시·완성 operation ID/경로를 기록한다.
용량 부족/다른 준비 작업은 실패 격리 대신 WAITING_STORAGE로 60초 후 확인한다.
기존 파일은 삭제하지 않으며 폴더 밖 DB/보고서와 NAS 원시 자료 용량 제어는 별도다.
[계약과 검증](CR2C3B_STORAGE_CAPACITY_LEDGER.md)을 따른다.

**2026-09-16 CR2c3c1 미완성 임시 정리 구현:** 연구 v16은 operation별 staging_cleanups를 추가한다.
현재 source root의 24시간 이상 지난 검증된 미완성 staging만 정리하며 완성 manifest가 있으면 무조건 보존한다.
worker/의도/기존 모든 캠페인 참조를 삭제 직전 DB fence에서 다시 확인한다. 알려진 파일만 개별 제거하고
marker 마지막 삭제/영속 READY로 중단을 재개한다. IO 실패는 별도 backoff이며 기존 수집/연구 실패와 분리한다.
최대 64 entry/20 작업과 약 0.25초 checkpoint 예산을 적용한다. heartbeat/취소는 write fence 밖에서 실행한다.
이 단계는 외부 일지 DB 전체 참조 조사/완성 자료 삭제 기능이 아니다.
[계약과 검증](CR2C3C1_INCOMPLETE_STAGING_CLEANUP.md)을 따른다.

**2026-09-16 CR2c3c2 완성 자료 복구 구현:** 기존 discovery가 완성 폴더부터 검증·등록한다.
용량 상한/서버 접속 실패 이전에 복구하며 등록 후 활성 job 수를 반영해 대기열이 차면 새 준비를 건너뛴다.
NAS 준비 뒤 반환 경로만 추가 등록한다. 동결 manifest/scope/worker fence와 등록 후 signature 확인은 유지한다.
기존 v16의 jobs/acceptances/준비 원장만 재사용한다. 완료/미등록/다른 범위 완성 자료도 보존한다.
경로 이동·압축 archive/외부 일지 DB 참조 전수 스캔은 추가하지 않는다.
[계약과 검증](CR2C3C2_COMPLETED_INPUT_RECOVERY.md)을 따른다.

**CR3 진행:** 다음 10절을 따른다. 날짜 확장은 CR3 평가 구간 정책과 연결한다.
CR2 전체와 24시간 자동 가설 연구 완료 판정은 아직 아니다.

**목적:** GUI 타이머 재실행에서 영속 캠페인으로 전환.

**수정 대상:** application/research_queue.py/research_search.py, research_process.py, research_repository.py, presentation/research_dialog.py와 process_control.py의 기존 수명 경계. 거대 범용 스케줄러를 만들지 않는다.

**입출력:**

- campaign revision + desired_state(RUNNING/PAUSED/STOPPED) → 다음 cycle/job 선택.
- 재시도 가능한 미완료 trial, 기존 데이터 미실행 가설, 관련 새 자료 순으로 선택.
- job/cycle 생성과 sequence 증가를 원자 저장. 동시 launcher에도 같은 cycle 1개.
- run 중단은 attempt에 남기고 완료 경계부터 복구. 앱 종료는 desired_state를 보존한 채 worker 종료; 명시 중지는 STOPPED.
- 진행 상태 RUNNING/WAITING_DATA/SEARCH_SPACE_EXHAUSTED/RESOURCE_BLOCKED/NEEDS_ATTENTION과 이유를 분리.
- 완료 job을 보관 상태로 전환하고 active backlog 제한만 적용. 참조 보호/디스크 cap, 실패 backoff와 재시도 상한.

**완료 기준:** 창 숨김·앱 종료·재시작·lease 만료 뒤 미완료 trial 재계산은 허용하되 완료 결과의 중복 확정과 독립 실험 중복 집계는 없음. 100개 누적 완료 후에도 active 용량이 있으면 101번째 정상 생성. 새 watermark 없이도 미실행 가설 처리. 무관 watermark에는 재계산 없음.

**테스트:** queue/repository/process/controller 및 fake clock, 두 worker claim·heartbeat loss, commit 경계 충돌, 앱 종료/재시작, 입력 파일의 외부 수정이 실행 중 campaign을 바꾸지 않음, disk full·누락 파일·NAS offline backoff·반복 오류 격리.

## 10. CR3 — 다양한 종목·기간 검증과 최종 구간 분리

**2026-09-16 CR3a1 개발 후보 선택 근거 격리 구현:** 전체 보고서/OOS의 상태와 사유가
개발 후보 적격을 바꾸는 경로를 재현하고 수정했다. 기존 evaluation 모듈에서 불변 DevelopmentEvidence를
만들며 search는 TRAIN/VALIDATION 상태·사유·요약만 사용한다. 원본 전체 보고서와 기존 v1 산술은 보존한다.
최종 성과를 극단적으로 바꿔도 동일 개발 fold의 선택 입력/후보 카드가 불변인 회귀를 추가했다.
이 단계는 원시 입력/품질 계산/현금 자체의 분리 완료가 아니다. 기존 요청/DB v16을 유지한다.
[계약과 검증](CR3A1_DEVELOPMENT_EVIDENCE_BOUNDARY.md)을 따른다.

**2026-09-16 CR3a2 독립 개발 명시 실행:** 기존 v1 평가의 TRAIN/VALIDATION 한 구간을 선택하는
DevelopmentPartitionSpec(v2)와 RAM projection을 추가했다. 전체 source identity/quality/children/OOS count는 제외한다.
직전 순위·테마의 원래 시각/ID를 보존한다. 새 engine으로 시작하고 warmup에서는 거래·전략 상태·후보를 만들지 않는다.
구간 내부 날짜의 상태는 유지하며 경계 포지션은 구간 종료 시각에 censor한다. 품질은 선택 자료에서 계산한다.
JSON single_run/rank_comparison·CLI만 연결했다. 원본 파일 hash 검증과 기존 v1/DB v16은 유지한다.
순위만 있거나 뒤늦게 받은 분봉을 warmup 근거로 판단하던 두 경우를 재현해 최소 수정했다.
이 단계는 전체 coverage 검증/여러 partition 성과의 portfolio 합산/자동 최종평가 완료가 아니다.
[계약과 검증](CR3A2_INDEPENDENT_DEVELOPMENT_INPUT.md)을 따른다.

**2026-09-16 CR3a3 유한 탐색 연결:** 제출 spec의 원본 ID/hash를 검증한 뒤 선택 입력/단일 평가로 유효 spec을 만든다.
제출 요청은 그대로 유지하며 durable experiment/job/trial/card는 파생 spec을 사용한다.
기본/no-trade/ablation/cost-stress·완료 cache·중단 재시도는 같은 개발 입력에 연결했다.
구간 밖의 원본 내용/전체 quality/ID 변경은 개발 identity를 바꾸지 않는다. 구간 안 payload 변경은 같은 revision ID여도 바꾼다.
원본 손상/틀린 요청 ID는 cache로 우회하지 않는다. DB v16/NAS/API/주문 경로는 그대로다.
[계약과 검증](CR3A3_PARTITION_LIMITED_SEARCH.md)을 따른다.

**2026-09-16 CR3a4a 원본/실행 캠페인 계약:** 기존 실행 request_json/예산/완료 판정은 유지하고
연구 DB v17의 source_request_json에 불변 원본 spec을 별도 저장한다. 이전 v16 행은 빈 기본값으로 동일하게 재구성한다.
별도 프로세스 register_campaign_request/CLI는 원본 검증·projection 후 등록한다. 재시작은 원본에서 만든 유효 spec/ID를 대조한다.
고정 범위 source discovery는 같은 projection을 사용한다. 개발 근거가 같은 새 자료는 acceptance만 추가하고 job/spec/원본 경로는 보존한다.
운영 예산 revision은 실행/원본 조회 명세에 같은 값으로 합성하며 두 불변 JSON은 덮어쓰지 않는다.
[계약과 검증](CR3A4A_PARTITION_CAMPAIGN_CONTRACT.md)을 따른다.

**2026-09-16 CR3a4b 화면 등록:** 기존 프로세스 관리자로 독립 개발 구간을 background 등록한다.
UUID별 절대 경로 request snapshot/result/cancel과 paused 선택 선저장으로 요청 고정/취소/commit 뒤 복원을 연결했다.
종료 코드·결과 ID·원본 evidence는 저장 job 한 행으로 대조한다. UI가 전체 export나 전체 job 목록을 읽지 않는다.
등록 중 실행/설정 호출을 막고 창 숨김은 비동기 취소다. 등록이 이미 commit됐다면 완료 job/원본/선택을 보존한다.
오류 문구가 상태 refresh로 덮이는 경로와 등록 중 늦은 state 호출을 재현해 보완했다.
[계약과 검증](CR3A4B_CAMPAIGN_REGISTRATION_DIALOG.md)을 따른다. DB v17/NAS/API/주문 경로는 유지한다.

**2026-09-16 CR3b1 완료:** 명시 1~200개 run의 동일 조건·자료/비용/코드 버전 검증과 읽기 전용 집계를 연결했다.
구간별 표본 부족/결측/실패/편중과 중복/겹침/자료 revision을 보존하고 독립 초기 현금 성과를 한 연속 계좌로 합치지 않는다.
구간 손익 분포와 최악 구간 MDD만 제공한다. 전체 search attempt census와 시장 coverage는 대체하지 않는다.
기존 DevelopmentEvidence/search/DB v17은 유지한다. [세부 계약](CR3B1_INDEPENDENT_DEVELOPMENT_COMPARISONS.md).
**2026-09-16 CR3b2 완료:** 작은 명시 비교 JSON/--compare-runs CLI와 연구 화면의 결과 비교 viewer를 연결했다.
기존 연구 v17을 read_only/timeout 1초로 열며 UI DB/원본 자료 로드·새 runner·마이그레이션은 없다.
자기 낮은 우선순위 프로세스/UUID 파일을 사용하고 종료 코드/DB/run scope/version 대조 뒤 KST 표를 적용한다.
취소/창 숨김/오류 시 이전 표 보존, 앱 종료 시 own child 정리를 검증했다. [세부 계약](CR3B2_INDEPENDENT_COMPARISON_VIEWER.md).
**2026-09-16 CR3b3 완료:** --validate-partitions가 고정 전략/원본 ID·hash/시간순 2~20개 개발 구간을 순차 검증한다.
source 한 번 로드·구간별 독립 입력/초기 상태와 scoped scientific ID·v17 원자 선점을 연결했다.
완료 캐시/취소·예산·자원 차단 재개, failed/cache-invalid/BUSY 보존과 구현 hash 고정·대조를 확인했다.
새 scope 종료 상태를 batch만 확정하여 취소 재선점 경계를 보호하며 RUNNING/구간 종료/최종 진행 파일을 저장한다.
comparison은 확인된 ID만이며 미시작 구간/전체 batch 완료와 구별한다. [세부 계약](CR3B3_SEQUENTIAL_DEVELOPMENT_VALIDATION.md).
**2026-09-16 CR3b4 완료:** 연구 창의 여러 구간 순차 검증 버튼으로 별도 실행 창을 연결했다.
파싱된 절대 경로 snapshot을 UUID별 child 요청으로 저장하며 250ms 진행 파일만 GUI에서 확인한다.
모든 미시작/실패/선점 확인 상태와 독립 손익을 표시한다. 일부 비교 완료와 전체 batch 완료를 구별한다.
범위/기간/역할/ID/구현 hash/native 종료를 대조한다. 닫기는 비동기 취소이며 앱 종료는 own child 정리다.
같은 요청 이어서 실행은 마지막 snapshot을 재사용한다. 완료 cache는 기존 DB를 사용한다.
자동 반복/앱 재시작 batch 복원/강제 종료 orphan 복구/선택 편의 UI는 후속이다. [세부 계약](CR3B4_SEQUENTIAL_VALIDATION_DIALOG.md).
**2026-09-16 CR3c1 완료:** 버전/salt/종목코드 SHA256의 stock_hash_partition/v1을 명시 partition v3에 고정했다.
2~20 bucket은 가격/순위/성과와 무관하고 신규 종목은 기존 배정에 영향 없이 배정한다. v2 JSON/기본 의미는 유지한다.
기존 시간 projection은 full as-of TOP20/peer bars/테마를 보존한다. runner는 선택 bucket만 엔진/전략 평가에 보낸다.
각 별도 명시 실행의 새 현금/빈 상태와 warmup/경계 censor를 유지한다. 실행 전 코드와 v3 종목별 보고서 체결 합/허용 종목을 검사한다.
서로 다른 bucket은 기존 동일 조건 집계에 섞지 않는다. shared context 개발 검증이며 미사용 최종 종목 holdout 보장은 아니다.
기존 명시 partition 경로만 연결했다. --validate-partitions v1/화면은 시간 v2만이며 그룹×시간 자동 실행은 다음 CR3c2다.
[세부 계약](CR3C1_FIXED_SYMBOL_PARTITIONS.md).
**2026-09-16 CR3c2 완료:** independent_development_validation/v2는 한 hash 정책의 2~20개 bucket과
시간순 1~20개 개발 fold를 받아 총 200개 이하 step을 fold 먼저/bucket 다음 순서로 실행한다.
기존 source 한 번 검증/독립 v3 엔진/atomic claim/cache·취소·예산 처리를 재사용한다.
모든 fold/bucket/key·미시작/실패/소유자 확인 상태를 남긴다. 그룹 비교는 동일 bucket의 확인된 ID만으로 분리한다.
추가 bucket은 기존 bucket의 scientific ID를 바꾸지 않는다. group comparison COMPLETE를 전체 batch 완료로 해석하지 않는다.
기존 v1 요청/결과와 DB v17은 유지한다. 기존 화면은 v2 child를 만들기 전에 안내/거절한다. [세부 계약](CR3C2_SYMBOL_WINDOW_VALIDATION.md).
**2026-09-16 CR3c3 완료:** 기존 순차 검증 창에서 v1/v2 JSON을 낮은 우선순위 child로 실행/취소하며 250ms에 전체 단계와 그룹별 결과를 표시한다.
불변 snapshot을 명시 재개하고 완료 cache/부분 상태를 유지한다. GUI DB/원본 입력 읽기와 새 관리 계층은 없다.
matrix 순서/기간/IDs·그룹별 요청/완료/미시작·식별 결과 범위를 검증하고 종료 오류나 위조 진행 결과에서는 이전 두 표를 보존한다.
그룹별 적격/양수 구간·손익 중앙값/최악 구간 MDD를 표시한다. 그룹 간 합산과 부분 표본 판정으로 전체 완료를 추정하지 않는다.
검증 중 Windows GUI 읽기/child 결과 rename 충돌을 실제 반복 실행으로 재현하고 기존 발행 함수의 해당 오류만 제한 재시도하도록 수정했다.
[화면 계약과 검증](CR3C3_GROUPED_VALIDATION_DIALOG.md).
**2026-09-16 CR3d1 완료:** 기존 split 모듈에 final_holdout_batch/v1을 추가하고 연구 DB v18에 최종 창/접근·노출 event를 보존한다.
최초 batch의 후보 scientific hash/자료 hash/평가 기준을 고정한다. 변경 batch·겹치는 창·노출 창의 최종 재사용을 막는다.
같은 기술 요청은 멱등이고 같은 batch의 새 접근은 이력만 추가한다. 원장 mutation은 BEGIN IMMEDIATE로 원자 처리한다.
기간 key는 KRX active UTC 구간으로 고정하여 자료 revision/세션 profile/이름으로 우회하지 않는다. 기존 v17 기록과 v17/v18/v19/v20 read-only 비교를 보존한다.
이 단계는 원장 기반이다. 기존 v1 실행/CLI/화면에 자동 적용하거나 원장 밖 자료가 미사용임을 증명하지 않는다. [계약과 검증](CR3D1_FINAL_HOLDOUT_LEDGER.md).
**2026-09-16 CR3d2a 완료:** 고정 후보 full scientific hash와 단일 OOS/세션/전체 동결 source binding을 검증했다.
기존 시간 projection을 공유하여 final warmup/active/as-of context만 복사하고 전체 원본 identity/통계는 engine 입력에 넣지 않는다.
과거 연구의 실제 입력 captured_range와 evaluation_spec/warmup 합집합을 같은 BEGIN IMMEDIATE에서 검사한 뒤 접근을 저장하고 준비 입력을 반환한다.
연속 runner의 보고서 fold 사이/밖 입력도 이미 사용한 자료로 취급하며 평가 정책만으로 실제 사용 범위를 줄이지 않는다.
unknown/malformed footprint는 차단하며 기존 일반 runner/개발 projection은 final 입력을 run 시작 전에 거절한다.
raw bytes는 trusted 준비 내부에서 먼저 검증한다. 전체 시스템 미사용 증명이나 실제 실행 선점 허가는 아니다.
[세부 계약](CR3D2A_FINAL_HOLDOUT_PREPARATION.md).
**2026-09-16 CR3d2b 완료:** research v19에 batch+candidate별 final execution 원자 소유권을 추가했다.
준비된 고정 후보만 별도 scope로 실행하고 후보마다 새 엔진/현금/상태를 사용한다. 완료 output 게시 뒤 run/execution을 확정한다.
완료 cache는 DB/report/output을 모두 검증한다. 동시 실행은 하나만 claim하며 RUNNING은 BUSY다.
FAILED/CANCELLED는 terminal이고 자동 재시도하지 않는다. 실행 중 개발 노출을 막고 후보 간 비교·합산·재선택을 만들지 않는다.
[세부 계약](CR3D2B_FINAL_HOLDOUT_EXECUTION.md).
**2026-09-16 CR3d2c 완료:** research v20에 final execution generation과 recovery 요청 감사 원장을 추가했다.
FAILED/CANCELLED 후보만 candidate별 request ID/owner/reason을 명시해 같은 run/code/input으로 복구한다.
요청 기록과 claim을 분리하고 claim 트랜잭션에서 run/execution RUNNING, generation 증가, request CLAIMED를 함께 확정한다.
output manifest가 남아 있으면 불확실한 완료로 보고 차단하며 부분 DB 행은 기존 immutable equality로만 재사용한다.
같은 요청은 멱등이지만 CLAIMED 요청은 재실행하지 않고 매 추가 복구는 새 요청을 요구한다. 자동 retry/RUNNING orphan 회수는 없다.
[세부 계약](CR3D2C_EXPLICIT_FINAL_RECOVERY.md).
**2026-09-16 CR3d3a 완료:** independent_final_holdout_request/v1에 locked batch, 1~200 fixed candidates,
접근 request ID/시각, owner token과 선택 recovery를 고정했다. parser는 source/DB를 열기 전에 크기·정확한 필드·현재 후보 hash·시간대·recovery 대상을 확인한다.
--evaluate-final 별도 프로세스가 준비→접근 원장→실행/복구를 연결하며 result/cancel이 요청·DB·frozen source·run artifact를 덮지 못한다.
기존 final result에 status/kind만 추가하고 DB v20/엔진/NAS/API/계좌·주문은 유지한다. [세부 계약](CR3D3A_FINAL_HOLDOUT_PROCESS.md).
**2026-09-16 CR3d3b 완료:** 전략 연구 창의 최종 평가 별도 창이 불변 request snapshot을 UUID 작업 파일로 복사하고 기존 child를 실행/취소한다.
GUI는 DB/source를 열지 않고 exit/status·batch/window·candidate 순서·구현 hash·recovery ID를 검증한 결과만 표시한다.
완료 cache와 미시작 후보 이어서 실행을 지원하며 FAILED/CANCELLED는 선택·사용자 reason·새 request ID/owner로만 명시 복구한다.
오류/취소에서 이전 표를 유지하고 창 닫기는 블로킹하지 않으며 앱 종료에 자신의 child/임시 파일만 정리한다. [세부 계약](CR3D3B_FINAL_HOLDOUT_DIALOG.md).
**2026-09-16 CR3d3c 완료:** `final_holdout_exposure_request/v1`과 `--expose-final`이 DB/window/batch/spec과 RUNNING 후보 부재를 대조한 뒤 EXPOSED_DEVELOPMENT를 원자 기록한다.
화면은 검증된 final 결과와 사용자 근거가 있을 때만 별도 UUID child를 실행하고 DB를 직접 열지 않는다.
결과 envelope의 DB/window/batch/request/state를 검증하며 취소/오류에서 기존 최종평가 표를 유지한다. 전환은 되돌릴 수 없고 같은 창은 final로 재사용할 수 없다. [세부 계약](CR3D3C_FINAL_EXPOSURE_FEEDBACK.md).
**다음:** 11절 CR4 자동 가설 생성과 사용자 화면을 단계별로 연결한다.
자동 최종평가·영속 재개·그룹 coverage/편중·새 날짜 확장은 후속이다.

**목적:** 같은 자료를 많이 시험했다는 사실과 새로운 자료에서도 통한다는 증거를 구별.

**수정 대상:** research_splits.py/research_evaluation.py/research_search.py, scripts/run_research.py, research_repository의 보고·선택 저장, data_source의 partition 제공.

**입출력:**

- 동결 검증 정책 → 개발 train/validation rolling 창, 고정 종목 분할, final window/batch.
- generator는 DevelopmentEvidence만 받음. v1 전역 성과·outcome raw를 검색 입력으로 쓰지 않음.
- 독립 v2 partition은 초기 현금/빈 포지션과 warmup 진입 금지. 내부 날짜 상태는 연속 유지.
- final 입력은 개발 bundle에서 제외. 후보 batch 고정 → 평가 접근 기록 → 보고. 결과를 다시 개발에 쓰면 EXPOSED_DEVELOPMENT와 다음 미사용 창 필요.
- 종목별/날짜별/시간별/당시 시장상태의 표본·결측·편중·성과를 함께 기록. post-hoc market regime을 당시 Factor로 재사용하지 않음.
- 성과 적격 기준·비용 모델·최소 표본은 policy에 고정. 표본 부족은 불합격/무거래와 구별.

**완료 기준:** final 결과를 극단적으로 변경해도 개발 후보 순위·다음 가설이 불변. 수정 후보가 이미 본 창을 final로 재사용할 수 없음. 검증 전반에서 최신 테마·미래 봉·사후 일지 설명 누수 없음.

**테스트:** splits/evaluation/search/comparisons 회귀, OOS canary leakage, 종목 분할 seed 고정·신규 종목, training 뒤 warmup/gap/purge, final duplicate access와 동일 attempt 재시도, 데이터 노출 후 기간 재분류, 단일 종목 성과 합≠공통 현금 portfolio fixture, 전체 equity MDD, 경계 포지션 censor.

**보고:** 전체 시도/고유 trial/재시도/실패/자료부족, 사용한 개발 기간, 미사용 최종 기간, 편중을 표시. 이 단계에서 자동 주문 전송은 없다.

## 11. CR4 — 자동 가설 생성과 사용자 화면

**목적:** 사용자가 매 실험을 지정하지 않아도 연구하고, 원하면 한 파라미터 비교를 할 수 있게 한다.

**수정 대상:** research_families.py/research_search.py/research_queue.py, research_repository.py, presentation/research_dialog.py. 새 일반 가설 정책이 커지면 단일 research_hypotheses.py에 순수 생성/계보 정책을 둘 수 있다. C1 context_candidates의 뉴스 가설 표는 재사용하지 않는다.

**입출력:**

- 허용 Family/Factor/값 범위 + DevelopmentEvidence + seed → parent/변형 이유/evidence refs가 있는 중복 없는 가설 후보.
- 기준선/무거래·한 변수 영향 → 등록 조합/반증 변형 → 두 Family 순환. 생성 개수·범위 사전 검사.
- 미등록 새 계산식은 DRAFT_REQUIRES_IMPLEMENTATION; 자동 코드 실행 없음.
- UI는 자동 연구 시작/일시정지/중지, 데이터 기간, CPU 목표·메모리/디스크, 현재 trial·완료/재시도·대기 이유, 가설 근거/검증 범위를 표시.
- 수동 한 변수 비교는 같은 기준선에서 별도 cycle로 실행, baseline 변경은 새 campaign revision.
- 자원·운영 설정과 연구 범위·평가 기준을 화면에서 구별. JSON은 고급 입력으로 유지하되 자동 운영에 파일 수동 선택을 반복 요구하지 않음.
- 알림은 새로운 검증 상태·중요 오류·필요한 사용자 조치에 한정. 같은 결과 재확인마다 알림을 내지 않음.

**2026-09-16 CR4a 완료:** `research_hypotheses.py`에 `research_hypothesis/v1`과
`registered_single_parameter_neighbors/v1`을 추가했다. 호출자가 명시한 등록 Family·Factor·정수 허용값만
받고, 정규화된 기준 전략과 정확히 한 필드만 다른 READY 후보를 기존 config 검증으로 다시 확인한다.
seed는 생성 순서에만 쓰므로 같은 기준·부모·개발 근거·변경 내용은 seed나 재시도와 무관하게 같은 ID다.
연구 DB v21의 `research_hypotheses`와 `research_hypothesis_parents`는 canonical 문서와 부모 edge를
불변·원자 저장하며 부모가 먼저 없으면 전체 배치를 롤백한다. 기존 baseline/no-trade trial과 campaign/final은 유지한다.
[세부 계약](CR4A_HYPOTHESIS_LINEAGE.md). 자동 campaign 연결, 두 Family 순환, 실패 부모 반증,
미등록 계산식 draft와 UI는 CR4b 이후다.

**CR4a 이후 연결:** CR4b에서 저장된 READY 가설을 기존 `ExperimentSpec`/campaign job으로 연결하고,
완료 가설 중복 실행 방지·두 Family 순환·범위 소진 대기 사유를 구현한다.

**2026-09-16 CR4b 완료:** 가설의 `research_scope_id`를 `campaign:<id>`와 대조하고,
명시적으로 `auto_hypotheses=true`인 일시정지 campaign에만 READY 묶음을 등록한다.
연구 DB v22 `research_campaign_hypotheses`는 AVAILABLE→ENQUEUED 상태, 등록/예약 순서와 대응 job을
한 트랜잭션에서 고정한다. 활성 worker만 매 반복 최대 한 가설을 예약하며 마지막 Family 다음 Family를
우선한다. job은 가설의 전체 전략 config를 baseline으로 사용하고 parameter grid/ablation/cost stress 없이
기존 baseline/no-trade trial만 실행한다. 재시작·재시도는 바인딩을 되돌리지 않는다.
가설 미등록·범위 소진·기준 experiment 부재는 `WAITING_HYPOTHESIS` 사유다.
[세부 계약](CR4B_CAMPAIGN_HYPOTHESIS_SCHEDULING.md).

**2026-09-16 CR4c 백엔드 완료:** 완료한 자동 가설의 baseline run에서 기존 `build_development_evidence`로
TRAIN/VALIDATION만 복사하고 콘텐츠 주소형 evidence snapshot을 만든다. campaign 정책은 Family별 등록
파라미터 허용값, seed, 회차 생성 상한과 전체 가설 상한을 고정한다. 부모 설정에서 한 필드만 바꾼 자식을
생성하며 같은 Family+전체 설정은 재등록하지 않는다. 연구 DB v23 부모+근거+정책 revision 원장은 생성·소진·차단과 이유를
남기며 자식 등록 뒤 원장 commit 전 종료도 같은 ID로 복구한다. final 수치·원시 report·자동 final은 사용하지 않는다.
[세부 계약](CR4C_AUTOMATIC_FOLLOWUP_HYPOTHESES.md).

**2026-09-16 CR4c 화면 완료:** 전략 연구 창에 `자동 가설 설정 / 현황`을 추가했다. 일시정지 상태에서
Family, 한 번에 비교할 등록 파라미터, 허용값, seed, 회차 생성 상한과 전체 가설 상한을 campaign policy
revision으로 저장한다. 해당 Family가 비어 있으면 기존 기준 실험의 정규화 전략과 사용자 설정 근거 snapshot으로
기준/첫 이웃 가설을 멱등 등록한다. 표는 가설 종류·변경·큐 상태·개발 근거·revision별 확장 상태를 표시한다.

**2026-09-16 CR4 완료:** 최종 코드 상태의 연구 전체 회귀 626개가 통과했다. 자동 가설은 등록된
계산식과 명시 허용값 안에서만 동작하고, 개발 근거와 final 경계를 유지하며, 설정 화면·일시정지 revision·
재시작 멱등성을 포함한다. 실제 24시간 운전과 NAS 전체 규모 자원 계측은 V1 전까지 완료로 보지 않는다.

**다음:** A5 계좌별 일지 피드백 단계로 진행한다.

**완료 기준:** 합성 자료에서 사용자의 다음 클릭 없이 가설 생성→실험→다른 허용 가설→재검증이 이어진다. 같은 데이터 새 가설은 실행, 같은 조건 재시도는 독립 근거로 세지 않음. 범위 소진 시 이유 있는 대기.

**테스트:** 두 Family 순환·중복·상한·불허 Factor·동일 seed, baseline 주변과 실패 부모의 반증 가설, final refs 유입 거절, 수동 한 변수와 자동 요청 결과 일치, 설정 변경 중 실행 snapshot 유지, 창 숨김/중지/재시작·알림 중복.

## 12. A5 — 모의 체결과 계좌별 일지의 연구 피드백

**2026-09-16 A5a 완료:** 중앙 SQLite/PostgreSQL 실행 원장을 계좌 scope와
`accepted_sequence` 커서로 읽는 repository/API/PC client 경계를 추가했다. 조회는 여러 run을
이어 보되 다른 계좌와 binding revision 불일치를 거절하고, event 원본 ID와 intent 계보를 함께
반환한다. 아직 일지 체결 저장·aggregate 대조·FeedbackEvidence export는 하지 않는다.

**2026-09-16 A5b 완료:** 매매일지 DB v9에 상세 `FILL`과
`BROKER_FILL_AGGREGATE` 근거를 분리해 저장한다. source event ID와 계좌·KST 거래일·broker
주문/체결 key를 함께 사용하며 run ID는 계보에만 남긴다. 저장 행과 cursor는 같은 트랜잭션에서
확정하고, page 재조회는 멱등이며 충돌·공백은 전부 롤백한다. aggregate는 상세 누락수량만
표현하고 후착 FILL 수량과 합산하지 않는다. 기존 `trade_fills`와 사용자 복기는 변경하지 않았다.

**2026-09-16 A5c 완료:** kt00007 체결 요약과 projection 상세 FILL을 canonical 계좌·KST
거래일·주문번호·종목·매수/매도 단위로 대조한다. 수량과 금액이 모두 맞는 정확 일치, 상세만,
요약만, 수량 일부, 금액/체결 ID 충돌을 구분한다. 상세가 있으면 결과 체결에는 상세 한 벌만
선택하고 요약을 더하지 않는다. 일부·충돌·요약 전용은 미확인으로 남긴다. canonical alias의 같은
broker 체결 ID도 한 번만 세며 run ID 변경은 새 체결을 만들지 않는다. 기존 화면 체결, 사용자
복기와 수동 묶음은 변경하지 않았다.

**2026-09-16 A5d 완료:** A5c 대조 결과와 kt00015 비용을 회차별로 연결해
`journal_feedback_evidence/v1` 문서를 만든다. 계좌·평가기간·전략·선택 run과 선언 시각,
run/decision 계보, 체결 품질, 관련 연구 링크의 PIT 상태, 최종 검증 노출 상태를 모두 내용 hash에
포함한다. 부분/충돌/요약 전용 체결, 비용 누락, 열린 포지션은 확정 순손익을 만들지 않는다. 사전
선택·체결 당시 근거·개발 자료만 사용한 완결 문서만 ForwardEvidence로 변환한다. 문서는 기존 중앙
generic store의 비공개 컬렉션에 불변 저장하며 사용자 복기와 수동 유형은 수정하지 않는다.

**2026-09-16 A5e1 완료:** 저장된 FeedbackEvidence와 호출자가 명시한 최소 거래일·거래 수를
`journal_feedback_review_policy/v1`로 고정한다. 적격 evidence만 실제 비용 포함 순손익·승패·승률·
비용 비율을 계산해 개선안 생성 가능 상태로 만들고, 원본 부적격과 표본 부족은 각각 BLOCKED와
INSUFFICIENT_SAMPLE로 남긴다. 같은 evidence와 정책은 같은 review ID이며 저장 전에 원본으로 다시
계산해 일치 여부를 확인한다. 비공개 중앙 문서에 저장하고 사용자 복기·수동 유형·전략 원본은 쓰지 않는다.

**2026-09-16 A5e2 완료:** 적격 review와 등록 Family·factor, 정규화된 기준 전략과 명시 허용값으로
`journal_feedback_improvement/v1` 후보를 만든다. 각 후보는 정확히 한 정수 파라미터만 다르고
seed는 반환 순서에만 영향을 준다. 원본 review의 계좌·전략·evidence·평가 방향이 일치하는 제안만
비공개 중앙 문서에 불변 저장한다. 제안은 개선을 확정하지 않고 전략·연구 queue·주문을 변경하지 않는다.

**2026-09-16 A5e3 완료:** 명시 채택된 proposal을 부모 전략과 분리된
`feedback_strategy_version/v1`으로 만들고, 기존 개발 캠페인의 명시 template에서 exact
baseline/no-trade 재검증 spec을 만든다. final holdout template을 거절하고 proposal당 한 전략
버전과 버전당 한 재검증 request만 허용한다. request를 queue보다 먼저 저장하고 중앙 문서와 연구
SQLite 사이 중단은 결정적 strategy/request/experiment/job/receipt ID로 재시도해 연구 job을 중복
생성하지 않는다. 사용자 원본과 주문 경로는 변경하지 않는다.

**다음:** A5 완료. O2-M에서 최종 검증 후보·검증된 mock 계좌·동결 실행 정책을 연결하되,
동시 전략/포지션·자금·손실·시장시간·장애·후보 교체 정책이 모두 정해진 경우에만 자동 모의운영한다.

**목적:** 기존 실행 원장을 훼손하지 않고 진행 중 결과를 일지에서 보고 다음 연구에 반영.

**수정 대상:** execution_repository.py/order_lifecycle.py의 기존 read 경계, 중앙 실행 event 읽기 API, journal 저장/조회 projection, journal_enrichment.py, research data source와 FORWARD_EVALUATION 계약.

**입출력:**

- 계좌 scope + source ledger/cursor → 검증된 상세 FILL projection; cursor/행 원자 commit.
- source_event_id와 계좌·거래일·주문·체결 key로 멱등화. run_id는 계보이며 중복 회피 수단이 아님.
- BROKER_FILL_AGGREGATE는 수량/누락 표시만. 후착 FILL로 누락 해소, 누적량에 다시 더하지 않음.
- kt00007 요약과 상세 FILL의 충돌·겹침은 대조. 정확한 연결 불가 시 미확인으로 두고 수익을 이중 합산하지 않음.
- 계좌·기간·전략·선택 편향·체결 품질이 표시된 FeedbackEvidence로 동결 export.
- 기계 복기/연구 제안은 파생 revision으로 저장. 사용자 메모·수동 유형·전략 원본 보존.

**완료 기준:** 재시작/재조회/부분 체결/누적 복구/늦은 상세에도 실제 수량·손익 중복 없음. 모의와 real 일지 분리. 시뮬레이션 결과가 broker fill로 삽입되지 않음. 피드백으로 수정한 전략은 새 버전으로 재검증.

**테스트:** order_lifecycle/execution_repository/journal_research_links/enrichment/forward_report, 같은 초의 여러 상세 체결, 같은 주문번호 다른 거래일·계좌, 취소 후 체결, run 변경, REST 누적→WS 상세, 비용 미확인, 사용자 메모 불변, 피드백 export PIT/노출 상태.

**후속 O2:** 자동 mock 운영은 이 읽기 피드백과 별도 단계다. frozen forward profile+계좌+기간+주문/자금/손실/장애 한도 안에서 후보를 적용하는 별도 실행 조정 계약과 복구 시험을 먼저 완료한다. 연구 worker에 주문권한을 추가하지 않는다. 실제 활성화는 준비된 구체 정책과 기존 사용자 승인 범위에 따라 별도로 진행한다.

## 13. O2-M — 사전에 정한 정책 안의 자동 모의운영

**목적:** 연구→재검증→모의투자→일지 피드백을 사용자 클릭 없이 연결할 수 있게 하되 연구 계산과 계좌 실행의 소유자를 구분한다. 계좌·자금·손실 기준의 구체 값은 운영 설정으로 먼저 고정한다.

**수정 대상:** application/forward_evaluation.py, domain/execution_activation.py, central_server/execution_runtime.py와 기존 candidate/Decision 연결 경계, execution_repository.py. 연구 worker는 후보 패키지만 내보내고 주문 API를 호출하지 않는다.

**입출력:** 최종 평가를 마친 candidate/package hash + 검증된 mock scope + 동결 forward profile → shadow 확인 → 제한된 mock run → 기존 O1 event/O2 보고서 → A5 피드백. 후보 적용 정책에는 최대 동시 전략/포지션·자금/손실·평가기간·지원 시장/시간·입력 경로·장애 한도·신규 버전 적용 시 기존 포지션 처리·중지/복구를 포함한다. 미정 필드는 자동 운영을 BLOCKED로 두고 과거 연구는 계속한다.

**완료 기준:** 정한 정책 안에서만 후보를 순차 평가하고, 같은 계좌에 여러 비교 run이 중복 주문하지 않는다. 진행 중 spec을 바꾸지 않으며 후속 후보는 이전 run의 주문/포지션 정리 정책을 충족한 뒤 시작한다. 재시작은 broker와 대조하고 응답 유실 주문을 재전송하지 않는다. 실패한 후보를 자동으로 실계좌에 승격하지 않는다.

**테스트:** fake broker로 중복 candidate 제출, lease 만료, 계좌 변경, 손실/자금 한도, 미체결 상태의 후보 교체, 응답 유실·늦은 체결·긴급 중지, 데이터 단절 후 복구, 동일 Decision의 NAS/직접 대조 중 주문 담당 run 1개. 실제 mock 운영은 준비된 profile과 활성화 상태를 기록해 별도 확인한다. 이 문서 작성으로 transport 플래그를 변경하지 않는다.

**문서:** FORWARD_EVALUATION_CONTRACT와 KIWOOM_MOCK_EXECUTION_CONTRACT에 실제 구현한 적용/복구 정책을 반영한다. 새로운 실계좌 자동운영 정책은 이 단계에 포함하지 않는다.

**2026-09-16 O2-Ma 완료:** `mock_automation_operating_spec/v1`이 최종 후보/result hash,
현재 검증된 mock binding, forward profile과 동시 전략/포지션·자금·손실·데이터/장애·후보 교체·
중지·복구 값을 불변 고정한다. 미정값과 scope/profile/SHADOW 불일치, 현 O1 범위를 넘는 다중
전략/포지션·비정규장 session은 `BLOCKED`다. 현재 binding과 일치하는 명세만 저장하며 runtime과
주문 transport는 켜지 않는다.

**2026-09-16 O2-Mb 완료:** 입장 시 현재 mock binding·최신 SHADOW revision·CR3 final
batch/run/result를 다시 대조한다. spec당 admission과 자동 run ID를 결정적으로 만들고 기존 O1의
account 단일 lease를 재사용한다. runtime은 신규 주문을 닫은 채 lease를 얻으며 다른 수동/자동
run과 충돌하면 receipt와 주문을 만들지 않는다.

**2026-09-16 O2-Mc 완료:** account lease를 heartbeat로 확인하고 신규 주문을 다시 닫은 뒤
broker 전체 복구를 기존 주문·포지션·예약자금·검증된 당일 손익·데이터 공백·unknown·재접속·
잔고 불일치 한도와 대조한다. 미확인 값은 `BLOCKED`, 통과도 `CLEARED_ORDERS_DISABLED` revision으로
남겨 아직 주문을 열지 않는다.

**당시 다음 단계(이력):** O2-Md에서 최신 recovery decision 이후에도 lease·binding·시장시간·데이터 freshness·
자금/손실/장애 한도를 매 Decision 직전에 다시 검사하는 지속 gate를 만들고, 같은 Decision을
결정적 intent로 한 번만 O1에 넘기는 경계를 연결한다. 긴급 중지는 신규 주문을 즉시 닫고 기존
주문·포지션은 broker 대조/명시 정책으로 처리한다.

**2026-09-16 O2-Md 기본 구현(안전 보완 미완료):** 각 action Decision 전에 현재 binding·account lease·KRX 정규 연속장,
Decision/account/order freshness, 당일 순손익 scalar와 FIFO/broker 비용 출처 문자열,
데이터 경로/공백, 자금·손실·장애 한도와 기존 O1 비종결 intent를 다시 검사한다. 승인 gate를 먼저
저장하고 결정적 KRX LIMIT intent 하나를 실행 잠금 안에서 제출한 뒤 신규 주문을 다시 닫는다.
같은 Decision 재호출은 기존 O1 intent를 반환하며 broker로 재전송하지 않는다. 긴급 중지는 신규
주문만 닫고 이후 새 recovery를 요구한다.

**재검토 결과:** 중지와 제출의 경합, 체결 전 잔고 재사용, terminal 주문 오판정,
손실 도달값·평가기간 누락을 확인했다. 출처 문자열은 실제 손익 근거 검증이 아니다.
[감사 결과](O2MD_SAFETY_AUDIT_20260916.md)를 따라 위 설명을 안전 완료로 해석하지 않는다.

**2026-09-21 O2-M 완료:** M0의 영속 control/stop/resume/ENTER·EXIT 안전 gate, Me1의 동결 후보
게시, Me2의 실제 risk snapshot과 account bundle 단일 owner, Me3의 지속 runner·supervisor·운영 UI와
READY 명세 게시까지 연결했다. 게시→입장→위험 대사→가짜 매수·매도 체결→A5 계좌별 매매일지
투영을 실제 저장소/runtime으로 검증했다. shadow monitor 자체에는 주문권한을 붙이지 않았고 게시만으로
주문이 시작되지 않는다. **현재 다음은 V1 누적·장시간 검증**이다.

## 14. V1 — 연속 운전 완료 판정

**목적:** 단위 테스트 통과와 실제 장시간 사용 가능을 구별한다.

**수정 대상:** 필요한 운영 진단과 기존 테스트/검증 script만. 제품 구조 추가 없음.

**입력/산출:** 고정 bundle·campaign 정책·PC 자원 한도·NAS build → 재현 가능한 운영 보고서. 데이터 기간/종목/건수, 실행 시간, 고유 trial/attempt, 최고 RSS, CPU 관측 구간, DB/입력/임시파일 증가량, queue lag, 중단/복구/결측을 기록한다.

**완료 기준 및 방법:**

1. CR0~A5 관련 회귀 및 필수 핵심 회귀 종료 코드 0. Qt 종료 후 네이티브 오류도 실패로 처리.
2. 작은 고정 fixture 30분 반복으로 정확성·재시작·누수 확인 후 실제 규모 bundle로 확대.
3. 실제 PC 24시간 운전에서 메인 실시간 화면과 수집이 설정한 허용 범위 안에서 유지되는지 비교. 시험하지 않은 운영시간은 완료로 보고하지 않음.
4. 연구 중 NAS 단절·복구, 앱 종료·재시작, worker 강제 종료, disk cap, 계좌 변경과 페이지 failover를 재현.
5. 새 데이터 없이 새 가설 진행, 새 거래일 자료 반영, scope별 journal 피드백까지 한 흐름으로 확인.
6. NAS 변경분은 누적 배포 후 /health server_build, PostgreSQL migration/왕복/롤백, 실전·mock queue 분리를 검증. .env/postgres-data/server-data 보존·백업 규칙 유지.

24시간 검증을 수행할 실행 환경/시간이 준비되지 않았으면 구현 완료와 운영 검증 대기를 분리해서 보고한다. 이번 설계 작업에서 장기 시험을 실행했다고 쓰지 않는다.

## 15. 문서와 되돌리기

- 현재 동작만 ARCHITECTURE_CURRENT에 쓰고 발견/해결 범위는 AUDIT_REPORT에 남긴다.
- API/DB/모듈 실제 변경 때 API_CONTRACT/DB_SCHEMA/MODULE_MAP 및 요청 포맷·CHANGELOG를 갱신한다.
- 문서의 구현 상태와 CR/A/V 체크는 해당 테스트 증거가 있을 때만 완료로 바꾼다.
- 연구 v2 OFF는 신규 campaign만 중지하고 기존 결과를 삭제하지 않는다.
- account v2 OFF/구 NAS는 신규 계좌 수입·sync를 중지한다. 계좌 없는 쓰기로 돌아가지 않는다.
- 새 journal schema를 구 앱으로 열어 우회하지 않는다. 되돌리기는 검증된 백업과 코드 버전 쌍을 사용한다.
- 실제로 확인되지 않은 보호 저장소/HTTPS 지원, 계좌 응답 품질, NAS coverage는 구현 중 좁혀 검증한다. 결과가 설계 가정과 충돌할 때 해당 항목만 재검토한다.

다음 작업은 **A4: 실시간 스냅샷·계좌 선택 UI·중앙 journal v2 동기화와 백업 완성**이다. 그 뒤 CR1에서 다기간 replay 규모를 계측하며 CPU batch 양보와 실제 RSS 제한도 함께 완료한다.

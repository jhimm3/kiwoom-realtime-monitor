# GPT-5.6 Sol 구현 인계 계획

기준: 2026-09-12 / `0e93780` / `v2.0.0`  
상태: **O1 수동 모의주문 게이트웨이와 O2a 전진평가 기반 완료. 실제 mock 응답·주문 왕복 표본과 사용자 기준값 확정 전에는 보고서가 승격을 허용하지 않으며 D4 자동 연결과 O2b 실거래 경계는 미착수다.**  
선행 감사: [RESEARCH_ENGINE_ARCHITECTURE_AUDIT.md](RESEARCH_ENGINE_ARCHITECTURE_AUDIT.md)

## 1. 실행 방향

첫 제품 결과는 **현재 TOP20 관심순위에서 관측 가능한 KRX 분봉 돌파 후보를 감지하고, 근거·보류 이유를 남기며, 같은 입력으로 재생해 내부 모의 결과를 확인하는 것**이다. 뉴스·테마·네 시장 유형이 모두 완성되어야 시작할 수 있는 구조로 만들지 않는다. 기존 앱·뉴스창·일지를 유지하고 필요한 저장·판단 경계만 추가한다.

이번 사용자가 승인한 것은 이 계획 작성이다. 실제 구현 담당은 Sol이며, 이 문서 자체가 코드 실행·NAS 배포·모의/실계좌 주문 승인은 아니다. 구현을 시작하라는 후속 지시를 받으면 H1부터 작은 변경으로 진행한다. 현재 detached HEAD이므로 구현 시 `codex/` 접두사 작업 브랜치를 만든다. 현재 감사 단계에서는 브랜치를 변경하지 않았다.

모든 경로는 저장소 루트 기준이다. 아래 `application/...`, `domain/...`, `infrastructure/...`, `presentation/...`, `central_server/...`는 `src/kiwoom_monitor/` 아래다. **신규**라고 표시한 파일·API·테이블 이름은 제안이며 현재 존재하는 구현이 아니다. 기존 파일에 응집성 있게 추가할 수 있으면 새 파일을 만들 필요가 없다.

짧게 쓴 `persistence/...`는 `src/kiwoom_monitor/infrastructure/persistence/...`, `kiwoom_rest/...`는 `src/kiwoom_monitor/infrastructure/kiwoom_rest/...`다. 단계에 여러 파일이 나와도 모두 수정하라는 뜻은 아니다. 해당 입출력의 실제 생산자·저장자·직접 소비자만 좁혀 수정한다.

### 사용자 확정 데이터 수집 방향 (2026-09-12)

첫 구현 우선순위는 앱 UI나 전략이 아니라 NAS 데이터 축적이다. 기존 `AutonomousTop20Service`가 평일 08:00~20:00에 30초마다 저장하는 순위/membership, 당일 편입, 1분봉·일봉을 재사용한다. 기존 0B 구독에 새 키움 연결이나 종목 구독을 추가하지 않고, 현재 실시간조회순위 구독 종목의 체결을 NAS 메모리에서 1초로 집계해 중앙 DB에 기간 제한 없이 저장한다.

첫 1초 행은 `trading_date, second, code, market, open, high, low, close, volume, trade_value, trade_count, available_at`과 품질 참조를 가진다. 체결이 있는 초만 저장하고, 빈 초를 무거래로 해석할 수 있도록 별도 구독·연결·저장 공백 이력을 보존한다. TOP20 진입 전 초 단위 복원은 요구하지 않는다. 3/5/10초 값은 1초 자료에서, 5분 이상 봉은 기존 1분봉에서 계산한다.

원시 틱과 실제 호가의 전 종목·전 시간 저장은 저장량·연산량 때문에 보류한다. 초기 연구는 가격·거래대금·체결 건수 기반이며 기존 호가 잔량형 `LOCKED/BROKEN/RELOCKED`는 지원하지 않는다. 다만 2026-09-14 NXT 정적 VI부터는 VI 발생 추적 종목의 단일가 구간만 1초 호가 snapshot으로 제한 수집하는 별도 단계를 둔다. 실제 호가 유형/FID 확인 전에는 구현하지 않고 0B로 합성하지 않는다. 향후 용량 관리 기능은 보호된 실제 매매·후보·연구 참조 구간을 제외하고 오래된 거래일부터 정리한다. 첫 수집 단계에서는 자동 삭제를 구현하거나 켜지 않는다.

## 2. 단계와 의존성

| 단계 | 결과 / 시작 조건 | 이 단계에서 끝낼 범위 |
|---|---|---|
| D0 | 이번 감사·계획 완료 | 실제 구조와 원문 요구 매핑 |
| D1 | 순위 관측의 불변 이력 / D0 | 저장 경계 하나, 기존 latest 유지 |
| D2 | 고정 dataset 추출·기존 동작 재생 / D1 | 전략 없는 기록 재생 |
| D3 | KRX 봉·Factor 2개 이내·Family 1개·모의 결과 / D2 | 최초 수직 경로 |
| D4 | NAS shadow 후보·앱 알림 / D3 | 주문 없는 실제 장 관찰 |
| D5 | 테마/기준정보의 시점 이력 / D1 | 기록을 일찍 시작; D3 필수 아님 |
| N1 | 뉴스 기사·본문·분석 revision, 후속 작업 분리 / D1 계약 | 기존 watchlist로 기록 |
| N2 | 한 사건의 빠른 뉴스 후보·선별 AI / N1+D3 계약; live 연결은 D4 | 공급계약부터, 혼재/미확인 보존 |
| N3 | 명시된 피드/검색 범위의 상시 수집 / N1 | 공급자 범위 확인 후 확대 |
| D7 | Manual 연구·비용·OOS 보고 / D3 | 기능을 계속 붙이기 전 비교 기준 확보 |
| D8 | 매매일지 자동보완·근거 연결 / D3; 뉴스 연결은 N1 | 기존 보완의 영속 작업화 |
| F1 | 시장 특징·네 유형·필터 비교 / D5+D7, 필요한 자료 | 자료 부족 유형은 UNKNOWN |
| C1 | 장전·전일 후보·장중 확인 / D5+N2+D7 | 가설과 반응 분리 |
| H1 | TOP20 구독 종목 1초 집계·공백 기록 / D0 | 기존 0B 스트림 재사용, NAS 저장·조회만 추가 |
| H2 | 가격·거래대금 기반 대장·이탈·재가속 비교 / D5+H1+D7 | 호가 상태는 미지원 |
| V1 | NAS/직접 자료 대조 고도화 / D2 | 기존 대조기 확장; D4와 병행 가능 |
| R1 | 제한 자동 탐색 / D7 | 유한 trial·재개·후보 카드 |
| R2 | Family 확장·Free Research·반복 연구 / R1 | 등록된 범위·유한 예산 |
| O1 | 키움 모의 주문 최소 연결 / D3+D4+D7+지원 계약 | R1/R2 완성을 기다릴 필요 없음 |
| O2 | 모의 전진 평가→별도 실거래 단계 / O1+V1 | 정책·계좌·복구·승격 기준 |

권장 착수 순서는 **H1 → D1 → D2 → D3 → D4**, 이후 **D7·D8**로 사용자에게 검증 가능한 결과를 만든다. H1은 앞으로 복구할 수 없는 1초 자료를 먼저 쌓기 위한 독립 수집 단계다. 과거로 복구할 수 없는 테마·뉴스 자료는 **D5/N1을 D1 직후 작은 기록 작업으로 앞당길 수 있다.** F1/C1/H2는 원하는 가설별로 선택한다. 자동 탐색·주문 연동을 위해 모든 뉴스/테마 가설을 먼저 구현할 필요는 없다.

## 3. 공통 계약 — 사용하는 단계에서만 추가

### 3.1 관측·시각·revision

기존 `MarketDataObservation/MarketDataMetadata`를 재사용한다. 연구 봉투는 기존 값과 메타데이터의 불변 버전을 참조하는 얇은 확장이다.

```text
ObservationRevision:
  revision_id, observation_key, schema_version, source_id, source_session_id?
  kind, subject, venue, effective_at?, received_at?, available_at?
  source_sequence?, ingest_sequence, revision_of?, payload, payload_hash
  unit, value_kind, completeness, origin, quality_flags, clock_quality

DatasetManifest:
  dataset_id, schema_version, captured_range, recorded_from, exported_at
  source_ids, fixed_watermark, revision_ids/hash, universe_rule/version
  timezone, session_calendar_version, order_policy_version, quality_summary
  replay_profile: observed_replay | historical_bar_model | synthetic_fixture
```

- **신규 저장은 timezone-aware UTC ISO 8601, 한국 거래일·표시는 KST**로 한다. 기존 값은 경계에서 명시 변환하며 기존 표 전체를 다시 쓰지 않는다. naive 값은 출처가 KST임을 확인한 어댑터만 변환하고 미확인은 거부한다.
- `effective_at`은 시장 기준시각, `received_at`은 해당 수집기가 받은 시각, `available_at`은 그 revision을 실제 사용할 수 있게 된 시각이다. 제목·본문·규칙·AI의 가용시각을 따로 기록한다. 다른 PC/NAS에서 온 결과는 소비 측 수신시각도 남겨 NAS에서 먼저 계산됐다는 이유로 앱의 과거 판단에 소급 사용하지 않는다.
- 정렬은 단일 기록기에서 `(available_at, ingest_sequence, revision_id)`를 사용한다. 실제 실행이 소비한 순서를 별도로 남겨 재생의 기준으로 삼는다. 공급자 사건시각 순으로 늦은 자료를 과거에 끼워 넣지 않는다. 복수 기록기 결합 시 시계 오차/순서가 불명확하면 표시하며, 동일 Clock이라고 가정하지 않는다.
- 같은 수신 식별자의 재처리는 멱등이다. 동일 값이더라도 별도 시점의 새 관측은 삭제하지 않는다. 같은 관측의 값·품질·출처 변경은 새 revision이다. `A→B→A` 정정에서 마지막 A를 최초 A와 합치면 안 된다.
- 기존 최신값 upsert는 유지한다. **최신 값에 가장 이른 available_at을 붙이는 수정은 금지**한다. 불변 기록이 없는 과거 행에는 `legacy_unknown`을 남긴다.
- 필수 값 미수신은 null+이유이며 0으로 대체하지 않는다. 메타데이터가 COMPLETE여도 Factor별 필수 필드·단위·최신성을 검증한다.

### 3.2 Factor·판단·후보

```text
FactorDefinition: id, version, output_schema/unit, dependencies,
                  parameters, lookback, warmup, max_age, missing_policy, compute
FactorValue: id/version, parameters_hash, entity, as_of, available_at,
             value?, status(valid/missing/stale/error/unsupported), reason, input_refs
FeatureSnapshot: snapshot_id, run_id, decision_time, input_cutoff,
                 universe_ref, feature_values, input_revision_refs,
                 strategy/policy_version, state_before_ref
Decision: decision_id, run_id, snapshot_id, proposal?, final_action,
          reasons, constraints, state_before/after_ref, decided_at
CandidateEvent: event_id, run_id, decision_id?, snapshot_id, symbol,
                setup_kind, source_evidence_refs, revision, status,
                detected_at, available_at, expires_at, dedup_key
```

`Decision`은 ENTER/EXIT/REDUCE/HOLD/NO_TRADE를 명시한다. `NO_TRADE`는 신규 위험을 추가하지 않는 결과이며 기존 포지션 청산·주문 취소가 아니다. 후보는 볼 이유가 생겼다는 사건으로, ENTER나 체결과 동일하지 않다. 제안과 제약 적용 후 결과를 함께 저장한다. Snapshot에는 실제 사용값도 고정해 latest DB를 재조회해야만 이유를 알 수 있는 상태를 피한다.

등록은 `(id, version) → 함수+타입 메타데이터` dict로 시작한다. Factor 내부에서 REST·DB·UI를 읽지 않는다. 두 번째 합성 Factor가 생길 때 필요한 의존성 순서/순환 검사만 추가하고 범용 DAG 실행기를 만들지 않는다.

### 3.3 결과·실행·소유권

```text
OutcomeLabel: event_id, horizon_end, matured_at, computed_at, available_at,
              status(PENDING/COMPLETE/CENSORED/UNSUPPORTED), values, input_refs
RunSpec: run_id, mode, data_manifest, code/dirty_hash, factor/family/policy_versions,
         parameters, initial_state, seed, clock/order/cost/execution_model_versions
ExecutionEvent: run_id, execution_environment, account_ref, intent_id,
                broker_order_id?, broker_execution_id?, event_id,
                state, occurred_at?, received_at, quantities/prices, costs
EnrichmentTask: task_id, owner_scope, kind, target, input_fingerprint, policy_version,
                status, attempt, next_retry_at, output_refs, reason
```

초기 연구 결과는 별도 로컬 `research.sqlite3`와 `runs/<run_id>/manifest.json`에 둔다(신규 제안). 작은 SQLite 스키마·기존 migration/connection 도구를 사용하고 run/spec, snapshot/decision, execution/mark, outcome을 필요한 시점에만 추가한다. NAS 후보 결과는 NAS가 소유하는 불변 기록으로 보존하고 앱은 읽기 캐시를 둔다. 일반 설정 동기화로 trial 원장·로컬 실행 lock을 복사하지 않는다.

계좌 환경은 data_source와 별개다. `NAS 시세 + mock 실행`처럼 지정할 수 있지만 mock/paper 결과를 기존 `trade_fills`에 삽입하지 않는다. 사용자 원본, 실제 체결, 파생 복기, 시뮬레이션 결과의 저장 소유자를 분리한다. API 키·토큰은 manifest나 원장에 넣지 않는다.

## 4. D1 — 순위 관측의 불변 이력

**구현 상태 (2026-09-12): 코드·SQLite 검증 완료, NAS PostgreSQL 적용 전.** 중앙 schema v12와 `central_observation_revisions`에 ranking/top20_membership만 append한다. 기존 latest projection과 메타데이터를 같은 트랜잭션으로 유지하고 연속 동일 캐시 재처리는 합치며 A→B→A 정정 순서와 TOP20→ranking 원본 참조를 보존한다. 신규 시각은 UTC timezone-aware로 기록하고 `_ingest_ranking`은 주입 시계를 사용한다. 저장 실패는 화면 조회를 실패시키지 않고 `recording_gap` 로그를 남긴다. 환경 변수로 이력 append만 끌 수 있다. D1 관련 회귀 90개와 전체 핵심 회귀 631개(`280+351`)가 통과했다. PostgreSQL 검사기는 round-trip·정정·rollback을 포함하도록 갱신했으나 실제 NAS 실행은 사용자 빌드 뒤 확인한다.

**목적/선행:** 기존 순위 수집 한 경로에서 당시 값을 보존한다. D0를 읽은 뒤 시작한다. 신규 전략·UI·호가 수집은 포함하지 않는다.

**수정 대상:** `central_server/central_schema.py`, `database.py`, `market_observations.py`, `market_ingest.py`, `autonomous_top20.py`; `schema_migrations.py` 실행기 자체는 필요할 때만 수정한다. 기존 `domain/market_data_contract.py`를 감싸는 **신규 `domain/research_contract.py`** 한 파일을 고려한다.

**입출력:** 공통 3.1의 순위/membership revision. **신규 `central_observation_revisions`**에 sequence, source, 관측키, 시각, payload/metadata, hash를 append한다. 초기에 ranking/top20_membership만 허용한다. `ranking`과 `top20_membership`이 같은 응답을 표현하면 원본 참조를 연결하고, 전략의 주 universe 입력은 `top20_membership` 한 종류로 정한다.

**작업 단위:** (a) 구DB fixture+추가 migration·append/query 메서드, (b) 기존 저장 호출에 revision 연결, (c) 저장 실패·중복·정정 회귀. `_ingest_ranking`의 직접 현재시각 사용은 해당 경로에 한해 기존 주입 시계로 통일한다.

**완료 기준:** latest 응답 불변, A→B 정정 후 A 시점 재생 가능, 재시작 뒤 sequence/수신 ID 중복 없음. 값+메타+revision은 같은 저장 트랜잭션의 일관된 결과다. 연구 기록 실패를 사용자 화면 조회 실패로 전파하지 않되, 연구 품질은 `recording_gap`으로 표시하고 기록 없이 검증 성공을 보고하지 않는다. 캐시 재응답을 새로운 시장 관측으로 위장하지 않는다.

**테스트:** 신규 `test_research_observation_history`; 기존 `test_central_server_database`, `test_central_schema_migrations`, `test_central_market_ingest`, `test_autonomous_top20`, `test_market_data_contract`, `test_central_rest_broker`. SQLite/PostgreSQL 동일 fixture로 중복·정정·rollback·legacy_unknown·시간대·NAS 장애를 검사한다.

**되돌리기/운영:** 기능 flag로 신규 기록 중단, 추가 테이블은 유지한다. 새 schema를 거부하는 구 바이너리를 실제 DB에 바로 실행하지 않는다. NAS 배포 시 별도 운영 gate와 빌드 세 곳 갱신 규칙을 따른다.

## 5. D2 — 데이터 추출과 기존 동작의 최소 재생

**구현 상태 (2026-09-12): 코드·SQLite 검증 완료, NAS PostgreSQL 적용 전.** 중앙 schema v13은 첫 요청의 DB snapshot에서 대상 D1 revision ID와 ordinal을 immutable export manifest/member로 확정한다. 인증 API와 클라이언트는 고정 watermark·stable cursor·revision count/hash를 사용하며 최대 범위는 한 세션(24시간)이다. 5,001개 동일시각 revision, 추출 중 새 commit, 다중 page의 중복·누락 없음과 가상 시계 기준 TOP20 코드·순서의 결정론적 재생을 검증했다. D2 집중 회귀 77개와 전체 핵심 회귀 639개(`287+352`)가 종료 코드 0으로 통과했다. `scripts/export_research_dataset.py`는 기존 경로를 덮어쓰지 않고 manifest와 JSONL hash를 저장한다. coverage gap, 코호트 prepare/advance 순서, 봉·전략·거래 결과는 아직 재생하지 않는다.

**목적/선행:** D1 기록만으로 현재 순위 응답의 종목코드·순서를 정규화해 다시 만든다. 새로운 매매 가설을 넣기 전 입력 경계를 증명한다.

**수정 대상:** `central_server/database.py`, `app.py`; **신규** `infrastructure/research_data_source.py`, `application/research_replay.py`, `scripts/export_research_dataset.py`. 현재 순위 정규화 코드를 재사용한다. 순수 변환이 필요한 경우 그 함수만 기존 `ranking_schedule.py` 등 응집된 모듈로 옮기고 `RankingService` 전체를 분해하지 않는다.

**입출력:** 제안 API `GET /api/v1/research/observations?start=&end=&kinds=&subject=&cursor=&watermark=`. 서버가 첫 응답에서 고정 committed watermark를 발급하고 그 범위만 stable cursor로 반환한다. 허용 kind·범위·page 크기·인증을 검증한다. export 결과는 immutable manifest+관측 파일이며 데이터 hash가 다르면 같은 dataset으로 실행하지 않는다.

PostgreSQL의 sequence 할당순서와 commit 순서는 다를 수 있으므로 단순 `MAX(sequence)`만으로 추출 집합을 고정했다고 보지 않는다. 최초 DB snapshot에서 export 대상 revision ID 집합/manifest를 확정하고 이후 페이지는 그 집합만 읽는다. 먼저 할당됐지만 나중에 commit된 행도 진행 중 export에 끼어들지 않게 검사한다. 초기 범위는 한 세션 단위로 제한해 거대한 export 작업 플랫폼을 피한다.

**완료 기준:** 5000개를 넘는 기록·동일시각 복수 기록·추출 중 신규 수집이 있어도 중복/누락 없이 끝난다. 가상 시계로 같은 입력의 정규화 순위가 같아야 한다. 이 단계는 코호트 예약이나 TOP20 거래대금 전체 재현까지 주장하지 않는다. 현재 `prepare` 입력은 REST 요청 전 시각이고 membership 가용시각은 응답 후여서 30초 경계에서 다를 수 있다. 코호트/전체 지수 재생은 실제 `prepare/advance` 입력시각·flags·호출 순서, provider 값, 시장 분류 버전, 초기 누적 기준점을 기록하는 후속 작업으로 둔다.

**테스트:** 신규 `test_research_data_source`, `test_research_replay`; 기존 `test_central_server_app`, `test_ranking_service`, `test_top20_trade_value_collector`, `test_market_data_coverage`. 미래 revision 추가/변조가 이전 출력에 영향을 주지 않는 검사, 지연·역순·같은 값 재관측·공백 fixture, API page 경계·재개·범위 validation. 실행기는 네트워크를 끈 fake source로도 동작한다.

**되돌리기:** 새 읽기 API/CLI 비활성화. 기존 `/api/v1/market/*` 응답 구조는 그대로 유지한다. 원장·export 파일 삭제는 하지 않는다.

## 6. D3 — 첫 Factor·Family·모의 결과 수직 구현

**목적/선행:** D2에 가격 입력을 더해 `기록 → Factor → Snapshot → Decision → 내부 모의 결과` 한 경로를 완성한다. D3a/b/c를 각각 리뷰 가능한 변경으로 진행한다.

### D3a. KRX 봉 입력과 시간상 마감

**구현 상태 (2026-09-12): 코드·로컬 검증 완료, NAS 배포 전.** 중앙 스키마 v14의 operation 처리 원장으로 실시간 delta를 정확히 한 번 누적하고, 각 반영 뒤 전체 봉 revision과 분 종료+2초 타이머 마감 revision을 D1 원장에 기록한다. 분 중간 구독·연결 공백은 partial, 무체결 분은 미생성으로 유지한다. D2 고정 export가 `minute_bar`를 받고 가상시각 기준 최신 KRX strict 마감봉 reader가 이를 선택한다. 관련 회귀 92개와 전체 핵심 회귀 644개(`292+352`)가 통과했다. Factor·Family는 D3b에서 진행한다.

**수정:** `central_server/database.py`, `market_ingest.py`, `realtime_collector.py`, `minute_bars.py`, `market_observations.py`; 신규 reader의 KRX 봉 조회. D1 원장을 재사용한다.

**계약:** `bar_start/end`, 시장, OHLCV, 단위, revision, `available_at`, `window_closed`, `capture_quality`, `finalization_source`. 현 실시간 flush 자료는 1분 완성값이 아닌 **누적 테이블에 더하는 부분 delta**이며 메타는 IN_PROGRESS다. 이를 완성 분봉으로 그대로 기록하지 않는다. 저장 트랜잭션 안에서 병합된 전체 봉 값을 revision으로 보존하거나 delta+기준 revision 계약을 명시한다. 첫 구현은 전체 봉 revision이 단순하다.

delta flush에는 생성 시 stable operation/batch ID를 주고 재시도에서도 유지한다. 같은 트랜잭션에서 미처리 operation 확인→delta 합산→전체 봉·메타·revision·처리 ID 저장을 수행한다. 재시도 delta와 새 delta를 묶어도 원 operation ID 집합은 잃지 않는다. 신규 기록 경로부터 적용하고 다른 저장소 전체를 일괄 교체하지 않는다.

**마감:** 실제 관측 시계가 bar_end를 지난 뒤 설정된 지연 허용시간에 close 후보를 만든다. 마감 revision의 available_at은 실제 마감 처리·소비 가능한 시각이며 마지막 tick.updated_at이나 bar_end로 소급하지 않는다. 구독 연속성·수신 공백을 확인하고 `시간상 마감`과 `수집 완전` 및 `장후 최종 확정`을 분리한다. 마지막 틱이 없는 종목도 타이머로 마감 가능해야 하며 공백을 0거래로 합성하지 않는다. 늦은 정정은 새 revision, 기존 결정은 불변이다. 로컬 COMBINED 봉을 KRX로 재라벨하지 않는다.

**완료/검사:** 부분 delta 두 개가 전체 봉 하나로 정확히 반영되고 재시도 중 이중 합산되지 않음; commit 성공 후 응답 유실→동일 operation 재시도, minute 경계·늦은 틱·단절·무체결·첫 누적 기준점·KRX/NXT 혼합 fixture. 기존 `test_central_minute_bars`, `test_central_realtime_collector`, `test_central_market_ingest`, `test_central_server_database` + 신규 봉 revision 검사. 실제 strict 입력이 부족하면 NO_TRADE로 끝내고 합성 fixture만으로 구현 계약 통과를 구분한다.

### D3b. Factor 두 개와 Family 하나

**구현 상태 (2026-09-12): 코드·로컬 검증 완료.** `rolling_high_breakout/v1`은 평가봉을 제외한 같은 KRX 세션의 연속 strict 완료봉만 사용하고, `rank_persistence/v1`은 부분 목록·워밍업·긴 관측 공백을 결측으로 보존한다. `krx_bar_close_breakout/v1`은 당시 TOP20을 신규 진입 범위로 쓰며 Snapshot·Decision·상태·후보 중복 키를 별도 `research.sqlite3` v1에 기록한다. `scripts/run_research.py`는 D2 export의 파일/ID hash를 검증하고 가상 `available_at` 순으로 실행해 결정론적 run ID와 논리 결과 hash를 만든다. 이 단계의 ENTER/EXIT은 전략 판단이며 체결·손익·주문은 만들지 않는다.

**신규 대상:** `application/research_factors.py`, `application/breakout_strategy.py`, `infrastructure/persistence/research_repository.py`(migration 포함), `scripts/run_research.py`. 등록과 작은 runner는 D2 파일에서 확장한다.

**Factor 계약:**

- `rolling_high_breakout/v1`: 동일 종목·KRX·세션의 **평가봉을 제외한 직전 N개 사용 가능한 시간상 마감봉**의 최대 high가 reference. 평가봉 close가 `reference × (1+buffer_bps/10000)`을 넘는지, 거리 bps·reference·input IDs를 반환한다. 필요한 N개와 coverage가 없거나 capture 품질이 규칙을 못 채우면 missing. 당일 최종 고점·전일 고점과 이름을 섞지 않는다.
- `rank_persistence/v1`: `ka00198/qry_tp=5` 상위 K 체류시간·관측 비율. 긴 수신 공백은 체류를 이어 붙이지 않는다. 부분 목록·처음 한 관측·미확인 시각은 워밍업/결측이다. 거래대금 순위나 유동성 점수라고 부르지 않는다.

**Family 계약:** `krx_bar_close_breakout/v1`, 필수 rolling breakout, 선택 rank persistence. 첫 연구 프로파일은 KRX·단일 전략·동시에 한 포지션·매수 후 매도·추가 매수 없음으로 제한한다. 상태는 flat/candidate/open/cooldown 정도로 시작한다. 진입·청산·크기 결정은 한 모듈의 구분된 함수면 충분하다. N, buffer, 관측 시간, 손절/목표/최대 보유시간, 수량/자금, 신호 유효기간은 명시적 실험 설정으로 받는다. 운영 추천값을 임의로 하드코딩하지 않는다.

**출력:** 모든 판단마다 Snapshot, proposal/final Decision, 이유·상태 전이. 거래가 없는 run도 정상 결과다. 후보 재발행 키는 종목+setup+기준 관측+전이+전략 버전이며 같은 조건 지속만으로 새 후보를 만들지 않는다.

현재 TOP20은 신규 진입 universe다. 모의 보유·미체결 종목은 순위 이탈 후에도 청산/평가 입력을 유지해야 한다. live에서는 필요한 관리 종목을 구독에 명시적으로 합치며, 과거 데이터가 끊긴 경우 해당 실행/라벨은 unsupported/censored로 기록한다. 다음 재편입 시점 가격으로 자동 청산하거나 기록 끝에서 수익을 확정하지 않는다.

**완료/검사:** 독립 실행 두 번 동일 판단·상태·논리 결과 hash; 필수/선택 Factor 결측 정책; 비활성 Factor 영향 없음; 미등록/잘못된 버전 거부; 당시 universe만 사용; 미래 뉴스·테마·봉 변조에 과거 결정 불변. 신규 `test_research_factors`, `test_breakout_strategy`, `test_research_repository`. 구현에 따라 영향받은 기존 단위/핵심 회귀를 실행한다.

### D3c. 최소 내부 모의 실행과 사건 결과

**구현 상태 (2026-09-12): 코드·로컬 검증 완료.** `next_tradable_bar_open/v1`은 판단시각 이후 시작하는 첫 strict KRX 봉에서만 체결하고, 명시한 `fixed_bps/v1` 비용·슬리피지와 현금 예약을 적용한다. 단일 포지션의 체결·mark·미체결/보유 검열을 연구 DB v2에 기록하며 기록 끝 가격으로 강제 청산하지 않는다. 동일봉 손절·목표 동시 도달은 손절을 실제 결과로 사용하고 목표가 낙관 범위를 함께 보존한다. 후보의 T+1초는 현재 H1 export 부재로 UNSUPPORTED, 1/3/5/10분은 COMPLETE/PENDING/CENSORED로 기록한다. 비용 없는 성과는 INELIGIBLE, 완료 거래 없는 결과는 NOT_APPLICABLE이다. 실제 계좌 주문과 기존 `trade_fills`는 사용하지 않는다.

**신규 대상:** `application/research_execution.py`, `application/research_evaluation.py`; 위 연구 원장 확장. 현재 `TradeCostService`는 실제 비용 조회이므로 시뮬레이션에서 호출하지 않는다.

**입출력:** Decision과 명시한 비용/체결 모델 → 모의 주문·체결·현금/포지션·자산 평가, 사건 outcome. 최초 체결 모델은 완료봉 신호 다음의 거래 가능한 봉에서 체결하는 **근사**로 한정한다. 판단 시각이 다음 봉 시작보다 늦으면 이미 지나간 open에 체결시키지 않는다. bar-only strict 프로파일은 실행 시점 이후 시작하는 최초 봉으로 보수적으로 미루거나 unsupported를 반환한다. 동일 봉의 stop/target 순서는 보수적 기본+낙관 범위를 함께 표시한다.

**완료/검사:** 자금·미체결 예약을 넘는 체결 금지; 비용 미지정이면 성과 평가 부적격; 미성숙 1/3/5/10분 라벨은 PENDING, 구간 종료·정지는 CENSORED. `T+1초`는 H1 없으면 UNSUPPORTED. 동일 입력·seed·초기 상태에 동일 모의 결과. 손익 수기 계산 fixture, 비용 증가 비교, 전부 NO_TRADE/미체결인 경우 지표 해당 없음. 신규 `test_research_execution`, `test_research_evaluation`.

**D3 되돌리기:** 새 연구 실행/봉 기록 flag만 끈다. 기존 차트·복기 의미를 변경하지 않는다. run은 cancelled/failed 이유와 결과를 남기고 DB를 삭제해 되돌리지 않는다.

## 7. D4 — NAS shadow 후보와 앱 알림

**구현 상태 (2026-09-13): 코드·로컬 핵심 회귀 완료, NAS PostgreSQL 운영 검증 전.** 중앙 schema v15에 monitor checkpoint·불변 Decision·CandidateEvent를 추가했다. `CandidateMonitor`는 TOP20/strict KRX 완료봉 sequence를 D3 판단에 증분 연결하며, 최초 시작의 최근 자료는 워밍업만 하고 과거 후보를 소급 발행하지 않는다. 명시 전략 JSON과 후보군 최신성 한도가 없으면 생성은 OFF다. 인증 cursor API와 앱 `Shadow 후보` 창은 첫 목록을 무음으로 채우고 실행 중 새 ACTIVE event만 PC 로컬 설정에 따라 알린다. ENTER는 후보 제안일 뿐 open·paper fill·실제 주문·실제 일지를 만들지 않는다. D4 집중 회귀 99개와 전체 핵심 회귀 677개(`324+353`)가 통과했다.

**목적/선행:** D3 코어를 실제 수집에 연결하되 주문을 보내지 않는다. NAS가 후보 원장을 소유하고 앱은 표시·알림을 소비한다.

**수정 대상:** `central_server/app.py` 수명 조립, **신규 `central_server/candidate_monitor.py`**(증분 입력 cursor·run 상태·중복 방지 소유), D3 runner/원장; **신규 `presentation/candidate_monitor_dialog.py`** 또는 기존 표의 작은 진입 버튼, 얇은 데이터 수신 worker. `MainWindow`에는 열기·닫기 연결만 둔다.

**입출력:** 영속된 D1/D3 입력의 sequence → 동일 Factor/Family → CandidateEvent/Decision. 제안 `GET /api/v1/research/candidates?after_sequence=&limit=`는 high_watermark·events·next_cursor·quality를 반환한다. 첫 앱 연결은 현재 목록을 조용히 채우고 이후 새 transition만 소리/화면 알림으로 낸다. 초기에는 낮은 부하의 cursor polling으로 충분하며 별도 WebSocket 프로토콜은 지연 측정이 요구할 때 추가한다.

shadow는 후보의 감지/유지/만료와 제안만 기록한다. ENTER 제안을 체결로 간주해 포지션을 open으로 바꾸지 않으며 후보 만료·재준비 규칙을 따로 둔다. 내부 paper를 함께 돌릴 경우 같은 판단 함수를 쓰되 별도 execution-profile/run의 체결 이벤트로만 포지션을 갱신한다. 두 환경의 상태를 섞지 않는다.

**완료 기준:** 뉴스 없는 신규 편입도 후보 가능. 동일 후보 지속·재접속·앱 재시작에 알림 폭주 없음. stale/gap/expired는 신규 후보 차단 사유와 최신성으로 보인다. 소리 실패·창 종료가 후보 기록을 중단하지 않는다. 두 PC는 같은 서버 event ID를 읽되 각 PC의 알림 소비 cursor를 공통설정으로 덮어쓰지 않는다. 기존 신고가 근접 소리·행 강조는 유지한다.

**테스트:** 신규 `test_candidate_monitor`, `test_candidate_alerts`; `test_central_server_app`, `test_central_rest_broker`, `test_main_window`와 새 창 Qt 수명 검사. 느린 소비자/재시작/역순 재전송/설정 OFF/음원 실패, 앱 종료 후 NAS 후보 저장, 기록 공백→복구 시 상태 재준비. 운영에서는 수집→가용→판단→표시 p50/p95/p99와 broker 순위 지연을 실제 측정하고 합의한 지연 예산 안인지 확인한다. 이번 계획에 밀리초 처리량 보장값은 없다.

**되돌리기:** 신규 후보 생성과 앱 알림을 각각 끌 수 있게 한다. 현재 종목 표시·시세 수신·일지를 유지하며, 미전송 주문은 이 단계에 존재하지 않는다.

## 8. D5 — 테마·기준정보 이력부터 기록

**구현 상태 (2026-09-12): 코드·로컬 검증 완료, NAS 배포 전.** 사용자 우선순위에 따라 D1보다 먼저 테마 이력의 최소 수직 경로를 구현했다. 중앙 스키마 v5는 `theme_metadata(default,full)` 수락 트랜잭션에서 전체 문서를 append하고, 연속 동일 내용 hash를 중복 제거한다. 이 v5 변경은 이후 N1/N2a/N3/시장 이벤트와 뉴스 source 재사용·종목목록 조회 마이그레이션을 더한 현재 누적 중앙 스키마 v11에 그대로 포함된다. 삭제·활성 프로필 전환·지연 전송의 서버 가용시각과 revision을 보존하며 `/api/v1/themes/history`로 읽는다. D5 관련 백업·동기화·API·스키마 회귀 73개가 통과했고, 현재 D5를 포함한 누적 핵심 회귀는 580개(270+310)다. 종목 종류·상장 상태·기업행동 등 추가 기준정보 revision과 테마 전략은 아직 포함하지 않는다.

**목적/선행:** D1 뒤 일찍 기록을 시작한다. 현재 테마를 과거에 붙이지 않고 당시 소속과 실제 알려진 시점을 보존한다. 이 단계에서는 테마 전략을 만들지 않는다.

**수정 대상:** `infrastructure/central_theme_sync.py`, `central_content_sync.py`, `persistence/theme_backup.py`, `theme_repository.py`, 중앙 schema/database/app. 전체 프로필 직렬화는 기존 백업 형식을 재사용한다. **신규 theme snapshot 계약**은 `domain/research_contract.py`에 필요한 타입만 추가한다.

**입출력:** 현재 전체 프로필/활성 프로필 참조 → `snapshot_id`, profile_id, content_hash, effective_from?, received_at, available_at, origin_device, revision_of, 전체 관계. 기존 테마 전송은 세 컬렉션을 각각 교체하므로 그 중간 상태를 조합해 이력을 만들지 않는다. 최소 구현은 마지막 `theme_metadata`의 `owner=default,key=full`에 담긴 완전한 `ThemeBackupService.export_document()` 문서를 단일 원본으로 삼고, 그 문서 수락 트랜잭션에서 불변 이력을 append한다. 세 projection 전체 원자교체 API는 이번 단계에 요구하지 않는다. 기존 pending 테마 재시도와 최신성 비교를 유지한다. 삭제는 새로운 빈/수정 스냅샷으로 표현한다. 과거로 소급 입력한 관계는 effective와 available을 둘 다 보존한다.

**기준정보:** 실험이 쓰는 종목종류·상장 상태·시가총액·전일 고점·가격제한도 당시 관측 revision을 참조한다. `stock_catalog` 최신 교체만으로 역사 구성은 만들지 않는다. 필요한 종목 속성만 기록하며 기업행동/수정주가 버전이 없으면 해당 과거 비교를 제외한다.

**완료 기준/테스트:** 같은 프로필 재전송은 중복 없음, 삭제·활성 전환·오프라인 편집 후 동기화·충돌 후에도 과거 조회 불변. 두 PC의 늦은 전송이 미래 소속을 과거 가용값으로 만들지 않음. 신규 `test_theme_history`; 기존 `test_theme_backup`, `test_central_theme_sync`, `test_central_content_sync`, schema/database 검사. 최초 기록 이전은 unknown, 그 사실이 manifest와 화면에 표시된다.

**되돌리기:** 기록/조회만 중단하고 최신 테마 편집·NAS 대기 전송은 유지한다. 연구 테마 이력은 일반 설정 백업이 아닌 NAS 연구 원장+dataset export 범위로 명시한다.

## 9. N1 — 뉴스 관측·본문·분석 이력과 작업 분리

**구현 상태(2026-09-12): 코드·로컬 검증 완료, NAS 배포 전.** 중앙 스키마 v6에 article/body/AI 불변 revision과 영속 job을 추가했고 기존 `news_article/news_ai` projection 및 watchlist/Naver/DART 동작은 유지했다. 기사 revision+BODY job, AI latest+revision+일일 사용량은 각각 한 트랜잭션이다. worker는 한 번에 한 작업, BODY 15초·AI 90초 timeout, 최대 3회 시도와 120초 stale RUNNING 복구를 사용한다. AI 결과 commit 뒤 job 완료 전 종료되는 경계도 기존 immutable revision ID로 재개한다. N1 집중 86개와 당시 핵심 회귀 532개(259+273)가 통과했으며 N3 종목목록 조회를 포함한 현재 누적 스키마는 v11이다. `NEWS_HISTORY_JOBS_ENABLED=false`이면 worker 예약 실행을 끄고 기존 요청형 분석을 사용한다. 별도 export UI는 아직 구현하지 않았다. 외부 AI가 응답한 뒤 DB commit 전에 프로세스가 끊기면 공급자 비용의 중복 가능성은 남는다.

**목적/선행:** 기존 watchlist와 공급자 3종을 유지하면서 최초 인지와 분석 revision을 보존한다. 시장 수집 확대 전에 느린 본문/AI가 수집을 기다리게 하지 않는다.

**수정 대상:** 기본은 `central_server/news_service.py`의 `_collect/_serialize/refresh_once`, `ai_service.py`의 `_prepare_events`, `infrastructure/article_text.py`, 중앙 schema/database다. N1a는 NAS 원장+기존 projection 유지로 좁힌다. 로컬 직접 수집의 관측을 전달하거나 새 필드를 화면에서 소비할 때만 `persistence/news_schema.py`, `stock_news_repository.py`, `news_ai_repository.py`, `central_content_sync.py`를 조건부 확장한다. **신규 `domain/news_observation.py`** 및 **작은 `central_server/news_jobs.py`**는 기사/분석 타입과 작업 수명을 실제 소유할 때만 추가한다.

**N1a 입출력:** 기사 ID와 기존 `(stock_code, identity)` 호환 매핑; `article_revision_id`, title/summary/URL/publisher/published_at?, collector_id, `received_at?`, `available_at?`, collection_scope, content_hash. 기존 PK는 바꾸지 않는다. 최초 수신은 수집기별로 보존하고 NAS 최초 인지와 로컬 최초 인지를 구분한다. 기존 `first_seen_at` 또는 중앙 `updated_at`을 NAS 수신시각으로 복사하지 않는다.

**N1b 입출력:** 본문 결과는 text/hash, extractor_version, fetched_at, available_at, fulltext/summary_only/failed 상태. AI 결과는 target_id, article/body revision refs, provider/model/prompt/schema version, input hash, 실제 출력·사용량, computed/available_at. 재호출 결과로 이전 분석을 덮어쓰지 않는다. 기존 latest AI/기사 컬렉션은 UI 호환용 projection으로 계속 쓴다.

새 불변 원장은 일반 `central_content_sync._upsert_row` 대상이 아니다. latest projection pull이 원장 revision이나 최초 인지를 변경하지 않는다.

**작업 계약:** 기사 revision과 필요한 영속 job `{article_revision, stage, attempts, next_retry_at, state, output_refs}`를 같은 DB 트랜잭션으로 저장해 그 사이 종료에 의한 누락을 막는다. job 키는 stage+target+실제 입력 revision/hash+해당 처리 버전이다. bounded worker가 처리하며 같은 job 재시작을 멱등하게 한다. 기존 자동분석 identity 제한은 별도로 유지하며 프롬프트 변경만으로 과거 전체 재실행을 예약하지 않는다. 가벼운 제목 규칙과 느린 본문·AI를 별도로 완료시킨다. 모델 응답 유실 때 외부 비용 중복을 완벽히 방지한다고 보장하지 않고 시도 이력/예산을 남긴다. 새로운 queue가 조회 순위와 자원을 경쟁하지 않게 실행 수와 timeout을 제한한다.

**완료 기준:** 느린/실패 AI 중에도 다음 기사의 제목 저장 지속; 이미 보관한 본문 재사용; 기사 정정·동기화·프로세스 재시작 후 시각/이전 결과 보존; 로컬 최신 200건 정리 이후에도 연구에 참조된 중앙 기사/본문이 남는다. 메인·뉴스창을 닫아도 기존 NAS watchlist 수집은 계속된다.

**테스트:** 신규 `test_news_observation_history`, `test_news_jobs`; 기존 `test_central_news_service`, `test_central_ai_service`, `test_stock_news_repository`, `test_news_ai_repository`, `test_news_database`, `test_central_content_sync`, `test_article_text`, schema 회귀. 다른 PC의 늦은 first_seen 값, 요약→본문→AI 가용 지연, 정정 제목, fallback 본문, 일일 한도, 취소·재개, rollback fixture.

**백업/되돌리기:** 새 불변 뉴스 원장은 NAS 연구자료+명시 export로 보존한다. 로컬 기사·기존 AI 백업 의미는 유지한다. 로컬에도 새 필드를 저장하면 `news_ai_backup.py`, `google_drive_sync.py`, 콘텐츠 sync의 명시 범위와 호환 읽기를 함께 갱신한다. flag OFF로 신규 job/기록만 멈추고 기존 요청형 분석을 사용할 수 있게 한다.

## 10. N2 — 공급계약 한 사건의 빠른 후보와 선택적 AI

**N2a 구현 상태(2026-09-12): 코드·로컬 검증 완료, NAS 배포 전.** 중앙 스키마 v7에 영속 event ID의 사건 revision과 article membership revision을 추가했다. N1 본문 revision 저장과 RULE job 예약은 한 트랜잭션이며, worker는 정확한 article/body revision을 읽어 `SUPPLY_CONTRACT`의 role/scope/certainty/novelty, 근거 span, 안전하게 환산 가능한 원화 금액, 상대방, 대상 방향/직접성, 세 점수와 산식 버전, `ai_required/reason`을 불변 저장한다. 기존 `news_grouping`은 `possible_related` 후보 계산에만 쓰며 URL을 event ID로 쓰거나 과거 소속을 고치지 않는다. watchlist 및 N3 confirmed target은 검증된 종목 코드·이름을 RULE 입력에 전달하며, 짧은 회사명의 일반 문장 부분 문자열은 confirmed로 승격하지 않는다. 규칙 fixture 9개(기대 판정 7, UNKNOWN 2, false negative 0)와 N2 보완 회귀는 N3 관련 회귀 117개에 포함해 통과했고, 현재 핵심 회귀는 580개(270+310)가 통과했다. 이 수치는 합성 fixture 결과이며 실제 뉴스 정확도 평가는 아니다. N2b CandidateEvent 연결과 N2c 자동 AI 필터, 화면 표시, 자동 주문은 아직 켜지 않았다. 이전 v6 본문을 일괄 소급 분류하지 않고 배포 후 새로 저장되는 본문부터 기록한다.

**목적/선행:** N1의 증거를 읽어 제목에서 빠른 잠정 후보를 만들고 본문/AI로 후속 검증한다. 첫 릴리스는 사건 20개가 아니라 **SUPPLY_CONTRACT 1개**, 관련 MOU/해지/부인/기대 반례에 집중한다.

**수정 대상:** 기존 `application/news_analysis.py`, `news_grouping.py`, `news_auto_analysis.py`; **신규 `application/news_rules.py`** 한 파일, `CentralNewsService._auto_analyze`, `news_view_model.py`의 결과 표시. 기존 OpenAI/Gemini/Claude prompt 의미를 조용히 바꾸지 않는다.

**입출력:** article/body revision + 현재까지 알려진 사건 → role/scope/event_type/certainty/novelty, fact evidence spans, amount_won?, target별 direction/directness, importance/confidence/novelty 점수와 산식 버전, ai_required/이유. 실제 사실 추출과 규칙 해석을 구분한다. 매출 대비 비율은 당시 가용 매출 기준 자료가 없으면 null이다. 통화/금액 단위가 다르거나 조건부이면 억지 환산하지 않는다.

**사건 identity:** 대표기사 URL을 event ID로 쓰지 않는다. **신규 영속 event ID + article membership revision**을 추가하고 기존 grouping은 후보 매칭 계산으로 재사용한다. 기존 화면용 기사 묶음은 단계별로 분리할 수 있다. 동일 계약의 진전(MOU→본계약)·정정·취소라는 근거가 확인되면 같은 event ID의 새 revision으로 연결한다. 계약 상대가 다르거나 관계 근거가 부족하면 별도 event와 possible_related 관계로 보존한다. 나중의 병합/분리를 과거 시점에 소급 반영하지 않는다. 한 기사 복수 사건을 하나로 강제하지 않는다.

**N2a v1 보수적 한계:** 같은 기사 identity의 정정은 같은 event revision으로 이어지지만, URL이 다른 기사에서 상대방은 같고 금액만 바뀐 경우에는 자동으로 같은 계약이라 확정할 근거가 부족하다. 이 경우 별도 event ID와 `possible_related`를 남기며 독립 사건으로 확정했다는 뜻으로 표시하지 않는다. 향후 공시번호·계약 식별자 같은 근거가 생길 때만 새 규칙 버전으로 병합 판단을 추가한다.

**세 번에 나눌 변경:** (a) 기록 전용 규칙과 반례, (b) D4 CandidateEvent에 뉴스 근거/잠정·검증·철회 revision 연결, (c) 자동 AI 후보에서 확실한 가격반응/중복만 제외하는 flag 추가. c는 a의 누락·오분류 비교가 끝난 뒤 켠다. 수동 분석은 허용하고 UNKNOWN/신규 사건 혼재는 제외하지 않는다.

**완료 기준:** `공급계약 체결에 급등`은 가격 반응 단어만으로 탈락하지 않음. `공급 논의/기대`, `계약 해지/부인`, 재탕, 서로 다른 회사, 금액 단위, 대상 회사 반대 영향, 본문과 제목 충돌에 설명 가능한 결과. 같은 새 사실은 후보 1회, UPDATE는 명시된 재평가. AI 결과는 별도 보조이며 rule 결과/과거 Candidate를 덮어쓰지 않는다. 후보는 직접 주문하지 않는다.

**테스트:** 신규 `test_news_rules`, `test_news_event_history`; 기존 `test_news_grouping`, `test_news_analysis`, `test_news_auto_analysis`, `test_news_ai`, `test_central_news_service`, `test_central_ai_service`, `test_news_view_model`, `test_stock_news_window`. 라벨링한 예제/반례 묶음의 정확·미확인·오류 수와 false-negative 사례를 보고하고, 규칙+AI vs 기존 AI의 비용/누락을 비교한다. 성과 평가는 이후 D7에서 가격만 기준선과 뉴스 ablation으로 한다.

**되돌리기:** 자동 AI 필터와 뉴스 후보 소비를 각각 OFF, 원문·규칙·AI 이력은 유지. 기존 자동분석 identity 제한과 공급자 오류 상태 전달 회귀를 깨지 않는다.

## 11. N3 — 종목 선택 이전의 뉴스 수집 범위 확장

**목적/선행:** N1 후, 뉴스창이나 TOP20 진입 전에 사건을 발견할 수 있는 제한된 시장 범위 source를 추가한다. N2 전체 분류 완성을 기다리지 않아도 원문 기록은 시작할 수 있다.

**수정 대상:** `central_server/news_service.py`, `infrastructure/naver_news.py`, `dart_disclosures.py`, 중앙 config/운영 설정; **신규 source 설정·cursor 테이블**. 새로운 공급자 어댑터는 실제 선택된 한 공급자만 만든다. 기존 종목 관련성 필터를 전역으로 완화하지 말고 query/feed 수집 경로에서 기사→종목 관계를 분리한다.

**입출력:** `source_id`, scope(`watchlist/query_set/provider_feed`), query/feed ID, cursor, schedule, page/item/request budget → 기사 관측 + `checked_at/last_success/cursor/items/truncated/error/coverage`. 중복 기사는 하나의 article에 복수 target 연결을 둔다. 종목 이름 매칭의 불명확한 동명·약칭은 미확인으로 보존한다.

**먼저 확인할 외부 계약:** 사용할 뉴스 피드의 실제 범위, pagination·지연·한도·본문 접근 방식·보존 조건을 공식 명세로 확인한다. 확인 전에는 기존 watchlist/query_set 모드로 한정한다. 단일 '증권' 검색어 또는 등록 종목 일괄검색을 시장 전체 전수 수집으로 표시하지 않는다. 이 결정은 N3에서만 필요하며 D1~D4를 막지 않는다.

**완료 기준/테스트:** 앱이 없을 때도 선택한 source 수집, 재시작 cursor 이어받기, 중복 페이지·정정·pagination 잘림·429·인증 실패·본문 장애 독립 처리. 신규 `test_news_source_collection`, 기존 N1/네이버/DART 회귀. 운영 완료는 source별 최대 공백·지연·누락/잘림·저장량·대기열·순위 TR 지연 측정 보고이며 N의 1만~2.5만건 추정치를 보장값으로 쓰지 않는다.

**구현 상태 (2026-09-12):** N3 최소 수직 경로의 코드와 로컬 fake-page 검증을 완료했다. 공식 NAVER 검색 계약에 맞춰 `display<=100`, `start<=1000`, `sort=date`, 공식 25,000회보다 낮은 24,000회 hard budget을 적용했고, 기본 query set은 `증권, 코스피, 코스닥, 상장사, 수주 계약, 유상증자, 인수합병, 실적 전망, 최대주주`다. 중앙 schema v8에 cursor/run/observation/target/budget을 추가하고 global article→BODY→confirmed target RULE을 연결했다. 같은 기사의 여러 query 관측, 정정, 재시작 page 진행, 1,000건 절단/gap, auth·429 독립 실패, scope 예산, exact 회사명 경계를 포함한 관련 회귀 117개와 전체 핵심 회귀 580개(270+310)가 통과했다. 자동 AI 필터, CandidateEvent, 주문은 켜지 않았다. 후속 보완에서 중앙 schema v10의 source+identity 인덱스와 직전 판본 재사용을 추가해 query별 요약 순환의 중복 BODY를 막았고, 기간 진단에 `distinct_identity_count`를 분리했으며, 빈 KRX catalog 초기 채움과 늦은 confirmed target의 기존 BODY→RULE 연결을 추가했다. v11에서는 정확한 stock_code로 confirmed 연결된 GLOBAL 최신 판본을 기존 종목 뉴스 목록에 합친다. identity가 겹치면 기존 종목 owner 기사를 우선하고 이 읽기는 외부 호출이나 BODY/RULE/AI 예약을 만들지 않으며 자동 AI 입력도 owner 기사만 유지한다. 선택 기사 본문·규칙 근거는 중앙 모드 종목 뉴스창에서 읽을 수 있다. NAS PostgreSQL migration·실제 NAVER 자격증명·7일 공백/429/저장량/queue 실측은 사용자 재빌드 뒤 확인할 항목이다.

**후속 시장 이벤트 기록 상태 (2026-09-12): 코드·mock 검증 완료, NAS 배포 전.** 중앙 schema v9에 시장 전체 VI, 저장 조건식 15% cohort 현재/이력, 상한가 사실 원장을 추가했다. 조건검색은 KRX 범위이며 매 연결마다 이름으로 seq를 재해석한다. 편입 종목은 다음 실제 관측 KRX 세션 종료까지 기존 hub와 장후 보완 대상에 합류하고, `upl_pric`과 0B 근거만 상한가 사실로 기록한다. UI·후보전략·AI·주문은 연결하지 않았다. 오늘 Kiwoom 점검 중이므로 실제 1h/CNSRLST/CNSRREQ/ka10054 wire 및 장마감 수명은 사용자 NAS 재빌드 뒤 별도 확인한다.

**되돌리기:** 추가 source만 OFF, 기존 watchlist 유지. 수집 실패 동안 missing 구간을 기록하고 과거 게시시간을 실제 받은 시간으로 바꾸지 않는다.

## 12. D7 — Manual 연구와 현실적인 비교 보고

**D7a 구현 상태 (2026-09-13): 코드·로컬 핵심 회귀 682개(`329+353`) 완료.** `chronological_holdout/v1`은 TRAIN→VALIDATION→OOS 시간순 fold와 warmup/gap/purge, `continuous_state_and_cash/v1`, `censor_open_position/v1`, final holdout 접근 시각·이유를 불변 RunSpec으로 받는다. 비용 모델에는 실제/공식/추정 구분, 출처와 유효기간을 추가했다. 보고는 고정 입력 hash·strict KRX 봉·당시 후보군·warmup·비용 계약과 사용자가 정한 최소 거래/활동일부터 검사하며, fold별 체결 손익과 사건 반응을 섞지 않고 MDD·노출·turnover·거절/검열·NO_TRADE·종목/일자/시간대 통계를 `research.sqlite3` v3에 저장한다. 진입/청산 또는 결과 지평이 purge 경계를 넘으면 제외하고, 접근 기록 없는 final OOS는 결과 수치 없이 `SEALED`로 둔다.

**D7b 구현 상태 (2026-09-13): 코드·로컬 핵심 회귀 693개(`339+354`) 완료.** `--compare-rank-baseline`과 앱 `전략 연구` 창은 필터 없는 동일 돌파 Family와 관심순위 지속 Factor 적용 run을 같은 dataset·비용·체결·fold로 실행한다. 동일 candidate dedup key를 기준으로 공통·기준선 전용·variant 전용 거래를 구분하고, 기준선 전용 손실은 회피 손실, 이익은 놓친 이익으로 총손익 차이와 별도 보고한다. 연구 DB v4는 완료된 두 run의 비교를 불변 저장한다. 명시 JSON 요청은 낮은 우선순위의 단일 Windows 보조 프로세스에서 실행되며 취소된 run은 부분 원장을 보존한 채 같은 입력으로 재개 가능하다. OOS 봉인과 적격성은 두 보고서 모두 유지한다. 현재 export와 체결 모델에서 지원하지 않는 VI 주문 가능 여부·부분체결은 limitation으로 표시하며 실제와 같다고 주장하지 않는다. F1/N2가 구현되기 전인 시장유형·뉴스 ON/OFF 비교는 해당 선행 단계로 남긴다.

**목적/선행:** D3의 최소 모의를 연구 가능한 도구로 완성한다. 사용자가 파라미터/Factor를 골라 같은 데이터에서 비교하고 과적합·미수집 한계를 확인할 수 있어야 한다.

**수정 대상:** `application/research_execution.py`, `research_evaluation.py`, `research_replay.py`, 연구 원장·CLI; **신규 `presentation/research_dialog.py`**는 이 단계에서 실제 설정·진행·결과 비교만 표시한다. 연구는 NAS 밖의 별도 Windows 프로세스로 실행하여 Qt 메인 이벤트 루프를 막지 않으며, 로컬 worker 수·메모리·우선순위를 제한한다. 프로세스 분리만으로 자원 경쟁이 없어지는 것은 아니다. 기존 `process_control.py`의 명령/종료 방식은 실제 필요 범위만 재사용한다.

**입력:** 동결한 RunSpec+dataset, 시간순 train/validation/OOS split, warmup/gap/purge, 비용·체결/정산 모델, 기간말 포지션 정책, 연구 범위·최소 거래/활동일 적격성 기준. 비용률·시장 규칙은 해당 기간 공식 자료로 확인해 유효기간이 있는 설정으로 넣고 계좌 실비와 모델 추정 비용을 구분한다.

**출력:** 비용 차감 손익/기대값, 평균이익÷평균손실 크기, profit factor, 평가 자산 MDD, 거래/활동일·노출·미체결/부분체결·비용·turnover, NO_TRADE 이유, coverage/재현 등급, fold·종목·일자·시간대별 결과. 사건 반응 통계와 실제 모의 체결 수익은 별도 표다. 거래/분모가 없으면 N/A와 이유를 반환한다.

**비교군:** 필터 없는 동일 돌파 Family → 관심순위 지속 ON/OFF → F1 이후 시장 유형 필터 vs 원시 특징 필터 → N2 이후 뉴스 ON/OFF. 바뀐 조건 하나와 회피 손실/놓친 이익을 함께 본다. 09~10시/10~12시는 초기 실험 프로파일이고 운영 시간 규칙을 자동 변경하지 않는다.

**완료 기준:** 미래 입력 변조에도 이전 Snapshot/Decision 불변, fit은 train 범위 한정, 결과 지평·보유가 경계를 넘는 표본 purge, 같은 날 종목을 무작위로 나눠 독립 OOS로 표시하지 않음. walk-forward의 fold 초기화/연속 현금 정책과 중복 집계 없음. final holdout 접근 이력 기록, 열어보고 수정한 결과를 계속 미사용으로 표시하지 않음. 재분석은 새 run이며 기존 결과를 덮어쓰지 않는다.

**테스트:** 신규 `test_research_splits`, `test_research_reports`, D3 실행/평가 회귀. 수기 손익 fixture, 비용/지연/체결률 악화, same-bar stop/target 범위, partial fills와 자금 예약, 주문 불가/VI 정보 미지원 시 제외 사유, 한 날 편중, 거래 없음, 기간말 미청산 mark. 역순·늦은 정정·지표 warmup으로 누수가 생기지 않는 검사. 개발용 합성 데이터 통과와 실제 미사용 기간 성과 통과를 구분한다.

**되돌리기:** 연구 작업 취소·run 보존. 사용자 일지/실제 계좌에는 영향이 없다. 원본 자료가 없으면 재계산 가능하다고 표시하지 않는다.

## 13. D8 — 매매일지 자동보완과 판단 근거 연결

**계좌 확장 선행조건 (2026-09-13, 설계 완료/미구현):** 기존 D8 보완은 계좌 scope를 보장하지 않는다. [A1~A5 구현 계획](CONTINUOUS_RESEARCH_ACCOUNT_IMPLEMENTATION_PLAN.md)으로 기존 ID·복기·뉴스를 legacy로 보존하고 조회/실시간/FIFO/비용/sync에 검증된 계좌 출처를 전달한 뒤 연구 피드백을 연결한다.

**목적/선행:** D3의 불변 참조를 활용하되 기존 체결 조회·비용·봉·뉴스·수급·분류를 재작성하지 않는다. 자동보완의 범위를 창 선택에 덜 의존하게 하고 재시작 뒤 이어간다.

**구현 상태 (2026-09-13): 코드·로컬 핵심 회귀 완료.** 매매일지 SQLite v4에 대상·종류·입력 지문·정책 버전별 영속 작업, 파생 분석 revision, 실제 체결↔연구 근거 불변 링크를 추가했다. 기존 체결·비용·분봉 worker는 저장 성공 뒤 완료하며 비용 0건 정산 대기는 partial이다. 조회 기간의 선택하지 않은 회차도 단일 worker가 최대 20건씩 일봉·시장지수·사후 뉴스·수급·파생 판정을 확인한다. 사용자 복기·수동 묶음·회차 override는 수정하지 않는다. N1 revision과 실제 available_at이 없는 뉴스는 발표시각만으로 당시 근거라 판정하지 않는다. 작업 owner/재시도 시각과 새 파생·링크 표는 첫 버전에서 중앙 동기화하지 않으며, 매매일지 프로세스가 닫힌 동안 상시 실행은 아직 보장하지 않는다. D8 집중 회귀 84개와 전체 핵심 회귀 704개(`339+365`)가 통과했다.

**수정 대상:** `journal_process.py`의 `_auto_sync_history_once`/보완 시작·완료 연결, `presentation/journal_workers.py`, `application/trade_analysis_preparation_service.py`, `trade_history_query_service.py`, `persistence/journal_schema.py`, `journal_trade_repository.py`, `journal_bar_repository.py`, `journal_snapshot_service.py`, `trade_review_view_model.py`. **신규 `application/journal_enrichment.py`**는 대상 선택·상태·재시도 정책을 소유한다.

**D8a 작업 계약:** EnrichmentTask 상태는 pending/running/complete/partial/retryable_failed/unavailable/cancelled. 대상은 체결·비용·분봉·일봉·시장지수·사후 뉴스/수급·파생 분석으로 구분하며 target+kind+input fingerprint+policy version으로 중복을 막는다. 결과/메타데이터 저장 성공 뒤 완료 처리한다. 비용 정산 대기는 0원 완료가 아니다. 한 단계 실패가 이미 받은 체결을 버리게 하지 않는다.

**실행 범위:** 첫 버전은 기존 매매일지 프로세스 안의 단일 실행자이며 창이 닫혀 있는 동안에도 반드시 작업한다는 보장은 아직 없다. 메인/NAS에서 상시 수행하려면 후속으로 같은 정책을 headless 실행자에 연결하고 명시적 소유권을 정한다. 현재 timestamp 기반 콘텐츠 sync를 분산 lock으로 사용하지 않는다. `running/next_retry_at/owner` 같은 로컬 작업 상태는 다른 PC에 일반 콘텐츠로 복사하지 않는다.

**D8b 사용자/파생 소유 계약:** `trade_reviews` 사용자 메모, 수동 묶음·회차 유형 override, 전략팩 원문·초안·승인 상태는 자동보완 쓰기 금지다. 기계 작성 복기는 별도 파생 revision에 저장하고 `trade_reviews.review`에 덮어쓰지 않는다. 기존 수동 변경시각도 유지한다. 기존 분류/전략팩은 사후 분석 계약이며 live Strategy로 재사용하지 않는다.

**D8c 근거 계약:** execution_ref ↔ run_id/decision_id/snapshot_id/entry_thesis_id 관계를 별도 링크로 둔다. 수동/과거 거래는 decision_id=null 허용. 모의 체결은 별도 실행 원장에 두고 기존 일지에서 별도 보기/링크로만 소비한다. 일지의 뉴스는 `당시 알려진 기사`와 `장후 발견한 설명`을 분리하며 `snapshot_provenance`/`trade_snapshot_observations`로 판정한다. 기존 스냅샷 upsert를 불변 연구 증거로 사용하지 않는다.

뉴스의 당시 가용성은 **N1의 실제 available_at/revision 증거가 있는 경우에만** 인정한다. 기존 `news_at_execution`의 발표시각 범위나 backfilled 표식만으로는 당시 인지를 증명할 수 없으며 레거시는 `가용성 미확인/사후 복기`로 표시한다. 기존 provenance는 보완 출처 판정에 재사용하고 이를 넘어선 사실을 추정하지 않는다.

**완료 기준:** 선택하지 않은 대상도 정의된 범위에서 보완, 프로세스 재시작 후 running 작업 재검사, 반복 실행에 중복 없음. 다른 종목으로 바뀐 UI 선택·편집 내용 보존. 전체 보완 전후 사용자 영역 값·수정시각 동일. 재분석은 입력/분석 버전이 바뀔 때만 새 revision. tombstone으로 삭제한 수동 묶음을 되살리지 않는다.

**테스트:** 신규 `test_journal_enrichment`, `test_journal_research_links`; 기존 `test_journal_database`, `test_journal_schema`, `test_journal_workers`, `test_trade_analysis_preparation_service`, `test_trade_history_query_service`, `test_trade_group_edit_service`, `test_journal_snapshot_service`, `test_snapshot_provenance`, `test_central_journal_sync`. 저장 중 실패·정산 지연·날짜 전환·선택 변경·강제 중단→재개, 사용자 영역 불변을 비교한다.

**백업/되돌리기:** `journal_backup.py`는 SQLite 전체 DB 백업이므로 새 표가 포함된 export/import와 이전 DB fixture 호환을 검사한다. `central_journal_sync.py`의 명시 목록·API 컬렉션·문서에는 공유할 새 파생 결과/근거 링크만 추가한다. 계좌 민감 원장은 기존 인증 범위에서만 제공한다. flag OFF로 기존 자동/수동 보완에 복귀하고 새 상태·사용자 자료를 삭제하지 않는다.

## 14. F1 — 시장 특징과 네 유형의 비교 가능한 분류

**구현 상태 (2026-09-13): 코드·로컬 회귀 완료.** `market_research_features.py`는 같은 TOP20 `U(t)`, `COMBINED` venue, 누적 세션 window에서 Top1/Top5 집중도, 고정 K 교체율, Jaccard 변화, 현재 대장의 상위권 체류, 대장 교체, 과거 거래일 동일 세션 분의 거래대금 중앙값 대비 비율을 계산한다. 테마는 D5의 당시 가용 revision에서 첫 대표 테마 하나로만 배분하며 대표 테마 집중·상승 확산의 분모를 보존한다. 시장 폭은 전체시장으로 오해하지 않도록 `top20_breadth`로만 출력한다. 3분 Outcome 중 현재 시각까지 COMPLETE인 표본만 성공/실패 분모에 넣고 PENDING은 따로 센다. 0 거래대금, 신규 종목/시간대 이력 부족, 순위 공백, 테마·성숙 결과 부족은 0점으로 바꾸지 않는다.

`market_regime/v1`은 검증 전 고정 규칙으로 네 유형 후보와 `UNKNOWN`, 이유·품질·rule version을 반환한다. 지수만 강한 경우, 건강한 테마 확산, 빠른 실패 순환, 미성숙 돌파, 데이터 중단 fixture를 구분한다. 복수 근거가 겹치면 임의 우선순위를 적용하지 않고 `UNKNOWN`과 `candidate_types`를 남긴다. tradability/difficulty는 전략별 별도 함수이며 기존 돌파 Family의 진입 관문으로 연결하지 않았다. D2 export는 기존 NAS 테마 이력 API 결과를 별도 JSONL과 hash/count로 동결하고, run 종료 시점의 시장 분류를 불변 manifest와 연구 화면 상태에 표시한다. 기존 NAS 스키마·수집 연결·후보 생성·주문 경로는 바꾸지 않았다.

분류 임계값은 원 설계에서 미확정이므로 운영 기본값이나 수익 확률이 아니라 `top20_market_types/v1` 실험 규칙이다. D7 비교 원장은 `market_type_filter`와 `market_raw_feature_filter` 조건을 받아 같은 candidate key의 표본 수·회피 손실·놓친 이익을 보존한다. 현재 보유 데이터가 쌓인 뒤 필터 없음 대비 유형 또는 원시 특징을 **명시적으로 선택한 전략 프로파일**로 비교해야 한다. 이번 단계에서는 검증되지 않은 유형을 모든 전략의 필수 필터로 자동 적용하지 않았다.

**목적/선행:** D5/D7 이후 실제 확보한 자료로 시장 배경과 관측 후보군을 나눠 보여준다. 시장 유형은 선택 Factor이며 모든 전략의 필수 관문이 아니다.

**수정 대상:** `application/research_factors.py`의 명시 등록, 기존 `theme_ranking.py`/`trade_strength.py` 중 순수 계산 재사용; 필요 시 **신규 `application/market_research_features.py`** 한 파일. 시장 상태 입력은 `central_server/realtime_collector.py`/`market_observations.py`에 필요한 필드 revision만 더한다. `presentation/research_dialog.py`와 후보창은 계산 결과만 표시한다.

**계산 계약:** 같은 `U(t), venue, window` 안에서 Top1/Top5 거래대금 합÷U 합, K 고정 관심순위 교체율 `1-|교집합|/K`, 가변 집합 Jaccard 변화, 상위 체류·주도 교체를 각각 계산한다. 상대 거래대금은 과거 N거래일 **동일 세션 시각** 중앙값을 기준으로 하며 기록이 없는 신규 종목은 null이다. 0분모를 점수 0으로 만들지 않는다. 테마 중복 배분은 최초 대표 테마 또는 합계 1의 가중치 중 명시된 하나를 사용한다. 모든 분모와 유효 관측 수를 출력한다.

**분류 출력:** NORMAL_LEADER/SINGLE_LEADER/THEME_LED/DISPERSED_ROTATION + UNKNOWN, candidate_types, reasons, quality, rule_version. tradability/difficulty는 전략별 별도 출력. 관심순위 교체율 하나로 분산장을 확정하지 않는다. 지속·테마 확산·현재까지 성숙한 돌파 라벨이 부족하면 판정 유보한다. COMPLETE 시장상태 row도 일부 필드가 null이므로 필수 입력별 검사한다. TOP20만 읽은 market_breadth는 `top20_breadth`로 부르고 전체시장 breadth로 표시하지 않는다.

**완료 기준/테스트:** S의 지수만 강함·건강한 확산·빠른 실패 순환·미성숙 돌파·데이터 중단 시나리오를 fixture로 구분. 신규 `test_market_research_features`; 기존 `test_theme_ranking`, `test_trade_strength`, `test_market_data_coverage`, `test_central_realtime_collector`. D7에서 필터 없음/네 유형/원시 Factor의 OOS·ablation 비교와 표본 수를 보고한다. 미확보 전체시장 자료는 새 TR을 무제한 추가하지 않고 지원하지 않는 축으로 남긴다.

**되돌리기:** 분류 Factor OFF, 원시 값·이유·run은 보존. 분류 규칙 변경은 새 버전이며 과거 보고를 덮어쓰지 않는다.

## 15. C1 — 장전 가설·전일 모멘텀·장중 확인

**구현 상태 (2026-09-13): 첫 연구 계약·로컬 원장 완료.** `application/context_candidates.py`가 N2 뉴스, D5 관계 revision, 전 거래일 확정 사실, D4 장중 후보와 기존 Yahoo 지연 5분/일봉을 `context-candidates/v1`로 변환한다. 사실과 영향 추론을 분리하고 같은 사건·종목의 복수 출처는 장중 반응 전에만 합친다. 장중 응답 원문을 보존하면서 `UNCONFIRMED→FLOW_CONFIRMED→LEADERSHIP_CONFIRMED`와 `NO_RESPONSE/EXPIRED/REJECTED`를 새 revision으로 기록하며 가설은 진입 권한을 가질 수 없다. 전일 후보는 명시된 정상 KRX 세션 predecessor만 사용하고 상한가 터치/마감·신고가 종류·unknown을 구분한다. `research.sqlite3` v5는 최초 가설과 응답별 개정본을 run과 독립된 불변 이력으로 저장하고 as-of 최신 상태를 읽는다. 기존 NAS 후보 생성·알림·주문 경로는 바꾸지 않는다. 관련 회귀 36개와 새 C1 테스트가 포함된 전체 핵심 회귀 716개(`351+365`)가 종료 코드 0으로 통과했다.

**목적/선행:** D5/N2/D7에서 확보한 시점 자료를 사용해 GLOBAL_CONTEXT/STOCK_NEWS/CARRYOVER/INTRADAY_DISCOVERY 근거를 통합한다. 사전 후보가 없어도 D4에서 장중 발견할 수 있다.

**수정 대상:** **신규 `application/context_candidates.py`**(가설 수명·전일 후보 계산), 연구 원장, 기존 뉴스/후보 표시 모델. 해외 자료는 기존 `central_server/external_market_collector.py`/`futures_roll.py`의 실제 지연·계약 정보를 어댑터로 읽고 수집기를 전면 교체하지 않는다.

**입출력:** 당시 가용 뉴스 사실, 버전 있는 테마 관계, 전 거래일 확정 봉·신고가 종류·확인된 상한가 자료 → 가설/후보 `{id,source_refs,target,relation_version,facts,inferred_impact,created/available_at,expires_at,status}`. 장중 자료는 `UNCONFIRMED→FLOW_CONFIRMED→LEADERSHIP_CONFIRMED`, NO_RESPONSE/EXPIRED/REJECTED를 근거와 함께 전이한다. 원인이 틀린 것과 시장 반응이 없는 것을 구분한다.

**전일 후보 계약:** 강한 종목/신고가/상한가 도달/상한가 마감을 구분하며 실제 공급자 제한가격·당시 자료가 없으면 unknown. 전 거래일은 명시된 세션 달력으로 찾고 월요일-1일 같은 계산을 쓰지 않는다. 첫 프로파일은 지원되는 정상 KRX 거래일만 허용하고 휴장·특별개장 불명 구간을 제외한다. 나스닥/WTI 5분 지연 자료를 초단위 동시 관측이나 모든 거시 자료로 표현하지 않는다.

**완료 기준/테스트:** 사전 강한 뉴스에 실제 반응 없으면 자동 진입으로 승격 안 됨, 전일 상한가 무반응, 뉴스 없는 주도 발견, 동일 사건을 복수 출처에서 중복 가산하지 않음, 과거 계약/테마·달력 버전 보존. 신규 `test_context_candidates`; 기존 `test_external_market_collector`, `test_news_grouping`, D4/D5 회귀. D7에서 후보 출처별 다음 날 반응과 추가 기여·무반응·확인 소요시간을 비교한다.

**되돌리기:** 장전/전일 후보 생산만 OFF. D4의 가격 기반 후보와 기존 뉴스 화면은 유지한다.

## 16. H1 — TOP20 구독 종목의 1초 집계·공백 기록

**목적/선행:** 현재 0B 스트림에서 앞으로의 초 단위 가격·거래대금 연구 자료를 먼저 확보한다. D0만 읽으면 시작할 수 있으며 앱 UI·후보 전략·신규 키움 연결·추가 종목 구독은 포함하지 않는다.

**수정 대상:** 기존 `central_server/realtime_collector.py`, 중앙 schema/database와 `central_server/app.py`; 기존 `infrastructure/kiwoom_rest/realtime.py`는 현재 `TradeTick`에 필요한 값이 없을 때만 최소 보완한다. 별도 Manager/Service나 원시 틱 저장 경로를 만들지 않는다.

**입출력:** 기존 파싱된 `TradeTick`과 NAS 수신시각 → 종목·시장·거래초별 `open/high/low/close, volume, trade_value, trade_count, available_at` 및 품질 참조. `trade_time`의 날짜는 실제 세션·수신 날짜와 검증하고, KRX/NXT를 분리한다. 같은 초의 여러 체결은 한 행으로 집계하며 늦게 온 체결은 명시된 허용 범위 안에서 새 revision 또는 품질 상태로 처리한다. 매수/매도 방향은 0B에서 신뢰 가능한 부호가 확인될 때만 선택 필드로 추가하며 투자 주체로 표현하지 않는다.

**빈 초·공백 계약:** 체결이 없는 초에는 행을 만들지 않는다. 대신 종목 구독 시작/종료, 상위 WebSocket 연결·재연결, 세션, queue overflow와 DB 저장 실패 구간을 별도 append 기록한다. 조회 소비자는 구독·연결이 정상인 빈 초만 무거래로 채울 수 있고, 공백 구간을 0거래로 합성하지 않는다. TOP20 진입 전 초 단위 자료는 `UNAVAILABLE`이며 기존 1분봉만 사용할 수 있다.

**저장·조회:** 중앙 DB에 append/upsert 안전한 1초 테이블과 종목·시장·시작/종료 범위 조회 API를 추가한다. 3/5/10초는 조회 소비자가 1초 행을 합산한다. 기본 보존기간은 없고 첫 단계에서 자동 삭제하지 않는다. 향후 용량 정책이 생기면 실제 매매·후보·연구 manifest가 보호한 구간을 제외하고 오래된 거래일 단위로 정리한다.

**완료 기준/테스트:** 동일초 복수 체결의 OHLC·거래량·거래대금·건수 수기 합계 일치, 중복/역순/초·날짜·KRX↔NXT 경계, 무체결 초, 단절→재접속, 구독 변경, 저장 실패→재시도에서 이중 합산 없음. 신규 `test_second_trade_aggregation`, `test_second_trade_storage`, `test_second_trade_api`; 기존 `test_realtime`, `test_central_realtime_collector`, `test_central_server_database`, `test_central_server_app`, `test_minute_trade_value`. 실제 NAS 소량 표본에서 bytes/sec, 일일 증가량, CPU·메모리, queue, 최대 공백과 기존 순위·1분봉 지연을 측정한다.

**되돌리기:** 1초 집계 기록 flag만 OFF하고 기존 0B 최신값·1분봉·TOP20 순위 수집을 유지한다. 이미 저장한 1초 자료와 공백 기록은 삭제하지 않는다.

## 17. H2 — 가격·거래대금 기반 테마 대장·이탈·재가속과 진입 근거

**구현 상태(2026-09-13): 완료.** `theme-leadership/price-trade-value-v1`은 D5 테마 revision과 H1의 종목·시장별 1초 행을 받아 수익률·거래대금의 동일 가중 순위, 가격 이탈/재도달, 거래대금 둔화/재가속을 계산한다. 데이터 연속성이 `COMPLETE`가 아니면 새 판단을 만들지 않고 `UNKNOWN`과 결측 사유를 남긴다. 화면용 대장 교체는 확인 시간 뒤에만 반영하지만 가격·대금 사건은 원시 판단 시각으로 별도 보존한다. `entry-thesis/v1`은 진입 당시 대장·테마 revision·Factor/가설 참조를 고정하고, 이후 대장이 바뀌어도 과거 근거를 고쳐 쓰지 않는다. 네 가격 기반 대응 정책의 결과는 비교용 제안일 뿐 주문 권한이 없다. 연구 DB는 v6으로 확장되었고 호가가 필요한 `LOCKED/BROKEN/RELOCKED`는 출력하지 않는다.

**목적/선행:** D5/H1/D7 이후 1초 가격·거래대금으로 대장/후발주의 움직임과 진입 근거 붕괴를 비교한다. 호가 잔량 기반 잠김·풀림 연구는 포함하지 않는다.

**수정 대상:** **신규 `application/theme_leadership.py`**(대장/후발 순위·상태 전이), Family의 별도 exit 함수, Factor 등록, Outcome/Decision 저장, 후보/일지 근거 표시. 여러 유형별 Manager/Controller는 만들지 않는다.

**입출력:** 당시 테마 구성, 대장 순위 산식 버전, 1초 가격·거래량·거래대금·체결 건수·유지시간 → trend_state, limit_state(NONE/NEAR/TOUCHED/UNKNOWN), expansion_state, leader event. 상한가 가격 체결은 `TOUCHED`까지만 판정하며 `LOCKED/BROKEN/RELOCKED`는 출력하지 않는다. 가격 이탈·재도달·거래대금 둔화/재가속 사건과 표시 안정화 상태를 분리한다.

**진입 근거:** EntryThesis는 진입 당시 leader_id/theme_version/필수 Factor·가설 refs를 고정한다. 새 대장이 나와도 과거 근거의 ID를 바꾸지 않는다. 이탈은 THESIS_AT_RISK/INVALIDATED 후보이며 즉시 청산/부분 축소/추가 확인/기존 가격청산 유지 정책을 각각 D7 trial로 비교한다.

**완료 기준/테스트:** 가격 이탈→재도달, 거래대금 둔화→재가속, 데이터 중단, 대장 교체 시나리오를 신규 `test_theme_leadership`, `test_entry_thesis`에서 검사한다. 호가가 필요한 상태는 미지원, 기존 대장 vs 신규 대장 참조를 분리하고 시간차·지평별 후발 결과·비용 차감·표본을 비교한다. 현재 자료만으로 상관을 자금 이동 인과관계나 수익 확률로 표시하지 않는다.

**되돌리기:** 대장 기반 신규 진입/청산 정책 OFF, 기본 Family 유지. 이미 있는 포지션의 정책 교체는 run 버전 규칙에 따라 처리한다.

## 18. V1 — 기존 NAS/직접 대조기의 측정 범위 확대

**구현 상태(2026-09-13): 완료.** 기존 `ParallelValidationClient`와 `RealtimeValidationRecorder` 안에서 `MATCH/VALUE_MISMATCH/LATE/MISSING/DUPLICATE/OUT_OF_ORDER/NOT_COMPARABLE`을 분리하고, 비교 가능 분모·상태별 건수·대기열 skip·pending eviction·미매칭·도착 간격 p50/p95/p99/최대값을 bounded 통계로 남긴다. 조회는 공유 불변 source ref가 있을 때만 값 불일치를 확정하며, 참조 없는 변동 응답은 비교 불가로 낮춘다. 실시간은 종목·venue·시장시각을 기본 키로 쓰되 같은 시각의 두 번째 이후 사건에 공급자 ID가 없으면 n번째 짝을 확정하지 않고 비교 가능한 누적값과 함께 비교 불가로 기록한다. 수신 간격과 source clock 차이는 별도 필드다. 만료와 eviction은 `MISSING`으로 남고, 중앙 WebSocket 대기 중에도 만료를 처리한다. 비교 대기열은 주 NAS 응답을 기다리게 하거나 주 화면 신호에 로컬 비교값을 전달하지 않는다.

**목적/선행:** D2부터 병행 가능한 품질 작업. 현재 `ParallelValidationClient/RealtimeValidationRecorder`를 확장하고, 데이터 공급 원천이 같으면 전달·변환·저장 대조라고 설명한다.

**수정 대상:** `infrastructure/kiwoom_rest/validation_client.py`, `central_realtime_worker.py`, `bootstrap.py`의 기존 연결 설정, D1 기록 참조. 별도 대조 서버를 만들지 않는다.

**입출력:** 경로별 원본 refs·수신/사용가능시각·event/sequence·venue·단위·집계 범위 → MATCH/VALUE_MISMATCH/LATE/MISSING/DUPLICATE/OUT_OF_ORDER/NOT_COMPARABLE, 비교 가능 분모·미매칭·queue skip/eviction·p50/p95/p99·최대 공백. 동일초 경로별 n번째 이벤트 짝짓기는 한쪽 누락 뒤 틀어질 수 있다. 공급자 ID가 없고 매칭이 모호하면 구간 거래량/최종값 등 비교 가능한 집계로 낮추고 NOT_COMPARABLE을 남긴다.

**연결 정책:** 주 입력은 NAS 또는 직접 하나로 고정, 비교용 값을 동일 run에 두 번 넣지 않는다. 두 source의 Decision 비교는 별도 run/동일 spec으로 수행한다. 동일 계좌·토큰으로 세션을 새로 열어 기존 수신을 끊지 않도록 실제 지원 조건 확인 후 활성화한다. 장비별 시계 오차와 전송 지연을 시장값 오류와 구분한다.

**완료 기준/테스트:** 신규 분모·만료·순서·clock skew fixture와 기존 `test_parallel_validation_client`, `test_central_realtime_worker`, `test_kiwoom_client_factory`, `test_failover_kiwoom_client`. 중앙 응답이 비교 완료를 기다리지 않고, 느린/가득 찬 비교 queue가 순위·주 판단을 막지 않는다. 실제 데이터 품질 보고와 Feature/Decision 불일치를 연결하며 실제 장 테스트 전에는 무누락/지연 기준 통과라고 보고하지 않는다.

**되돌리기:** 비교만 OFF, 기존 주 입력 계속. 대조 결과로 원본이나 주 입력을 자동 교체하지 않는다.

## 19. R1 — 제한 자동 탐색

**후속 감사 (2026-09-13):** 아래는 최초 구현 기록이다. 취소 재개·전체 실행 식별·OOS 선택 결함이 이후 확인됐으므로 [F01~F07](CONTINUOUS_RESEARCH_ACCOUNT_SCOPE_REVIEW.md)과 CR0 수정 계획을 우선한다.

**구현 상태 (2026-09-13): 코드·로컬 핵심 회귀 완료.** `application/research_search.py`가 등록된 `krx_bar_close_breakout/v1` Family와 두 Factor, 명시된 soft parameter만 대상으로 작은 seed 고정 grid를 만든다. 기본전략·무거래 기준선·파라미터 민감도와 요청된 `rank_persistence` ablation·비용 가중 시나리오는 모두 D7 시간순 report builder를 통과한다. trial 수·경과시간·동시 실행 1개의 resource budget을 강제하며 실패·취소·부적격도 사용량에 포함한다. 같은 ExperimentSpec은 같은 ID와 후보 순서를 만들고 SQLite 연구 원장 v7의 완료 trial을 건너뛰어 재개한다. dataset ID/hash, Family/Factor 버전, hard 전략 필드, split/OOS 접근 기록은 탐색 중 바뀌지 않으며 운영 설정을 수정하지 않는다. 후보 카드는 순손익·최대 낙폭·거래 수·활동일·원래 판정 사유와 선택 조건을 함께 보존한다. 연구 화면은 단일 최고 점수 대신 모든 완료 카드를 표시한다. R1 집중 회귀 26개와 전체 핵심 회귀 760개(`395+365`)가 통과했다.

**목적/선행:** D7의 수기 비교·OOS·비용 검증이 완료된 뒤 등록된 Family/Factor/파라미터만 탐색한다.

**수정 대상:** **신규 `application/research_search.py`**, D7 runner/원장/UI. 처음에는 작은 grid 또는 seed 고정 random search. 별도 최적화 프레임워크·분산 실행은 필요하지 않다.

**입출력:** ExperimentSpec(`hypothesis_refs`, dataset hash, Family/Factor allowlist, parameter_space, objective/constraints/split 버전, trial/time/resource budget, seed, stopping rule) → 정규화 spec hash별 trial과 후보 카드. failed/cancelled/ineligible도 시도 수에 포함하고 성과 0으로 바꾸지 않는다. 기본전략·무거래 기준선·비용 스트레스·민감도·ablation을 동일 평가 파이프라인으로 실행한다.

**완료 기준:** 최대 budget 준수, 완료 trial 중복 없이 재개, 동일 spec/seed는 같은 후보 목록, Hard 조건을 탐색 파라미터로 완화 못 함. 운영 spec과 연구 spec은 분리. holdout을 열어 본 이력과 전체 탐색수/선택 기준이 보고서에 남는다. 여러 점수의 원래 값·낙폭·표본을 보여주고 최고 점수 하나로 채택하지 않는다.

**테스트:** 신규 `test_research_search`, D7 split/report 검사. 실행 중단→재개, 동일 후보 생성, timeout·실패·부분 결과, 예산 0/경계, objective 변경시 새 experiment, 반복 holdout을 미사용으로 분류하지 않는 검사. 실제 전략 우위는 별도 미사용 자료/forward로 검증한다.

**되돌리기:** 탐색 취소·queue 정지, 완료 run/후보는 남김. 실행중인 운영 전략을 변경하지 않는다.

## 20. R2 — Family 확장·Free Research·지속 연구

**기존 구현 기록 (2026-09-13): 두 번째 Family와 유한 탐색의 코드·당시 회귀 기록. 연속 연구 완료는 아님.** `krx_pullback_reacceleration/v1`을 실제 두 번째 Family로 추가했다. 연속 strict KRX 완료봉에서 고점 뒤 눌림 폭과 현재 봉 재가속을 계산하며 TOP20·선택적 순위 지속, 기존 단일 포지션 진입/손절/목표/최대보유/고정수량 계약을 그대로 사용한다. `research_families.py` 등록표가 두 Family의 입력·상태·진입·청산·수량·허용 실행환경과 Family별 탐색 파라미터를 고정한다. R1 ExperimentSpec은 `manual/constrained_auto/free_research` 생성 모드와 `replay/simulation` 실행환경을 독립 필드로 보존하며, 한 유한 experiment에서 등록 Family 하나만 선택한다. 임의 코드·미등록 Factor·live 실행은 거부한다. 후보 계획 수, 동시 trial 1개, job 보존 수, 최소 메모리 예산값을 검사한다. 연구 DB v8은 같은 dataset/spec의 작업을 `queued/running/completed/failed/cancelled` projection과 불변 event로 기록하고 실행 임대 만료 뒤 재개한다. 완료된 동일 manifest는 다시 enqueue하지 않으며 보존 한도에 도달하면 과거 결과를 지우지 않고 새 작업을 거부한다. 완료·실패·원지표가 달라진 후보만 화면에 알림 대상으로 제안하고 외부 알림이나 운영 전략 변경은 하지 않는다. R2 집중 회귀 57개와 전체 핵심 회귀 771개(`406+365`)가 통과했다.

**장시간 연구 재검토 (2026-09-13): 설계 결정 완료, 수정 구현 전.** 한 변수 비교·CPU 휴식·GUI 재개를 추가했으나 취소 trial 영구 소비, baseline/비용/fold가 빠진 search identity, OOS 선택 혼입을 이번에 재현했다. 예약 취소·lease·자원 제한도 보완 대상이다. [재검토 결정](CONTINUOUS_RESEARCH_ACCOUNT_SCOPE_REVIEW.md)과 [Sol 구현 계획](CONTINUOUS_RESEARCH_ACCOUNT_IMPLEMENTATION_PLAN.md)을 우선한다. 같은 자료의 미실행 가설도 계속 시험하며 관련 새 자료로 검증을 넓힌다. 새 watermark만이 cycle 조건인 이전 제안은 폐기한다.

**목적/선행:** R1 이후 실제 두 번째 사례(예: 눌림 후 재가속)를 추가해 확장 비용을 검증한다. 모든 미래 판단을 표현하는 언어를 먼저 만들지 않는다.

**수정 대상:** 두 번째 Family 파일/등록·상태 스키마, `research_search.py`, 필요 최소 job 저장·UI. 등록된 연산/데이터/체결 모델 안에서 탐색 범위를 넓힌다. 임의 코드 실행·무제한 데이터 수집은 범위 밖이다.

**입출력:** 생성 범위와 실행 환경은 별도 축이다. 현재 replay/simulation은 모두 과거 paper 실행이다. 확정 v2는 전체 유효 전략·입력·평가의 trial, 중단 가능한 attempt, 유한 cycle, 지속 campaign을 구분한다. CPU·slice는 운영 정책이며 최종 검증은 개발 탐색과 분리한다. 구체 계약·완료 기준은 CR0~CR4 계획을 따른다.

**완료 기준/테스트:** 두 번째 Family 추가가 기존 Family 결과를 바꾸지 않음, 사용하지 않는 Factor 교체/비활성화에 영향 없음, 버전 전환 후 과거 run 재현, 중복 manifest를 변화 없이 무한 재탐색하지 않음. 신규 `test_research_extension`, `test_research_queue`; R1 전체 회귀. 완료·실패·의미 있는 후보 변화만 제품 알림으로 제안하며 이번 문서로 Codex 예약 작업을 만들지 않는다.

**되돌리기:** 새 Family 선택/새 연구 job 중단. 기존 코드/버전은 참조 run이 있으면 보존하고 deprecated만 표시한다.

## 21. O1 — 키움 모의 주문의 최소 기술 검증

**구현 상태 (2026-09-13): 인증 수동 모의주문 게이트웨이·fake broker 검증 완료, 실제 mock 응답 표본 대기.** 공식 키움 예제 기준으로 모의 도메인·KRX 한정, 현금 주문 `kt10000~kt10003`, 복구 조회 `ka10075/ka10076/kt00018/kt00001`, 계좌 실시간 `00/04`를 확인했다. `order_contract.py`와 `order_lifecycle.py`가 mock 전용 intent, 전송 전 계좌·만료·예약 검증, 응답 유실 시 `SUBMISSION_UNKNOWN`, 부분체결·취소 경합·오래된 snapshot 비소급을 소유한다. `mock_execution.py`는 모의계좌 인증/1초 호출 간격을 재사용하되 주문 통신을 한 번만 수행하며 failover/병행검증 조회기로 주문이 들어가는 것을 거부한다. `mock_account.py`는 account 전용 broker의 네 REST 연속조회를 주문·잔고 snapshot으로 바꾸며, 00 단위체결의 실제 ID와 개별 ID가 없는 REST 누적체결량을 분리해 늦은 상세 체결을 이중 합산하지 않는다. `mock_account_monitor.py`는 명시적으로 켠 mock 환경에서만 단일 임대를 얻고 시작 복구, 비공개 00 상세체결 대조, 04·접수·취소·재연결 후 전체 REST 재조회를 직렬 처리한다. `execution_runtime.py`의 수동 게이트웨이는 별도 기본 OFF 플래그와 인증 API 뒤에서 KRX 지정가만 받고, `run_id+request_id`를 멱등키로 사용하며 전송·취소 직전 계좌를 새로 복구한다. 04 예수금과 계좌번호 원문은 저장·공개하지 않는다. 중앙 DB v16 원장은 그대로 사용하고 후보·전략 자동주문은 연결하지 않았다. 관련 집중 회귀 83개와 전체 핵심 회귀 823개(`458+365`)가 통과했으며 다음 KRX 세션의 제한 주문 1주 왕복이 남아 있다.

**분리 정정 (2026-09-13):** 위 구현 상태의 “기존 인증/호출 간격 재사용”은 같은 모의계좌 내부의 조회와 향후 주문 transport에만 적용한다. 실전 조회와 모의투자 조회는 각각 초당 5회와 초당 1회의 별도 bucket이며, 별도 App Key/App Secret·`KiwoomRestClient`·요청 잠금·최근 호출 시각·broker queue·WebSocket을 소유한다. `mock_account_monitor.py`는 전용 00/04 세션까지 소유하고 주 시세 환경이 real이어도 독립 실행한다. 분리 후 관련 회귀 96개와 전체 핵심 회귀 817개(`452+365`)가 통과했다.

**목적/선행:** D3/D4/D7과 단일 전략의 불변 Decision이 준비되면 자동 탐색 완성 전에도 진행 가능하다. 새 주문 상태/계좌 경계의 신뢰성을 확인한다.

**O1a 명세 확인:** 공식 주문·정정·취소·미체결·체결·잔고 TR/이벤트 지원표와 mock KRX 범위, 주문/체결 식별·복구 조회 필드는 공식 저장소 예제로 확인했다. 실제 mock 응답 표본, 계좌·세션별 호출 제한과 장 운영 범위는 아직 운영 상수로 확정하지 않았다. A의 1초/200종목 숫자는 재확인 없이 복사하지 않는다.

호출 제한의 운영 계약은 실전 조회 초당 5회와 모의투자 조회 초당 1회를 별도로 적용한다. 실제 표본으로 남은 확인 대상은 모의 주문과 계좌조회 사이의 세부 제한 및 장 운영 범위다.

**수정 대상:** **신규 `domain/order_contract.py`, `application/order_lifecycle.py`, `infrastructure/kiwoom_rest/mock_execution.py`, `persistence/execution_repository.py`**와 NAS 수명/계좌 상태를 연결하는 **작은 `central_server/execution_runtime.py`**. 기존 `client.py`는 인증·공통 요청 간격과 전송 정책을 나누는 최소 seam만 추가한다. `validation_client.py`/`failover_client.py`/factory의 조회 경계는 허용 조회 ID만 받도록 검사한다. 중앙 broker는 이미 조회 allowlist가 있으므로 유지하며 주문 ID를 추가하지 않는다.

**NAS 모드의 계좌 조회 배치:** 최초 mock 실행 소유자는 NAS 한 프로세스로 정한다. 시세용 기존 broker와 별개로 **동일 CentralRestBroker 클래스의 mock 계좌 전용 인스턴스**를 사용해 미체결·체결·잔고 조회를 처리한다. 새로 확인한 조회 TR만 allowlist에 추가하며 namespace는 environment+account_ref로 나눠 cache/inflight 키와 저장을 격리한다. 기존 실전 시세 broker 기본 namespace/우선순위는 유지한다. mock 주문 전송은 조회 broker에 넣지 않고 같은 NAS 소유자의 전용 transport에서 하며 mock 조회 인스턴스와 인증·계좌/TR별 호출 제한 상태를 공유한다. Windows는 상태/근거를 소비하고 mock 계좌 REST를 직접 우회 조회하지 않는다. 계좌 상태·실행설정은 전용 인증 경계로 제공하며 일반 공통설정/뉴스 콘텐츠에 섞지 않는다.

여기서 “공유” 대상은 동일한 모의계좌의 조회와 향후 주문 transport다. 실전 시세 broker와 모의계좌 broker 사이에는 키·토큰·limiter·queue·WebSocket을 공유하지 않는다.

execution 원장의 쓰기 소유자도 이 NAS 실행자 하나다. repository는 중앙 SQLite/PostgreSQL의 추가 실행 표를 사용하고 기존 migration/트랜잭션 경계를 따른다. Windows에서 NAS 파일의 SQLite를 직접 열지 않는다. 연구용 로컬 paper 원장과 NAS broker 실행 원장은 run/environment/account_ref로 구별한다.

**전송 계약:** 공통 제한/인증을 우회하는 직접 HTTP는 만들지 않는다. 그러나 `_post`의 통신 오류 자동 4회 재시도, NAS→로컬 failover, 동일 요청 양쪽 비교는 주문에 사용하지 않는다. 실제 동일 토큰·계좌를 공유하는 실행자끼리는 하나의 제한 관리 경로를 소유하도록 배치하고, 단순 프로세스별 limiter를 계좌 전역 제한 준수로 주장하지 않는다. 위험 축소·취소·상태 확인과 일반 조회의 우선순위는 계좌 경계에서 정의하며 기존 NAS 순위 우선순위 계약을 임의 변경하지 않는다.

**향후 수신 감사:** 모의투자 직접 API 수신은 NAS 경유 수신과의 도착 지연, 누락·중복, 필드 정규화 차이를 재는 독립 비교원으로 사용할 수 있다. 비교 관측에는 source·broker event time·local receive time·NAS available time을 함께 남긴다. 이 감사 기능을 구현하기 전에는 모의 값을 실전/NAS 주 데이터와 합치거나 누락 자동 보정에 사용하지 않는다.

**입출력:** OrderIntent(intent/run/decision/account_ref, environment=mock, symbol/venue, side, qty, order_type, limit_price?, expires_at, policy_version) → 내부 ID↔broker ID, QUEUED/SUBMISSION_UNKNOWN/ACCEPTED/PARTIALLY_FILLED/FILLED/CANCEL_PENDING/CANCELLED/REJECTED, 상태·체결·대조 이벤트. 전송 큐 진입/실전송/응답/접수/체결/확인 시각을 보존하고 전송 직전 만료·가용현금·미체결 예약을 다시 검사한다.

**복구:** timeout은 접수 안 됨이 아니라 unknown일 수 있다. 저장된 intent와 브로커 미체결/체결/잔고/주문가능금액을 대조한 뒤에만 후속 행동을 정한다. 로컬 idempotency key만으로 외부 중복을 막았다고 주장하지 않는다. 00 단위체결은 실제 broker execution ID로, `ka10076`은 ID 없는 누적 체결량으로 구분한다. 두 근거가 역순 도착해도 상세합계와 broker 누적합계의 큰 값만 유효 체결수량으로 사용해 이중 합산하지 않는다. broker snapshot의 as-of를 기록하고 오래된 조회가 새로운 체결 상태를 되돌리지 않게 한다.

**소유권:** 최초 주문 실행은 지정 NAS 호스트/프로세스 1곳·mock 계좌 1개·주문 담당 run 1개다. Windows PC들은 읽기 전용이며 실행 설정을 공통 sync로 복제하지 않는다. 같은 호스트의 중복 실행은 배포 단일 인스턴스와 OS lock 등으로 막는다. 복수 호스트 실행을 실제 지원할 때만 중앙 atomic lease/fencing과 계좌 대조를 추가하며 로컬 lock이 다른 호스트 중복을 막는다고 주장하지 않는다. paper/비교 run은 같은 계좌에 주문하지 않는다.

**완료 기준/테스트:** mock 이외 환경 거부, mock 실패 후 real 자동 전환 불가, 주문 함수가 검증/failover 조회기에 도달하지 않음, 응답 유실 후 접수 확인 중 재전송 0건, 재시작·중복 체결·동일초 복수 체결·계좌 불일치·잔고 재대조·제한 queue 만료·취소 중 체결 회귀. 신규 `test_mock_execution`, `test_order_lifecycle`, `test_execution_repository`와 기존 `test_kiwoom_rest_client`, `test_kiwoom_client_factory`, `test_parallel_validation_client`, `test_failover_kiwoom_client`, `test_central_rest_broker`. 먼저 fake broker로 재현하고, 이후 해당 시점에 승인된 모의 계좌에서 제한된 주문 왕복을 검증한다.

**되돌리기:** 새 intent 생성 중단 → 현재 broker 미체결/포지션 대조 → 사전에 정한 취소·관리 정책 적용 → 안전한 실행 상태 확인. 프로세스 종료를 주문 취소로 간주하지 않는다. 실제 일지 기존 PK는 이 단계에서 바꾸지 않고 별도 execution 원장을 D8 링크로 연결한다.

## 22. O2 — 모의 전진 평가와 별도 실거래 활성화

**O2a 구현 상태 (2026-09-13): 코드·로컬 핵심 회귀 완료, 실제 전진 자료 대기.** 전략/Family/Factor/정책 버전, 주 데이터 경로, mock 계좌, 유한 평가 기간과 데이터/시스템/성과 기준을 content-addressed profile로 동결한다. 모든 기준은 명시값 또는 `TBD`이며 하나라도 `TBD`면 BLOCKED다. V1 대조 통계와 O1 execution event를 조립해 coverage·지연·공백, unknown·거절·취소 실패·재접속·잔고 불일치·NO_TRADE, broker 순손익·추가 누락 비용·MDD·노출·거래/활동일을 분리 보고한다. broker 순손익에 이미 포함된 비용은 다시 차감하지 않는다. 프로파일·보고서·stage revision은 기존 중앙 문서 저장소의 내부 컬렉션에 content ID로 보존하며 공개 API나 서버 시작 경로는 바꾸지 않았다. PASSED도 자동 주문/자동 stage 변경을 만들지 않고 `approved_for_live`는 O2a 코드에서 거부한다. `broker_mock_validated` 저장에는 같은 전략의 저장된 최종 PASSED 보고서가 필요하다. 집중 회귀 32개와 전체 핵심 회귀 794개(`429+365`)가 통과했다.

**목적/선행:** O1/V1 후 실제로 앞으로 들어오는 자료에서 동결한 후보를 관찰한다. 모의 연동 성공과 전략 성과를 별도로 판단한다.

**수정 대상:** 기존 research/execution 원장·보고서, **작은 운영 프로파일/상태 검사**. 연구 후보 상태는 draft/evaluated/validated/shadow/broker_mock_validated/approved_for_live/retired처럼 분리한다. 기존 전략팩의 approved는 복기 규칙 승인이지 실거래 승인으로 재사용하지 않는다.

**입력:** 전진 시작 전에 전략/Factor/정책 버전, 데이터 주경로, 계좌 환경, 기간·활동일·최소 표본, 데이터/시스템/성과 통과 기준을 고정한다. 숫자는 사용자 운영 의도와 실제 표본에 맞춰 이 단계에서 결정하고 `TBD`이면 승격을 허용하지 않는다. 진행 중 수정하면 새 run으로 시작한다.

**출력/완료 기준:** (1) 데이터: 비교 가능 coverage·지연·공백, (2) 시스템: NO_TRADE/거절/unknown·취소·재접속·잔고 정합성, (3) 성과: 비용 차감 손익·MDD·노출·거래/활동일·OOS/paper 차이를 분리 보고. 모의 서버가 반영한 비용을 또 빼지 않고 빠진 비용만 추가 시나리오로 계산한다. 모의 체결률/대기열/시장충격이 실거래와 같다고 주장하지 않는다.

**실거래는 O2b 별도 작업:** 사용자 또는 사전 승인된 운영 정책이 지정한 계좌·총자금·종목/테마 노출·일일 손실·운영시간·주문수·stale 한도·긴급 정지·장애 중 포지션/미체결 처리가 확정된 뒤 real adapter를 연결한다. 자동 연구 결과로 live 설정을 덮어쓰지 않는다. 새 live spec 적용시각·기존 포지션 처리·롤백 버전을 기록한다. 실전 송신은 기본 OFF이며 이 문서나 모의 수익으로 활성화되지 않는다.

**테스트:** 운영 config 경계값·승인 상태/계좌 불일치·킬스위치·데이터 단절·broker timeout·프로세스 crash→재기동·중복 실행·버전 변경 중 포지션 fixture. 신규 `test_execution_activation`, `test_forward_report`; O1 회귀. 실제 모의 장기 검증과 사용자 정한 승격 기준의 결과를 artifact로 남긴다. 실거래 활성화 전 concrete config·복구 시나리오·검증 보고가 모두 준비돼 있어야 한다.

**되돌리기:** 신규 위험 차단, 미체결 처리·기존 포지션 관리 정책 수행, 대조 완료 후 실행 spec 이전 버전으로 복원. 이미 체결된 시장 거래를 코드 rollback으로 되돌릴 수 있다고 표현하지 않는다.

## 23. 원문 요구·가설 추적표

N/S/A는 감사 문서의 원문/해시를 뜻한다. 아래는 구현 완료표가 아니라 **출처→기능→검증** 연결이다. 상태는 현재 모두 계획/자료대기다.

| ID / 출처 | 분류·원문 의도 | 담당 단계·코드 경계 | 검증 / 보류 조건 |
|---|---|---|---|
| N01 / N §1~3,25,34~35 | 데이터·UX: NAS 상시 수집, 종목 AI 유지 | N1/N3 `CentralNewsService` | 기존 watchlist 창 독립 회귀, source 범위·부하 실측. 시장 전체 미확인 |
| N02 / N §4~9,11~13 | 해석: role/scope/event/certainty/novelty | N2 `news_rules` | 한 사건+반례, UNKNOWN. 새 사건은 별도 추가 변경 |
| N03 / N §6~7,13~14 | 정책: 중복/후행 제외 | N2 grouping/AI 후보 | 계약+급등 누락 방지, 원본 보존, 수동 경로 유지 |
| N04 / N §10~12,15 | Factor/가설: 대상별 방향·직접성·규모 | N2, C1 | target별 근거, 당시 매출/관계 없으면 null. 간접 수혜는 검증 가설 |
| N05 / N §16~17,31 | Factor: 중요도·신뢰도·반응 분리 | N2+D7 | 별도 값·산식 버전, 반응만/뉴스 추가 ablation. 확률 표현 금지 |
| N06 / N §18~21,32 | 데이터·평가: 최초 인지·잠정 경보·지평 | N1/N2+D3/D7/H1 | 제목·본문·AI 가용 시각, 5분 미성숙 제외, 초단위는 H1 대기 |
| N07 / N §22~28 | 저장/정책: 본문 실패·AI 보조·충돌 | N1/N2 | 본문 fallback·job 재개, rule/AI 별도 결과·충돌 이유 |
| N08 / N §29~31,40 | 후보: 장전/장중 소비 | C1/N2/D4 | 사전 가설→실제 반응; NEWS_TRIGGER에서 주문 직결 없음 |
| N09 / N §33,36,41 | 구조: 버전/테이블/모듈 | D1/N1/N2 | 최소 이력부터, 원문 폴더 구조 일괄 생성하지 않음 |
| N10 / N §37~38,42 | 진행 제안 | N1→N2, N3 기록은 조기 가능 | 20~50 사건은 첫 수직 경로 완료 조건 아님 |
| S01 / S §1~4 | 연구 범위: 오전·시장/초대형주/U 분리 | D3/D7/F1 | universe/세션 버전, 09~10 vs 10~12 비교. 전체시장 자료 부족 표시 |
| S02 / S §5 | Factor/UX: 네 유형·난이도 | F1 | 네 유형+UNKNOWN, 전략별 tradability, 임계값은 실험 |
| S03 / S §6 | Factor: 집중·교체·지속·유동성 | D3/F1 | 같은 U·분모·관측 창; 건강한 확산과 실패순환 구분 |
| S04 / S §6.1,9~10 | 사건/평가: 돌파·MFE/MAE·성숙 | D3/D7 | 기준봉 고정, PENDING/CENSORED 분리, label 가용시각 제한 |
| S05 / S §7 | 가설: 장전/전일/장중 발견 | C1+D4 | 전일 상한가 무반응, 뉴스 없는 후보, 독립 근거 중복 방지 |
| S06 / S §8.1~8.2 | 데이터/Factor: 테마 대장·잠김 | D5/H1/H2 | 1초 가격·거래대금으로 대장 관계와 TOUCHED까지만 지원; 호가 잠김은 보류 |
| S07 / S §8.3 | 가격·거래대금 기반 진입 근거 붕괴 | H2/D7/D8 | 당시 대장 ID 고정, 즉시/축소/확인 정책 비교; 우위 없으면 채택 안 함 |
| S08 / S §9~10 | 계약: Raw/Feature/Decision/순서 | D1~D4 | 늦은 정정 비소급·동일 입력 재생; 중복 엔터티 계층 금지 |
| S09 / S §11 | 평가: 사건연구·전략모의·OOS | D7/R1 | 필터 없는 기준선·비용·표본/활동일·시간순 검증 |
| S10 / S §12~15 | UX·수용 시나리오 | D4/F1/C1/H2 | 아래 S 수용 시나리오 연결표 |
| A01 / A §3~6,13 | 공용 코어·시점·불변 manifest | D1~D4/D7 | 동일 입력/상태/버전의 판단 재현, 최종봉 모델과 observed 구분 |
| A02 / A §6.1~6.4 | NAS/직접·mock 전진 | V1/O1/O2 | 주 입력 하나, 데이터/시스템/성과 분리, 계좌 대조 |
| A03 / A §7~12 | 연구 모드·Hard/Soft·탐색·OOS | D7/R1/R2 | 유한 예산, 전체 trial, 누수·비용, holdout 접근 이력 |
| A04 / A §14~20 | 원문 편입·확장·승격·최소 변경 | 이 표/각 단계/O2 | 두 번째 Factor/Family 회귀, 과도한 추상화·자동 실거래 승격 없음 |

S §14의 10개 수용 시나리오: **1 지수만 강함→F1, 2 건강한 확산→F1, 3 실패 순환→F1/D7, 4 뉴스 없는 주도주→D4, 5 전일 상한가 무반응→C1, 6 이탈/재잠김→H2, 7 미성숙 돌파→D3/D7, 8 데이터 중단→D1/D4/H2, 9 대장 교체→H2, 10 동일 입력 재실행→D2/D3.**

## 24. 단계별 공통 검사·문서·배포 조건

### 개발 검사

이번 감사에서 테스트를 실행하지 않았다. 아래는 Sol 구현 후 실행할 방법이다. 신규 테스트 파일명은 계획이며 해당 단계에서 만든 뒤 실행한다. 모든 기존 `test_*`는 `tests.unit.test_*` 모듈명으로 지정한다.

```powershell
# 현재 구현 워크트리에서 실행. 실제 데이터 DB에 연결하는 테스트를 만들지 않는다.
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
$researchPython = 'C:\Users\pc-1\Documents\ChatGPT\kiwoom-realtime-monitor\.venv\Scripts\python.exe'
& $researchPython --version
& $researchPython -m unittest tests.unit.test_market_data_contract tests.unit.test_central_server_database tests.unit.test_central_schema_migrations tests.unit.test_autonomous_top20
if ($LASTEXITCODE -ne 0) { throw '관련 회귀 실패' }
# 변경한 단계의 신규 회귀 모듈을 추가 실행한 뒤 핵심 묶음 실행
& .\scripts\run_core_regression.ps1
if ($LASTEXITCODE -ne 0) { throw '핵심 회귀 실패' }
```

원본 `.venv`는 인터프리터일 뿐 현재 `src`가 코드 원본이다. 실행 제한을 미설치로 오판하지 말고 저장소 Python 탐색/승인 규칙을 따른다. 관련 테스트→핵심 회귀가 통과하면 이유 없이 전체 테스트를 반복하지 않는다. GUI 변경 단계는 별도 프로세스로 `test_main_window`, `test_stock_news_window`, `test_news_process` 등 실제 영향 GUI만 추가하며 `deleteLater`/DeferredDelete 처리 후 **종료 코드 0**까지 확인한다.

새 SQLite 표는 이전 DB fixture·멱등 migration·실패 rollback·신버전 거부 검사를 갖춘다. 중앙 변경은 SQLite만 통과했다고 PostgreSQL 검증 완료로 보고하지 않는다. 격리된 PostgreSQL에서 같은 CRUD/원자성 fixture를 검증하고, 배포 시 기존 `scripts/check_postgres_integration.py`의 검사 범위도 새 계약에 맞게 보완한다. 실제 사용자 행을 테스트용으로 삭제하지 않는다.

### 코드와 함께 갱신할 문서

| 변경 | 같은 변경에서 갱신 |
|---|---|
| API/필드·인증·오류 | `API_CONTRACT.md` 및 API 회귀 |
| 스키마/소유권·시각·revision/보존 | `DB_SCHEMA.md`, `HISTORICAL_DATA_CONTRACT.md` |
| 모듈 추가/이동 | `MODULE_MAP.md` |
| 실제 동작·사용자 영향 | `ARCHITECTURE_CURRENT.md`, `CHANGELOG.md` |
| 단계 완료/가설 상태 | 이 계획에 실제 구현 commit·검사 결과·다음 한 항목 기록 |
| 새 설정/자료 동기화 | settings/theme/news/journal 백업과 Google Drive·NAS 명시 경계별 포함/제외·삭제/복구 회귀 |

미래 계획을 현재 구조 문서에 완료형으로 쓰지 않는다. 기존 `REFACTORING_CLOSEOUT_PLAN.md`의 완료 단계나 보류 운영 검증을 신규 기능 완료로 변경하지 않는다.

### NAS 배포

현재 리팩터링은 종료됐으므로 과거 중간 배포 금지를 새 기능 전체의 영구 금지로 해석하지 않는다. 그렇다고 각 코드 패치마다 NAS에 쓰지도 않는다. D1~D3 등 관련 검증 묶음이 끝난 시점에 review 가능한 누적 배포물을 만든다. 실제 배포는 그 시점 사용자 범위/권한에 따른다.

서버 소스·의존성·Compose 변경 시 같은 변경에서 `central_server/app.py:SERVER_BUILD`, `deploy/synology/docker-compose.yml:server.image`, `deploy/synology/server.Dockerfile` 검증 문자열을 동일한 새 ID로 올린다. 같은 태그로 다른 서버 코드를 재배포하지 않는다. 검증된 `X:\kiwoom-monitor` 대상만 사용하고 `.env`, `postgres-data`, `server-data`를 보존·백업한다. 재빌드 후 `/health.server_build` 일치, 인증 API·WebSocket·PostgreSQL·새 기록/후보 상태를 확인한다. 새 schema가 있는 DB를 구 바이너리가 읽을 수 있는지 확인하지 않은 단순 이미지 rollback은 하지 않는다.

기존 장시간 NAS 연속성/다른 PC 사용은 별도 운영 gate다. 기록·후보·뉴스 확대 시 수집 공백, 큐 적체, 저장량, 순위 우선순위 영향도 함께 확인한다. 일반 기능 테스트만으로 장시간 운영이나 모의 주문 검증을 통과했다고 쓰지 않는다.

## 25. 구현 시점에 필요한 결정과 비차단 항목

| 결정 | 필요한 단계 | 결정 전 기본 처리 |
|---|---|---|
| 첫 N/lookback·buffer·관찰시간·비용/체결 가정 | D3/D7 | 명시 실험 설정만, 운영 추천값 없음. 합성 fixture로 계약 개발 가능 |
| 초대형주·ETF/ETN·신규상장 등 universe 정책 | F1 | 현재 KRX 관심순위 관측 범위 표시, 미확인 속성 제외/보류 사유 |
| 뉴스 source/범위·본문 보존 조건·예산 | N3 | 기존 watchlist 기록부터; 전체시장 커버리지 주장 금지 |
| 실제 NAS 자원·용량 상한 | H1/N3 운영 전 | 1초 자료는 기간 제한 없이 축적하고 일별 증가량 계측. 자동 정리는 보호 참조 정책과 용량 설정 이후 |
| 휴장·특별개장·기업행동 자료 | D3 운영/D7 역사 확대 | 지원 확인된 KRX 세션만 허용; 미지원 날짜/가격조정 표시 |
| 비교 경로 계좌/세션 공유 여부 | V1 활성화 전 | 기존 주 입력 유지, 합성 대조 테스트만 |
| mock TR 지원·계좌 설정·동일 제한 소유자 | O1 | fake broker로 상태기계 개발, 실제 연결은 준비 후 |
| 전진 기간·최소 표본·통과 지표 | O2 | 결과 수집만, 기준 미확정이면 승격 안 함 |
| real 계좌·자금/손실·노출·장애/긴급정지 정책 | O2b | 실전 송신 OFF |

이 항목 때문에 지금 D1 감사/기록 구현까지 사용자에게 다시 결정해 달라고 묻고 멈출 필요는 없다. 해당 단계에서 실제 선택이 결과를 바꿀 때만 필요한 결정을 요청한다.

## 26. Sol의 첫 작업 메시지

다음은 구현 착수 시 사용할 수 있는 인계문이다. **이번 작업에서 전송하거나 실행하지 않았다.**

> AGENTS.md, DEVELOPMENT_GUARDRAILS.md, MODULE_MAP.md, REFACTORING_CLOSEOUT_PLAN.md와 reports/RESEARCH_ENGINE_ARCHITECTURE_AUDIT.md, reports/RESEARCH_ENGINE_IMPLEMENTATION_PLAN.md를 읽어라. 현재 기준은 0e93780/v2.0.0이다. 첫 변경은 계획 H1의 NAS 1초 집계 중 저장 수직 경로만 구현한다. 기존 평일 08:00~20:00 TOP20 순위 저장, 0B 구독, 최신값·1분봉·일봉 의미를 유지하고 새 키움 연결이나 추가 구독을 만들지 않는다. 파싱된 기존 TradeTick을 종목·시장·거래초별 OHLC·거래량·거래대금·체결 건수로 집계해 중앙 SQLite/PostgreSQL에 저장하고, 동일초 복수 체결·재시도 이중 합산·KRX/NXT 경계 회귀를 추가한다. 공백 이력과 조회 API는 다음 리뷰 가능한 변경으로 분리한다. 앱 UI, 후보 전략, 뉴스, 주문, 자동 삭제, NAS 배포는 포함하지 않는다. 새 Manager/Service 계층을 늘리지 않는다. 수정 전 현재 작업 트리 상태를 확인하고 구현용 codex/ 브랜치에서 시작한다. 관련 검사와 핵심 회귀 결과, 실제 변경, 미검증 PostgreSQL/운영 범위, 다음 한 항목을 구분해 보고한다.

이후에도 한 번에 현재 단계의 작은 변경만 진행한다. 새로운 불일치가 발견되면 근거를 먼저 확인하고 현재 단계와 무관한 것은 감사의 보류 원장에 남긴다.

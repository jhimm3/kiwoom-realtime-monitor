# O2-M 설계 재검토 결정과 Sol 구현 계약

기준일: 2026-09-16, 구현 갱신 2026-09-21. **O2-M0과 Me1~Me3 구현 완료, V1 운영 검증 대기**.
이 문서는 `O2ME_AUTOMATION_RUNNER_SCOPE_REVIEW.md`의 미결 대안을 대체한다.
제품 코드·운영 DB·NAS·주문 설정은 변경하지 않았다. 현재 O2-M과 직접 연결되는 연구 패키지,
계좌 runtime, 일지 피드백을 검토했으며 CR0~A5 전체를 다시 시험한 것은 아니다.

## 1. 결론과 완료 판정 정정

PC에서 무거운 연구를 계속하고 NAS가 수집·가벼운 전략 판단·계좌 실행을 담당한다.
다만 **O2-Md 안전 경계 완료 판정은 보완 필요로 정정**한다. 기존 9개 테스트는 통과하지만
중지 경합과 체결 직후 잔고 지연 등의 사례가 빠져 있다. 패키지 게시/runner 연결 전에
**O2-M0 안전 보완**을 먼저 한다. 기존 주문 엔진과 연구·일지 구조는 재사용한다.

현재는 명세·입장·복구·Decision gate 함수와 테스트만 있고 실제 자동 runner 호출자는 없다.
이번 가짜 transport/메모리 SQLite 진단에서 잘못된 승인과 차단을 확인했다. 운영 NAS에서 실제
발생했다는 증거는 없으며 실제 주문도 보내지 않았다. 상세 결과는
[안전 감사](O2MD_SAFETY_AUDIT_20260916.md)에 기록했다.

## 2. 책임과 흐름

| 영역 | 소유자 | 책임 |
| --- | --- | --- |
| 자동 가설·다종목/날짜 시뮬레이션·파라미터 비교 | PC 연구 worker | CR 연구 DB, 자원 제한, 개발/최종평가 분리 |
| 동결 후보 게시 | PC → 인증 NAS API | 실행 설정·평가 근거 전달; 게시 자체에는 주문 권한 없음 |
| 시장자료·순위·계좌 원본 | NAS 기존 수집기/account bundle | 순위 최우선, 실전/모의 한도 분리, 중복 WS/TR 금지 |
| 모의 전략 판단 | NAS의 제한된 runner | 승인된 후보 하나, 계좌당 실행 하나, 입력 cursor/checkpoint |
| 주문·대조·취소 | 기존 O1 runtime/원장 | 단일 account lease, 전송 전 UNKNOWN 기록, 불명 주문 재전송 금지 |
| 운용 손익·한도 | NAS 최소 위험 projection | 원장/계좌/비용 근거 revision, 진입과 위험 축소 구분 |
| 상세 복기·자동 개선 제안 | PC 매매일지/A5 | 계좌별 원본·사용자 편집 보존, 새 전략 버전·재검증 |

`PC 연구 → 동결 후보 게시 → NAS 검증·입장 → broker 복구 → 전략 판단 → O1 →
계좌별 체결/일지 → A5 새 가설 → PC 재시뮬레이션`으로 연결한다.
PC를 끄면 새 연구는 멈추지만 이미 승인된 NAS 모의운영은 정책 내에서 계속할 수 있다.
NAS에 전체 연구 탐색·일지를 옮기지 않는다. 기존 shadow monitor에는 주문권한을 붙이지 않는다.

CR3의 자동 FINAL 평가 금지는 유지한다. 반복 최적화는 개발 자료로 하고, 노출된 FINAL을 반복
점수판으로 사용하지 않는다. 이번 설계는 실계좌 자동 승격이나 미확인 SOR/애프터 주문 지원을 추가하지 않는다.

## 3. 결정한 계약

### 3.1 긴급 중지와 복구

현재 `new_orders_enabled=False`는 정상 검사 대기와 사용자 중지를 구별하지 못한다.
계좌별 영속 제어에 `desired_state`, 단조 증가 `control_revision`, `active_spec_id`,
`execution_run_id`를 둔다. 새 recovery만으로 사용자 중지를 해제하지 않는다.

- `STOPPED`: 자동 ENTER/EXIT 모두 전송 금지. 기존 주문/보유를 임의 취소·청산하지 않는다.
- `RUNNING` + 진입 차단 사유: 신규 매수 금지. 아래 reduce-only 조건의 매도만 별도 검사한다.
- 재개: 명시 재개 명령 + 현재 활성 credential/binding + 전체 broker 대조 + 현재 정책 검사.
- 최초 입장/후보 교체: 기존 주문·예약·보유가 모두 정리돼야 한다.
- 같은 run 재시작: 자기 run 보유를 검증해 `MANAGE_ONLY`로 복구할 수 있어야 한다.

중지 접수는 느린 network submit의 잠금 해제를 기다려야만 기록되는 구조를 피한다.
짧은 제어 잠금/조건부 DB 갱신으로 중지 revision을 먼저 선형화하고 전송 claim이 이를 재검사한다.
중지보다 먼저 claim한 주문은 이미 진행 중일 수 있으므로 응답에 intent를 표시하고 대조를 계속한다.
**중지 이후 새 claim은 0건**이어야 한다. 전송 중 주문이 취소됐다고 표시하지 않는다.
중지 저장 실패 시 메모리도 닫고 성공 응답을 주지 않으며 재시작은 복구 전 닫힌 상태다.

control/binding/lease owner/account/risk revision을 gate와 묶고 기존 O1 제출 경계에서 한 번만
소비한다. boolean을 잠깐 True로 만드는 것만으로 승인됐다고 간주하지 않는다.
credential disable/drain과 retired runtime은 항상 우선한다.

### 3.2 체결 이력·진행 중 주문·잔고 반영 완료

`MockAccountRecovery.orders`에는 완료된 주문도 있다. terminal 이력 존재만으로 차단하지 않는다.
다만 terminal이어도 상세 체결·잔고 대조가 덜 됐으면 `RECONCILIATION_PENDING`으로 진입을 막는다.

계좌 조회 종료시각을 모든 필드의 동시 관측시각으로 취급하지 않는다. 조회 시작/종료,
잔고·미체결의 관측시각, event cursor와 대조 revision을 보존한다. 조회 중 새 체결/04가 들어오면
dirty 상태로 두고 후속 대조를 하나로 합친다. 시간 차가 5초 이내여도 체결 전 flat 잔고를
재사용하지 않는다. 체결량과 보유/현금이 일치해야 다음 진입을 허용한다.
재시작 시 원장과 broker를 대조하며 수동/다른 run 보유를 자동 전략 소유로 삼지 않는다.

### 3.3 매수와 위험 축소 매도

| 조건 | ENTER | EXIT |
| --- | --- | --- |
| 손익/비용 미완결, 일손실 한도 도달 | 차단 | 자기 run의 확인된 보유 감소만 별도 검사 |
| 연구 입력 지연·평가기간 종료 | 차단 | 유효한 청산 근거·현재 실행 가격이 있을 때만 허용 |
| 잔고 불일치, 주문 불명, scope/key/lease 오류 | 차단 | 대조 우선; 매도 가능 수량 추정 금지 |
| 사용자 긴급 중지 | 차단 | 차단; 자동 청산으로 의미 변경 금지 |
| 미지원 시장/시간, 오래된 가격 | 차단 | 차단; 취소·체결 대조는 계속 |

reduce-only는 동일 scope/run/symbol의 확인된 매도가능 수량에서 미체결 매도 예약을 뺀 범위다.
가격·주문 만료·시장시간·중지·lease 검사는 생략하지 않는다. 진입 신호의 데이터 공백과 매도
전송에 필요한 가격 신선도를 구별한다. 오래된 EXIT를 실행하거나 임의 강제청산 전략을 만들지 않는다.
일손실은 `pnl <= -limit`에서 차단하며 재접속으로 손실/중지 상태를 초기화하지 않는다.

### 3.4 당일 손익의 실제 근거

비공개 `mock_automation_risk_snapshot/v1`은 최소 다음을 가진다.

- scope/run, KST 거래일, observed_at, broker 조회 관측 구간
- account/reconciliation revision, execution event high-watermark, cost evidence revision
- 당일 FIFO 실현 순손익(미확인 null), 비용 완결 여부·누락 이유
- 계좌 보유/주문 대조 완료, 데이터 경로/관측시각, 누적 장애 횟수

gate는 NAS가 생성·저장한 revision을 읽는다. scalar와 출처 문자열만으로 근거를 인증하지 않는다.
거래일·계좌/run·체결 cursor·비용 완결성을 대조한다. 최초 flat 계좌에서 당일 체결이 없다는
전체 조회 근거가 있을 때만 손익 0을 사용할 수 있다.

첫 구현은 실제 확인된 비용만 사용한다. 비용이 늦으면 매수는 대기하고 위 조건의 매도는 가능하다.
비용을 임의 0으로 채우거나 시뮬레이션 비용을 확정값이라고 표시하지 않는다. 장중 실제 비용 확보
주기는 V1에서 확인한다. 이것이 다음 진입을 계속 막으면 보수적 비용 상한을 별도 동결 정책으로
도입할지 후속 판단하며 이번에 몰래 추정값으로 바꾸지 않는다.

현재 일손실의 의미는 당일 실현 순손익으로 유지한다. 전일 매수분의 오늘 매도 원가는 FIFO로
이어 받고 전체 episode 손익을 오늘 손익으로 복사하지 않는다. 평가손익 손절은 별도 정책이다.
날짜가 바뀌어도 보유/주문/사용자 중지는 보존하고 당일 손익·비용 근거를 새로 확보한다.

### 3.5 후보 패키지·합격·코드 호환

별도 불변 `mock_automation_candidate_package/v1`을 게시한다. 등록 family, 정규 parameters,
execution model, session profile, scientific implementation hash, source final batch/run/result와
평가 근거를 담는다. 원격 코드·pickle·PC 경로·비밀키는 넣지 않는다.

기존 `final_candidate_spec_hash()`의 `final_candidate/v1` JSON identity를 유지한다.
포장 문서 hash와 candidate hash는 별개다. provenance를 추가해 기존 ID를 다시 정의하지 않는다.
정규화 로직을 옮기면 golden fixture로 기존 hash를 보존한다.

전용 인증 게시 경계에서 크기·version·allowlist family·필드·hash·계보를 검사한다.
일반 콘텐츠 동기화로 실행 권한 문서를 쓰지 않는다. 게시 성공은 주문 시작이 아니다.
`COMPLETED`는 연구 완료이지 합격이 아니다. 사전에 동결한 합격 정책과 결과를 묶은 eligibility
receipt가 필요하며 수치가 미정이면 BLOCKED다. FINAL 결과를 본 뒤 합격선을 낮추지 않는다.
인증된 PC 결과 게시를 신뢰하는 경계와 NAS hash 검증을 구별한다. hash만으로 연구의 과학적
정확성이 증명되는 것은 아니다. NAS에서 전체 연구를 재실행하지 않는다.

첫 버전은 scientific implementation hash 일치를 요구한다. 단순 서버 build 문자열 변경과는 다르다.
현재 hash가 연구 저장소/실행 script까지 포함하는 점은 보존한다. 불일치 우회와 범용 family
호환 registry는 만들지 않는다. 필요해지면 명시 버전과 동등성 시험으로 별도 확장한다.

### 3.6 계좌 bundle의 단일 실행 owner

현재 `MockRuntimeBundle` monitor는 시작하면서 account lease를 얻는다. 별도 자동 runtime을
동시에 띄우지 않고 기존 계좌 registry 안에서 수동/자동 모드를 전환한다.

최초 전환/후보 교체: flat 확인 → 명령 접수 닫기 → gateway/monitor drain → 기존 runtime
종료·조건부 lease 해제 → 새 자동 run runtime/monitor → lease → broker 대조 순서다.
repository owner는 불변이므로 실행 객체의 run/token을 덮어쓰지 않는다. 기존 credential 교체의
prepare/drain/commit 구조를 재사용하되 mode/run 선택을 명시한다. 설정 revision 경쟁은 거절하고
닫힌 상태로 복구한다. 늦은 이전 binding callback을 새 run에 적용하지 않는다.

수동 run UUID와 자동 `mock_auto_run_<hash>`를 구별하는 version된 automation context를 둔다.
credential UUID 검증을 전역 완화하지 않는다. 자동 모드의 같은 계좌 수동 신규 주문은 conflict로
거절하되 계좌 조회·명시 취소/대조는 기존 소유권 경계에 남긴다. 다른 계좌/실전은 영향받지 않는다.

### 3.7 runner와 저장 조회

전용 runner 하나가 기존 등록 family 계산을 호출한다. ENTER 제안만으로 실제 보유 상태를
만들지 않고 O1 확인 체결량으로 포지션을 갱신한다. NAS 저장 입력을 사용하며 판단마다 TR을
요청하지 않는다. strict KRX 연구 입력에 SOR를 섞지 않고 현 O1 시장/시간 범위를 유지한다.

checkpoint는 package/spec/run, 입력 cursor, 전략 상태, 체결 cursor, pending intent를 담는다.
중복 입력/재시작은 같은 Decision/intent로 재인식하고 오래된 신호를 소급 전송하지 않는다.
원장·broker 대조가 checkpoint보다 우선한다. UNKNOWN 재전송 금지. QUEUED는 전송 전임을
원장으로 확인하고 재검사/만료 처리하여 영구 방치하지 않는다.

gate 이력 전체 반복 검색을 없애고 기존 repository/store에 identity 단건·현재 revision·active
intent 조회를 추가한다. 10,000건 제한 목록으로 최신 상태/멱등성을 판단하지 않는다. 감사 목록은
별도 페이지 조회로 유지한다. 범용 저장 프레임워크나 단순 전달 wrapper는 만들지 않는다.

## 4. 단계별 목적·수정·입출력·완료 기준

**현재 작업은 O2-M0 한 단계다.** 아래는 의존성에 따른 완료 단위이며 의미 없는 하위 단계로
다시 쪼개지 않는다. 수치 미정 정책은 사용자가 설정에서 동결할 때까지 차단 상태로 둔다.

### O2-M0 — 안전 gate 보완

**2026-09-21 완료:** 영속 control revision과 stop/resume, gate v2, ENTER/EXIT 분리,
terminal·dirty 대사 판정, 손실 포함 경계, server-now/평가기간 검사, identity/current/active
단건 조회를 구현했다. v1 이력은 읽기 보존하되 control revision 없는 승인은 재사용하지 않는다.
관련 O2/DB 회귀 118개와 기존 credential barrier/account owner 회귀 19개가 통과했다.
실제 risk snapshot producer와 runner가 없으므로 운영 자동 진입은 계속 닫혀 있고 NAS에 배포하지 않았다.

- 목적: 잘못된 승인과 위험 축소 차단 제거.
- 수정: `application/mock_automation_execution.py`, `mock_automation_recovery.py`,
  `central_server/execution_runtime.py`, `persistence/forward_evaluation_repository.py`,
  `execution_repository.py`, `central_server/database.py`와 필요한 schema migration.
- 입력: frozen spec/profile, 현재 lease/binding/control revision, server clock,
  대조된 계좌·진행 주문·체결 cursor. 출력: version된 gate/제어 revision, 한 번 소비되는 intent.
- 계약: 영속 stop, ENTER/EXIT 구분, terminal 주문 구분, 체결 후 dirty 차단, 손실 경계 포함,
  평가기간과 server-now 검사, 단건/active 조회. v1 기록은 보존하되 증거가 부족한 gate 재승인 금지.
- 완료/시험: 아래 공통 경합·경계 fixture와 기존 O1/credential barrier/scope 회귀 통과.
  아직 실제 위험 producer가 없으면 자동 진입은 닫힘. UI/runner/배포 연결하지 않음.

### O2-Me1 — 동결 후보 게시와 합격 근거

**2026-09-21 완료:** 기존 `final_candidate/v1` identity를 변경하지 않는 별도 후보 package와 사전 동결
수치 정책/결과 receipt, 현재 mock binding 및 scientific hash를 재검증하는 전용 인증 API를 구현했다.
세 문서는 일반 콘텐츠 allowlist 밖의 비공개 컬렉션에 내용 주소형으로 저장된다. 미정·늦은 정책과
미합격 OOS 근거는 BLOCKED이며, 게시 성공은 runtime/주문을 시작하지 않는다. NAS 배포는 V1까지 보류한다.

- 목적: PC 후보를 NAS가 정확히 복원하고 미합격 후보를 입장시키지 않음.
- 수정: `research_process.py`, `scripts/run_research.py` 기존 canonical 경계,
  `mock_automation_admission.py`, `forward_evaluation_repository.py`, `central_server/app.py`.
  독립 직렬화·검증 책임의 package 모듈 하나까지 허용.
- 입력: final run 설정/결과와 사전 동결 합격 정책. 출력: 불변 package, 검증/eligibility receipt.
- 완료/시험: golden hash, 설정/코드 변경, 미합격/누락 근거, 권한/계좌 충돌, 과대 payload,
  중복 게시/재시작. 같은 ID·내용은 멱등, 같은 ID·다른 내용은 거절. 게시로 transport 호출 0회.
- 전용 인증 API·비공개 컬렉션을 API/DB 계약에 기록하고 기존 경로/필드 호환 유지.

### O2-Me2 — 실제 위험 근거와 계좌 owner 연결

**2026-09-21 완료:** 자동 account bundle의 기존 broker 복구에서 O1 상세 체결과 `kt00015` 실제 비용을
계좌별 FIFO로 대조해 불변 risk/current revision을 만들고, 운영 recovery/Decision이 같은 저장 revision만
사용하는 경계를 추가했다. 수동/자동 모드는 기존 credential owner 안에서 flat 확인→명령 차단→drain→
lease 해제→새 불변 run bundle→broker 재대조 순서로 전환한다. 비용 누락·aggregate-only 체결·잔고 불일치는
unknown이고 자동 모드의 수동 신규 주문은 거절한다. 지속 runner/UI와 NAS 배포는 아직 연결하지 않았다.

- 목적: 임의 LiveMetrics를 NAS 원장 근거로 대체하고 수동/자동 lease 충돌 제거.
- 수정: `central_server/mock_runtime.py`, `mock_account_monitor.py`, `execution_runtime.py`,
  account reader·execution/forward repository. 순수 FIFO/비용 계산은 재사용.
  독립 위험 projection이 필요하면 하나만 추가; A5 전체 복제 금지.
- 입력: 기존 WS/복구 결과, 계좌별 O1 체결/실제 비용, credential revision.
  출력: risk snapshot, reconciliation revision, account bundle 자동 mode 상태.
- 완료/시험: 다계좌 분리, disable/교체 중 판단 폐기, 이전 run drain, 늦은/부분 체결,
  비용 누락, 전일 보유 FIFO, KST 날짜 변경, 중복 이벤트, 모의 TR 한도 독립. 미확인을 0으로 채우지 않음.

### O2-Me3 — 지속 runner와 운영 UI

**2026-09-21 완료:** 지속 runner core가 중앙 관측 cursor와 package/spec/run checkpoint를
보존하고 등록 Family를 그대로 호출한다. O1 상세 FILL만 실제 전략 포지션으로 반영하며 active intent
대사 중에는 다음 주문 판단을 보내지 않는다. NAS lifespan과 supervisor, 시작·중지·재개·상태 API,
저장 RUNNING control의 fail-closed 재시작 복원을 연결했다. ELIGIBLE 후보·forward profile·3단계
stage chain·실제 중앙 shadow event·현재 binding을 저장 전에 대조하는 READY 명세 게시/조회 API도
연결했다. 저장된 READY 명세의 계좌별 상태·시작·중지·재개 PC 화면과 후보 선택·평가기준/한도 동결·
명세 게시 UI를 연결했다. 게시→입장→위험 대사→가짜 매수·매도 체결→A5 계좌별 매매일지 투영은
실제 저장소와 runtime을 사용하는 통합 fixture로 검증했다. 다음은 V1 누적·장시간 검증이다.

- 목적: 승인된 한 후보가 사용자의 매번 실행 없이 정책 안에서 동작.
- 수정: NAS runner/시작·종료 조립, 기존 family 계산 호출, 기존 연구/forward 화면의 운영 영역.
- 입력: 게시·합격 후보, READY 명세, RUNNING revision, 저장 시장자료, 계좌 위험 revision.
  출력: Decision/gate/O1 event, checkpoint, 운영 상태·차단 사유.
- 완료/시험: 게시→입장→신호→가짜 체결→매도→A5 조회의 통합 흐름. 전송 전후/체결 전후 종료,
  보유 중 재시작, 평가 만료, stop/restart, 자료 단절, 중복 입력에서 중복 전송 0건.
- UI는 연구·shadow·모의운영의 켜짐을 구별. 기본 OFF이며 사용자 동결 정책 범위만 자동화.

### V1 — 통합·장시간 시험과 누적 배포

- 목적: 단위 시험 통과와 실제 연속 운전 성공 구분. 기존 V1 계획 재사용.
- 수정: 검증 script·진단만. 입력: 고정 build/설정. 출력: 환경·규모·시간·queue lag·RSS·CPU·
  DB 증가량·중복/누락/복구 근거 보고서.
- 완료: 30분 fake 반복/장애 주입 → 누적 소스 검증·동기화/빌드 → PostgreSQL 실제 트랜잭션 →
  승인된 모의 계좌 제한 시험 → PC 연구/NAS 수집 동시 24시간. 순위 지연은 연구 OFF 기준과 비교.
- 실전/mock queue 분리와 순위 최우선 회귀 필수. 실계좌 자동주문 금지. 사용자 빌드 절차 유지하며
  소스 동기화 확인 전에 재빌드를 요청하지 않는다.

## 5. 공통 필수 시험

1. 중지가 context 읽기 전후·전송 claim 전후 도착. 중지 이후 claim 0, 앞선 주문은 대조 유지.
2. DB 실패·재시작·새 recovery 후에도 STOPPED 유지.
3. terminal 이력만으로 차단하지 않음; 체결 미반영 잔고는 반드시 차단.
4. 손익 정확히 -limit, 비용 null, stale metrics/미래시각, 평가 시작 전/종료 후.
5. 비용 null/손실 도달 시 확인된 보유 EXIT만 허용; 다른 run/수동 보유/수량 초과 거절.
6. 동일 Decision의 다른 조회시각 재호출로 submit/가짜 dispatch receipt가 늘지 않음.
7. 10,001건 이상에서도 오래된 intent/최신 stop 조회 정확, 판단 비용이 감사 이력에 비례하지 않음.
8. SQLite/PostgreSQL start/stop/credential 경쟁, 조건부 갱신, 구DB migration/rollback.
9. 계좌 handoff, UNKNOWN/늦은 체결, disable 후 submit 재개 불가.

현재 추가 모델 에스컬레이션은 필요 없다. 구현 중 이 계약으로 해결할 수 없는 구조 충돌이
확인될 때만 해당 결정을 재검토하며, 이번 계획 자체를 완료된 구현으로 보고하지 않는다.

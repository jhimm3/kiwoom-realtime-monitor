# O2-Ma~Md 및 Me 연결 경계 감사

2026-09-16. 제품 수정 전 검토. 실제 NAS/키움/사용자 DB 접근과 주문 전송 없음.

## 방법과 실행 결과

지침·모듈 지도·종료 계획·연속 연구 계획과 admission → recovery → dispatch → runtime → O1 →
repository 호출 경로를 대조했다. 기존 `test_mock_automation_admission` **9개 통과**, 종료 코드 0.
`tmp/o2_design_review_probe.py`는 기존 fixture/메모리 SQLite/가짜 transport로 6개 진단을 실행했다.
종료 코드 0은 진단 완료이지 버그 수정이나 안전 시험 통과가 아니다.

| 사례 | 현재 결과 | 의미 |
| --- | --- | --- |
| context 읽기 → 중지 기록 → guard 진입 | APPROVED, fake submit 1회 | 예전 stop 문맥으로 중지 뒤 제출 가능 |
| 손익 -300,000원, 한도 300,000원 | APPROVED, fake submit 1회 | `<`라서 정확히 한도 도달값 미차단 |
| 보유 1주, EXIT 1주, 손익 null | BLOCKED: DAILY_NET_PNL_UNKNOWN | 손익 미완결이 위험 축소까지 차단 |
| recovery에 FILLED 이력 하나 | BLOCKED: BROKER_OPEN_ORDER_PRESENT | 완료 주문을 미체결로 취급 |
| 첫 intent FILLED 대조 후 체결 전 flat 계좌로 다음 Decision | APPROVED, fake submit 총 2회 | 체결 미반영 잔고 재사용 |
| 예전 시각끼리 일치하는 metrics/decision/account의 순수 gate | APPROVED | gate 자체에 server-now 입력 없음 |

중지 사례는 실행 guard 진입 직전에 stop을 넣은 결정적 interleaving이다. 실제 스레드 스케줄러의
우연에 의존하지 않는다. 운영 NAS에서 발생했다는 주장은 하지 않는다.
fixture의 평가 시작일은 판단시각 다음 날이므로 승인 사례는 평가기간 전 승인도 함께 보여준다.
마지막 사례는 순수 gate 범위다. O1은 intent 만료를 별도 검사하므로 만료 주문까지 실제 전송된다고
단정하지 않는다. 이번에는 실제 운영 장시간 부하·PostgreSQL 경쟁 쓰기를 시험하지 않았다.

## 코드로 확인한 원인

### 중지와 승인

`mock_automation_execution.py:dispatch_mock_automation_decision`은 `_load_context` 후 잠금을 얻는다.
`emergency_stop_mock_automation`은 boolean을 닫고 별도로 stop을 저장한다.
`ExecutionRuntime.submit_automation_intent`는 닫힌 boolean을 다시 열어 제출한다.
false가 사용자 중지인지 정상 gate 대기인지 구별하지 못한다. stop은 recovery 존재도 요구하고
느린 submit과 같은 잠금을 기다린다. 더 늦은 recovery로 stop을 해제하는 현재 계약도 수정 대상이다.

### 체결 반영과 미체결

`kiwoom_rest/mock_account.py:read`는 unfilled/completed를 합친다. Mc는 terminal을 제외하지만
Md의 `if recovery.orders`는 모두 open으로 취급한다. Md의 in-flight 검사는 terminal intent를
제외하며 계좌가 해당 체결을 반영했는지 보지 않는다. 경과 초와 대조 완결성은 별도 계약이다.

### 손익·매도·재시작

Md는 손익 출처 문자열만 비교하고 실제 원장/비용 근거와 값을 대조하지 않는다. producer도 없다.
Mc도 호출자가 넘긴 scalar를 받는다. 이전 문서의 “검증된/완결된 당일 손익”은 실제보다 강한 표현이다.
Mc는 모든 보유를 차단하므로 최초 입장과 같은 run 보유 중 재시작을 구별해야 한다.

### 후보 identity와 실행 owner

- Decision hash 검증은 내용 무결성이며 동결 후보로 계산했다는 증거가 아니다.
- admission의 final 검사는 completed/hash 일치이며 정책상 합격 여부 검사와 다르다.
- 실제 canonical candidate 설정은 PC final run에 있고 중앙 spec에는 hash만 있다.
- `CandidateMonitor`는 env 설정/monitor ID로 동작하므로 직접 주문에 연결하지 않는다.
- `MockRuntimeBundle`의 monitor가 account lease를 먼저 얻는다. 별도 자동 runtime은 충돌한다.
- `ExecutionRepository.bind_runtime_owner`는 불변이므로 run/token 덮어쓰기로 해결하지 않는다.
- credential 경로의 UUID run과 자동 `mock_auto_run_<hash>` 사이에 명시 context 계약이 필요하다.

### 지속 운전 조회·멱등성

`forward_evaluation_repository.py:_find`와 load 함수는 10,000건 제한 목록을 뒤진다. Md는 매번
승인 gate를 모두 읽고 각각 O1 intent를 조회한다. 이력에 비례한 비용과 페이지 밖 기록 누락 위험이
있다. 이번에 10,000건 부하를 실행한 것은 아니다. `_save_immutable`은 read→upsert→read이며
경쟁 쓰기의 조건부 insert가 아니다. 제어 revision/claim에는 실제 DB 원자 연산이 필요하다.

receipt ID에 새 observed_at이 포함되어 같은 Decision도 조회시각이 달라지면 새 receipt를 만들 수
있다. 기존 시험은 같은 시각만 재사용한다. QUEUED 저장 후 종료된 intent는 재호출이 기존 값만
반환하므로 전송 전 종료와 UNKNOWN을 구별한 복구/만료 처리가 필요하다.

## 유지할 기반과 다음 단계

O1의 전송 전 UNKNOWN 원장, account lease, scope 분리, credential drain, 결정적 ID,
기존 family와 A5 피드백을 재사용한다. 새 주문 엔진·전체 일지 NAS 이전·범용 framework는 불필요하다.

현재 상태는 **O2-Md 기본 구현 존재/안전 보완 미완료, Me 운영 연결 미구현, V1 미검증**이다.
[설계 결정과 구현 계약](O2M_DESIGN_REVIEW_DECISIONS_20260916.md)에 따라 O2-M0부터 진행한다.

> **과거 기록** · 원래 경로: `reports/O2MD_CONTINUOUS_DECISION_GATE.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# O2-Md — 지속 안전 gate와 결정적 O1 intent

기준일: 2026-09-16. 기본 구현·기존 회귀는 존재하나 **안전 보완 미완료**.

> 후속 [실행 감사](O2MD_SAFETY_AUDIT_20260916.md)에서 중지 경합, 체결 전 잔고 재사용,
> terminal 주문 오판정 등을 확인했다. 아래는 최초 구현 설명이며 안전 완료 근거가 아니다.
> 손익 출처 문자열 검사는 실제 FIFO/비용 evidence 검증이 아니다. 중지 직후 전송 차단도
> 모든 interleaving에서 보장되지 않는다. [설계 재검토 결정](O2M_DESIGN_REVIEW_DECISIONS_20260916.md)의
> O2-M0 보완을 운영 연결보다 먼저 수행한다.

## 목적

초기 broker recovery 통과를 계속 유효한 주문 허가로 취급하지 않는다. 각 action Decision 직전에
운용 한도와 현재 계좌 상태를 다시 검사하고, 통과한 같은 Decision은 O1 intent 한 개로만 전달한다.

## 판정과 제출

- 저장된 spec/admission/lease와 최신 recovery revision 계보를 다시 확인한다.
- 현재 `ka00001` mock binding을 다시 확인하고 account lease를 heartbeat한다.
- KRX 정규 연속장, Decision/account/order freshness와 명세의 NAS/direct 데이터 경로를 검사한다.
- 당일 순손익은 `account_scoped_fifo_broker_cost/v1` 출처만 허용한다. 비용·상세 체결이 덜 끝난 값,
  unknown 또는 추정 출처는 신규 주문을 열지 않는다.
- 자금·일손실·데이터 공백·submission unknown·재접속·잔고 불일치 한도와 기존 O1 비종결 intent를
  검사한다. ENTRY는 flat 계좌와 한도 내 자금, EXIT는 충분한 해당 종목 보유량을 요구한다.
- 승인 gate를 먼저 저장한 뒤 runtime 실행 잠금 안에서 결정적 KRX LIMIT intent를 한 번 제출한다.
  제출 구간 밖에서는 신규 주문이 닫혀 있다. 같은 Decision 재호출은 기존 O1 intent를 읽는다.

긴급 중지는 신규 주문을 즉시 닫고 불변 stop revision을 남긴다. 기존 주문과 포지션은 자동 취소·
청산하지 않는다. 중지보다 늦은 전체 broker recovery가 다시 통과해야 새 Decision을 받을 수 있다.

## 저장

비공개 중앙 문서 컬렉션은 decision gate, dispatch receipt, stop revision 세 개다. owner는 익명 mock
`account_ref`이고 key는 전체 문서 내용 hash다. raw 계좌번호·API key·token은 저장하지 않는다.

## 검증

- 같은 Decision 두 번 호출 시 gate/intent/receipt가 멱등이고 broker submit은 한 번이다.
- 첫 주문이 O1 비종결 상태인 동안 다른 Decision은 `EXECUTION_ORDER_IN_FLIGHT`로 차단한다.
- 미검증 손익 출처, stale Decision/account, 데이터 공백 초과, 긴급 중지 뒤 새 recovery 부재를
  함께 차단한다.
- 기존 수동 submit은 자동 제출 후에도 `MOCK_NEW_ORDERS_DISABLED`로 닫혀 있다.

## 남은 운영 연결

현재 NAS shadow `CandidateMonitor`는 환경변수 config와 monitor ID를 사용한다. CR3 final candidate
package hash에서 정확한 실행 payload를 복원하는 저장/배포 계약과, A5 완결 당일 손익 및 현재
broker/data 지표를 매 Decision에 공급하는 owner가 아직 없다. 이 둘을 정하지 않은 채 shadow
monitor에 주문권한을 추가하지 않는다.

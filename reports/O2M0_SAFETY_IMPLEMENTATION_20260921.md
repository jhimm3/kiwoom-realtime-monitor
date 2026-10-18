# O2-M0 자동 모의운용 안전 보완 구현

구현일: 2026-09-21. 로컬 누적 build: `2026.09.21-o2m0-safety-v1`.
이 단계는 안전 경계와 저장 계약만 구현했으며 NAS 동기화·이미지 빌드·runner/UI 연결은 하지 않았다.

## 구현 결과

- 계좌별 `mock_automation_control/v1`에 RUNNING/STOPPED, 단조 증가 revision, 활성 spec/run을
  저장한다. STOP은 DB 조건부 갱신으로 먼저 선형화하며 intent 생성 트랜잭션이 승인에 사용한
  control revision을 다시 확인한다. 저장 실패 때도 메모리 신규 주문 gate를 닫는다.
- 재개는 STOPPED 상태, 현재 binding 재검증, 중지 이후 새 broker recovery, 허용 recovery 상태를
  모두 요구한다. 새 recovery만 기록해서 사용자 중지가 자동 해제되지는 않는다.
- decision gate v2는 서버 시각, forward 평가기간, control revision, 대사 진행 상태를 기록한다.
  v1 문서는 읽을 수 있지만 control revision이 없어 새 intent 승인에는 사용할 수 없다.
- ENTER는 정확히 `pnl <= -limit`부터 차단한다. EXIT는 손익/비용·연구 데이터 공백 때문에 막히지
  않지만 같은 run/symbol의 확인 보유에서 미체결 매도 수량을 뺀 범위만 허용하며, 중지·lease·scope·
  잔고 불일치·UNKNOWN·오래된 현재 관측·시장시간 검사는 그대로 적용한다.
- broker terminal 주문은 open order로 세지 않는다. 체결 뒤 잔고 대사가 진행 중이면
  `RECONCILIATION_PENDING`으로 ENTER와 EXIT 모두 차단한다.
- spec/admission/lease/current recovery/current stop/approved gate/dispatch receipt는 중앙 문서
  기본키로 단건 조회한다. 진행 intent는 기존 scope index로 조회하며, 같은 Decision 재호출은 최초
  receipt를 반환해 관측시각 변경으로 감사 문서가 늘지 않는다.

## 검증

- O2-M0·execution repository·runtime barrier·order lifecycle·forward/recovery·중앙 DB 회귀:
  `118 tests`, 성공.
- 기존 credential barrier·mock execution·account bundle 회귀: `19 tests`, 성공.
- 변경 Python 모듈 `compileall`: 성공.
- 추가 환경 의존 suite는 현재 재사용 venv에 `cryptography`가 없어 실행하지 못했다. 이 실패는
  O2 변경의 assertion 실패가 아니며 이미지/V1 환경에서 다시 실행한다.

## 남은 경계

- O2-Me1: 동결 후보 package와 사전 합격 근거 게시.
- O2-Me2: 임의 `LiveMetrics`를 대체하는 계좌/run/체결 cursor/실제 비용 기반 risk snapshot과 기존
  account bundle의 단일 실행 owner 전환.
- O2-Me3: 기본 OFF인 지속 runner와 운영 UI.
- V1: PostgreSQL 실제 경쟁 쓰기, 장애 주입, 장시간 운전, 제한된 모의계좌 시험과 누적 배포.

실제 risk snapshot producer가 없으므로 O2-M0 함수만으로 운영 자동 진입을 시작하지 않는다.

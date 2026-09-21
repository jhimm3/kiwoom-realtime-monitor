> **과거 기록** · 원래 경로: `reports/O2ME2_RISK_OWNER_IMPLEMENTATION_20260921.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# O2-Me2 실제 위험 근거와 계좌 owner 구현 보고

## 구현 범위

- 자동 모드의 기존 `MockAccountBundle`이 broker 복구와 같은 모의 REST queue에서 `kt00015` 실제 비용을
  읽고, 계좌별 O1 상세 `FILL`과 FIFO로 대조한다.
- `mock_automation_risk_snapshot/v1`은 account/run, KST 거래일, broker 조회 구간,
  binding/account/reconciliation/event/cost revision, 당일 실현 순손익 또는 unknown 사유,
  run 보유·미체결 매도·장애 수를 불변 저장한다.
- 비용 누락, 비용 총액과 상세 체결 금액 불일치, aggregate-only 체결, FIFO 원가 누락, broker 잔고와
  O1 순포지션 불일치는 0으로 보정하지 않는다. 최초 flat·빈 원장이 완전히 확인된 경우만 손익 0이다.
- 전일 매수분을 오늘 매도하면 전일 실제 매수 비용을 FIFO 원가에 이어 받고 오늘 매도 실현분만
  당일 손익에 포함한다. KST 날짜가 바뀌면 보유 이력의 비용을 다시 조회한다.
- 운영 recovery와 Decision 제출용 wrapper는 중앙 current risk의 snapshot ID와 reconciliation revision,
  account/run/account-as-of가 모두 같은 경우만 기존 gate를 호출한다.

## 단일 owner 전환

- `MockCredentialOwner.switch_execution_mode`가 설정·credential revision과 계좌 scope를 확인한다.
- 전환은 broker flat 확인, 수동 명령 접수 차단, gateway/account query/WebSocket/monitor drain,
  기존 runtime lease 해제, 새 불변 자동 run bundle 시작, 초기 broker 재대조 순서다.
- `ExecutionRepository` owner는 새 bundle마다 새로 만들어지며 기존 run/token을 덮어쓰지 않는다.
- 자동 bundle은 runtime 신규 주문 gate가 닫힌 상태로 시작한다. 같은 계좌의 수동 신규 주문은
  `MOCK_ACCOUNT_AUTOMATIC_MODE`로 거절하지만 조회, 명시 취소, 체결 대사는 유지한다.
- 다른 계좌와 실전 runtime, 실전 초당 5회 queue에는 변화가 없다. 비용 조회는 모의 broker의
  독립 초당 1회 제한 안에서만 실행한다.

## 검증

- 비용·자동 admission/gate·계좌 monitor/bundle·실행 repository·중앙 DB/API/config까지 인접 회귀
  164개를 함께 실행해 모두 통과했다.
- 전일 FIFO, 비용 누락, aggregate-only 부분 체결, 중복 상세 체결, 계좌 scope 분리, KST 날짜 변경,
  current revision 역행 차단, manual→automatic drain과 수동 신규 주문 차단을 확인했다. 자동 bundle을
  닫고 같은 account/run으로 다시 시작했을 때도 저장된 마지막 reconciliation revision 다음 값으로
  이어지는 것을 별도 회귀로 확인했다.
- 전체 Python 문법 검사와 `git diff --check`를 통과했다.
- credential vault 회귀는 현재 호스트 Python 환경에 `cryptography`가 없어 실행하지 못했다. 해당 테스트의
  기존 owner 경로 대신 암호화 저장소를 사용하지 않는 bundle 전환 회귀로 이번 변경 경계를 검증했다.

## 배포 상태와 다음 단계

- 로컬 누적 build는 `2026.09.21-o2me2-risk-owner-v1`이다.
- 설계의 V1 전 중간 배포 금지에 따라 NAS 동기화·이미지 재빌드는 하지 않았다.
- 다음 단계는 O2-Me3 지속 runner와 운영 UI다. 현재 구현만으로 자동 모의주문은 시작되지 않는다.

## 모델 에스컬레이션

- 없음. 기존 account owner, O1 원장과 모의 REST queue 계약 안에서 구현했다.

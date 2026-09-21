> **과거 기록** · 원래 경로: `reports/O2MC_BROKER_RECOVERY_GATE.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# O2-Mc — broker 복구와 중지 gate

기준일: 2026-09-16. 로컬 구현·회귀 완료, NAS 누적 배포 전.

## 목적

자동 후보가 계좌 lease를 얻었다는 이유만으로 주문을 열지 않는다. 기존 mock account reader가
`ka10075/ka10076/kt00018/kt00001`을 모두 읽은 결과와 운용 한도를 대조하고 판정을 불변 저장한다.

## 판정 입력

- admission, lease receipt, operating spec 계보와 현재 runtime heartbeat
- broker account snapshot과 주문 snapshot fingerprint
- 복구 완료 여부와 검증된 당일 순손익
- 데이터 공백 초, submission unknown, 재접속, 잔고 불일치 횟수

현재 후보 교체 정책은 `wait_until_flat`이다. 미체결 주문, 보유 포지션, 매수 예약금 중 하나라도
있으면 차단한다. snapshot이 명세의 freshness보다 오래됐거나 미래 시각이면 차단한다. 당일 손익과
데이터 공백을 알 수 없을 때는 0으로 간주하지 않는다.

## 출력과 저장

결과는 `mock_automation_recovery/v1`이며 `BLOCKED` 또는 `CLEARED_ORDERS_DISABLED`다. 후자도
신규 주문을 열지 않는다. 비공개 `execution_mock_automation_recovery_decisions`에
owner=`mock account_ref`, key=`decision_id`로 append-only 저장한다. 원문 계좌번호나 자격증명은
포함하지 않는다.

## 검증

- O2-Ma~Mc 대상 테스트 7개 통과.
- mock 계좌 파서·복구·lease·O1 원장·전진평가·A5·CR3 연관 회귀 119개 통과.
- 정상 flat/fresh 복구, 주문 비활성 유지, 기존 주문·포지션·예약자금, 일일 손실·데이터/장애 한도,
  미확인 손익/공백, 불완전 복구, stale snapshot과 lease 상실 차단을 확인했다.

## 다음

O2-Md에서 최신 복구 판정 이후의 지속 안전 gate를 만들고 매 Decision 직전에 같은 한도를 다시
검사한다. 통과한 Decision만 결정적 intent로 O1에 한 번 전달하며 긴급 중지는 신규 주문을 즉시
닫는다.

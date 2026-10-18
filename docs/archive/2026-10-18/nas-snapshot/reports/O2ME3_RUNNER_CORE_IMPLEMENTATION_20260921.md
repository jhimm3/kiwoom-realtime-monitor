# O2-Me3 지속 runner core 구현 기록

## 이번 완료 범위

- 저장된 후보 package에서 등록된 Family와 정규 설정을 복원한다.
- NAS의 `top20_membership`과 strict KRX 완료 분봉을 accepted sequence cursor로 한 번씩 처리한다.
- 새 판단마다 Kiwoom TR을 호출하지 않는다. 실제 주문 후보가 생겼을 때만 기존 계좌 monitor 복구,
  최신 risk revision, recovery, Decision gate, O1 intent 순서로 연결한다.
- 전략의 보유 상태는 ENTER 신호나 주문 접수로 만들지 않는다. O1의 broker execution ID가 있는 상세
  FILL만 반영하며 부분 매수의 평균가, 부분 매도 잔량, 전량 매도 후 cooldown을 복원한다.
- package/spec/run, 입력 cursor, 체결 cursor, 전략 상태, 확인한 fill ID, pending intent와 운영 상태를
  account별 current checkpoint로 저장한다. 다른 spec의 checkpoint는 재사용하지 않는다.
- active intent가 있으면 새 판단을 전송하지 않고 계좌 대사를 기다린다. checkpoint 저장 전후 중단으로
  입력이 다시 보이더라도 기존 결정적 Decision/intent 경계에서 중복 주문을 막는다.
- 입력이 없거나 같은 차단 상태가 유지되는 매 poll마다 checkpoint를 다시 쓰지 않는다.

## 검증

- 상세 매수 FILL만 candidate를 open으로 바꾸는지 확인했다.
- 재시작 후 체결 cursor·open 상태·확인한 broker execution ID가 복원되는지 확인했다.
- 같은 broker execution ID의 후착 중복 행이 수량을 다시 늘리지 않는지 확인했다.
- 전량 매도 상세 FILL이 cooldown으로 전환되는지 확인했다.
- 다른 spec context를 runner가 거절하는지 확인했다.
- 동일 차단 상태의 유휴 poll이 중앙 checkpoint revision을 계속 증가시키지 않는지 확인했다.

## 아직 연결하지 않은 범위

- NAS lifespan에서 자동 runner 복원·시작·종료
- 게시 후보와 READY spec을 선택하는 인증 운영 API
- 명시 시작·중지·재개와 상태 조회 UI
- 게시→입장→가짜 체결→매도→A5 조회 전체 통합 fixture

따라서 O2-Me3 전체 완료나 자동 모의운용 시작으로 해석하지 않는다. NAS 동기화·재빌드도 하지 않았다.

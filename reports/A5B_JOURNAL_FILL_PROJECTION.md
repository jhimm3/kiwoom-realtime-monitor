# A5b 매매일지 상세 체결 projection

완료일: 2026-09-16

## 구현 범위

- 중앙 실행 page의 계좌 scope, 연속 sequence, cursor를 application 경계에서 검증한다.
- `FILL`은 broker execution ID가 있을 때만 상세 체결로 받는다.
- `BROKER_FILL_AGGREGATE`는 수량 근거와 상세 누락량 계산에만 사용한다.
- 매매일지 DB v9의 event projection 행과 계좌별 cursor를 한 트랜잭션으로 저장한다.
- bounded sync 함수가 저장 cursor부터 중앙 page를 이어 읽고 `has_more=false`까지 진행한다.

## 멱등 기준

- 중앙 원본 `source_event_id`
- origin 계좌 scope + KST 거래일 + broker 주문번호 + broker 체결번호

run ID와 decision ID는 계보로 보존하지만 중복 방지 키에는 쓰지 않는다. 같은 broker 체결이 다른
run에 다시 결합돼도 새 수량으로 세지 않는다. 같은 식별자의 수량·가격·종목 등이 다르면 cursor를
전진시키지 않고 전체 page를 롤백한다.

## 누적량과 상세량

누적 체결 event의 수량 합에서 상세 FILL 수량 합을 뺀 양수만 `unresolved_quantity`다. 상세 체결이
늦게 도착하면 이 값만 줄어들며 실제 체결 합에 누적량을 다시 더하지 않는다.

## 검증

- 같은 초의 서로 다른 체결번호 두 건 보존
- exact page 재조회 0건 추가
- aggregate 3주 뒤 상세 1주+2주 도착 시 상세 합 3주, 미확인 0주
- 같은 체결의 run 변경 중복 방지
- 충돌 page의 행/cursor 동시 롤백
- cursor 공백·다른 계좌 거절
- 다른 거래일·다른 계좌의 같은 broker 번호 분리
- v8 fixture의 v9 migration과 repository 재시작 복구
- 두 source page의 cursor 연속 처리와 재시작 후 저장 cursor 재개

직접 projection·migration 회귀 12개와 기존 매매일지 전체 회귀 124개가 통과했다.

## 다음 단계

기존 kt00007 `trade_fills`와 상세 projection의 일치·부분 겹침·연결 불가를 구분하는 읽기 projection을
추가한다. 정확한 연결이 없는 비용과 손익은 확정값으로 이중 계산하지 않는다.

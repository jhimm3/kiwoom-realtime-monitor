> **정리 전 스냅샷** · 원래 경로: `docs/KIWOOM_MOCK_EXECUTION_CONTRACT.md` · [현재 문서](../../../../KIWOOM_MOCK_EXECUTION_CONTRACT.md) · 원문 바이트는 아카이브 ZIP에 보존했다.

# 키움 모의 주문 기술 계약

확인일: 2026-09-13

## 공식 지원 범위

| 종류 | ID / 경로 | 이번 구현 |
| --- | --- | --- |
| 매수 | `kt10000` / `/api/dostk/ordr` | 단발 전송 body 고정 |
| 매도 | `kt10001` / `/api/dostk/ordr` | 단발 전송 body 고정 |
| 정정 | `kt10002` / `/api/dostk/ordr` | ID만 등록, O1 자동 흐름 미사용 |
| 취소 | `kt10003` / `/api/dostk/ordr` | 단발 전송 body 고정 |
| 주문가능금액 | `kt00001` / `/api/dostk/acnt` | mock 계좌 전용 조회·파서 |
| 미체결 | `ka10075` / `/api/dostk/acnt` | mock 계좌 전용 연속조회·단위체결 파서 |
| 체결 | `ka10076` / `/api/dostk/acnt` | mock 계좌 전용 연속조회·누적체결 파서 |
| 잔고 | `kt00018` / `/api/dostk/acnt` | mock 계좌 전용 연속조회·보유수량 파서 |
| 주문·체결 실시간 | `00` / WebSocket | 모의계좌 전용 세션에서 단위체결·누적수량 대조 |
| 잔고 실시간 | `04` / WebSocket | 모의계좌 전용 세션에서 전체 REST 복구 신호로 사용 |

공식 계좌 조회 예제: [미체결 ka10075](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/%EA%B5%AD%EB%82%B4%EC%A3%BC%EC%8B%9D/%EA%B3%84%EC%A2%8C/get_domestic_unfilled_orders.py), [체결 ka10076](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/%EA%B5%AD%EB%82%B4%EC%A3%BC%EC%8B%9D/%EA%B3%84%EC%A2%8C/get_domestic_filled_orders.py), [잔고 kt00018](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/%EA%B5%AD%EB%82%B4%EC%A3%BC%EC%8B%9D/%EA%B3%84%EC%A2%8C/get_domestic_account_evaluation_balance.py), [예수금 kt00001](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/%EA%B5%AD%EB%82%B4%EC%A3%BC%EC%8B%9D/%EA%B3%84%EC%A2%8C/get_domestic_deposit_detail.py)  
공식 실시간 00 예제: <https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/%EA%B5%AD%EB%82%B4%EC%A3%BC%EC%8B%9D/%EC%8B%A4%EC%8B%9C%EA%B0%84%EC%8B%9C%EC%84%B8/subscribe_domestic_order_fill_async.py>  
공식 실시간 00/04 필드 문서: <https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/kiwoom_docs/%EC%8B%A4%EC%8B%9C%EA%B0%84%EC%8B%9C%EC%84%B8.md>  
공식 모의투자 안내: <https://openapi.kiwoom.com/intro/mockInvestInfo?dummyVal=0>

모의투자 주문·계좌조회는 KRX만 지원한다. NXT/SOR 주문은 O1에서 거부한다. 실전과 모의 App Key/App Secret은 별도이며 접근 토큰 유효기간은 공식 이용안내 기준 24시간이다.

## 안전 경계

- `OrderIntent.environment`는 `mock`, `venue`는 `KRX`만 허용한다.
- 기존 조회 `request()`는 통신 오류를 재시도할 수 있지만 주문 transport는 `request_once()`만 사용한다.
- 주문 API는 중앙 일반 조회 broker, NAS→로컬 failover, 중앙·로컬 병행검증에 들어갈 수 없다.
- 수동 모의주문 API는 `MOCK_ORDER_TRANSPORT_ENABLED=1`일 때만 열리고 KRX 지정가 주문만 받는다. 후보·전략은 이 API를 자동 호출하지 않는다.
- 신규 주문은 공통 `krx-nxt-schedule/2026-09-14` 정책의 KRX 정규장 연속매매
  `09:00 <= KST < 15:20`에서만 허용한다. 현재 모의 장후종가, KRX 애프터,
  NXT, 정규장 동시호가 주문은 검증되지 않았으므로 `UNSUPPORTED`로 거절한다.
  거절 event에는 reason, venue, 주문유형, session, phase, schedule revision,
  `krx-regular/v1` profile을 남긴다.
- `request_id`는 run별 멱등키다. 같은 주문 재요청은 기존 원장을 반환하며 다른 주문에 같은 ID를 재사용하면 거부한다.
- 실전 조회와 모의투자 조회는 각각 초당 5회와 초당 1회의 별도 한도를 적용한다. App Key/App Secret, REST client의 잠금·최근 호출 시각, broker queue, WebSocket 세션을 두 환경 사이에서 공유하지 않는다.
- 응답 유실은 미접수로 판단하지 않고 `SUBMISSION_UNKNOWN`으로 보존한다.
- unknown 주문은 자동 재전송하지 않는다. `ka10075`, `ka10076`, `kt00018`, `kt00001`과 `00/04`에서 받은 broker 시각을 대조한 뒤 상태를 갱신한다.
- 같은 초의 여러 체결은 broker execution ID별로 한 번씩 누적한다.
- 오래된 broker snapshot은 더 새로운 체결 상태를 되돌릴 수 없다.
- 신규 주문시간 gate는 취소, 주문·체결·잔고 대조, 재연결 복구, 늦은 체결 수신에
  적용하지 않는다. 15:20 이후에도 정규장 미체결 잔량은 broker가 종료를 확인할
  때까지 유지한다. 시각만으로 주문을 종료하거나 예약자금을 해제하지 않으며,
  16:00에 애프터 주문으로 자동 이월·재주문하지 않는다.
- `ka10076`은 개별 체결번호가 없는 주문별 누적 체결량이다. 가짜 execution ID를 만들지 않고 `broker_reported_filled_quantity`로 저장한다. 00 또는 `ka10075`에서 실제 체결번호가 늦게 도착하면 `detailed_filled_quantity`와 ID를 보완하되 유효 체결수량은 두 합계의 큰 값으로 계산한다.
- `kt00001.ord_alow_amt`는 broker가 직접 준 순 주문가능금액이다. 알려진 지정가 매수 미체결 예약을 더해 `available_cash_won`을 복원하므로 사전검사의 `available-reserved` 값은 다시 `ord_alow_amt`와 같아진다. 실제 계좌번호 필드는 저장하지 않는다.
- 계좌 모니터는 `MOCK_ACCOUNT_MONITOR_ENABLED=1`, 별도 `KIWOOM_MOCK_APP_KEY`/`KIWOOM_MOCK_SECRET_KEY`, 익명 `MOCK_ACCOUNT_REF`, 단일 `MOCK_EXECUTION_RUN_ID`가 모두 있을 때만 시작한다. 주 시세 환경인 `KIWOOM_ENVIRONMENT`가 `real`이어도 독립 실행하며, 시작 시 REST 전체 복구를 실행하고 00 상세 체결은 broker 주문번호로 기존 intent를 찾아 대조한다.
- 04의 `951` 예수금은 `kt00001.ord_alow_amt`와 같은 주문가능금액으로 간주하지 않는다. 04는 잔고 변경을 알리는 비공개 신호로만 받아 네 REST 조회를 다시 실행하며, 계좌번호와 04 원문을 일반 WebSocket client에 전달하거나 저장하지 않는다.
- A5의 계좌별 읽기는 `accepted_sequence` 커서를 사용해 현재 run뿐 아니라 같은 익명 계좌의 이전
  run도 이어서 본다. 각 행의 `source_event_id`는 중앙 event ID이며 run ID는 계보로만 사용한다.
  계좌/binding 불일치는 서버와 PC 클라이언트 양쪽에서 거절한다. 이 읽기는 주문·계좌 TR을 만들지
  않으며 `BROKER_FILL_AGGREGATE`를 상세 체결로 승격하지 않는다.
- 매매일지 projection은 상세 `FILL`과 `BROKER_FILL_AGGREGATE`를 별도 불변 행으로 저장한다.
  상세 체결의 멱등 키는 계좌·KST 거래일·broker 주문번호·broker 체결번호이며 run ID를 바꿔 같은
  체결을 새 수량으로 만들 수 없다. aggregate는 상세 누락량만 나타내고 상세 수량/손익에 더하지 않는다.
  page 행과 cursor는 같은 SQLite 트랜잭션으로 확정한다.
- O2-M 자동 운용은 후보·최종 결과·forward profile·현재 검증 binding과 모든 자금/손실/장애
  한도를 `mock_automation_operating_spec/v1`으로 먼저 동결한다. 미정값은 `BLOCKED`다. 현재
  `READY` 지원 범위는 단일 전략·단일 포지션·`krx-regular/v1`이며, 명세 저장 자체는 기존
  `MOCK_ORDER_TRANSPORT_ENABLED`나 runtime을 켜지 않는다.
- O2-Mb 자동 후보 입장은 기존 `central_execution_runtime_leases`의 `mock:account_ref` owner를
  그대로 사용한다. 수동 O1 또는 다른 자동 run과 동시 소유할 수 없다. 자동 runtime은 lease를
  `new_orders_enabled=false`로 획득하며 broker 복구와 stop gate가 끝나기 전에는 이를 열지 않는다.
- O2-Mc는 `ka10075/ka10076/kt00018/kt00001` 전체 복구 뒤 open order, position, 예약 매수금,
  snapshot freshness를 판정한다. 당일 손실은 현재 account snapshot에서 추정하지 않고 검증된
  별도 값이 없으면 차단한다. recovery decision이 통과해도 신규 주문은 비활성 상태다.
- O2-Md의 자동 제출은 `ExecutionRuntime.automation_decision_guard()` 안에서만 수행한다. 각 action
  Decision마다 binding/lease/session/freshness와 `account_scoped_fifo_broker_cost/v1` 손익 출처,
  자금·손실·장애 한도 및 기존 비종결 O1 intent를 다시 검사한다. 승인 gate를 먼저 저장하고 한
  결정적 KRX LIMIT intent를 제출한 즉시 신규 주문을 다시 닫는다. 같은 Decision은 재전송하지 않는다.
- 긴급 중지는 신규 주문을 닫고 stop revision을 저장한다. 기존 주문·포지션은 자동 취소·청산하지
  않으며 더 늦은 전체 broker recovery가 통과해야 새 Decision을 받을 수 있다.

## 아직 운영값으로 확정하지 않은 사항

- 모의 주문 TR과 계좌조회 사이의 세부 한도 분리 여부와 주문 queue 만료 시간
- 계좌별 WebSocket 동시 세션 및 재접속 후 누락 복구 범위
- `00/04`, `ka10075/ka10076/kt00018/kt00001` 응답의 실제 모의계좌 표본과 주문번호 자리수
- 시장가 주문의 전송 직전 현금 예약 기준가격 정책

현재 모의 클라이언트는 모의 REST 전체에 1초 간격을 적용하며 실전 클라이언트의 0.2초 간격과 완전히 분리한다. account reader와 전용 00/04 WebSocket은 계좌 모니터 설정 뒤 서버 시작 경로에 연결한다. 수동 주문 transport는 별도 기본 OFF 설정으로 열며 전송·취소 전 계좌를 새로 복구한다. 파서·상태 대조·수동 API와 정규장/15:20/15:30/15:35/15:40/16:00/19:59 경계는 fake 응답으로 검증한다. 실제 모의계좌의 제한 주문·취소·종료 대조 왕복으로 마지막 계약을 확정한다.

모의투자 직접 수신값은 이후 NAS 경유 수신값과의 도착 지연, 누락·중복, 정규화 차이를 감사하는 독립 비교원으로 사용할 수 있다. 비교 기능을 만들기 전까지 모의 값을 실전/NAS 주 데이터에 합치거나 자동 보정값으로 사용하지 않는다.

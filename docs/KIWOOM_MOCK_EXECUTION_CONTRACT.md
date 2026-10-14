# 키움 모의 주문 기술 계약

현재 모의 live 수락 준비: 선택 NAS recorder-shared-10m-v1의 관련 source 5개는 개발과 다르고 계좌는 MANUAL/control·Runner 없음·계좌별 mock_order_enabled=false·native 원장 0건이었다. 연결/legacy env ON을 자동운용/실제 00/04/REST 수락으로 쓰지 않는다. ③ 비중 계산은 별도 supplied 연구 입력/주문 없음으로 시작하며 실제 운영 수락과 승인 대기는 분리한다. [74단계](AI_TRADING_SYSTEM_REDESIGN.md#74-실제-모의계좌-수락-준비-확인과-상위-동적-비중-계산-시작)

종목 stop-loss는 admitted 후보 stop_loss_bps와 ACK된 실제 상세 FILL 원가/수량·승인 KRX RAM quote에만 근거한다. quote 비교에는 DB/REST/추가 수집이 없고 최초 위반은 기존 보호 Runner를 깨운다. 첫 가격/ref/시점은 고정하며 현재 full account/00·04·REST 대사/UNKNOWN·owner/control·정규 정책 검사를 통과하기 전 새 보호 SELL을 보내지 않는다. 일반 cue는 review 전용, 미확정 취소/UNKNOWN의 중복 전송은 금지한다. 실시간 분봉/Shadow/LLM/EV 완료를 기다리지 않으며 원래 보호 루트의 미체결 경로를 사용한다. 실제 체결/청산 보장은 없고 만료는 운영자 주의다. 실제 모의 00/04/REST 수락은 아직 남는다. [73단계](AI_TRADING_SYSTEM_REDESIGN.md#73-승인된-collector-ram-가격의-즉시-종목-손절과-native-보호-exit)

2026-10-14 native 연구 결과의 인증 게시가 기존 active Runner RAM으로 연결된다. 원래 native 결과의 최대 available_at과 내용 hash를 고정하며 HTTP 재전송/계좌 ACK/재시작으로 갱신하지 않는다. 계좌·profile/spec/run/control·원주문/known child·동결 비용/horizon/표본·같은 native 정책/모델·완결 짝/가치·1MiB/200창/10,000 event budget을 검사한다. 새 패킷에는 현재 native RUNNING control 한 번을 확인하고 같은 패킷의 inert 중복은 새 조회/깨우기/슬롯 교체가 없다. 등록 전환과 RAM 교체는 기존 input lock 안에서 검사한다. 최초 현재 control READ/표지된 PC 출처는 실제 현재 Risk/REST 확인이나 native 주문/취소 transaction fence를 대체하지 않는다. 보호 목적은 출처/EV를 기다리지 않으며 새 부분체결/CANCELLED 자료가 없는 상태에서 ACCEPTED cohort 재사용·취소 확정 전 재주문·UNKNOWN 중복 제출을 허용하지 않는다. PC의 readonly/replay 검증 보고이며 서버의 시장 원본/호가/체결 확률/취소 지연 승인이 아니다. PC_READONLY_REPLAY_REPORTED·source_durability=not_verified·order_authorized=false를 유지한다. native v1은 ACCEPTED 분봉 자료만 지원하고 현재 PARTIALLY_FILLED/CANCELLED/UNKNOWN에서는 EV 미확인(native_unfilled_model_state_unsupported)이며 이전 cohort를 재표지하지 않는다. 실제 00/04/REST·PG/NAS/장중 수락·부분체결/CANCELLED 모집단/취소 지연 보정은 남는다. [72단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#72-pc-native-원장-결과의-인증-게시와-기존-runner-ram-접수).

2026-10-14 새 미체결 짝 입력은 실제 native Risk/owner/control 검증을 대체하지 않는다. 명시 공급 자료는 not_verified이며 selected supplied-mock 정책에만 사용한다. 비용 모델은 기존 동결 후보의 유효기간/출처, 실제 비용 완결은 현재 Risk다. 취소 전 ACCEPTED/PARTIALLY_FILLED 자료는 CANCELLED 후 재진입에 재사용하지 않고 새 상태/수량/가격의 짝 자료를 요구한다. 새 주문은 원래 EV/가격/판단 시각 뒤 실제 Risk ACK에서만 검토하며 보호 취소는 기존 REST 전 검증으로 바로 처리한다. 기존 등록 전략 손절/기한 신호는 EV보다 우선한 보호 cue이며 종목별 즉시 Risk producer의 완성은 아니다. UNKNOWN/취소 확정 전 재주문 금지 유지. [70단계](AI_TRADING_SYSTEM_REDESIGN.md#70-waitreprice-짝-입력동결-비용익절-판단의-공통-연결).

2026-10-14 기존 자동 취소의 once-only 기본은 유지하며 새 동결 명세가 한도/지연을 선택한 보호 매도만 마지막 CANCEL_REJECTED 이후 최신 실제 full broker/account/Risk·가격/슬리피지 확인으로 제한 재시도한다. CANCEL_ACCEPTED/CANCEL_RESPONSE_UNKNOWN/응답 유실은 재전송하지 않는다. 계보 전체 native CANCEL_STARTED 예산/원래 보호 기한·현재 transaction fence를 적용하고 기한/한도 도달은 ESCALATE다. BUY/익절의 기존 정책과 CANCELLED/상세 체결 확인 후 고정 child만 제출하는 계약을 유지한다. 기본 0/OFF, 실제 계좌/운용 미활성이다. [69단계](AI_TRADING_SYSTEM_REDESIGN.md#69-취소-거절불확실-이후-bounded-보호-대응).

2026-10-14 명시 unfilled_execution_policy가 선택된 RUNNING control에서 native 일손실 EXIT_REQUIRED 요청은 첫 보호 EXIT를 고정 ACK/기존 승인 경로로 제출한다. 신선한 full account/실제 상세 체결·run 보유 수량과 승인 KRX RAM 가격을 사용한다. UNKNOWN/미종결/미매핑·불일치, stale/미정렬 가격·계좌는 차단하며 원래 만료를 갱신하지 않는다. queue claim이 최신 Risk/요청·고정 payload/TTL/owner/control을 검사하고 원래 목적을 JSON proof로 보존한다. UNKNOWN/ACK 유실 복구는 새 전송 없이 같은 native root에 등록한다. 실제 청산은 FILL/신선한 잔고 0, 재시작 후 재검증이며 새 BUY를 자동 재개하지 않는다. caller cue/RECONCILE_REQUIRED는 검토, 종목별 즉시 손절 입력은 후속이다. [68단계](AI_TRADING_SYSTEM_REDESIGN.md#68-운영-risk-보호-요청의-첫-exit-생성과-native-claim-확인).

2026-10-14 기존 승인 ENTER/EXIT의 native 제출 뒤 같은 owned 작업에서 목적/원래 목표·가격·기한을 checkpoint에 등록한다. 등록 없는 원래 BUY는 승인 lineage로 한 번 발견하며, 목적을 복구할 수 없는 EXIT와 여러 활성 root는 추측하지 않고 차단한다. Collector 승인 KRX RAM 가격과 ACK된 full account/Risk/broker cut을 공급하며 R4 EV를 WAIT-vs-REPRICE로 전용하지 않는다. native Risk 미완료의 첫 flat 무체결 BUY는 확인된 무체결 BUY들만 있고 UNKNOWN/불일치/현재 상세 체결 누락이 없는 전체 관측에서 취소 후 ABANDON만 허용한다. Risk/신규 제출 승인 조건은 유지한다. 보호 EXIT는 bootstrap/분봉·EV/비용 없이 실제 취소/상세 체결 확정 뒤 잔여 수량만 같은 고정 gate에서 재주문한다. 일반 재주문의 짝 EV/비용·익절 추세, 거절 후 bounded 대응/신규 보호 EXIT 첫 생성과 실제 00/04/REST 수락은 남는다. [67단계](AI_TRADING_SYSTEM_REDESIGN.md#67-기존-collector계좌-입력의-자동-공급과-원래-주문-관리-등록). 아래 supplier 미연결 표현은 각 과거 단계 기록이다.

2026-10-14 running 입력 소비가 목적별 CANCEL을 기존 gateway에 자동 전달한다. refresh 뒤 runtime 잠금에서 원래 타점/가격/EV TTL과 현재 owner Risk/계좌·보유/전체 주문 및 실제 broker confirmation을 재검증한다. 판단이 바뀌면 claim 전에 멈추고 수동 원래 API/명시 재시도는 유지한다. 매수 기회 무효화/보호 pending은 취소 후 ABANDON, 익절 reprice 취소는 명시 허용 EV/현재 비용, 보호 EXIT는 EV/비용 미확인보다 위험 정책을 우선한다. CANCEL_PENDING/UNKNOWN/거절에서는 child를 제출하거나 같은 취소를 자동 재전송하지 않는다. 새 current cut의 실제 CANCELLED/상세 체결 확인 후 고정 child가 승인돼야 제출한다. 보호 거절은 review/attention이며 bounded 자동 재시도/우회·최초 공급자/자동 관리 등록·실제 00/04/REST 운용 수락은 남는다. [66단계](AI_TRADING_SYSTEM_REDESIGN.md#66-목적별-자동-취소와-취소-직전-native-계좌risk-재검증).

2026-10-14 running Runner가 최신 typed 입력을 같은 잠금/native dispatcher로 소비한다. 고정 child·원래 anchor/예산·현재 owner/control/Risk·취소 확정/상세 체결·UNKNOWN 중복 금지는 유지한다. pending 보호 요청은 BUY/일반 익절 소비를 차단하며 protective EXIT는 일반 EV/다음 봉을 기다리지 않는다. 단발 제출/ACK/receipt/checkpoint가 진행 중이면 close가 그 owned 작업을 합류한다. 입력 없는 보호 기한은 요청만 arming하고 체결을 보장하거나 청산 성공으로 처리하지 않는다. **production input supplier 및 자동 cancel caller는 아직 없으며**, 실제 00/04/REST 수락/활성화는 별도다. [65단계](AI_TRADING_SYSTEM_REDESIGN.md#65-running-runner의-사건기한-소비와-고정-주문의-동일-native-실행).

2026-10-14 내부 idle 명시 dispatcher에서 동결 `unfilled_execution_policy`와 별도 `mock_automation_replacement_gate/v3`로 고정 대체 주문을 승인·제출한다. 원래 root/parent/ordinal·고정 payload/expiry/예산, 실제 계좌 전체 미체결·현재 native Risk/대사·owner/control을 확인하고 실제 QUEUED transaction에서도 검사한다. 새 명세가 선택된 경우만 지원하며 기존 명세/hash/OFF, v1/v2 ENTER/EXIT와 원래 KRX LIMIT 지원은 유지한다. BUY/익절의 공급 짝 EV는 모의 정책의 명시 허용이 필요하고 보호 EXIT는 EV/비용 미확인을 기다리지 않는다. 최신 gate/Risk 참조를 intent JSON에 원자 저장해 QUEUED ACK 유실·UNKNOWN·재시작 후 새 제출 없이 복구한다. 실제 running 자동 사건/기한/현재 가격·타점/EV producer, 목적별 취소·거절 후 보호 대응과 실제 00/04/REST·부분체결/잔고·장중/운용 승인 수락은 후속이다. 계좌 활성화/실제 broker 전송·배포는 하지 않았다. [64단계](AI_TRADING_SYSTEM_REDESIGN.md#64-목적별-고정-대체-주문의-native-승인과-제출).

2026-10-14 사용자 순서 ②의 첫 실행 기반으로 내부 `cancel_automation_intent`를 기존 gateway/runtime/lifecycle에 추가했다. 현재 검토한 ExecutionRecord를 그대로 전달하고 계좌 갱신 뒤 내용이 변하면 다시 판단한다. RUNNING/spec/run/control revision·owner는 CANCEL_STARTED의 native transaction에 묶으며 ACK 후 control 바인딩을 해제해 이후 STOP에도 응답/체결 대사는 이어간다. 같은 주문의 기존 CANCEL_STARTED가 있으면 ACK 유실/거절/재시작에도 추가 취소를 전송하지 않는다. 수동 취소 API/명시 재시도·주문 시간 지원은 유지한다. 새 공개 API/계좌 활성화·production caller는 없고 purpose dispatcher/고정 child 승인/제출·취소 거절 후 bounded 보호 재시도·실제 00/04/REST 수락은 후속이다. [63단계](AI_TRADING_SYSTEM_REDESIGN.md#63-모의계좌-자동-취소의-native-실행-경계).

2026-10-14 사용자 순서 ①에서 기존 자동 계좌 monitor의 ACK된 Risk를 같은 Runner의 보호 요청으로 자동 연결했다. source/binding/account/run/시점과 기존 한도·누적 mismatch/실제 노출 변화 구분을 유지한다. 기존 00의 상세 체결 원장 반영과 04/REST 복구가 Risk를 생산하고, 이번 callback은 추가 broker 조회/전송을 만들지 않는다. EXIT_REQUIRED/RECONCILE_REQUIRED는 우선 검토이며 실제 취소/재주문/주문 제출은 ②에서 현재 owner/control/Risk·계좌 대사로 검증한다. STOP/UNKNOWN 중복 금지·취소 확정 전 재주문 금지·주문 지원 범위를 변경하지 않는다. [62단계](AI_TRADING_SYSTEM_REDESIGN.md#62-운영-risk의-자동-보호-요청-연결과-개발-순서-개정).

2026-10-14 목적별 고정 대체 child 검토와 기존 runner의 보호 요청 우선 처리를 OFF로 로컬 검증했다. 이 optional gate/요청은 native 실행 승인·실제 취소/재주문이 아니며 기존 owner/control/UNKNOWN·주문 지원 계약을 유지한다. 보호 요청은 같은 checkpoint로 복구하고 실제 최신 flat/계좌 전체 활성 원장·Risk/control·ACK 후 owner/신선도를 확인해야 명시 해제한다. 운영 Risk producer 자동 연결·실제 청산/체결/잔고·장중 지연 수락은 후속이다. [60단계](AI_TRADING_SYSTEM_REDESIGN.md#60-목적별-고정-대체-주문-검토와-보호-요청의-우선-처리).

공식 자료 확인일: 2026-09-13. 구현 상태 정리: 2026-09-22.

## R4 이후 미체결 관리의 후속 설계

매수·일반 익절 매도·손절/위험 청산의 정책 선택은 [설계 정본 7.6절](AI_TRADING_SYSTEM_REDESIGN.md#76-미체결-주문-관리-정책)에 명시했다. 기존 취소·CANCEL_PENDING·체결/잔고 대사와 UNKNOWN/owner/control 계약을 재사용한다. 취소 확정 전 재주문·UNKNOWN 중복 제출은 금지하고, 보호는 일반 EV 추천보다 위험 한도/기한을 우선해 PC LLM·다음 1분봉·Shadow를 기다리지 않도록 연결해야 한다.

자동 WAIT/CANCEL/REPRICE·REENTER/ABANDON 선택과 보호 우선 깨우기/지연 수락은 아직 미구현이다. 이번 설계는 기존 API·KRX 정규장 지정가/계좌 권한·긴급 STOP 의미를 변경하지 않는다. 직접 정정·시장가/다른 venue는 별도 native 지원/수락 전 미지원이며 체결 보장을 뜻하지 않는다.

이 문서는 기존 모의 주문·계좌·멱등·비용 계약을 유지한다. O1 수동 경로와 O2-M 자동 운용 구현, 실제로 확인된 배포 범위는 [현재 상태](CURRENT_STATUS.md), 제한 모의 왕복·장중·장시간 검증은 [남은 작업](OPEN_ITEMS.md)을 따른다. 코드상 허용 경로와 broker의 실제 지원 확인을 구분한다.

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
- 신규 수동 주문의 검증된 기본 범위는 공통 `krx-nxt-schedule/2026-09-14` 정책의 KRX 정규장
  연속매매 `09:00 <= KST < 15:20` 지정가다. 별도로 인증된 수동 KRX LIMIT는 `16:00~20:00`에
  `manual-mock-krx-after-limit-probe/v1` 경로로 broker에 전달할 수 있다. 이는 애프터 지원 확정이
  아니며 접수·거절 결과와 `policy_version`을 원장에 남기는 운영 확인 경로다.
  장후종가·NXT·정규장 동시호가 및 허용되지 않은 주문유형은 `UNSUPPORTED`로 거절한다.
  거절 event에는 기존 reason, venue, 주문유형, session, phase, schedule revision과 해당 profile 근거를 남긴다.
  O2-M 자동 운용의 READY 지원은 아래 `krx-regular/v1`이며 수동 애프터 probe로 확대되지 않는다.
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
- 명시적인 정규장 단타 시간 정책의 runner 시계 관리에서는 별도 판단·가격 근거를 저장한 뒤 기존 O1 gate로 청산하며, clock 중지 이후 broker가 확인한 미체결만 기존 gateway로 취소한다. 사용자 긴급 중지와 UNKNOWN 보존 계약은 유지한다. 상세 순서·가격 신선도·취소 중복 방지는 [독립 시계 계약](FORWARD_EVALUATION_CONTRACT.md#독립-시계-청산미체결-관리--2026-10-12)을 따른다.

## 아직 운영값으로 확정하지 않은 사항

- 모의 주문 TR과 계좌조회 사이의 세부 한도 분리 여부와 주문 queue 만료 시간
- 계좌별 WebSocket 동시 세션 및 재접속 후 누락 복구 범위
- `00/04`, `ka10075/ka10076/kt00018/kt00001` 응답의 실제 모의계좌 표본과 주문번호 자리수
- 시장가 주문의 전송 직전 현금 예약 기준가격 정책

현재 모의 클라이언트는 모의 REST 전체에 1초 간격을 적용하며 실전 클라이언트의 0.2초 간격과 완전히 분리한다. account reader와 전용 00/04 WebSocket은 계좌 모니터 설정 뒤 서버 시작 경로에 연결한다. 수동 주문 transport는 별도 기본 OFF 설정으로 열며 전송·취소 전 계좌를 새로 복구한다. 파서·상태 대조·수동 API와 정규장/15:20/15:30/15:35/15:40/16:00/19:59 경계는 fake 응답으로 검증한다. 실제 모의계좌의 제한 주문·취소·종료 대조 왕복으로 마지막 계약을 확정한다.

모의투자 직접 수신값은 이후 NAS 경유 수신값과의 도착 지연, 누락·중복, 정규화 차이를 감사하는 독립 비교원으로 사용할 수 있다. 비교 기능을 만들기 전까지 모의 값을 실전/NAS 주 데이터에 합치거나 자동 보정값으로 사용하지 않는다.

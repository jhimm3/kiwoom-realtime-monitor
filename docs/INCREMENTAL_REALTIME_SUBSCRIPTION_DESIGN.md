# 기존 구독을 유지하는 실시간 등록 설계

2026-10-12 · 로컬 구현·관련 회귀, 미커밋·미배포 · 제품 2.1.0

사용자 승인 범위는 PC 직접 연결과 NAS에서 순위 변경 때 기존 구독을 유지하고 편입·이탈만 반영하며, 등록 대기 중에도 체결을 처리하는 것이다. 순서만 바뀐 PC coordinator의 불필요한 UPDATE 제거 이후 아래 차등 송신·승인·녹화를 로컬에 구현하고 관련 회귀를 실행했다. 실제 NAS 적용과 같은 장중 조건의 체감·누락 검증은 남으며, 원본 체결 무손실을 뜻하지 않는다. 최신 검증과 제한은 문서 끝의 구현 결과를 따른다.

## 확인한 경계

- NAS `_send_subscription`은 전체 그룹을 refresh=0으로 다시 보내고 0.25초 간격을 같은 수신 루프에서 기다린다. 가짜 소켓 4그룹 재현은 752ms였다. 원본 체결 누락이나 PC 직접 연결의 지연 길이는 이 재현으로 증명하지 못한다.
- 현재 NAS ACK 처리는 `len(전체 그룹)`만큼 REG 응답을 세며, 갱신 완료 때도 `connection_opened`를 보낸다. PC `MainWindow`는 이 신호의 모든 종목에 `reset_cumulative_baselines`를 적용한다. 유지 종목 재등록과 이 기준점 초기화를 모두 없애야 한다.
- 현재 녹화 v1은 `subscription_end` 뒤 전체 그룹 수만큼 ACK가 온다는 계약이다. 실제 송신 수가 다른 차등 등록, 송신 완료 전 도착한 ACK, 등록이 없는 해제 전용 변경을 v1처럼 기록할 수 없다.
- 조건검색은 같은 소켓에서 CNSR* 명령을 사용한다. 이를 REG/REMOVE 승인으로 세지 않는다. LOGIN·자격증명·계좌 신원·DB/HTTP 경로의 소유자는 그대로 둔다.

근거: `data/diagnostics/realtime-30s-registration-reproduction.json`, `realtime-order-retention-before.log`, `realtime-order-retention-after.log`, `incremental-subscription-official-protocol.json` 및 아래 파일 지도.

## 선택한 방식

기존 collector/worker가 연결·희망 구독·승인의 유일한 소유자로 남는다. 공유할 코드는 키움 item/type 등록 상태를 계산하는 순수 계획·상태 전이 모듈 하나다. 새 Controller/Manager, 두 번째 WebSocket, 무제한 송신 큐는 만들지 않는다.

### 1. 희망 정책과 그룹 배치를 분리

NAS의 현행 TOP20 → 보유 → 일반 요청 우선순위, 0B 상세 venue 예산, 0w 독립 예산, KRX/NXT/SOR 선택을 보존한다. PC의 현행 대상 선택도 보존한다. 양쪽이 계산한 최종 `(type, item)` 집합을 공통 계획기에 넣는다. 우선순위 순서가 바뀌어도 실제 선택 집합이 같으면 패킷은 0개다. 예산 경계에서 실제 venue 선택이 달라지는 경우는 차등 변경 대상이다.

상태는 `(group, type, item)` 등록과 wildcard 행으로 표현한다. `item=[]`인 1h 행을 빈 등록으로 소실시키지 않는다. wildcard는 일반 item과 구별하는 내부 표식을 사용하고 송신에서는 기존 `[]`로 복원한다. 계좌 `item=[""]`와도 구분한다.

- 기존 item/type은 같은 그룹에 둔다. 전체 정렬·재청킹·그룹 압축으로 유지 item을 이동하지 않는다.
- 이탈한 item만 정확한 type과 함께 REMOVE한다. 빈 그룹도 그 그룹의 실제 남은 항목만 해제한다. 그룹 전체 교체를 사용하지 않는다.
- 해제 후 빈자리에 새 item을 안정된 순서로 넣는다. 각 type의 그룹은 기존 번호 순, 새 item은 정렬 순으로 채운다. 일반 그룹은 100 item 이내, type별 합계는 200 이내다.
- NAS의 1000/2000/4000 계열과 고정 3000 그룹을 보존한다. PC도 기존 그룹 1을 유지하며 같은 type에 대한 한도를 검증한다. 이미 승인된 비변경 행은 송신하지 않는다.
- 모든 REMOVE의 성공 ACK 뒤에 추가 REG를 보낸다. REG는 refresh=1이다. 이 순서로 200개가 꽉 찬 상태의 A→B 교체도 순간 초과하지 않는다. 최초 연결도 비어 있는 상태에 refresh=1로 추가한다.
- 정적 시장·계좌 행은 최초 연결 또는 실제 정책 변경 때만 반영한다. 일상적인 TOP20 갱신마다 재등록하지 않는다.

### 2. 한 개의 미승인 명령, 수신은 계속

연결 generation마다 `desired_revision`, 고정된 `batch`, ACK로 확정한 `wire_state`, 외부에 허용한 `approved_state`를 구분한다. batch는 시작 시 희망 상태와 명령 목록을 고정한다. 진행 중 희망 변경은 최신 값 하나로 합치고 현재 batch 완료 뒤 그 승인 상태에서 다시 계산한다. 오래된 희망 변경을 큐에 누적하지 않는다.

REG/REMOVE는 소켓 전체에서 **동시에 한 명령만 승인 대기**한다. ACK 없는 다음 명령 송신, 같은 연결 안의 재송신, 실패한 변경을 성공으로 간주하는 동작은 금지한다.

기존 `_receive`가 상태를 소유한다. 지속되는 recv task 하나와 최대 한 send task, 다음 송신 시각/ACK deadline을 `asyncio.wait`로 함께 감시한다. 0.25초 pacing을 기다릴 때 수신을 await하는 것은 허용하지만, 전체 등록 배치나 pacing sleep이 recv 처리를 막아서는 안 된다. 송신 I/O task는 상태를 변경하지 않고 결과만 반환한다. NAS task는 기존 owned-task 경계 안에 넣는다.

1. 현재 명령과 generation을 설치하고 `command_start`를 기록한 뒤 send task를 시작한다.
2. REAL/PING/조건검색 응답은 기존 수신 경로에서 계속 처리한다. recv 호출자는 한 개뿐이다.
3. send 완료와 ACK가 모두 성공해야 해당 명령을 wire_state에 한 번 적용한다. ACK가 send 완료 관찰보다 먼저 오면 수신 즉시 기록하되 commit은 두 조건 이후다.
4. 직전 send 완료 뒤 최소 0.25초와 직전 ACK 성공을 모두 만족해야 다음 REG/REMOVE를 시작한다. PING 응답을 이 pacing 큐에 넣지 않는다. PING/CNSR 송신은 같은 연결의 짧은 송신 직렬화 경계를 공유하고 pacing 대기 동안 잠금을 잡지 않는다.
5. 송신 자체와 ACK 대기를 15초 이내로 제한한다. REAL이 계속 들어와도 monotonic deadline을 매 반복에서 검사한다. `recv` timeout이 발생할 때만 승인 timeout을 확인하지 않는다.
6. send 실패, 실패 ACK, timeout, generation 변경은 batch를 중단한다. 남은 명령을 보내지 않고 기존 연결 실패/재연결 경로로 처리한다. 중간 wire_state를 새 연결에 승계하지 않는다.
7. 종료는 신규 명령 금지 → recv/send task 취소 및 join → 소켓 종료 → 기존 producer/writer drain 순서다. 예전 task/ACK가 새 generation을 승인할 수 없다.

한 batch의 전송 중에 새 revision이 도착해도 그 명령 목록을 몰래 바꾸지 않는다. 중간에 폐기된 revision을 READY로 발표하지 않는다. 필요한 정리/재계산은 다음 batch가 담당하며, 현재 희망 상태에 없는 새 종목은 UI/집계 허용 대상에 넣지 않는다.

### 3. ACK 판정과 실제 프로토콜의 한계

[키움 공식 패킷 구현](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/kiwoom/realtime/packets.py)은 refresh=1 유지와 item/type REMOVE를 제공한다. [공식 0B 명세를 포함한 JSON](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/kiwoom/_data/kiwoom_api_spec.json)은 등록·해제 응답의 trnm이 요청값이며 return_code=0이 정상이라고 설명한다. REG 응답 예제에는 grp_no가 없다. 따라서 그룹 번호나 서버 request ID가 반드시 온다고 가정하지 않는다.

- 명령의 trnm과 응답의 trnm이 같고 `return_code`가 정수 0 또는 문자열 "0"일 때만 성공이다. bool, 누락, 다른 타입, 실패 코드는 승인하지 않는다.
- grp_no가 있으면 현재 명령과 일치해야 한다. 없으면 유일한 미승인 명령에 연결한다. 로컬 command_id는 녹화용이며 서버가 되돌려준 값처럼 기록하지 않는다.
- 명령이 없는 ACK, 완료 명령에 대해 식별 가능한 중복 ACK, trnm/group 불일치는 승인하지 않는다. 프로토콜 상태 불명으로 현재 연결을 실패 처리한다. 승인 대기 중 다음 동일 명령을 재전송하지 않아 늦은 ACK의 재시도 오인 경로를 제거한다.
- **서버가 식별자 없이 동일 trnm의 응답을 임의로 중복하고, 그 중복이 다음 같은 trnm 명령의 대기 중에 오면 구별할 수 없다.** 어떤 코드도 이 프로토콜에서 완전한 중복 판별을 보증하지 못한다. 정상 요청당 한 응답이라는 계약을 사용하고, 이 한계를 검증 보고에 남긴다. 인위적인 타임아웃·대기 간격으로 해결됐다고 주장하지 않는다.

### 4. 수신 허용·누적 기준·READY

wire_state는 서버 등록 결과이고, approved_state는 소비 가능한 등록이다. 양자를 같은 변수로 쓰지 않는다.

- batch 시작 때 변경 전/후에 공통인 item/type은 계속 허용한다. 제거되거나 venue가 대체되는 source는 해당 batch 동안 제외한다. 새 item/source는 batch의 모든 명령이 성공하기 전까지 허용하지 않는다. 등록 전 도착한 체결을 나중에 소급해서 완전 자료로 승격하지 않는다.
- 빈 approved_state는 '아무 source나 허용'이 아니다. 실제 연결/리플레이 입력에서는 승인 필터가 활성화됐다는 명시적 상태를 사용한다. 현재 `if _approved_trade_sources`의 빈 집합 우회는 이 경계에서 제거한다. 독립 parser 테스트가 필요하면 명시적 fixture로 구성한다.
- NAS `_apply_trade_source_approval`은 batch 시작의 제거 대상과 완료 때의 추가 대상에만 적용한다. 유지 source의 cumulative 기준과 continuous_from을 보존한다. 최초 연결/실제 재연결만 전체 reset이다. 세션 전환 또는 SOR↔KRX/NXT 전환은 영향을 받는 source의 연속성이 끊긴 것으로 처리하고, 다른 종목의 공백으로 확대하지 않는다.
- 첫 승인/실제 재연결에서만 `connection_opened`와 계좌 connected를 보낸다. 일반 구독 갱신은 `codes_added`와 `subscription_ready`의 기존 의미를 유지한다. 제거 전용 완료도 정상 완료지만 가짜 REG ACK를 만들지 않는다. effective no-op은 승인 이벤트를 재발행하지 않는다.
- 부분 변경 중 hub의 준비된 종목은 기존 승인과 현재 희망의 교집합이며, 요청한 0B source가 모두 허용된 종목만 포함한다. 새 종목이나 venue 전환 중 종목을 READY로 보이지 않는다. 유지 종목 때문에 global ready=false를 반복하지 않는다. v2 녹화에는 이 부분 승인 상태의 원인도 기록한다.
- PC의 분봉 누적 키는 `(code, market)`이다. 직접 worker와 NAS worker에 `sources_activated` 신호를 추가하고 기존 controller를 통해 `MinuteTradeValueAggregator.reset_cumulative_sources`로 연결한다. 새로 활성화된 source만 지운다. 기존 `connection_opened`의 전체 코드 reset은 재연결 의미로 보존한다. `codes_added`의 신규 코드 reset은 호환을 위해 유지한다.
- NAS는 기존 `codes_added` 이벤트에 선택적 `source_resets=[{code, market}]`를 붙인다. venue만 바뀌면 codes는 빈 목록일 수 있다. 새 PC는 codes 유무와 별개로 source_resets를 처리하고, 구형 PC는 알지 못하는 필드를 무시한다. 단, 구형 PC의 venue 재활성화 기준점 보존은 보증하지 않으므로 전체 기능의 운영 확인은 새 PC/NAS 조합으로 한다. 새 코드/기준점 신호가 그 source의 첫 허용 trade보다 먼저 같은 신호·큐 경로로 도착해야 한다.
- NAS 녹화의 control-event allowlist에 이 source_resets만 명시적으로 추가한다. 계좌정보나 서버 오류 원문은 기록하지 않는다. 기존 REST/DB 스키마는 바꾸지 않는다.

### 5. 새 녹화는 v2, v1은 그대로 읽고 재생

v1에 차등 요청을 억지로 넣거나, 새 ACK 수를 전체 그룹 수로 보정하지 않는다. 새 연결 정책은 `top20-realtime-tape/v2`로 기록한다. 한 trace epoch 안의 혼합 버전은 거절한다.

v2의 최소 기록:

| 기록 | 필수 내용 |
|---|---|
| batch_start | generation/revision, hub intent와 기여자, 이전 승인 상태, 목표 상태, 순서 있는 명령 목록 |
| command_start | batch와 로컬 command_id, ordinal, trnm/group, 공개 item/type delta, 송신 시작 시각 |
| command_sent | send 반환 또는 실패, 같은 command_id, 관측 시각 |
| command_ack | 실제 수신 시각, 같은 command_id, 실제 trnm/선택적 group, 결과 코드와 허용 여부 |
| batch_end | completed/failed/superseded, 실제 확정 wire_state 및 허용 상태, 실패/취소 이유의 분류 |
| approval projection | 이전 승인 + 제거 대상 또는 완료 batch를 참조한 현재 허용 source/ready 코드 |

`command_ack`는 `command_sent` 전에 기록될 수 있지만 command_start 전에는 올 수 없다. 순차 ACK, 명령 목록과 송신의 일치, send/ACK 양쪽 성공, generation, 한도, 최종 상태 재계산을 reader가 검증한다. 실패 batch는 실패 경로 재생 근거일 뿐 READY 승인 근거가 아니다. 잘린 명령/누락 ACK/불완전 recorder receipt는 성공 재생에 사용할 수 없다. observer 실패는 운영 구독 동작을 바꾸지 않지만 해당 녹화의 적격성을 실패시킨다.

계좌 00/04의 item과 데이터는 공개 tape에 넣지 않는다. 같은 패킷에 공개 행과 계좌 행이 섞여도 명령 개수와 승인 순서는 보존하고, 계좌 부분은 허용된 제외 분류로만 표시한다. 재생은 계좌 구독을 실제 송신하거나 계좌 이벤트를 생성하지 않는다.

- v2 offline transport는 live와 같은 계획기·ACK 상태 전이를 사용한다. 실제 전송 명령을 tape와 대조하고 기록된 시간에 ACK를 입력한다. REAL 재생 task는 그동안 계속 실행하며 허용 상태로 필터링한다. 과거 READY/큐 내용을 강제로 넣지 않는다.
- v1 reader와 엄격한 기존 승인 조건은 보존한다. v1 offline 경로에는 기존 그룹 생성/전체 교체/ACK 의미를 명시적인 legacy 정책으로 남긴다. 이를 live의 fallback으로 쓰지 않는다. 현재 `_send_subscription`에 새 delta를 넣은 뒤 v1 fixture 기대값만 새 방식으로 고치는 것은 금지다.
- v1 재생은 당시 정책의 재현이며, 새 v2 정책의 성능·연속성 확인으로 계산하지 않는다. v1 자료를 v2 승인 자료로 변환하거나 변경된 패킷의 ACK로 재사용하지 않는다.
- 기존 SubscriptionTape 외부 진입점과 trace/sealed reader는 버전 분기만 추가한다. v2의 원인 연결은 `batch_end/approval projection → upstream_ready → hub_event → delivery`로 검증한다. 중간 제거 상태는 이전 승인과 현 batch_start를 함께 참조한다.

## 구현 파일 지도와 순서

| 경계 | 변경 대상 |
|---|---|
| 공통 순수 등록 상태/계획 | 신규 `infrastructure/kiwoom_rest/realtime_registration.py`: item/type/group 상태, 안정 배치, 명령 계획, 직렬 ACK 전이. 소켓·Qt·DB·hub 참조 없음 |
| PC 소켓 수명·신호 | `infrastructure/kiwoom_rest/realtime_worker.py`: receive/send 감시, desired snapshot, source gating, 승인 신호 |
| NAS 수명·정책·집계 | `central_server/realtime_collector.py`: 기존 target 선택 보존, 공통 계획 적용, owner task 정리, 부분 허용·최종 승인 |
| PC/NAS 전달·누적 기준 | `infrastructure/kiwoom_rest/central_realtime_worker.py`, `presentation/realtime_worker_controller.py`, `presentation/main_window.py`, `application/minute_trade_value.py`: source_resets 수신·source 단위 초기화. failover worker의 명시적 신호 전달 목록도 포함 |
| 인과 녹화/엄격 reader | `central_server/diagnostic_top20_lifecycle_input.py`: v1 보존과 v2 기록/검증; 분량상 분리하면 v2 codec 한 모듈로 한정 |
| offline transport/입력 scheduler | `central_server/diagnostic_top20_transport.py`, `diagnostic_top20_execution.py`: legacy v1와 native v2 선택, ACK 도중 REAL과 허용 상태 갱신 |
| 계약 | 구현 시 `DEVELOPMENT_GUARDRAILS.md` 8-5-2의 refresh=0 정책, `API_CONTRACT.md`의 선택적 source_resets, `MODULE_MAP.md`, OPEN_ITEMS의 실제 상태 갱신 |

기존 직접 경로는 coordinator→worker→socket, NAS는 hub→collector→socket이다. 계획기와 상태 전이는 두 owner가 직접 호출하는 한 단계다. 같은 요청을 넘기기만 하는 새 service 계층을 추가하지 않는다. source 신호는 이미 존재하는 worker→controller→MainWindow 경로를 이용한다.

구현 순서는 (1) 순수 계획·ACK 재현 테스트, (2) 두 owner의 송수신/승인 및 source 전달, (3) v2 녹화·재생과 v1 호환, (4) 관련 회귀·계약 반영이다. (2)만 구현된 상태를 운영 배포하지 않는다. 변경 파일의 동시 작업을 먼저 재확인하며 unrelated dirty diff를 덮지 않는다.

## 완료 검증

1. 순위 순서만 변경: PC/NAS 패킷 0개, 승인/기준점 reset 0회. 우선순위가 실제 상세 venue 선정을 바꾸는 경계는 별도 기대값으로 검증한다.
2. 99/100/101/200 item, 경계 앞 이탈, 그룹 축소/재사용, A→B 교체: 유지 item/type/group 동일, 정확한 REMOVE/REG delta, 모든 중간 wire_state 한도 준수. 계좌·시장·wildcard 행 유지.
3. fake websocket에서 pacing/send/ACK를 의도적으로 보류하는 동안 유지 종목 REAL을 연속 투입한다. 마지막 등록 완료 전에 dispatch가 발생하고 지연이 batch 길이에 묶이지 않음을 검증한다. 같은 4그룹 시나리오의 전후 수신 지연을 남긴다.
4. REMOVE만 있는 변경, no-op, send 실패, 실패/누락/지연 ACK, 식별 가능한 중복/오응답, grp_no 없는 정상 응답, send 반환 전 ACK, 끊임없는 REAL 속 timeout, 연속 desired 변경, 취소/재접속 generation을 검증한다. 임의의 무식별 중복 ACK를 구분했다고 주장하지 않는다.
5. KRX/NXT/SOR 전환, NXT 자격 제거·재추가, 신규/기존 source 기준점, 전환 중 승인 집합 0개, 재연결 전체 reset, 기존 source 보존, source 신호→첫 trade 순서, PC↔NAS failover/복구와 후속 조회 중복 방지를 검증한다.
6. 고정 v1 fixture를 변경하지 않고 엄격 reader/legacy replay 통과. 새 v2에 초기/차등/해제/no-op/부분 실패 fixture, recorder on/off 동등성, 계좌 비노출, 누락/변조/부분 receipt 거절, native offline wire/승인 대조를 추가한다. 구형 sealed 자료를 새 방식으로 다시 만들어 통과시키지 않는다.
7. 최소 관련 회귀: `test_realtime_subscription`, `test_realtime`, `test_central_realtime_collector`, `test_central_realtime_worker`, `test_realtime_worker_controller`, `test_app_controller`, 분봉 source/gap 회귀, `test_top20_lifecycle_inputs`, `test_top20_replay_transport`, `test_top20_replay_execution`, `test_top20_session_plan` 및 연결된 sealed gate. FastAPI/Starlette 교체나 httpx2 추가 금지.
8. 실제 앱·NAS 적용 뒤 순위 수신/REG·REMOVE 송신/ACK/REAL dispatch/UI flush 시각을 같은 조건에서 비교한다. 운영 관측 전까지 '코드상 등록 대기 제거'와 '사용자 체감 해결'을 구분한다. 다른 작업의 NAS pause/job은 건드리지 않는다.

## 단계 인계

당시 실행 기록 `gpt-6-astra / High`. 위 정책·상태·호환 경계의 설계 판단은 완료했다. 다음은 이 계약에 따른 상태·동시성 구현과 회귀이므로 [현행 모델 정책](MODEL_HANDOFF_POLICY.md)에 따라 **GPT-6.1 Sol / High**로 인계한다. GPT-6 Sol은 6.1 Sol 미지원 환경에서만 대체한다. 설계만으로 신규 구현·회귀·배포를 완료 처리하지 않는다.

## 로컬 구현 결과와 운영 잔여

인계 뒤 실행 기록 `gpt-6.1-sol / High`로 구현했다. PC/NAS owner의 recv task와 한 send task가 등록 간격/성공 ACK를 함께 감시하고 유지 source는 계속 처리한다. 레거시 `_send_subscription`의 전체 교체는 offline v1 경로와 그 역사 fixture에만 남겼으며 live NAS는 새 계획기를 사용한다. 생성 fixture는 명시적으로 v1을 선택하고 원래 sealed 자료와 기대 wire/ACK를 변경하지 않았다.

추가 확인된 PC 연결 응답 경계도 수정했다. 같은 NAS client socket의 재구독은 connection_opened를 반복하지 않고 subscription_ready를 보낸다. NAS에 이미 활성화된 새 종목이 즉시 전달되는 경우를 위해 PC는 새 요청을 공개하기 전에 그 신규 종목의 옛 source 기준점을 초기화한다. 순위 도중 희망 목록이 다시 바뀌면 과거 batch를 READY로 발표하지 않고 최신 목록을 다음 변경에 반영하며, 재활성화된 source는 기준점을 다시 잡는다. 조건검색 02는 기존 독립 승인 경로를 유지하고 미승인 시장/VI 행은 새 구독 승인 필터를 따른다.

검증 근거:

- `incremental-subscription-source-verified.log`: 구독/worker/분봉/누락 품질/v1·v2 재생 및 고정 core 순서 **105건 통과**.
- `incremental-subscription-coalescing-final.log`: 최신 희망 재변경·REMOVE 전용 완료·recv/send task 종료·그룹 전체 100 item/타입 200 item·장애전환/자격 수명 **42건 통과**. 앞선 105건과 겹치므로 더해 전체 수량으로 쓰지 않는다.
- `incremental-subscription-targeted-final.log`: 조건검색·ASGI client 갱신·직접 승인·장애전환/자격 경계 **74건 통과**.
- `incremental-subscription-combined-regression.log`: 초기 통합 실행의 136건은 통과했지만 조건검색 보조 모듈 경로 누락으로 1건 로딩 실패였다. 보조 경로를 포함한 후속 74건에서 조건검색을 실제 재실행해 통과했다. 로딩 실패를 성공으로 계산하지 않는다.
- `incremental-subscription-api-regression.log`: 진단 제어/인증 등 6건 통과, 전체 realtime OpenAPI 기준선 1건 실패. 앞선 TOP20 `/api/v1/market/top20-series`와 schema 추가가 이번 WS 변경 전에도 존재함을 `incremental-subscription-native-api-comparison.json`에서 확인했다. 현재 소스의 변경 전후 모든 ASGI 결과와 불변 legacy 프레임·SQLite snapshot·태스크 정리 결과는 동일하다. 관련 없는 전체 OpenAPI 기준선은 수정하지 않았다. httpx2를 추가하거나 선택 검사를 생략하지 않았다.
- `incremental-subscription-dispatch-comparison.json`: REMOVE send 반환을 보류한 격리 실험에서 유지 종목은 해제 송신 완료 전에 dispatch됐다(PC 0.190ms/NAS 0.364ms). 이는 등록 대기와 수신 처리의 분리 근거이며, 실전 feed/UI 지연이나 원본 무누락 측정은 아니다.

새 테스트 두 모듈은 필수 Windows `pc-lifecycle` profile에 등록했고 기존 core 순서/fingerprint를 보존했다. 저장소 전체 catalog/new-module gate는 다른 변경의 `tests.unit.test_seed_news_jobs_from_nas_rankings` 미등록 때문에 실패한다. 해당 뉴스 작업의 등록과 전체 catalog 검증은 별도 잔여이며 이 변경의 두 모듈은 등록 여부를 직접 확인했다. 최초 core 삽입 시 fingerprint 검사가 실패해 기존 core 순서를 복원하고 명명 profile에 등록했다. 초기 테스트 method 이름 오타도 바로잡아 105건을 다시 실행했다.

운영 읽기 대조는 `incremental-subscription-nas-status.json`을 따른다. 현재 NAS는 `2026.10.12-recorder-account-index-v3-875421f5e5e3fb26`, health=ok, trace=running, blocker=diagnostics_enabled다. 이 작업으로 NAS 녹화·구독·운영 job·배포를 바꾸지 않았고 PC 앱도 재시작하지 않았다. PostgreSQL/sealed NAS 전용 gate와 새 PC/NAS 조합의 실제 장중 연속성은 아직 검증하지 않았다. 다른 작업의 녹화를 종료해 배포하지 않는다.

v2 성공 재생은 등록의 완전한 인과 prefix를 요구한다. 이미 승인된 warm 연결의 과거 등록 증거 없이 중간 batch부터 시작한 자료는 `top20_v2_prefix_unavailable`로 거절한다. 임의의 무식별 중복 ACK 한계와 함께 이를 운영 입력 적격성 조건으로 유지한다. 구현과 로컬 재현 통과를 배포·실전 체감 해결로 승격하지 않는다.

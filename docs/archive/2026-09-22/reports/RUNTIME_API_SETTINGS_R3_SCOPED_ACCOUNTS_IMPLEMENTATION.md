> **과거 기록** · 원래 경로: `reports/RUNTIME_API_SETTINGS_R3_SCOPED_ACCOUNTS_IMPLEMENTATION.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# 런타임 인증 R3g 선택 계좌 조회·모의주문

2026-09-15. R0~R3 서버 기능 로컬 완료, NAS 소스 동기화/배포 전.
누적 build: `2026.09.15-runtime-credentials-r3-scoped-accounts-v1`.

## 목적과 수정 대상

사용자가 선택한 계좌에 정확한 조회·주문을 보내고 다른 계좌의 cursor/주문 이력이 섞이지 않게 한다.
기존 app.py, AccountQuerySessionManager, MockAccountBundle, ManualMockOrderGateway를 확장했다.
새 코드 계층/manager/실행 저장소는 추가하지 않았다. 계좌별 불변 repository/owner는 그대로 유지한다.

## 입출력 계약

- POST /api/v3/kiwoom/account-query: 기존 v2 페이지 필드에 account_scope, credential_profile_id, expected_binding_revision을 필수 추가한다. 응답은 기존 v2 payload/batch/page/context/provenance 형태다.
- POST /api/v2/mock/accounts/{account_ref}/orders: 기존 지정가 수동 주문 필드와 위 대상 필드를 전달한다. 응답에 검증된 context를 추가한다.
- GET /api/v2/mock/accounts/{account_ref}/orders/{intent_id}: query의 environment=mock, credential_profile_id, expected_binding_revision과 path의 계좌를 검증한다. broker=kiwoom 기본.
- POST 위 orders/{intent_id}/cancel: 위 대상 필드와 선택 quantity를 전달한다. intent를 account/current run으로 검증한 뒤 기존 취소 정책을 사용한다.
- 모든 경로는 bearer 인증을 요구한다. path/body scope 충돌과 binding 버전 불일치는 409, 미준비 profile은 503, 외부 계좌/run intent는 404다. 대상이 잘못됐으면 TR/주문을 보내지 않는다.
- 모의 profile은 실제 admitted bundle의 client/broker를 선택한다. 실전은 현재 검증된 main profile만 지원한다. 다른 실전 runtime은 R6다.

## 페이지·작업 수명

bundle마다 기존 페이지 manager를 갖는다. cursor는 API/path/body/binding에 고정되고 같은 broker의
조회 한도를 사용한다. mock 페이지는 기존 공통 2개 read limiter에 참여한다. 최대 owned 작업 32개,
열린 cursor 256개이며 TTL 정리 정책을 유지한다.

페이지는 manager의 owned task이고 HTTP 대기자 취소는 실제 호출/페이지 완료를 취소하지 않는다.
연결 교체/종료는 gateway 접수를 먼저 닫고 페이지 실제 작업까지 기다린 뒤 WS/monitor/lease를 종료한다.
응답 전 binding/종료 상태도 확인하며 구형 response/cursor는 새 bundle에 게시하지 않는다.
main manager도 서버 종료 때 기다린다. cursor에 사용된 raw next_key는 계좌 간 재사용하지 않는다.

## 주문·호환 정책

새 scoped intent ID는 mock/account_ref/run/request_id를 함께 사용한다. 두 계좌가 같은 request_id와
run을 사용해도 서로 다른 원장 행이다. 동일 요청은 기존 상태를 반환하고 UNKNOWN도 다시 보내지 않는다.
기존 v1 run/request_id ID 계산은 유지한다. scoped와 legacy 요청 ID 공간은 별개이므로 네트워크 오류를
이유로 v2 scoped 명령을 v1 명령으로 바꿔 재시도하면 안 된다.

수동·명시 주문 허용·KRX 지정가·시장시간/잔고/취소 검증은 기존 정책을 사용한다. 후보·연구는 이 API를
호출하지 않는다. 주문 OFF인 활성 계좌는 자신의 저장 주문 GET만 가능하고 신규 submit/cancel은 503이다.
비활성 profile의 과거 이력은 후속 일지/선택 UI의 기록 열람 경계이며 여기서 다른 profile로 대체하지 않는다.

v1 기본 모의 nas-mock-default와 v2 legacy main 계좌 조회는 화면 선택에 따라 바뀌지 않는다.
capability multi_account_query_v3/scoped_mock_orders_v2는 API 지원 여부다. 각 계좌의 연결/주문 허용
상태는 credential profile·account settings에서 확인한다.

## 검증과 완료 기준

전체 관련 회귀 336개 실행: 335개 통과, Windows POSIX 권한 1개 생략, 종료 코드 0.
최종 Python 문법 9개 파일·관련 diff 공백 검사 통과, 서버/Compose/Dockerfile build 세 곳 일치.
fake client/WS/주문 transport, 임시 vault/DB와 생성 UUID만 사용했다. 실제 키·공급자 네트워크·주문은 사용하지 않았다.

확인한 항목:

- 두 계좌의 broker/payload/context/cursor 분리, 외부 cursor·잘못된 계좌·낡은 binding 거절.
- 키 교체 후 구형 cursor 무효화, 늦은 응답의 binding 재검사, 취소된 HTTP 대기자와 별개로 실제 query 종료 대기.
- 두 계좌의 동일 request_id 주문/중복 방지, 같은 run까지 겹친 경우의 실제 원장 분리.
- 외부 계좌 intent GET/cancel 거절, 자신의 취소가 자신의 transport로만 진행.
- 주문 OFF의 저장 주문 GET 허용, submit/cancel 차단, UNKNOWN 중복 재전송 없음.
- 기존 v1 기본 모의 대상 고정과 v2 real main 조회, v3 explicit main 조회의 응답 context 일치.
- 기존 모의 owner·장벽·DB·인증·시장/뉴스·GUI 관련 회귀.

한 기능의 호출 경계는 API→기존 bundle/manager/gateway→기존 broker/repository로 유지했다.
조회 owned task는 독립 수명/종료 책임 때문에 기존 manager 안에 추가했다. DB 테이블/스키마 변경 없음.
실제 NAS PostgreSQL·Linux·TLS·중단/복구·장중 TR 응답 검증은 R7 미실행이다.
모델 에스컬레이션 없음.

## 다음 한 단계

R4: NAS 인증/계좌 입력·선택 UI와 PC client/worker의 명시 context 전달을 연결한다.
계좌 변경 때 옛 응답을 새 선택에 표시하지 않고, scoped 명령은 같은 경로/request_id로만 확인한다.
R5 news·R6 real owner, R7 NAS 누적 동기화/재빌드·실환경 검증은 남아 있다.
현재 PC 직접 연결의 복수 계좌 지원과 실제 자동주문 기능까지 완료된 것은 아니다.

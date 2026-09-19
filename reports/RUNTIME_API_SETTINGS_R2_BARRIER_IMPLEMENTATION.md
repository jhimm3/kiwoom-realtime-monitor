# 런타임 인증 R2a 공통 REST 장벽

2026-09-15 R2a 완료 당시 기록이다. 후속 [R2b API 결과](RUNTIME_API_SETTINGS_R2_API_IMPLEMENTATION.md)에서
외부 공통 API를 구현했다. 아래 검증과 미완료 항목은 R2a 당시 기준이다.
build `2026.09.15-runtime-credentials-r2-barrier-v1`. NAS 동기화·배포 전.

## 목적과 변경

기존 구조에서 실제 동기 HTTP·DB 저장·토큰 refresh가 끝났는지 판단하는 경계를 먼저 확정한다.
새 클래스 계층·공급자 프레임워크를 만들지 않고 기존 client와 broker에 명시 메서드를 추가했다.

- `KiwoomRestClient`: 후보 전송은 원래 RLock 안에서 transport snapshot을 사용한다.
  활성 설정/토큰을 바꾸지 않고 원래 최근 호출 시각·간격·감속 상태를 상속·반영한다.
  후보 token과 계좌 확인 결과는 repr에서 제외한 메모리 자료이며 저장하지 않는다.
- broker 후보 OAuth와 ka00001은 별도 priority 20 작업이다. OAuth 중 들어온 priority 10
  ka00198이 계좌 확인보다 먼저 실행된다. 추가 병렬 client queue나 한도는 없다.
- `begin_credential_change`: broker 새 접수를 잠그고 queue.join으로 기존 HTTP와
  응답 handler/캐시 저장의 종료까지 기다린다. 원래 client 잠금을 획득해 별도 토큰 refresh도 기다린다.
- `activate_prepared_credentials`: 실제 drain이 완료됐을 때만 같은 환경·세대의 완료 후보를
  메모리에 교체한다. 토큰만 받은 후보·만료 후보·낡은 세대는 거부한다.
- `end_credential_change`: 활성화 thread 종료를 확인하고 client/broker 접수를 연다.
  실제 인증 변경이 있었을 때만 cache generation을 갱신한다. 최초 캐시 hash 계약은 유지한다.
  파일/DB commit 전 중단이었다면 활성화 없이 재개해 이전 인증을 유지할 수 있다.
- drain/activate/resume/close는 소유 task를 shield한다. 대기자 취소가 실제 작업 취소가 아니다.
  thread 진행 중 재개를 거부하고, 취소됐던 재개가 끝난 뒤 다음 변경 회차도 정상 처리한다.

## 내부 입출력 계약

broker prepare(settings) → 메모리 완료 후보. active 인증은 그대로다.
begin → 실제 drain 완료 또는 기다리는 task. 취소해도 gate와 실제 작업은 유지된다.
activate(준비 객체) → 신규 client generation. 같은 객체 재대기는 같은 진행 task를 읽는다.
다른 객체 적용은 거부한다. end → 실제 완료 검증 후 재개. 실패/진행 중이면 gate를 유지한다.
close → 진행 중 broker/인증 task의 실제 종료 대기. 대기자 취소로 HTTP나 DB 작업을 잃지 않는다.

이 내부 메서드를 GUI에서 직접 호출하지 않는다. R2b 소유 operation task가 deadline/상태를 관리하고,
동일 프로필 적용을 직렬화한다. 저수준 대기를 취소하고 즉시 예전 상태로 rollback해서는 안 된다.

## 다음 부분과 한계

R2b: profile 생성, prepare/apply/status/cancel API, 5분 TTL·상한·request digest/revision·
HTTPS/trusted proxy/secret body 오류 제한·vault commit/DB 활성화/적용 상태 조정.
현재 internal 오류는 HTTP BUSY 응답으로 연결 전이다. 신규 자격 검증도 운영 경로에서 아직 호출하지 않는다.
키움 ka00001 원문→HMAC UUID와 계좌 미리보기는 기존 reader/신원 준비 경로를 다음 부분에서 연결한다.
계좌 주문·WS 인증·조회 cursor·lease fencing은 R3/R6의 실제 runtime 연결까지 완료로 보지 않는다.
신규 WS가 gate 도중 live get_access_token을 우회해 임의 로그인하도록 허용하지 않는다.
재연결 인증 snapshot과 연결 세대는 해당 runtime 적용 단계가 명시적으로 연결해야 한다.

함수 분리는 큐 작업 단위와 실제 취소 수명을 설명하기 위해서만 했다. 신규 소유 계층은 없다.
현재 단계 모델 에스컬레이션 없음. 후속 동시성 난도는 실제 문제를 분석한 뒤 판단한다.

## 검증 결과

- 최종 회귀 192개 실행: 191개 통과, R1 POSIX 권한 1개 Windows에서 생략, exit=0.
- 후보 키·활성 토큰 격리, 원래 요청 잠금/호출 이력 유지, 안전한 후보 오류,
  token-only/만료 후보 거부, 같은 후보 재대기/다른 후보 충돌과 캐시 세대 분리.
- OAuth 중 들어온 순위가 후보 계좌 확인보다 먼저 실행되는 것을 확인했다.
- HTTP 대기/DB 저장 대기 중 요청·drain 취소, token refresh 동시 실행, activation thread 취소,
  resume 취소 후 다음 회차 재개, close 취소 후 실제 HTTP/DB 완료를 확인했다.
- 기존 REST·서버·R0/R1·설정 GUI·뉴스·AI·후보 회귀를 함께 실행했다.
  실제 키움 후보 인증·NAS 배포·operation API의 TLS/프로세스 중단은 아직 실행하지 않았다.

# 런타임 인증 R2b 준비·적용 API

2026-09-15. 공통 R2 로컬 구현 완료. build `2026.09.15-runtime-credentials-r2-api-v1`.
R0·R1·R2a 누적 소스이며 NAS 동기화·배포 전이다.

## 구현 범위

`credential_runtime.py` 한 파일이 고정 7개 공급자의 profile/operation 수명과 HTTP 경계를 소유한다.
새 공급자 프레임워크나 별도 DB를 만들지 않았다. 실제 owner의 prepare/drain/publish/resume 경계는
R3(mock)·R5(news/AI)·R6(real)가 연결한다. 지금은 실제 provider owner를 등록하지 않았으며
prepare 503으로 저장·적용을 거절한다. 기존 기동 env/vault 설정은 R1 경로를 유지한다.

- 키 없는 draft profile UUID 생성은 request_id와 keyed digest로 멱등 처리한다.
  기존 v19 profile 테이블과 central_documents의 credential_profile_requests를 한 트랜잭션으로 쓴다.
- prepare는 expected_revision과 전체 키 쌍 또는 explicit disable을 받는다. 활성 키를 바꾸지 않는다.
  완료 후보는 검증·계좌 UUID/run_id·메모리 준비 객체를 가진다. 토큰/키는 응답·DB 원장에 없다.
- TTL 5분, 프로필당 진행 작업 1개, 전체 진행 32개, 상태 보존 최대 256개, 프로필 최대 64개다.
  취소/만료는 coordinator의 비밀 참조를 제거하지만 이미 시작한 실제 통신은 끝까지 기다린다.
- Kiwoom 완료 후보는 검증 registry의 동일 환경 canonical UUID여야 한다. 기존 프로필의 계좌가
  달라지면 ACCOUNT_CHANGED로 파일 저장 전에 막는다. 새 프로필의 UUID는 apply 요청으로 확인한다.
- apply는 서버 소유 task가 실제 drain→vault 파일 commit→binding/activation DB transaction→
  runtime publish/resume 순서로 처리한다. 실제 runtime revision이 일치해야 ACTIVE를 표시한다.
- deadline 뒤 BUSY를 표시하고 실제 종료를 기다린다. drain이 늦게 끝나면 기존 owner를 재개하고
  늦은 키 저장은 하지 않는다. 파일 commit 시작 이후 오류는 이전 키 rollback 성공으로 표시하지 않는다.
- 파일 commit 뒤 DB 실패는 RECOVERY_REQUIRED다. 재시작 최종화는 멱등 처리하며,
  DB가 계속 실패해도 복구 대상 operation은 알 수 없는 작업으로 사라지지 않는다.
- 적용 완료 request는 DB 활성화 원장으로 재현한다. 준비만 한 작업은 재시작 뒤 410이다.

## HTTP 계약과 보안 경계

인증된 GET profiles, POST draft, POST prepare, GET operation, POST apply, DELETE cancel을 추가했다.
기존 경로·입출력은 유지한다. capability runtime_credentials_v1은 공통 API 존재를 의미하고
providers[].supported가 실제 변경 가능 여부다.

쓰기는 HTTPS 또는 명시 trusted proxy IP의 단일 HTTPS 헤더에만 허용한다.
CREDENTIAL_TRUSTED_PROXIES는 기본 빈 값이며 Uvicorn proxy_headers=False다.
기존 origin 관측 매칭 경계와도 암묵적 forwarded-header 신뢰를 공유하지 않는다.
키 입력은 최대 16KiB 수동 JSON 파싱으로 unknown field/중복 JSON key/빈 값/토큰 필드를 거절한다.
오류는 고정 code만 반환하고 예외 원문·provider 응답 메시지·raw input을 전달하지 않는다.
credential URL query는 거절하며 서버 access-log scope에서 제거한다.

## 검증

최종 회귀 210개 실행: 209개 통과, POSIX 권한 1개 Windows에서 생략, 종료 코드 0.
변경 Python 문법·공백 검사 통과, 서버/Compose/Dockerfile build 3곳 일치.
실행 결과는 NAS_DEPLOYMENT_PENDING.md에 함께 기록했다.
임시 키는 매 테스트에서 생성하고 실제 Kiwoom·뉴스·유료 AI 호출은 하지 않는다.
새 회귀는 준비/적용 멱등성·revision 충돌·TTL·취소·실제 종료 대기·deadline·
파일 교체/DB 최종화/runtime publish 실패·재시작 복구·계좌 변경/확인·HTTPS/proxy·
비밀 없는 422/413 오류·query scope 제거·미연결 공급자 거절을 보호한다.
기존 R0/R1/R2a·REST·서버·설정 UI·뉴스/AI·후보 회귀를 함께 실행한다.
PostgreSQL 통합 스크립트에는 draft 멱등성·profile 목록·activation direct lookup 검증을 추가했다.

## 다음 단계와 실제 미검증

다음은 R3 실제 모의계좌 owner/독립 broker·monitor·주문 lease·cursor 세대 연결이다.
R4 입력 UI·HTTPS/redirect 거절 credential client, R5 뉴스/AI, R6 실계좌, R7 누적 동기화·배포 순서다.
NAS PostgreSQL·Linux 권한·실제 proxy IP/인증서 TLS·프로세스 중단·실제 후보 키 인증은 미실행이다.
ASGI HTTPS/proxy 테스트는 실제 네트워크 인증서 검증을 대신하지 않는다.
현재 단계 모델 에스컬레이션 없음. NAS 중간 재빌드는 요청하지 않는다.

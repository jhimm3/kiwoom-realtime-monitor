> **과거 기록** · 원래 경로: `reports/RUNTIME_API_SETTINGS_R3_BUNDLE_IMPLEMENTATION.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# 런타임 인증 R3c 계좌 bundle

2026-09-15. 로컬 구현 완료, R3 전체 진행 중.
build `2026.09.15-runtime-credentials-r3-bundle-v1`, R0/R1/R2/R3a/R3b 누적.

## 구현과 계약

- `MockAccountBundle`이 한 mock 계좌의 client·broker·repository·runtime·monitor·WS·gateway와
  owned 시작/종료 task를 소유한다. 계좌/run/profile은 읽기 전용이며 연결을 다른 신원으로 바꾸지 않는다.
- 기존 app의 단일 모의 연결도 같은 조립을 사용한다. 실전 client·큐와 공유하지 않는다.
- 시작은 broker → ka00001 검증/UUID 대조 → binding → monitor/lease → WS다.
  불일치한 UUID의 registry 등록은 가능하지만 binding·lease는 확정하지 않는다. ka00001을 두 번 요청하지 않는다.
- 종료는 실제 시작 작업을 기다린 뒤 gateway → WS → monitor/lease → broker 순서다.
  대기자 취소가 실제 시작/종료를 취소하지 않으며 닫은 bundle은 재시작하지 않는다.
- WS resolver는 해당 bundle의 검증 신원/binding만 사용한다. 닫는 중에는 scope를 반환하지 않는다.
- 주문 submit/read/cancel은 요청 시작 gateway를 고정해 응답 events까지 같은 계좌 실행을 사용한다.
- 모의 인증·계좌 불일치·monitor 시작 실패는 모의 기능만 비활성화한다.
  공개 health/로그는 고정 오류 코드이며 upstream 원문을 노출하지 않는다.
- main 신원 실패의 종료 경로도 DB를 닫기 전에 mock bundle을 정리한다.

## 근거와 검증

계좌 불일치 회귀를 먼저 실행해 기존 app의 ACCOUNT_CONTEXT_MISMATCH가 NAS startup 전체를
중단하는 것을 재현했다. 수정 뒤 서버 생존과 binding 0건을 확인한다.
두 계좌 신원/연결 분리, 닫힌 epoch 거절, 시작 대기자 취소, gateway drain 전에 lease 유지,
종료 대기자 취소, 기본 주문 transport OFF, real settings 거절을 별도 bundle 테스트로 확인한다.
기존 주문 API 테스트는 fake client와 fake WS 시작을 사용한다. 초기 실행에서 client의 module-level
import가 기존 mock 교체를 우회하고 fake token WS가 실제 공급자에 접속하는 테스트 격리 결함을 발견해
중단했다. client 생성 시 기존 lazy import 경계를 복원하고 WS 시작도 명시적으로 mock 처리했다.
실제 사용자 키·계좌 주문을 사용하지 않았다. 최종 회귀 274개 중 273 통과,
POSIX 권한 1개는 Windows에서 생략했으며 종료 코드 0이다. 관련 diff 공백 검사와
서버/Compose/Dockerfile build 3곳 일치도 확인했다.

## 추상화 비용과 남은 작업

app의 조립 블록을 bundle로 옮겼다. 이름만 다른 전달 계층 대신 독립 client/lease/WS 수명을 소유한다.
주문·계좌 조회의 실제 서비스 호출 깊이는 유지한다. 시작/종료 이해에는 bundle 파일 한 개가 추가된다.
기존 구체 모듈을 재사용하며 새 framework·DB schema·별도 주문 구현은 추가하지 않았다.

다음은 복수 계좌 owner와 credential hooks 등록, 동일 계좌 client/rate history 재사용,
계좌 설정 CAS·중복 account 활성화 차단, 초기 read/lease 준비 뒤 ACTIVE publication,
사전 commit 실패의 이전 연결 재조립과 commit 이후 RECOVERY_REQUIRED 유지,
scoped query/order API다. 이 단계만으로 env 없이 키 변경이 가능해진 것은 아니다.
NAS 동기화/중간 재빌드는 진행하지 않았다. 실제 NAS PostgreSQL 검증은 R7이다.
현재 단계 모델 에스컬레이션 없음.

# 런타임 인증 R5a — 네이버 뉴스 키 교체

2026-09-15. 로컬 구현/가짜 API 검증 단계. NAS 소스 동기화·배포 전.
최신 누적 build: `2026.09.15-runtime-credentials-r5-naver-rotation-v1`.

## 목적과 범위

NAS 재시작 없이 네이버 키 교체/비활성화와 키 없는 설치의 첫 활성화를 지원한다.
기존 R5를 공급자별로 나눴으며 이번에는 네이버 owner와 두 뉴스 수집 경로만 연결했다.
DART·AI·일반 공급자 PC 입력 UI·조건식/해외시세 운영 확대는 완료가 아니다.
뉴스 원문/요약/분류 품질 규칙·순위 주기·키움 TR 경로는 변경하지 않았다.

## 파일과 입출력

- `central_server/news_credentials.py`: 기존 Runtime hooks에 검증/실제 drain/commit fence/공개/재개/release를 제공한다.
- `central_server/news_service.py`: 종목별 수집 task의 시작 client를 고정하고 실제 완료를 대기한다.
- `central_server/news_sources.py`: 공통 검색의 페이지와 성공/실패 DB 기록을 하나의 owned task로 관리한다.
- `central_server/app.py`: AI 설정 여부와 무관하게 vault 설치에 keyless service/네이버 owner를 연결한다.
- 서버 build와 Compose image/Dockerfile 검증 문자열을 같은 누적 버전으로 갱신했다.

기본 프로필은 `naver/nas-naver-default` 한 개다. 키가 없어도 기존 DB profile을 등록하며,
다른 임의 프로필은 지원하지 않는다. prepare 입력은 request_id/expected_revision/
replacement `{client_id,client_secret}` 또는 disable+빈 객체다.
기존 검색 클라이언트로 `증권`, display=1을 검증하고 VERIFIED 후보를 만든다.
실제 요청 시도는 기존 영속 watchlist/shared 예산으로 claim한다. 실패·취소도 이미 사용한 예산을 되돌리지 않는다.
신규 키/토큰/검증 응답 원문은 operation 응답·PC 설정·중앙 content DB에 저장하지 않는다.
계좌 대상은 null이다. 기존 모의계좌 PC client/UI의 계좌 UUID 검사를 우회하지 않는다.

## 실제 작업 경계

종목별 수집과 공통 검색은 시작 때 받은 client를 모든 네이버 요청에 쓴다.
공통 검색의 `_run_query`가 페이지 처리와 늦은 오류 저장까지 소유한다.
HTTP/정기 loop waiter를 취소해도 shield한 수집 task는 진행 중 sync HTTP/thread와 DB 저장을 끝낸다.
종목 수집 task의 완료 callback은 요청자가 사라져도 해당 owned task를 제거하고 늦은 실패를 관찰한다.
기존 waiter의 finally만으로 정리하면 취소 시 진행 task가 남아 다음 조회에 완료된 이전 결과를 재사용할 수 있음을
가짜 실행으로 재현했다. 동일 task인 경우만 완료 callback으로 제거하며 새 수집 task를 지우지 않는다.
pause는 새 공급자 작업을 막고 두 경로의 owned task 완료를 기다린다.
pause 중 새 종목 요청은 등록/우선 종목 설정과 저장 목록 조회만 하며 fresh 확인 시각을 쓰지 않는다.
이미 진행 중인 동일 종목 task는 그대로 합친다. BODY/RULE/AI runner는 재생성/초기화하지 않는다.

drain deadline 초과는 BUSY로 표시하지만 실제 종료 전 완료라고 주장하지 않는다.
실제 종료 뒤 commit 없이 FAILED가 되고 기존 client를 재개한다.
commit 시작 때 이전 client를 두 경로에서 먼저 제거한다. vault write가 성공 후 오류를 보고할 수도 있으므로,
이 지점 이후 실패에는 이전 키를 복원하지 않는다. RECOVERY_REQUIRED, active revision 없음,
NAVER client None을 유지한다. release는 NAVER 부재 상태로 gate만 열어 DART/캐시/독립 작업을 허용한다.
정상 공개는 두 경로에 같은 후보 client를 할당하고 새 revision을 공개한 뒤 재개한다.

disable은 검증 HTTP를 하지 않고 빈 credentials의 tombstone을 commit한다.
disabled ACTIVE는 비활성화 적용 완료다. 초기 env보다 tombstone이 우선하며 기존 기사·계좌 데이터는 지우지 않는다.
명시적 새 prepare/apply 또는 서버 시작 시 vault 기반 복구를 사용하고 예전 env 키로 우회하지 않는다.

## 보존 및 완료 기준

같은 service/collector/runner를 유지한다. source cursor와 next schedule, 기사 revision,
BODY/RULE/AI job 상태, 공통/종목 예산 사용량을 초기화하거나 기존 기사 재수집/재분류를 예약하지 않는다.
확인된 API 오류는 secret-safe operation 실패로 반환하며 타 공급자와 시세 owner에 전파하지 않는다.
새 DB table/migration·범용 작업 관리 계층·wrapper는 없다.
추가 owner 파일은 단순 전달이 아니라 후보 인증 수명·commit fence·활성 revision 책임을 갖는다.
기존 수집 정책은 원래 두 파일에서 이해할 수 있고 credential 변경 시에만 owner/runtime 계약을 함께 읽는다.

## 검증

신규 테스트 `test_naver_credential_owner.py`: 공급자 owner 14개와 HTTPS route 통합 1개.
가짜 네이버/DART와 임시 SQLite/vault만 사용한다. 실제 네이버/키움/AI 호출·키·사용자 DB·NAS 접속은 없다.
확인: 두 경로 교체, cursor/기사/작업/예산 보존, 취소된 종목 waiter의 실제 thread drain,
취소된 query waiter의 모든 구 키 페이지 완료, 늦은 오류 DB 저장 완료 뒤 공개,
deadline 뒤 이전 키 재개, keyless 설정 유지, 비활성화/환경 키 부활 차단,
검증 실패·stale revision·예산 소진, vault/공개 실패의 fence와 DART 독립 동작,
다음 명시 적용 복구, 후보 취소, 서버 종료 시 실제 페이지/종목 저장 완료 대기,
keyless/AInone 서버의 기본 프로필 인증/적용과 임의 profile 거절.
회귀 API 테스트에서 기존 미연결 공급자 fixture를 NAVER에서 DART로 옮겼다. 미지원 정책은 유지한다.
관련 회귀 316개: 315개 통과, Windows POSIX 권한 검사 1개 생략, 종료 코드 0.
최종 취소 완료 객체 정리 보완 뒤 네이버/뉴스/서버 회귀 112개를 재실행해 모두 통과했다.
로그: `tmp/r5a-regression.log`, `tmp/r5a-final-focused.log`.
최초 신규 테스트용 SQLite 연결 종료 누락은 명시 close로 수정했다. 제품/테스트 미해결 실패 없음.
Python 문법 6개 파일·관련 diff/신규 파일 공백 검사 통과, 서버/Compose/Dockerfile build 세 곳 일치.
실제 NAS API/운영 자료를 검증한 것은 아니다.

## 다음 한 단계와 남은 검증

다음은 R5b DART 인증 owner와 기존 corp-code/cache 수명 보존이다.
AI 공급자·PC 일반 공급자 입력 UI/운영값 확대, R6 실전 owner, R7 누적 배포/실환경 검증은 남았다.
실제 NAVER 키 검증/fallback·일일 예산 소진·NAS TLS/PostgreSQL/Linux 및 장중 검증은 아직 하지 않았다.
NAS 소스 동기화는 수행하지 않았고 R7까지 중간 재빌드를 요청하지 않는다.
모델 에스컬레이션 없음.

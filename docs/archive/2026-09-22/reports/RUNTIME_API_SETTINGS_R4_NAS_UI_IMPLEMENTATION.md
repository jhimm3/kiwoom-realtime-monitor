> **과거 기록** · 원래 경로: `reports/RUNTIME_API_SETTINGS_R4_NAS_UI_IMPLEMENTATION.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# R4a NAS 모의계좌 입력 UI 구현 결과

2026-09-15. 로컬 구현 완료, 실제 NAS 적용 전. R4 전체 완료는 아니다.

## 구현과 책임

설정 → NAS → `NAS 모의계좌·API 키 관리`에서 새 프로필 추가, 키/계좌 확인,
확인한 익명 계좌로 명시 적용, 키 비활성화, 계좌 조회/수동 모의주문 허용을 관리한다.
같은 계좌는 키 교체로 이력을 유지하고 다른 계좌 키가 들어오면 새 계좌 추가를 안내한다.
main mock 시세용 예약 프로필은 이 관리 목록에서 제외한다. 실전/뉴스 공급자 관리는 후속이다.

`central_credentials_client.py`는 전송 정책·안전한 요청 ID·응답 대상 검증을 소유한다.
`nas_credentials_dialog.py`는 화면 상태·입력 지우기·명시 적용·상태 확인 주기를 소유한다.
`api_settings_dialog.py`는 진입점과 같은 NAS 대상의 client 재사용을 담당한다.
단건 I/O는 기존 parentless SettingsRequestWorker의 실제 완료 수명을 사용한다.
추가 manager/wrapper/저장소나 기존 계좌 worker 변경은 없다.

## 입출력 계약

- HTTPS NAS 모드와 접속 토큰을 검사한 뒤에만 요청을 직렬화/전송한다. 시스템 인증서 검증을
  사용하고 HTTP redirect와 응답 URL 변경은 거절한다. 10초 timeout, 응답 상한 1MiB.
- 구 NAS capability 또는 provider/profile 미지원은 입력/적용을 비활성화한다.
  키는 Password 입력이며 준비를 누르거나 창을 닫으면 비운다. 기존 키를 조회/채우지 않는다.
  PC 설정/DB/미러/백업에는 쓰지 않는다. 키는 worker 통신 동안 메모리에만 존재한다.
- 프로필 추가/prepare timeout 재시도는 같은 UUID를 유지한다. 준비 응답을 못 받아 operation ID를
  모르면 같은 프로필/revision/disable에 키를 재입력한다. 다른 내용의 동일 요청은 서버가 충돌로 거절한다.
  이 ID는 메모리 수명이며 앱 재시작을 넘어 저장하지 않는다.
- operation/provider/profile/revision과 익명 계좌를 확인한다. READY의 확인된 대상만 apply한다.
  apply timeout에는 DRAINING으로 새 적용을 막고 동일 operation GET으로만 확인한다.
  서버 재시작 후 committed receipt도 같은 ID/profile과 revision+1로 확인한다.
- 진행 중 상태는 0.75초 간격으로 확인하며 네트워크 오류가 이어지면 최대 5회 후 수동 확인을 남긴다.
  창 종료는 서버 작업 취소/완료를 뜻하지 않는다. 같은 NAS 주소/토큰으로 재열 때 진행 상태를 확인한다.
- 계좌 조회 OFF는 수동 주문 허용도 OFF로 만든다. 설정 PUT은 같은 profile/revision을 명시한다.
  응답을 못 받으면 버튼을 비활성화하고 GET을 재확인한 뒤 다시 CAS한다.
- 기존 매매일지 기본 계좌나 주문 대상을 자동 변경하지 않는다. 선택 계좌의 명시 context를
  PC client/worker에 전달하는 기능은 R4b에서 구현한다.

## 검증

최종 관련 회귀 253개 실행: 252개 통과, Windows POSIX 파일 권한 1개 생략, 종료 코드 0.
신규 client/UI/화면-서버 통합 24개와 기존 API·뉴스 설정·선택 계좌·계좌 조회·모의 owner·
인증 store/runtime·계좌 설정·종료 장벽·bundle/monitor drain·주문 수명·실행 원장·서버/DB 회귀를 실행했다.
실행 로그는 `tmp/r4a-final-regression.log`이며 Python 문법 6개 파일·관련 diff 공백 검사도 통과했다.
서버/Compose/Dockerfile의 R3g build 세 곳이 일치한다.
초기 명령에 없는 `test_system_ssl` 모듈을 잘못 포함한 loader 오류가 있었고 명령을 정정했다.
실제 TLS 인증서/hostname 검사는 신규 client 테스트 안에서 통과했다. 제품 테스트 실패는 없었다.
실제 NAS·공급자 네트워크·사용자 데이터/키·주문은 사용하지 않았다.
HTTPS transport fake와 임시 SQLite/vault의 실제 FastAPI 라우트 및 Qt 화면을 사용했다.

확인 범위: HTTP/redirect 차단, 키/오류 비노출, 구 NAS 입력 차단, 같은 요청 ID,
잘못된 계좌/프로필/revision 거절, 중복 클릭, GUI 응답, 창 파괴 중 worker 완료,
재열기, 적용 응답 유실 시 같은 상태 조회, 계좌 토글 CAS와 PUT 응답 유실 재확인.
통합 검증은 화면의 새 계좌 추가→prepare READY→apply ACTIVE→disable READY→apply ACTIVE,
신규 주문 OFF, 비활성화 후 같은 계좌와 OFF 설정, PC 설정 파일 미생성을 확인했다.

## 다음 한 단계와 배포

R4b: 매매일지 계좌 선택과 PC의 v3 조회/v2 모의 명령 context를 연결한다.
다른 선택의 늦은 응답/페이지 혼합을 차단하고 scoped 명령을 legacy v1로 우회하지 않는다.
R5 뉴스·R6 실전 인증 owner·R7 NAS 누적 동기화/실환경 검증은 남아 있다.
이번에는 서버 build/스키마 변경이 없다. 최신 NAS 누적 build는
`2026.09.15-runtime-credentials-r3-scoped-accounts-v1`이며 중간 NAS 재빌드는 요청하지 않는다.
모델 에스컬레이션 없음.

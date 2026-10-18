# R5d1 — NAS 뉴스·AI 공급자 키 입력 화면

2026-09-15. 로컬 구현 완료, NAS 동기화/배포 전.
이번 단계는 PC client/UI 변경이며 NAS 누적 build는 R5c
`2026.09.15-runtime-credentials-r5-ai-rotation-v1`을 유지한다.

## 구현

NAS 연결 설정의 `NAS 뉴스·AI API 키 관리` 메뉴에서 네이버/DART/OpenAI/Gemini/Claude를 선택한다.
기존 NasCredentialsDialog/CentralCredentialsClient를 공급자 고정 옵션으로 확장했다.
계좌 management/worker/HTTPS/operation 수명은 기존 경계를 사용하고 새 wrapper는 만들지 않았다.

- 네이버는 Client ID/Secret, DART·AI는 API Key만 입력한다. 서버 고정 global 프로필만 표시한다.
- 키는 빈 password 입력으로 시작한다. 전달 직전/창 종료/비활성화 준비 때 입력을 비운다.
  일반 PC 설정/DB/뉴스 미러/백업 저장 경로에 전달하지 않는다.
- global 화면에는 계좌 추가/조회/주문 UI와 계좌 설정 요청이 없다. 기존 모의계좌 기능은 유지한다.
- 공급자·같은 NAS 주소/토큰별 client를 재사용해 pending operation을 유지한다. 다른 공급자/NAS와
  요청 상태를 공유하지 않는다. 변경한 대상에는 새 client를 만든다.
- provider/profile/revision/operation ID/계좌 null/READY 검증 종류를 확인한다. NAVER/DART는 VERIFIED,
  AI는 UNVERIFIED도 명시 적용 가능하다. 인증 실패를 성공으로 바꾸거나 자동 apply하지 않는다.
- timeout 뒤 같은 prepare request ID/operation 상태를 확인한다. apply 응답 유실은 GET으로 확인하고
  새 apply를 자동 재전송하지 않는다. 실제 I/O는 기존 SettingsRequestWorker가 완료까지 소유한다.
- AI ACTIVE는 키 적용 완료다. validation이 없는 완료 receipt에도 인증 성공을 주장하지 않는다.
  현재 revision의 runtime_validation을 표시하고 실제 분석으로 인증을 확인한다.

## 검증

- 신규 client/Qt/API 연동 11개 통과. 다섯 공급자 폼을 실제 로컬 서버 prepare/apply/disable에
  연결했고, 각 공급자 계좌 null·키 비움·revision 0→1→2·계좌 설정 요청 0회·유료 AI 호출 0회를 확인했다.
- provider별 입력 계약, 잘못된 계좌/프로필/버전/ID 거절, timeout/같은 ID 재사용,
  창 종료/GUI 응답, 공급자/NAS별 pending 분리, 미검증 NAVER 거절과 AI receipt 표시를 검증했다.
- 변경 직후 기존 모의계좌/client/NAS 설정 회귀 32개 통과. 최종 관련 회귀 163개 모두 통과,
  종료 코드 0 (`tmp/r5d-regression.log`). 모의계좌/설정 허브/운영 client/AI·DART·NAVER owner/
  credential runtime/서버 route를 포함한다.
- Python 4개 구문/공백 정상. 가짜 데이터로 네이버/Gemini offscreen 화면을 렌더링해 배치를 확인했다.
  미리보기에는 시스템 한글 글꼴을 명시했으며 실제 앱의 글꼴 설정은 변경하지 않았다.
- 가짜 공급자·임시 SQLite/vault·offscreen Qt만 사용했다. 실제 NAS/유료 API/사용자 키·DB는 변경하지 않았다.

## 다음

R5d2 기존 운영값 확대가 남았다. 조건검색·해외시세 등 재시작 없는 운영 연결을 이어서 구현한다.
R6 실전 인증 owner와 R7 누적 NAS 동기화/실환경 검증도 남았으며 중간 재빌드를 요청하지 않는다.
새 서버/API/SQL/뉴스 품질/순위 경로 변경 없음. 모델 에스컬레이션 없음.

# R6b3a — 시세 역할 저장과 담당 계좌 보호

2026-09-15. 로컬 구현 완료. 실제 시세 담당 전환·실전계좌 실시간 수집 완료는 아니다.
누적 build: `2026.09.15-runtime-credentials-r6-market-profile-settings-v1`.
NAS 소스 동기화/배포 전이며 R7까지 중간 재빌드를 요청하지 않는다.

## 구현 계약

- 공통 시세 담당 market_profile_id와 기존 암묵적 계좌 조회 대상 legacy_real_profile_id를 분리했다. legacy는 nas-real-default로 고정하며 UI 계좌 선택·매매일지 binding을 변경하지 않는다.
- 기존 central_documents의 server_market_profile_settings/global/settings를 사용한다. 저장 필드는 두 profile ID와 revision뿐이다. 문서 미저장 읽기는 revision=0 기본값을 반환하고 DB에 쓰지 않는다. 손상은 복구 필요 오류로 닫는다.
- 내부 save 입력은 market_profile_id/expected_binding_revision과 별도 expected_revision이다. 활성 실전 profile·최신 real binding·활성 registry·해당 계좌의 active_profile_id를 한 트랜잭션에서 확인한다. monitor OFF는 시세 수집 역할과 독립이다.
- 역할 revision 충돌/잘못된 binding/미검증·비활성 후보는 저장하지 않는다. 동일 역할 저장은 revision/updated_at을 유지한다. SQLite BEGIN IMMEDIATE와 PostgreSQL 기존 credential-activation advisory lock을 사용한다.
- 담당 profile disable/계좌 연결 해제를 DB 저장 경계에서 거절한다. real owner도 현재 실행 담당과 저장된 담당의 disable을 vault commit 전에 거절한다. 이미 완료한 credential replay는 이후 역할/계좌 설정을 되돌리지 않는다.
- 인증된 GET /api/v1/settings/market-profile은 settings와 applied_revision=null을 반환한다. 손상은 409, 미인증은 401이다. PUT은 없으며 일반 content POST로 역할 문서를 쓰는 경로도 없다.

## 수정 범위와 구조

database.py의 공통 helper와 기존 SQLite/PostgreSQL 저장 메서드, real_runtime.py 준비 검증,
app.py 읽기 API, 기존 API 목록 회귀를 수정했다. 새 범용 manager/저장 계층은 만들지 않았다.
역할 정책에 필요한 파일은 기존 DB·owner·API 세 개이며, DB backend→공통 helper라는 기존 호출 깊이를 유지한다.
새 test_market_profile_settings.py는 저장/경쟁/보호/API 검증을 모았다. 스키마 v19와 기존 계좌 API를 유지한다.
시장 REST/WS·집계/hub·순위/TR 우선순위·real/mock 호출 한도는 변경하지 않았다.

PostgreSQL 검사 스크립트에는 검증된 임시 후보 역할 CAS/unchanged/stale/운영 역할 보존 네 조건을 추가했다.
이 검사는 역할 변경을 commit하지 않고 정상/예외 모두 rollback한다. 임시 SQLite adapter로 공통 SQL/rollback
논리를 검증했으며 실제 PostgreSQL advisory lock/동시 실행 검증은 R7에 남는다.

## 검증

- 역할 저장 신규 테스트 17개 포함 관련 58개 통과: tmp/r6b3a-target-final.log.
- 인접 회귀 283개 실행, 282 통과·Windows POSIX 권한 검사 1 skip, 실패 0·exit=0, 108.279초: tmp/r6b3a-regression-final.log.
- 회귀 범위: 역할/실전 owner/계좌 설정·신원/암호화 vault/인증 runtime/모의 owner/선택 계좌 API/재연결 장벽/계좌 query/DB/서버 API/REST broker/실시간 collector/조건검색.
- 최초 전체 회귀에서 새 GET이 API 목록 fixture에 빠져 1개 실패했다. 허용 목록에 GET을 추가한 뒤 같은 전체 회귀를 재실행해 통과했다. 새 API 인증·PUT 미제공·content 우회 차단은 별도 테스트로 확인했다.
- 두 SQLite 연결의 CAS 경쟁은 한 저장만 성공한다. 담당 변경이 기존 계좌 설정·binding을 유지하며, 담당 disable 거절 시 새 receipt/binding 변경이 rollback되는 것을 확인했다.
- Python 6개 구문·변경 공백·build 3곳 일치를 확인한다.
- 실제 키·사용자 DB·키움 API·계좌 주문·NAS·PostgreSQL 실행에는 접근/변경하지 않았다.

## 다음 단계

실제 역할 변경 owner/PUT과 재기동 반영을 연결해야 한다. DB 저장 revision은 연결 적용 완료가 아니다.
credential 후보/계좌 lock과 역할 전환 경계를 조정하고 vault 적용 revision·현재 binding을 재검증한다.
DB CAS만으로 vault commit/연결 전환 경쟁이 해결됐다고 가정하지 않는다. 기존 단일 시장 collector와
REST broker·집계 상태를 보존하며 새 담당 연결 준비가 끝난 뒤 적용 revision을 공개해야 한다.
기존 기본계좌/v2 암묵적 대상은 불변 legacy 역할로 유지한다.
실전계좌 잔고/체결/미체결 수집·운영 PUT, R6c PC 입력/계획된 재연결 표시와 R7 누적 배포/실환경 검증도 남았다.
이번 변경으로 실전 자동주문 지원을 추가하지 않았다. 모델 에스컬레이션 없음.

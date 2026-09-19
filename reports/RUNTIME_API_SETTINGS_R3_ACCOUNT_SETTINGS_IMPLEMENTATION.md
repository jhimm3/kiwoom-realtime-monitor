# 런타임 인증 R3d 계좌 설정 CAS

2026-09-15. 로컬 구현 완료, R3 전체 진행 중.
build `2026.09.15-runtime-credentials-r3-account-settings-v1`, R0/R1/R2/R3a/b/c 누적.

## 목적과 구현 범위

복수 계좌 활성화 전에 검증 계좌와 운영 profile의 단일 연결을 DB에서 확정한다.
계좌별 토글을 글로벌 env나 화면의 현재 선택 계좌와 연결하지 않는다.
기존 database.py의 공통 SQL helper와 SQLite/PostgreSQL store를 확장하고 별도 manager를 추가하지 않았다.
설정 조회·저장에 필요한 파일은 하나이며 SQL 정책을 두 store에 중복 구현하지 않았다.

## 계약

- `load_account_settings(scope)`는 검증된 kiwoom real/mock scope만 허용한다.
  초기 읽기는 profile=null·두 토글 OFF·revision=0이며 문서를 만들지 않는다.
- `save_account_settings(value, expected_revision=...)`의 value는 scope, active_profile_id,
  monitor_enabled, mock_order_enabled만 받는다. unknown 필드·비bool 토글·bool revision은 거절한다.
- profile은 같은 provider/환경의 active profile과 최신 verified binding을 요구한다.
  다른 계좌 binding·draft/다른 provider profile은 거절한다. real에 mock 주문을 켤 수 없다.
- expected revision이 다르면 ACCOUNT_SETTINGS_REVISION_CONFLICT다. unchanged 저장은
  revision/updated_at을 유지한다. 같은 계좌에 다른 active_profile_id를 덮으면 ACCOUNT_PROFILE_CONFLICT다.
- profile 해제는 두 토글 OFF일 때만 가능하다. 실제 runtime 해제/재연결 owner는 다음 단계다.
- 모의 activation 최종 커밋은 binding·profile·activation·초기 설정을 한 트랜잭션으로 저장한다.
  초기 monitor ON·주문 OFF, 같은 profile 키 갱신은 토글/설정 revision을 유지한다.
  중복 profile이나 문서 손상으로 설정 저장이 실패하면 binding/profile/activation도 롤백한다.
- 완료 replay는 이후 OFF/해제한 설정을 되돌리지 않는다. 이전 버전의 완료 activation에
  설정 문서가 없을 때만 최초 기본 설정을 만든다. 기존 문서 손상은 복구 필요로 닫는다.
- authenticated GET은 settings와 applied_revision=null을 반환한다. 저장 설정이 실제 runtime
  적용을 의미하지 않는다. PUT은 owner의 실제 drain/적용 확인을 연결하는 후속 단계다.
- 일반 content API에서 이 collection의 쓰기를 허용하지 않는다.

## 검증

별도 테스트에서 기본 OFF/비저장 읽기, 토글 보존, 완료 replay, unchanged timestamp,
scope/provider/binding 검증, bool/unknown 필드, real/mock 분리, 문서 손상 fence,
중복 activation 전체 롤백과 두 독립 SQLite 연결의 CAS/활성화 경합을 확인한다.
PostgreSQL placeholder 분기도 동일 SQL helper 테스트로 검증한다. 이는 실제 PostgreSQL 실행을 대체하지 않는다.
인증 GET/401/404/400/405, applied_revision=null, content 쓰기 차단을 HTTP 회귀로 확인한다.
실제 공급자 키·계좌 주문은 사용하지 않고 임시 DB와 생성한 UUID만 사용한다.

check_postgres_integration.py에 동일 CAS·replay·중복 rollback 검사를 추가했다.
실제 NAS 실행은 R7이다. 실패 때 생성된 duplicate profile과 계좌 설정까지 테스트 데이터 정리에 포함한다.
최종 회귀 291개 중 290 통과, POSIX 권한 1개는 Windows에서 생략했고 종료 코드 0이다.
초기 HTTP 테스트의 빈 content batch가 본문 검증에서 422로 거절돼 유효한 batch로 수정했다.
기존 public route 계약 테스트는 추가한 GET을 예상 목록에 반영했다. 제품 코드 우회는 하지 않았다.
관련 Python 문법/diff 공백 검사와 서버/Compose/Dockerfile build 3곳 일치도 확인했다.

## 다음 부분

계좌별 owner가 기존 bundle/credential revision과 설정 revision을 함께 소유해야 한다.
동일 계좌는 client/rate history·run을 재사용하고 신규 계좌는 별도 run과 실제 주문 OFF로 연결한다.
prepare 중 확인한 profile/scope가 apply까지 유효한지 계좌 lock/설정 revision으로 재확인한다.
파일 commit 전에 중복 route를 막고, 파일 commit 뒤 DB/runtime 실패는 RECOVERY_REQUIRED로 보존한다.
실제 lease/초기 계좌 읽기 완료 뒤 ACTIVE/applied_revision을 공개하고 설정 PUT·scoped query/order를 연결한다.
현 단계는 그 선행 DB 계약이며 컨테이너 재시작 없는 키 갱신 자체는 아직 미연결이다.
중앙 schema v19·기존 단일 계좌 주문 API 의미를 유지한다. NAS 동기화/재빌드는 하지 않았다.
현재 단계 모델 에스컬레이션 없음.

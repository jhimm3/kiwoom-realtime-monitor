> **과거 기록** · 원래 경로: `reports/RUNTIME_API_SETTINGS_R1_IMPLEMENTATION.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# 런타임 인증 R1 구현 결과

2026-09-15. 로컬 구현 완료, NAS 소스 동기화·실제 키 이관·재빌드 전.
build: `2026.09.15-runtime-credentials-r1-v1`.

## 구현 경계

- `central_server/credential_store.py`: AESGCM 256비트 master, 매 저장 새 12바이트 nonce,
  provider/profile/schema/revision 인증 데이터, 암호화 active와 이전 revision 한 벌.
  임시 파일→fsync→replace→Linux 디렉터리 fsync. 프로세스 독점 소유와 thread lock을 사용한다.
  심볼릭 링크·다중 hard link와 Linux 느슨한 파일 권한은 거부한다.
- 초기 키가 있고 저장 이력이 없을 때만 env를 이관한다. 최초 파일 전에 중앙 문서에
  initialized 표식을 저장한다. 파일 분실·중앙 revision보다 오래된 파일·master 분실은
  RECOVERY_REQUIRED다. 새 master를 생성하거나 env로 돌아가지 않는다.
  disabled는 env보다 우선하며 빈 입력은 삭제가 아니다.
- 기동 시 저장 키와 대기 활성화 metadata를 합성한다. 파일 커밋 뒤 DB 쓰기가 중단되면
  같은 operation을 완료한다. 복구 실패는 해당 공급자만 격리한다.
- 신원 준비 함수는 검증 UUID만 등록하고 binding을 추가하지 않는다. 기존 bind 계약은 유지한다.
  준비 취소 시 미사용 UUID가 남을 수 있으나 조회·주문 권한이나 실행 세대를 만들지 않는다.
- 중앙 v19 프로필·활성화 원장은 비밀을 저장하지 않는다. 기존 environment가 하나인
  binding profile만 초기 등록하며 binding·일지·시장 행은 수정하지 않는다.
  binding 증가와 활성화는 한 트랜잭션이다. 동일 operation/request는 기존 결과를 반환하고
  기존 profile을 다른 account_ref로 변경하려 하면 ACCOUNT_CHANGED다.

## 계약과 다음 단계

입력: provider/profile, 완전한 키 필드, expected_revision, 선택적 내부 activation metadata.
출력: 내부 CredentialRecord 또는 고정 오류 코드. payload는 repr에서 제외하고 외부 읽기 API는 없다.
activation DB에는 request_id와 master 기반 keyed digest만 남긴다. OAuth 토큰은 저장하지 않는다.

secret 경로가 없는 기존 배포는 환경 기반 동작을 유지한다. 새 Compose는 `/app/secrets`를 지정한다.
검증된 mock 활성화가 저장되면 env ref/run 없이 합성한다. 처음 설치에서 활성화·ref/run도 없으면
모의 모니터/주문을 켜지 않는다. 실제 계좌 검증·새 프로필 추가·실행 중 키 교체는 R2/R3에서 연결한다.
복수 계좌 runtime·고유 active route·시세 프로필 선택·UI는 이 단계의 완료 범위에 포함하지 않는다.

파일 commit 이후 DB 실패는 이전 키로 자동 rollback하지 않는다. 새 runtime 공개 전 복구를
완료하는 적용 장벽은 R2 책임이다. 저장 함수의 실패를 반드시 미커밋으로 해석하면 안 된다.
legacy 주 실행 환경이 mock이면 모의 모니터 키와 별도 파일 `nas-main-mock-default`로 보존한다.

## 검증

- 167개 회귀 실행: 166 통과, POSIX 권한 1개 Windows에서 생략, exit=0.
- roundtrip/nonce/이전 revision, 변조·profile 복사·master 분실, 파일 삭제와 env 재이관 방지,
  disabled/불완전 키, concurrent revision, replace 실패, commit 뒤 DB fence 실패 복구.
- 활성화 멱등성·다른 신원 거부·DB trigger 실패 binding/profile 롤백·대기 활성화 재기동,
  준비 시 binding 없음, legacy 주 mock와 모니터 키 분리, startup 실패 시 vault 해제.
- v18 fixture 보존/upgrade, v19 실패 시 v18 rollback, 공통 SQL placeholder 방언 대조.
  기존 설정 GUI·뉴스·AI·후보·중앙 조회 회귀를 함께 실행했다.
- 실제 PostgreSQL과 NAS Linux 권한은 미실행. 컨테이너용 기존 통합 검사에 임시 생성 키의
  vault roundtrip/권한/활성화 멱등성 검증을 추가했다. R7에서 실행한다.
- cryptography 46.0.7은 워크트리 `tmp/r1-test-deps`에만 설치했다. 원래 앱 venv는 변경하지 않았다.

## 배포·복구

`deploy/synology/server-secrets`는 Git·Docker context·일반 백업에서 제외하고 NAS 동기화 때 보존한다.
Linux 디렉터리 0700, 파일 0600. 복구 백업은 별도 접근 제한 경로에 master와 암호문을 함께 보호한다.
일반 DB 백업에는 initialized/revision과 비밀 없는 원장만 포함한다. DB 복원 시 vault와 대조하고
이전 파일로 기동하지 않는다. DB와 vault 일부만 임의로 복원하지 않는다.

한 기능을 이해하는 데 필요한 파일은 암호화 상태·파일 수명·복구를 맡는 한 파일만 늘었다.
신원 준비 함수 호출 깊이는 한 단계 늘지만 기존 검증 로직을 재사용하고 별도 wrapper는 만들지 않았다.
현재 단계 모델 에스컬레이션: 없음. 다음 단계는 설계 문서의 R2 기준으로 판단한다.

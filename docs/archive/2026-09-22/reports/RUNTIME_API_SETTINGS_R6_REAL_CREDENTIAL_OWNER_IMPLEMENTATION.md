> **과거 기록** · 원래 경로: `reports/RUNTIME_API_SETTINGS_R6_REAL_CREDENTIAL_OWNER_IMPLEMENTATION.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# R6b2 실전 인증 owner/API·복수 계좌 과거 조회 — 로컬 구현 결과

2026-09-15. 누적 build `2026.09.15-runtime-credentials-r6-real-owner-v1`.
기존 R0~R5·R6a·R6b1 누적 소스를 보존했다. NAS 소스 동기화·배포 전이며 R7까지 중간 재빌드는 요청하지 않는다.

## 완료 범위

`real_runtime.py`의 RealCredentialOwner를 기존 credential runtime의 kiwoom_real hooks와
HTTPS prepare/apply/status API에 연결했다. vault·계좌 신원 HMAC·real 주 실행 환경이 있는 설치에 적용한다.
기존 vault 없는 설치와 주 시세 환경 mock 설치의 조립/호환 경로는 유지한다.

시세 담당은 기존 `nas-real-default`를 유지한다. 기존 client/broker/collector/hub/초·분봉 누적기를
재생성하지 않고 R6a의 실제 작업 drain·재연결 경계를 사용한다. 추가 실전계좌는 profile별
read-only client/broker/AccountQuerySessionManager를 갖고 v3 선택 계좌의 kt00007/kt00015를 처리한다.
추가 계좌 등록·갱신·선택 조회가 시장 WS를 재연결하거나 TOP20/뉴스 수집을 복제하지 않는다.
비시세 계좌 과거 조회와 인증 준비에는 공통 동시 실행 2개를 적용하며 실시간 순위는 이 제한에 넣지 않는다.

이는 인증/과거 조회 단계의 완료다. 시세 역할 CAS 변경, 실전계좌별 실시간 체결/잔고/미체결 수집과
계좌 운영 PUT, PC 실전 키 입력/계획된 재연결 표시, NAS 실환경은 아직 완료하지 않았다.

## 입력·출력과 적용 계약

- 기존 provider/profile/request_id/expected_revision/replacement → 준비 operation. 후보는 ka00001로
  확인하고 원계좌번호를 HMAC 신원과 registry UUID로 변환한다. 실제 키/토큰/원계좌번호는 응답에 넣지 않는다.
- 기존 profile이 다른 계좌를 반환하면 ACCOUNT_CHANGED. 같은 계좌의 다른 활성 profile은
  ACCOUNT_PROFILE_CONFLICT. 준비에서 거절하고 기존 vault/binding/조회 경로를 교체하지 않는다.
- 같은 계좌 갱신은 account_ref/run_id를 유지하고 binding revision만 확정된 적용에 따라 증가시킨다.
  동일 client 요청 잠금/호출 간격을 이어받으며 신규 계좌는 검증 probe 이력과 별도 잠금을 받는다.
- apply는 기존 query 실제 작업 → 시장 담당의 socket/token/저장 작업 → broker HTTP 작업을 drain한다.
  신규 query 접수와 broker 일반 조회를 차단한 후 vault→DB(binding/activation/settings)→runtime 순으로 적용한다.
  후보 준비 뒤 계좌 설정 revision이 달라지면 commit 전에 거절한다.
- commit 전 실패는 정상 이전 context의 query cursor/키를 유지해 복귀한다. 이전 상태가 사용 중지이면
  disabled revision을 유지하며 키를 복구하지 않는다. keyless/이전 실패 context는 차단을 유지한다.
- commit 후 실패는 RECOVERY_REQUIRED이며 이전 key/token으로 자동 rollback하지 않는다.
  명시적 새 준비는 기존 client가 paused인 상태에서 후보만 검증하고 일반 조회는 계속 차단한다.
- 비시세 profile disable은 기존 binding/일지 이력을 유지하고 키·토큰을 비운다. 시세 담당 disable은
  MARKET_PROFILE_REQUIRED로 거절한다. 대체 시세 담당 적용은 R6b3의 별도 역할 CAS로 구현한다.
- v3 목록/조회는 admitted 실전 context만 사용한다. 미준비 profile은 PROFILE_RUNTIME_NOT_READY이며
  다른 계좌/잘못된 binding revision은 ACCOUNT_CONTEXT_MISMATCH다. v2 기본 계좌 대상은 그대로 유지한다.
- 적용 시 기존 query manager의 cursor를 폐기한다. 추가 계좌별 manager/lock/큐를 분리하고 새 키 조회가
  이전 키의 broker 메모리 캐시를 재사용하지 않도록 기존 generation 전환을 사용한다.

## keyless 기동과 복구 보완

키가 없는 설치에도 기본 실전 profile 메타데이터와 동일 market client/broker/collector를 조립한다.
owner start는 market broker/collector를 paused로 유지해 토큰 없는 자동 수집을 시작하지 않는다.
후속 명시 키 적용으로 같은 market broker를 활성화한다. 기존 ENV 최초 이관 프로필도 ka00001로
재검증한 신원에만 연결한다. 추가 저장 프로필은 background bootstrap하며 한 계좌 실패는 그 profile에 기록한다.

`client.py.prepare_drained_credentials`는 같은 요청 잠금/호출 간격의 private transport snapshot으로
명시 후보만 검증한다. active client의 key/token/generation과 paused 상태를 공개 변경하지 않는다.
`rest_broker.py`에서도 해당 작업을 중앙 큐로 처리한다. 물리 drain 완료와 activation 작업 완료를 요구하며,
완료된 실패 activation 참조는 명시 재준비에서만 정리한다. live client에는 이 경로를 허용하지 않는다.
따라서 복구를 위해 일반 조회를 열어 과거 키의 순위 조회/토큰 refresh를 허용하는 우회는 없다.

실전 계좌 GET의 applied_revision은 계좌 실시간 수집 정책이 구현되기 전 None이다.
비시세 disable의 실제 OFF 적용은 해당 revision을 확인할 수 있다. monitor ON 영속 설정과
실시간 account WS 수집 완료를 혼동하지 않는다. operation ACTIVE는 REST/binding 적용을 확인하며
시장 WS의 모든 REG 승인/수신 연속성/PC planned reconnect 완료는 R6c에서 별도 확인한다.

## 검증 결과

- 신규 owner 회귀 14개: 동일 market transport/run/scope 보존·cursor 폐기·복수 실전계좌 분리·
  후보 오류/계좌 변경/중복 profile·precommit 복귀·postcommit DB/activation 실패와 명시 복구·
  disable 및 시세 담당 보호·HTTP 대기자 취소 뒤 실제 read 완료·keyless 등록/기동 이관·
  drained 후보의 active token/generation/rate history 보존·HTTPS 인증/prepare/apply/v3 선택 계좌.
- 재활성화 abort에서 disabled revision이 사라지는 경우를 추가 회귀로 재현하고 최소 보완했다.
  수정 전 증거 `tmp/r6b2-extra-before.log`(14개 중 1개 실패).
- 최종 인접 회귀 266개 실행: 265개 통과, Windows POSIX 권한 1개 skip, 실패 0, exit=0, 103.813초.
  인증 저장/runtime·모의 owner/scoped API·선택 계좌·REST/client/실전 재연결 장벽·계좌 query·
  중앙 DB/서버·실시간 collector/조건식을 포함한다. 로그 `tmp/r6b2-regression-final.log`.
  예상 DB/설정 실패 주입 로그를 포함하며 이는 회귀 실패가 아니다.
- Python 6개 AST 구문/공백·build 3곳 일치 확인(`tmp/r6b2_verify.py`), 관련 diff 공백 검사 완료.
- 실제 키움 API/주문/키/사용자 DB/NAS/PostgreSQL 변경 및 배포 없음. 실환경 동시 세션/한도/공백 측정은 R7에 남긴다.

## 구조와 남은 계약

독립 계좌 context·후보/계좌 lock·admission·기동/종료 수명이 있어 owner 파일 하나를 추가했다.
기존 runtime과 client/broker/query/collector를 재사용하며 단순 전달 manager나 범용 provider 계층은 없다.
인증 기능 이해에 필요한 핵심 파일은 기존 runtime/app/broker/client에서 owner 포함 5개로 늘지만
profile별 독립 상태/수명이 owner 한 곳에 모인다. broker 복구는 기존 큐 작업 경로에 후보 phase 하나만 추가한다.
새 SQL/schema/주문 endpoint/시장자료 TR/자동 PC fallback은 없다.

다음 R6b3:

1. market_profile_id와 legacy 기본 역할을 별도 영속 문서/revision으로 소유하고 CAS 역할 전환을 구현한다.
   화면 계좌 선택은 시세 담당 변경으로 해석하지 않는다. 기존 시장 WS 한 개와 계좌별 broker 한도를 보존한다.
2. 실전계좌 체결/잔고/미체결 수집을 scope별로 연결하고 시세 담당은 같은 시장 WS에서 account event를 분배한다.
   비시세 계좌는 해당 계좌 세션만 쓰며 보유/매수 종목 수집 범위는 union으로 공유한다.
3. 실전 계좌 운영 PUT·applied revision/기동 대기 상태·활성 context/실시간 상태를 정확하게 공개한다.

이후 R6c PC 입력/계획된 재연결·등록 승인/deadline와 R7 NAS 누적 배포/실환경을 진행한다.
모델 에스컬레이션: 없음. 실행 모델 변경을 수행했다고 주장하지 않는다.

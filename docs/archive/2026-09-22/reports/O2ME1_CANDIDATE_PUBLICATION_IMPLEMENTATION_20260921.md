> **과거 기록** · 원래 경로: `reports/O2ME1_CANDIDATE_PUBLICATION_IMPLEMENTATION_20260921.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# O2-Me1 동결 후보 게시 구현 보고

구현일: 2026-09-21. 로컬 누적 build: `2026.09.21-o2me1-candidate-publish-v1`.

## 구현 범위

- 기존 `final_candidate_spec_hash()`의 `final_candidate/v1` JSON을
  `final_candidate_spec_document()`로 노출했으며 hash 내용은 바꾸지 않았다. 고정 implementation hash를
  사용한 golden fixture가 기존 canonical identity를 보호한다.
- CR3 final execution/run/report에서 경로·원격 코드·pickle·비밀 없이 등록 family, 정규 parameters,
  execution/session model, scientific hash, final 계보와 OOS 근거만 묶는
  `mock_automation_candidate_package/v1`을 추가했다.
- final 시작 전에 동결해야 하는 최소 거래수·활동일·순손익과 최대 drawdown 정책을 별도 내용 주소형
  문서로 만들었다. 수치 미정, 늦은 동결, 누락 지표, OOS 미합격과 기준 미달은 receipt를 BLOCKED로 만든다.
- NAS의 Bearer 인증 전용 POST는 256 KiB 크기, version, family/정규 필드, 모든 hash와 계보,
  현재 PC/NAS scientific implementation hash, 현재 검증된 mock binding을 다시 확인한다.
- package/policy/receipt는 일반 콘텐츠 allowlist 밖의 비공개 컬렉션에 불변 저장한다. 같은 내용 재시도는
  unchanged이며 충돌 문서는 거절한다. 후보별 정책을 package hash key로 먼저 고정하므로 중간 중단 뒤
  낮춘 정책으로 바꿔 재시도할 수 없다. 게시 응답의 `orders_started=false` 계약과 기존 O1 transport 0회
  경계를 유지한다.
- admission은 이제 로컬 연구 DB의 `COMPLETED`만 보지 않고 저장된 package와 같은 계좌의 ELIGIBLE
  receipt, 완결된 policy, operating spec의 final batch/run/result 계보를 확인한다.

## 검증

- package/policy/receipt 직렬화, 별도 hash, TBD·늦은 정책·기준 미달 BLOCKED, 변조·과대 payload,
  binding 충돌, 동일 게시 멱등, 인증 API 401/저장/재시도/capability를 검증했다.
- 실제 final holdout 실행 결과에서 package와 BLOCKED receipt를 만드는 경로를 검증했다.
- 기존 admission·중지/복구/gate, final preparation/execution/CLI, O1 repository/runtime/order,
  중앙 API/DB 회귀까지 총 200개가 통과했다.
- PC와 NAS가 공유하는 scientific implementation hash 함수가 같은 값을 반환함을 확인했다.

## 배포 상태와 다음 단계

NAS 동기화·이미지 빌드는 하지 않았다. 종료 설계에 따라 O2-Me2 실제 위험 근거와 계좌 owner 연결,
O2-Me3 지속 runner/UI까지 마친 뒤 V1에서 누적 배포한다. 게시만으로 자동주문은 시작되지 않는다.

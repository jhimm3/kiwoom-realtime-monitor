> **과거 기록** · 원래 경로: `reports/CR2C3C2_COMPLETED_INPUT_RECOVERY.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# CR2c3c2 — 완성 연구 자료 등록 복구와 보호 보관

2026-09-16. CR2c3c1의 미완성 임시 정리에 이어, 게시됐지만 연구에 등록되지 않은 완성 자료를 복구한다.

## 확인된 문제

기존 discovery는 NAS 준비 → 폴더 스캔 → 자료 등록 순서였다. 완성 폴더 게시 후 등록 전에 중단되면
그 파일이 용량을 차지한다. 다음 준비가 용량 상한에 막히거나 NAS 접속에 실패하면 스캔 전에 반환해
기존 완성 자료도 등록하지 못했다. 파일 유실이 아니라 등록 순서의 문제다.

수정 전 순서만 메모리에서 복원한 회귀 두 개는 모두 `registered=0`으로 실패했다.
`tmp/cr2c3c2-old-order.log`에 용량 부족/NAS offline 재현을 기록했다. 실제 소스는 되돌리지 않았다.

## 입력과 출력 계약

- 기존 enabled/due source, 동결 template/spec/scope, live worker claim과 명시 root를 그대로 받는다.
- 처음부터 활성 대기열이 가득 찬 NAS source는 기존처럼 파일/연결 설정/NAS 조회 없이 대기한다.
- 여유가 있으면 같은 상위 폴더의 완성 manifest 디렉터리를 먼저 검증·등록한다.
  최대 10,000 상위 entry/1,000 완성 폴더/manifest 1MiB 및 기존 CPU/RSS/30초/취소 예산을 유지한다.
- NAS source root의 경로 변경/리디렉션과 baseline manifest 변조를 로컬 등록 이전에 확인한다.
- 등록된 manifest는 hash만 확인하고 본문 전체를 재로딩하지 않는다. 미등록 자료는 기존 reader로
  scope/hash/fingerprint를 검증하며 enqueue의 worker/실행 의도 fence로 jobs/budget/acceptance를 원자 저장한다.
- 새 등록 건수만큼 활성 job 수를 반영한다. 대기열이 차면 새 NAS 준비를 건너뛰고 waiting_backlog를 반환한다.
- 여유가 남으면 기존 NAS 설정/준비 경로를 사용한다. acceptances를 다시 읽어 복구한 fingerprint도 전달한다.
- NAS 준비 이후에는 반환된 prepared 경로만 검증·등록한다. 상위 폴더 전체 재스캔/unchanged 이중 집계는 없다.
- prepared signature는 자료 등록 이후에만 확인 처리한다. 등록 실패/취소/용량 부족 시 새 signature를 확인하지 않는다.
- 서버 접속 실패로 source backoff가 생겨도 그 전에 성공한 로컬 등록은 유지한다. 직접 키움 fallback은 없다.

## 보호 보관과 변경 경계

DB v16의 기존 jobs/acceptances/storage_operations를 재사용한다. 신규 테이블/별도 catalog/Manager는 없다.
준비 원장의 PUBLISHED는 파일 게시 사실이며 연구 등록 성공은 acceptance/job으로 구분한다.
등록 실패한 완성 자료도 다음 허용 scan에서 다시 확인한다. 동결 범위 밖의 완성 자료는 등록하지 않고 보존한다.

완성 자료는 참조 여부나 연구 완료 여부와 무관하게 자동 삭제하지 않는다. 완료된 job의 입력도 기존
전체 캠페인 참조 진단에서 보호된다. 임시 정리는 완성 manifest를 발견하면 보존하므로 미등록 완성 근거도 보호한다.
cap이 꽉 차면 새 준비를 기다린다. 파일 이동/압축 archive·완성 자료 삭제·외부 일지 DB 참조 전수 스캔은 추가하지 않는다.
NAS 원시자료 보관 정책과는 별개이며 사용자 파일/실제 NAS/계좌/API 인증키를 변경하지 않았다.

변경 전후 모두 기능 실행은 기존 worker의 discovery → 기존 repository enqueue 경로다.
같은 함수 안에서 등록 루프만 재사용하므로 단순 전달 계층이나 호출 파일 수가 늘지 않는다.
NAS 경로 검증은 기존 research_storage helper를 재사용한다. 공개 API/요청 형식/DB migration은 그대로다.

## 검증

- 새 회귀 8개: 10.547초, OK, native exit 0.
  용량 부족/접속 실패·재시작/등록 후 대기열 상한/동결 baseline/일시정지/
  완료 참조 보호/다른 범위 보존/등록된 manifest 변조를 검증했다.
- 기존 호출 순서 복원 재현 2개: 예상된 등록 누락 실패 2개, 재현 도구 exit 0.
- 관련 전체 회귀 284개: 169.855초, OK, native exit 0 (`tmp/cr2c3c2-regression.log`).
  자료 준비/용량/임시 정리/worker/운영 예산/캠페인/리플레이/bundle/자원/유한 탐색/offscreen UI를 포함한다.
- Python AST와 변경 문서 공백 검사 9개 파일 통과. tracked 변경의 `git diff --check` 통과.
- 임시 DB/파일·가짜 NAS·offscreen Qt에서만 검증했다. 실제 NAS 전체 자료/장시간 운영 검증은 수행하지 않았다.

## 남은 범위

다음은 CR3a 개발·최종 검증 구간 분리의 최소 계약이다. 새 날짜 자동 확장과 자동 최종평가/가설 생성은
아직 연결하지 않는다. CR2 전체 또는 24시간 자동 가설 연구 완료로 판정하지 않는다.
실제 NAS 전체 규모 장시간 부하 검증은 V1이다. 이번 PC 변경은 NAS 소스 동기화/재빌드가 필요 없다.
모델 에스컬레이션 없음.

# CR2c3c1 — 오래된 미완성 연구 임시 자료의 안전한 정리

2026-09-16. CR2c3b에 이어 폐기 가능한 미완성 임시 자료만 정리한다. 완성 데이터 보관 정책 완료는 아니다.

## 범위와 입출력

- 기존 NAS 자동 source worker가 자료 준비 전에 호출한다. GUI/NAS 수집기/키움 TR 경로는 추가하지 않는다.
- 현재 enabled/NAS-auto source root와 준비 원장의 source/op ID/경로가 일치한 임시 폴더만 대상이다.
- operation 시작·종료와 marker 생성 시각 모두 24시간 이상 지났어야 한다. PREPARING/ABANDONED의
  기존 작업자가 아직 live owner이면 보호한다. 일시정지/새 worker/변경된 source root는 재확인한다.
- 준비 원장과 일치하는 preparing marker가 필수다. operation ID 없는 이전 임시 자료는 소급 채택하지 않는다.
- staging root marker, payload 및 days/ISO-date 디렉터리, observations/theme JSONL 부분 파일만 허용한다.
  manifest.json이 어디에든 있거나 알 수 없는 파일/디렉터리/링크/reparse point가 있으면 모두 보존한다.
  marker 4KiB/전체 최대 64 entry를 넘는 자료도 보호한다. 데이터 본문 전체를 읽지 않는다.
- 반환 deleted/missing/protected/yielded/errors는 작업 수/진단이다. missing은 실제 삭제나 회수 용량으로 추정하지 않는다.
- worker 결과 input_discovery.staging_cleanup에 넣는다. source당 최대 20 작업/pass다.

## 참조와 중단 처리

삭제 intent READY를 먼저 커밋한다. 파일 삭제 직전에 BEGIN IMMEDIATE에서 worker/의도/current root/경로와
모든 캠페인의 jobs/acceptances 및 준비 입력/다른 PREPARING staging 참조를 다시 확인한다.
완료/다른 캠페인/parent/descendant 참조도 보호한다. marker hash/파일 목록도 다시 검사한다.

약 0.25초 cooperative checkpoint 예산에서 알려진 파일만 unlink하고 디렉터리는 rmdir한다.
재귀 rmtree나 root 전체 삭제는 사용하지 않는다. 각 최종 경로가 명시 root 내부인지 확인하고 링크는 따라가지 않는다.
caller heartbeat/취소는 DB 쓰기 fence 밖에서만 실행하므로 같은 DB heartbeat 재진입 잠금이 발생하지 않는다.
OS 파일 호출 중 hard timeout은 아니다. 기존 source CPU/RSS/취소/30초 예산도 fence 밖에서 유지한다.

marker를 마지막에 제거한다. 부분 중단 시 READY/marker hash로 이어 처리하며 marker 제거 후 남은 빈 디렉터리도
해당 intent에 한해 재개한다. 없어진 경로는 MISSING이다. 표시 변경/새 파일은 PROTECTED로 보존한다.
파일 잠김/IO 오류는 FAILED/실패 횟수와 60초부터 최대 1시간 backoff다. 작업당 한 원장 행을 갱신한다.
기존 source/연구 실패 횟수에는 넣지 않는다. 오류 내용은 예외 종류만 기록해 경로/상세를 복사하지 않는다.

## 저장 경계

연구 DB v16은 staging_cleanups 테이블/조회 인덱스만 추가한다. v1~v15 migration 함수와 기존 행은 변경하지 않았다.
operation_id PK/FK, source FK, path/marker_hash/state/failure_count/next_retry_at/bytes_expected/reason/updated_at이다.
기존 파일 수/호출 경로는 worker→Repository→research_storage 세 모듈이며 새 Manager 계층을 만들지 않았다.
PC 로컬 운영 원장이므로 NAS/Google/공통설정 백업에 추가하지 않는다. 인증키/토큰을 넣지 않는다.

완성 manifest는 실제 파싱 성공 여부와 무관하게 보존한다. 따라서 유효한 완성 연구·일지 근거를 삭제하지 않는다.
외부 매매일지 DB의 모든 링크를 조사한 것으로 보고하지 않는다. 그런 보호 보관과 완성 orphan 등록 복구는 CR2c3c2다.
기존 무표시 파일/사용자 파일/완성 export/보고서/DB/NAS 원시 데이터는 이번 삭제 대상이 아니다.

## 검증

임시 테스트 폴더/DB·가짜 NAS API만 사용한다. 실제 NAS/사용자 DB/앱 데이터/주문/접속키는 사용하지 않았다.
오래된 부분 파일만 삭제/원장 idempotency, young/live 보호, daily manifest/사용자 파일/이전 표시 보호,
완료/다른 캠페인 참조와 preflight 후 새 참조, pause/marker 변경/redirect/root 이탈/entry cap 보호를 검증한다.
caller heartbeat의 DB 재진입, IO backoff, 부분 삭제 yield/marker 제거 후 중단 재개,
root 변경 시 이전 폴더 미삭제, NAS 준비 전 정리, v15 원장 전체 필드 보존도 검증한다.
집중 회귀 **39개 / 28.624초 / OK / exit 0**.
삭제 직전 race/경로/표시/entry cap 보완을 포함한 최종 전체 연구 회귀
**276개 / 158.776초 / OK / exit 0** (`tmp/cr2c3c1-regression.log`).
변경 Python 10개와 새 보고서의 AST/공백 검사 및 관련 git diff --check도 통과했다.

## 남은 범위

CR2c3c2: 완성 orphan의 등록 복구/보호 보관. 완성 데이터만으로 cap이 차면 아직 새 준비를 보류한다.
새 날짜/최종평가/자동 가설/모의 주문은 이번에 바꾸지 않았다. 실제 NAS 대량 자료/PC 24시간 운용은 V1이다.
NAS 재빌드 필요 없음. 모델 에스컬레이션 없음.

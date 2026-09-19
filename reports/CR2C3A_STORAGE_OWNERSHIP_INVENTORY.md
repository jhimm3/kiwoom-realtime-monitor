# CR2c3a — PC 자동 생성 연구 자료의 소유 표시와 보관 진단

2026-09-16. CR2c3 전체 완료가 아닌 첫 안전 단계다. 실제 삭제와 NAS 배포는 없다.

## 구현 계약

- 새 `.nas-preparing-*` 임시 폴더와 새 `nas-<source_id 앞12자리>-<fingerprint>` 완성 폴더에
  `.research-storage.json`을 exclusive create한다. 기존/사용자 폴더에 표시를 소급 작성하지 않는다.
- sidecar version은 pc_research_storage/v1, source_id/kind/UTC created_at/fingerprint/manifest_hash다.
  인증키/접속 설정을 넣지 않으며 manifest/관측 파일/evidence hash를 변경하지 않는다.
- `ResearchRepository.inspect_campaign_input_storage(source_id)`는 전체 캠페인의 jobs와 acceptances를
  보호 참조로 조회한다. COMPLETED/실패/다른 캠페인도 제외하지 않는다. parent/descendant 경로 겹침도 보호한다.
- `inventory_research_storage`는 기본 최대 10000 filesystem entry, worker 호출은 최대 1000 entry다.
  작은 marker(4KiB)와 manifest(1MiB)만 읽고 데이터 본문 전체를 읽지 않는다. 기존 CPU/RSS/취소 checkpoint를 따른다.
- NAS 자동 source 처리 뒤 기존 worker 결과 input_discovery.storage_inventory에 진단을 포함한다.
  GUI에 새 파일 읽기/네트워크 경로를 추가하지 않았다. 진단 결과는 보관 원장이 아니다.
- 반환: source_id/complete/total_bytes/entries. entry는 path/kind/bytes/protected_reasons다.
  root/entry 모두 deletion_authorized=false. IO/entry cap/링크 때문에 complete=false면 용량은 확인한 부분만 의미한다.
- 무표시/다른 source/손상/이름 변경 자료는 unverified_ownership, symlink/Windows reparse point는
  redirected_path, 참조 겹침은 research_reference다. 임시 자료는 staging_requires_lease_check로 보호한다.

## 검증 범위

임시 DB/파일과 가짜 NAS API/offscreen Qt만 사용했다. 실제 NAS·사용자 DB·토큰·키움 TR·주문은 사용하지 않았다.
자동 생성 표시/manifest 유지, 무표시·손상·다른 source·이름 변경 보호, 링크 미순회,
상한/IO 불완전 표시, 취소 전파, 완료/다른 캠페인 참조 보호와 실제 미삭제를 검증한다.
NAS 준비→등록 뒤 표시/참조 진단과 기존 연구 전체 회귀를 함께 실행한다.

- 집중 회귀 41개 / 26.417초 / OK / exit 0.
- 최종 전체 연구 회귀 243개 / 118.654초 / OK / exit 0 (`tmp/cr2c3a-regression.log`).
- 변경 Python 6개와 새 보고서의 AST/공백 검사, 관련 `git diff --check` 통과.

## 남은 범위

CR2c3b에서 cap(기본 무제한)과 보관 원장/정리 실행을 연결한다. 참조 목록은 읽기 snapshot이므로
삭제 직전 새 참조/활성 worker lease/외부 매매일지 참조를 재확인하는 계약이 필요하다.
표시만 존재하는 폴더를 삭제 허가로 해석하면 안 된다. 기존 무표시 자료는 자동 삭제하지 않는다.
완료된 연구를 삭제 가능 상태로 전환하려면 재현 자료 보관/참조 해제 정책을 먼저 구현해야 한다.
NAS 원시 시장자료 보존 정책과 이번 PC 자동 생성 export는 별도다. 새 날짜/자동 가설/주문은 이번에 바꾸지 않았다.
모델 에스컬레이션 없음.

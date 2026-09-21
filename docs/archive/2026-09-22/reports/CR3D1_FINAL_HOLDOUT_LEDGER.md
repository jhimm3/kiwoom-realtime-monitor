> **과거 기록** · 원래 경로: `reports/CR3D1_FINAL_HOLDOUT_LEDGER.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# CR3d1 — 최종 평가 접근·노출 원장 기반

2026-09-16. **고정 최종 batch와 영속 접근/노출 원장 계약 완료**. 실제 최종 실행 연결은 다음 CR3d2다.
누적 PC 연구 코드다. NAS/API/실제 계좌·주문은 변경하지 않으며 NAS 재빌드는 필요하지 않다.

## 문제와 최소 변경

기존 ResearchEvaluationSpec의 final_holdout_accessed_at/reason은 요청에만 저장된다.
새 요청으로 지우거나 자료/후보를 바꿔도 기간 전체의 접근 이력을 확인하는 원장은 없었다.
기존 chronological_holdout/v1/개발 partition/실행·보고서 의미는 보존하고, 별도 최종 batch 내부 계약과 두 표만 추가한다.
구현 파일은 기존 application/research_splits.py와 infrastructure/persistence/research_repository.py다.
새 실행기/Manager/Service/플러그인 계층을 추가하지 않는다.

## 입력·identity 계약

FinalHoldoutBatchSpec(final_holdout_batch/v1): dataset_id, SHA256 dataset_hash, 등록된 KRX session_profile,
정렬된 고유 candidate_spec_hashes 1~200개와 노출 표시 없는 단일 OOS ResearchEvaluationSpec.
평가 조건 전체 필드를 요구하고 추가 필드/정수 한도의 bool을 거절한다. 후보는 전체 scientific spec hash 참조다.
현재는 형태를 검증하며 실제 후보 full spec/자료 내용과 hash의 일치는 다음 실행 연결에서 검증해야 한다.
파일 경로·CPU·메모리·재시도 nonce는 scientific batch identity에 넣지 않는다.

UTC 시각은 microsecond 고정 형태로 정규화한다. 동등한 KST/UTC 표현은 같은 identity다.
window ID는 strict KRX active start/end만 hash한다. 같은 기간은 자료 ID/hash·세션 profile·fold 이름으로 새 미사용 창이 되지 않는다.
batch ID는 정규 최종 spec 전체의 hash다. 후보·자료·기준 변경은 다른 batch이며 이미 사용한 창에 연결할 수 없다.
반열린 [start,end) 기간이 겹치는 다른 창도 거절하고 정확히 인접한 창은 허용한다.
전체 active 구간을 보수적으로 막으며 종목 bucket별로 같은 기간을 따로 미사용이라고 세지 않는다.
warmup 공유는 미사용 active 구간과 다른 문제이며 이 원장이 모든 원시 자료의 독립성을 보장하지 않는다.

## 저장·mutation 계약

연구 v18 migration final_holdout_access_ledger는 창과 event 표만 추가한다. 기존 run/report/campaign과 v1~17 migration 이력을 변환하지 않는다.

- record_final_holdout_access(batch, request_id, accessed_at): 최초 batch를 FINAL_RESERVED 창으로 고정하고 FINAL_ACCESS event를 기록한다.
- 동일 batch의 새 기술 접근 nonce는 별도 event로 남긴다. 이것은 실행 소유권 선점이나 재실행 승인이 아니다.
- 동일 nonce/모든 내용/정규 시각이 같으면 False(멱등), 새로운 기록이면 True다. nonce는 전역 unique이며 다른 창/행동/시각으로 재사용하면 거절한다.
- expose_final_holdout(window_id, request_id, exposed_at, reason): 알려진 창만 EXPOSED_DEVELOPMENT로 변경하고 이유/event를 보존한다. 상태를 되돌리는 API는 없다.
- 노출 후에는 동일 batch/기존 access nonce를 포함한 모든 최종 접근을 거절한다. 개발자료 사용 연결과 다음 미사용 창 선택은 후속이다.
- 모든 event는 창 종료 이후의 timezone-aware 시각을 요구하고 같은 창의 과거 event보다 앞서는 시각을 거절한다.

최초 고정·overlap/nonce 확인·event 저장과 노출 변경은 각각 BEGIN IMMEDIATE 트랜잭션이다.
다른 후보/겹치는 창의 동시 접근은 하나만 성공한다. nonce 충돌과 검증 실패는 창/state/event를 함께 롤백한다.
load_final_holdout_window는 없으면 None, 있으면 decoded spec과 현재 state를 반환한다.
load_final_holdout_events는 최근 1~1000건을 시각/event 순서로 제한 조회한다. 영속 event 전체는 삭제하지 않는다.
SQLite row_factory는 이 네 원장 메서드 안에서만 설정하여 기존 연결/호출자의 tuple 계약을 유지한다.

## 호환과 실행 경계

write constructor는 v18로 명시 migration한다. read_only 비교는 기존 v17와 새 v18만 마이그레이션 없이 지원한다.
v17 comparison을 읽는 것만으로 DB bytes가 바뀌지 않는다. 원장 표 조회는 v18을 요구한다.
다른 미래 schema는 기존처럼 reader가 거절한다. 기본 과거 실행/보고서와 기존 개발 JSON/CLI를 바꾸지 않는다.
기존 schema 기대값을 최신 18로 갱신했지만 이전 version의 행 보존 검증은 유지했다.

## 검증

test_research_final_holdout_ledger.py 14개 계약: UTC aliases/불변/hash·정책·최대 후보 한도,
중복/기술 요청, 변경 후보/자료/profile/기준·fold 이름, subset/superset/shift/분수초 겹침·인접 창,
노출 영구 유지, 시각/nonce/이유 검증·롤백, 후보 경쟁과 겹치는 창 경쟁, read-only v18 mutation 차단,
v17 읽기 bytes 보존과 v17→18 run/report/migration 행 보존을 확인했다.
관련 32개/18.864초 OK, native exit 0. 최종 원장 14개/13.706초 OK, native exit 0.
전체 연구 핵심 회귀 508개/336.612초 OK, native exit 0. tmp/cr3d1-regression.log와 tmp/cr3d1-regression-exit.txt에 남겼다.
코드/테스트/문서 21개 파일 AST/공백 검사와 git diff --check 통과. 실제 사용자 DB/NAS/주문/운영 앱에는 접근하지 않았다.

## 완료 범위와 다음

현재는 명시 원장 API 기반이다. 원장에 없는 기간을 미사용이라고 증명하지 않는다.
다른 DB/외부에서 본 자료·기존 v1 전체 실행의 접근을 자동 수집하거나 개발 경로를 이 원장으로 차단하지 않는다.
candidate hash가 실제 전략/비용/implementation의 immutable full spec을 가리키는지는 실행 연결에서 검증한다.
같은 batch의 기술 접근 event는 실행 attempt/owner/성공 상태를 대신하지 않는다. 영속 실행 복구 계약은 후속이다.

다음 CR3d2는 기존 동결 입력/독립 실행 경계에 후보·입력 hash 검증, 과거 개발/노출 판정,
최종 실행 전 원장 접근을 연결한다. CLI는 후속 CR3d3a에서 연결했고 화면과 자동 최종 평가·피드백 노출은 그 이후다.
그룹 coverage/편중·새 날짜 확장/24시간 반복/새 가설 생성·실제 장시간 운영은 후속이다.
모델 에스컬레이션 없음.

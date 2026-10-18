# CR3d2a 최종 후보·동결 입력 검증과 원장 선행 준비

기준일: 2026-09-16. CR3d2를 준비(a)와 소유권 실행(b)으로 나누고 이번에는 a만 완료한다.
일반 개발 실행기의 OOS 차단을 해제하지 않고 기존 DTO/로더/시간 projection/원장을 연결한다.
NAS 소스 동기화·재빌드·실제 앱/사용자 DB/키/계좌·주문은 이번 작업에 포함하지 않는다.

## 목적과 입출력

기존 research_process.final_candidate_spec_hash는 final_candidate/v1 scientific canonical JSON SHA256을 계산한다.
등록 family와 typed 전략의 일치, 고정 single_run, 명시 세션, search/개발 partition 부재를 요구한다.
전체 전략 파라미터·실행/현금·비용률과 근거/유효기간·세션 문서·실제 구현 hash가 후보를 정의한다.
날짜, 원본/DB/run 경로와 자원 설정은 후보 hash에서 제외한다. batch가 전체 원본 ID/hash와 OOS 평가 정책을 별도로 고정한다.
비용은 명시 rate_basis/source/양쪽 유효기간과 bool이 아닌 정수 자본/비용률을 요구하며 active 평가 기간을 포함해야 한다.

prepare_final_holdout_evaluation의 입력은 FinalHoldoutBatchSpec, ResearchProcessRequest의 immutable tuple(1~200개),
request_id, accessed_at, 취소 등을 전달할 checkpoint다. 각 요청의 OOS·세션·source/DB/run 경로가 같아야 한다.
상태 DB/run은 원본 동결 디렉터리 밖에 두고 후보 hash set이 batch의 정렬된 집합과 정확히 일치해야 한다.
출력 PreparedFinalHoldoutEvaluation은 batch, hash 순서의 후보 tuple, 격리된 FrozenResearchDataset, 실제 구현 hash다.
이 DTO는 실행 소유권이나 재시도 허가가 아니다. 이 단계에서는 새 영속 요청/CLI/화면 입력을 만들지 않았고 CLI는 후속 CR3d3a에서 연결했다.

검사 순서:

1. 고정 후보/family/명시 비용과 hash 집합, 경로·단일 평가·비용 유효기간을 검증한다.
2. 기존 load_research_input으로 full frozen bytes/manifest를 한 번 검증한다.
3. 기존 projection 공유 코드로 final warmup/active/as-of context만 deep copy한다.
4. 실제 구현 hash가 변하지 않았는지 다시 확인하고 자원/취소 checkpoint를 적용한다.
5. 동일 연구 DB의 BEGIN IMMEDIATE 안에서 과거 연구 기간을 검사하고 접근 원장을 기록한다.
6. 기록 성공 후 준비된 final 입력을 호출자에게 반환한다. 전략/체결 엔진이나 연구 run은 생성하지 않는다.

## 격리와 기존 계약

research_data_source의 기존 prepare_development_partition 루프를 private _prepare_independent_partition으로 공유한다.
개발 descriptor/manifest/identity의 기존 직렬화 의미는 유지한다. 단순 전달용 새 manager/service는 추가하지 않는다.
최종 입력은 independent_final_holdout_input/v1과 final_holdout_partition/v1 정책으로 구별한다.
선택 구간의 available_at 순서, 포함된 봉의 양쪽 경계, 직전 membership/테마 seed와 원래 revision/시각을 유지한다.
알 수 없는 테마 availability와 미래 자료를 제외하고 coverage를 새로 증명한 것으로 표시하지 않는다.

full source ID/hash·후보 hash 집합은 outer batch/원장에 남기고 projected descriptor/identity에는 넣지 않는다.
children, 전체 quality/watermark/future count도 옮기지 않는다. 미선택 값/원본 ID 변화가 같게 선택된 입력 ID를 바꾸지 않는다.
final source는 full frozen 입력이어야 한다. 이미 선택된 개발/final 입력의 ID를 위조해 재활용하는 경우도 거절한다.
최종 평가를 batch canonical UTC 문서에서 복원하여 동일 시각을 KST/UTC로 표현해도 projected 입력/identity가 같게 한다.
일반 execute_research는 기존 development_partition_start에서 final tag 또는 descriptor를 발견하면 start_run 전에 거절한다.
final 입력을 개발 projection에 재투입하는 경로도 거절한다. final 엔진 사용 허용은 CR3d2b에서 별도 계약으로 한다.

## 과거 사용 검사와 접근 기록

기존 record_final_holdout_access에 check_development_history=False와 checkpoint를 추가한다.
기존 CR3d1 metadata 호출 기본값은 유지하고 새 준비 경로는 True를 항상 사용한다. schema는 v18 그대로다.
같은 BEGIN IMMEDIATE 트랜잭션 안에서 연구 run의 spec_json/input_manifest_json만 순차 조회한다. 성과/보고서는 읽지 않는다.
실제 입력 captured_range와, evaluation_spec이 있으면 모든 fold의 start-warmup부터 end까지 합집합을 검사한다.
TRAIN/VALIDATION/legacy OOS와 모든 실행 상태를 보수적으로 포함한다. 실제 범위가 없거나 malformed/unknown이면 차단한다.
evaluation_spec 없는 legacy run도 captured_range를 검사한다. 평가 정책만 있고 실제 입력 범위가 없는 기록은 미사용을 증명할 수 없다.
호출 경로 확인 결과 일반 execute_research는 _ordered_minute_observations(dataset) 전체를 순회한다.
보고서 fold는 평가 분류이며 입력 격리가 아니다. 따라서 fold 사이/밖을 검사에서 제외하면 이미 읽은 자료를 미사용으로 잘못 통과시킬 수 있다.
이에 검사에 실제 입력 범위를 추가하고 disjoint 허용 테스트도 실제 projected 개발 입력으로 바꾸었다.
어느 footprint든 final active 기간과 겹치면 차단한다. 겹치지 않는 개발 이력은 허용한다.
검사/원장 고정/접근 삽입 중 예외나 취소 callback은 window/event를 함께 롤백한다.
원장의 기존 overlap/고정 batch/노출 상태/nonce·시각 규칙을 그대로 적용한다.

## 확인 범위와 한계

raw bytes 검증과 private projection은 접근 event 이전 trusted 준비 코드 안에서 수행한다.
이 단계의 선행 접근 기록은 호출자에게 입력 반환/전략 실행 이전을 뜻한다. 파일 bytes 읽기 전 방화벽이라고 주장하지 않는다.
이력 검사는 같은 연구 DB에 기록된 실행의 footprint만 대상으로 한다. 외부 열람·다른 DB·사용자 복기/forward 자료를 증명하지 않는다.
기존 일반 개발 실행 전체를 새 원장으로 자동 감시하거나 이후 실행의 사용을 전역 잠그는 기능도 아직 없다.
보수적 legacy 검사 때문에 정상 사용 가능한 기간도 불명확한 과거 기록이 있으면 차단할 수 있다. 우회 자동화 없이 후속 명시 검토 대상이다.
동일 batch의 새 접근 nonce/동일 nonce 멱등은 로그 계약이며 실제 최종 실행의 claim/완료 캐시/재시도 허가가 아니다.
준비된 DTO는 Python 내부 객체이며 프로세스 격리나 악의적인 객체 변조 방어 계약은 아니다.

## 검증

- 신규 test_research_final_preparation.py 26개: 실제 frozen bytes 로드, 후보 hash/집합/정책/모든 절대 경로/비용, 입력 경계/deep copy/미선택 canary/동일 시각 UTC 표준화,
  일반 runner 시작 전 차단/재활용 거절, 과거 fold/warmup/legacy/unknown/malformed 검사, 동일 트랜잭션 순서, 취소 rollback, 구현 drift.
- 초기 인접 집중 검증: 62개, 31.697초, OK, native 종료 0. 실제 입력 범위 검사 보완 후 다시 검증한다.
- 초기 전체 회귀: 529개, 338.501초, OK, native 종료 0. 그 뒤 확인된 실제 입력 범위 검사를 추가했으므로 최종 전체 회귀를 다시 실행한다.
- 최종 전체 연구·화면 회귀: 534개, 337.749초, OK, native 종료 0. tmp/cr3d2a-regression-final.log / tmp/cr3d2a-regression-final-exit.txt.
- 시장 자료 관측 시각·완료 상태·거래소 계약: 7개, OK. tmp/cr3d2a-contract.log / tmp/cr3d2a-contract-exit.txt.
- 변경 12개 파일 AST/whitespace와 문서 Git diff check 통과. LF→CRLF 안내만 있고 오류는 없다.

## 다음 CR3d2b

원장/batch를 가진 별도 final execution scope를 정의하고 candidate별 atomic claim과 새 현금/빈 상태를 연결한다.
일반 개발 scope와 공유할 수 있는 순수 replay/체결 경로를 최소 수정으로 사용하되 final 허용을 기존 기본 API에 퍼뜨리지 않는다.
최종 완료/실패/취소/부분 노출의 소유권 및 캐시/재시도 계약을 먼저 고정하고 결과를 기록한다.
이때 final 실행 자체의 footprint와 허가된 동일 batch 기록을 과거 개발 검사와 구별해야 한다.
그 뒤 CLI/UI와 노출 후 개발 피드백·다음 미사용 창/새 날짜 확장을 연결한다. 24시간 자동 반복은 후속이다.

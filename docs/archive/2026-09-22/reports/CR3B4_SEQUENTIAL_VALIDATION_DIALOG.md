> **과거 기록** · 원래 경로: `reports/CR3B4_SEQUENTIAL_VALIDATION_DIALOG.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# CR3b4 — 여러 개발 구간 순차 검증 화면

2026-09-16. **화면 실행/취소/진행 표시/명시 재개 완료**. 기존 CR3b3 JSON/CLI 실행기를 PC 연구 화면에 연결했다.
실제 NAS/API/사용자 DB/계좌·주문 변경과 DB 마이그레이션은 없다. 누적 PC 소스이며 NAS 재빌드 불필요.

## 사용 방법과 목적

전략 연구 → **여러 구간 순차 검증** → 검증 파일 선택 → **검증 실행**.
파일은 기존 independent_development_validation/v1 JSON이다. 전략/비용/원본 ID·hash/개발 구간을 미리 지정한다.
[필수 객체와 실행 계약](CR3B3_SEQUENTIAL_DEVELOPMENT_VALIDATION.md)을 따른다. 새 JSON 편집기는 이번 범위가 아니다.

**취소**는 자기 실행에만 신호를 보내며 완료 scientific 결과는 DB에 남는다.
**같은 요청 이어서 실행**은 마지막으로 성공적으로 시작한 파싱 snapshot을 다시 사용한다.
원본 JSON이 수정되어도 이 버튼의 전략/비용/경로/선택 구간은 바뀌지 않는다.
수정한 JSON을 사용하려면 **검증 실행**을 다시 누른다. 원본 자료가 달라졌으면 기존 source ID·hash 검증이 거절한다.

완료 구간은 CACHED로 재사용하고 취소/예산 종료 구간은 기존 실행기가 다시 실행한다.
FAILED/CACHE_INVALID/BUSY는 기존 실행기의 보존 계약을 유지하며 GUI가 삭제/재선점/실패 초기화를 하지 않는다.
버튼은 명시 실행이다. 이번 단계는 시간 slice 자동 반복이나 24시간 가설 생성 완료가 아니다.

## 책임과 최소 변경

- research_process.py의 DevelopmentValidationRequest.to_dict: 파싱된 fixed request의 절대 경로 직렬화.
  기존 검증 계약을 재사용하고 원본 파일을 다시 읽지 않는다. GUI 입력/DB 로드 없음.
- research_dialog.py의 DevelopmentValidationDialog: 별도 실행 수명, 요청/진행/취소 파일, 표시 상태를 소유.
  기존 AuxiliaryProcessManager/build_auxiliary_command와 --validate-partitions를 재사용한다.
  기존 읽기 전용 IndependentComparisonDialog와 캠페인의 상태/취소 파일을 공유하지 않는다.
- ResearchDialog: 버튼/창 재사용/부모 close·stop 전달만 추가.

새 Manager/Service/interface/실행 알고리즘/테이블은 없다. 실행기/입력 projection/atomic claim/엔진/집계는 CR3b3 그대로다.
화면을 이해하는 핵심 경로는 research_dialog → research_process → 기존 runner/repository로 유지한다.
독립 창은 캠페인 또는 저장 결과 비교와 별도의 실행 수명을 갖는다. 단순 전달 계층을 추가하지 않는다.

## 요청·결과와 진행 계약

1. 1 MiB 이하 기존 요청을 파싱하고 절대 경로로 고정한다. UUID별 request/result/cancel을 만든다.
   파일이 원본 요청/DB와 같거나 frozen dataset 아래이면 디렉터리를 만들기 전에 거절한다.
2. 낮은 우선순위 숨김 child로 --validate-partitions를 실행한다. 실행/미소비 native 종료 중 중복 실행을 거절한다.
3. 250ms Qt timer로 최대 16 MiB atomic 진행 파일만 확인한다. 변경 없는 문서는 다시 표에 적용하지 않는다.
   입력 확인 중에는 선택 구간 수/시간 예산을 표시하고, 실행기가 진행 파일을 게시하면 전체 구간을 표시한다.
4. kind/database/선택 fold 순서/기간/역할/run ID 순서·중복/부분 비교 version·ID 범위를 대조한다.
   첫 진행의 implementation hash를 고정하고 이후 변경을 거절한다. GUI에서 source/코드를 다시 hash하지 않는다.
5. 모든 선택 구간을 표시한다: 미시작/실행 중/완료/캐시/실패/소유자·복구 확인/취소/예산·자원 차단.
   상태를 scientific 적격과 분리하고 구간별 손익/MDD만 표시한다. 손익·MDD를 연속 계좌로 합산하지 않는다.
6. 전체 batch_status는 모든 steps와 status에서 확인한다. 확인된 일부 ID만의 comparison COMPLETE를 전체 완료로 쓰지 않는다.
   native 종료 0/2/3은 각각 ok/cancelled/resource_blocked와 일치해야 한다. 종료 뒤 RUNNING checkpoint만 남으면 성공이 아니다.
7. 오류/범위 불일치/잘못된 JSON/native 실패는 이전 정상 표를 유지하고 이유를 표시한다.
   사용자 취소·자원 차단의 유효 최종 결과는 미시작 구간까지 표시한다.

## 취소·종료와 보존

닫기는 cancel 파일 작성/숨김만 수행한다. GUI thread에서 child 종료를 기다리지 않고 숨김 뒤 timer가 결과를 소비한다.
앱 stop은 자기 cancel을 보내고 기존 manager의 graceful 3초/terminate 1초 경계를 사용한다.
완료/실패/앱 stop 후 자기 UUID request/result/cancel만 정리한다. 원본 JSON/DB/다른 운영 파일은 보존한다.
정리 실패는 tooltip에 남긴다. 과거 비교 창과 캠페인의 수명/동작은 유지한다.

## 검증

- 신규 화면 계약 17개: 실제 엔진 실행/완료 캐시/고정 snapshot 재개/취소/자원 차단/미시작·부분 비교,
  진행 hash 변경/범위·기간·ID·전체 완료/native 불일치/큰 결과·손상 JSON/중복 실행,
  입력 아래 운영 파일 차단/launch 실패/숨김 비동기 종료/앱 stop/부모 창 재사용을 검사한다.
- 실제 숨김 child 실행 중 10ms Qt heartbeat가 반응하고 child native exit 0인 것을 검사한다.
- focused 92개 / 56.323초 / OK / native exit 0: tmp/cr3b4-focus.log, tmp/cr3b4-focus-exit.txt.
- 전체 연구 회귀 440개 / 270.763초 / OK / native exit 0: tmp/cr3b4-regression.log, tmp/cr3b4-regression-exit.txt.
- 변경 소스/신규 테스트/문서 10개의 AST·공백 검사 및 git diff --check 통과: tmp/check_cr3b4.py.
- 고정 fixture/임시 DB/offscreen Qt만 사용했다. 실제 NAS/사용자 DB/계좌·주문/키/실행 앱에 접근하지 않았다.

## 실제 남은 한계와 다음

- 명시 재개 snapshot은 현재 창 수명에만 보관한다. 앱 재시작 후 같은 원본 JSON으로 실행하면 완료 DB cache를 쓰지만
  영속 batch 선택/운영 상태 복원은 아직 없다. 자동 반복/연속 배치 예산 정책도 후속이다.
- 강제 프로세스 종료로 running이 남으면 기존 BUSY/소유자·복구 확인 계약을 따른다. 자동 orphan 복구는 없다.
- 각 child의 자원 budget은 별도다. 동시에 실행한 캠페인/검증의 PC 전체 CPU·RSS 합산 보장은 이번 범위가 아니다.
- 전체 source 로드/검증 후 partition projection을 한다. 실제 전체 규모·장시간 앱 부하/종목 선택 편의는 미검증이다.
- 다음 CR3c: 고정 종목 분할 계약/독립 입력 검증. 최종 접근 원장/새 날짜 확장/자동 가설/모의 운영·일지 피드백은 후속이다.
- 모델 에스컬레이션: 없음.

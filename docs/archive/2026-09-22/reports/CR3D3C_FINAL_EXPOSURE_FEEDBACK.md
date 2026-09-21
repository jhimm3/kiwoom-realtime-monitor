> **과거 기록** · 원래 경로: `reports/CR3D3C_FINAL_EXPOSURE_FEEDBACK.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# CR3d3c — 최종 결과의 개발 사용 노출

2026-09-16. CR3d3b의 검증된 final 결과를 후속 전략 개선에 사용했을 때 해당 창을 되돌릴 수 없는 `EXPOSED_DEVELOPMENT`로 기록하는 경계를 연결했다.

## 요청과 child

- `final_holdout_exposure_request/v1`은 존재하는 연구 DB, full locked batch, 1~256자 request ID, timezone-aware 노출 시각, 1~2000자 사용자 근거를 고정한다.
- parser는 1 MiB·정확한 필드·batch 계약·시각·문자열 제한과 DB 파일 존재만 확인하고 DB를 열지 않는다.
- `research_process --expose-final`이 원장 window의 batch ID/full spec을 request와 대조한다. 변경 직전 cancel을 다시 확인한 후 기존 `expose_final_holdout` 트랜잭션을 호출한다.
- 기존 repository가 RUNNING candidate를 차단하고 request ID 멱등, 시각 순서, 창 불변 전환을 계속 소유한다. DB schema는 v20을 유지한다.

## 화면

- `FinalHoldoutDialog`은 검증된 final result를 적용한 후에만 `개발 사용 근거`와 기록 버튼을 활성화한다.
- 사용자가 기록 버튼을 누르면 새 request ID·UTC 시각·UUID request/result/cancel 파일을 만들고 기존 낮은 우선순위 child를 실행한다. UI는 DB를 열지 않는다.
- 결과의 native exit/status, kind/version, database, window/batch/request ID, state, 멱등 기록 여부를 모두 대조한 뒤에만 `EXPOSED_DEVELOPMENT`를 표시한다.
- 성공 후에는 같은 final 창을 다시 미사용 평가로 쓸 수 없고, 다음 final에 새 미사용 기간이 필요하다는 사실을 보여준다.

## 실패·취소

- cancel은 원장 mutation 전에만 취소로 확정한다. 트랜잭션 시작 후에는 repository 원자성을 우선한다.
- 요청·DB·batch 불일치, RUNNING candidate, 결과 envelope 불일치, 결과 누락/크기 초과에서는 화면의 final 표를 덮어쓰지 않는다.
- 화면이 성공 envelope를 받지 못한 경우 성공을 추정하지 않는다. 같은 요청은 원장에서 멱등이며 새 기술 요청은 별도 event로 남는다.

## 검증

- `test_research_final_exposure_cli.py`: 6개 통과.
- `test_research_final_dialog.py`: 11개 통과.
- 최종 원장·실행·복구·노출 집중 회귀: 66개 통과, 50.461초.
- 전체 `test_research*.py`: 597개 통과, 410.165초.

## 남은 범위

- CR3의 최종 창 고정·실행·복구·수동 개발 노출 경계는 연결됐다.
- CR4 자동 가설 생성, 자동 final 선택/실행, 앱 재시작 영속 재개, RUNNING orphan 회수, 새 날짜 확장은 후속이다.
- NAS/API/계좌·주문 경계는 바꾸지 않았다.

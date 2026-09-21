> **과거 기록** · 원래 경로: `reports/CR3D3B_FINAL_HOLDOUT_DIALOG.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# CR3d3b — 최종평가 실행·명시 복구 화면

2026-09-16. CR3d3a의 불변 요청·child 계약을 기존 전략 연구 창에 연결했다.

## 구현

- `ResearchDialog`에 `최종 평가` 버튼과 재사용되는 `FinalHoldoutDialog`을 추가했다.
- 선택한 `independent_final_holdout_request/v1`을 UI에서 최대 4 MiB로 파싱하고 절대경로로 정규화해 UUID request/result/cancel 파일에 고정한다.
- 실제 source 검증, 접근 원장, 후보 실행·cache·recovery는 기존 `research_process --evaluate-final` 낮은 우선순위 child가 담당한다. GUI는 DB/source를 열지 않는다.
- 표는 후보 hash, 실행 상태/ID, 성과·보고서 판정, 결과 hash, recovery ID, 사유를 보여준다. full identity는 tooltip에 보존한다.

## 결과 신뢰 경계

- native exit/status, kind/version, batch/window ID, 64자 구현 hash, 정렬 candidate hash, 정확한 candidate 필드, run ID 중복, recovery ID, batch 완료 계산을 모두 검증한 후에만 기존 표를 교체한다.
- 파싱·범위·종료 불일치, 16 MiB 초과, 결과 누락에서는 이전 표를 유지한다.
- 입력 확인 전 취소는 배치 결과로 오인하지 않고 이전 표를 유지한다.

## 재개와 복구

- 같은 snapshot 재개는 `NOT_STARTED` 후보가 남았을 때만 열린다. 완료 cache는 재사용한다.
- `FAILED/CANCELLED`는 자동 재실행하지 않는다. 선택한 후보 하나와 1~2000자의 사용자 근거, 새 request ID/owner를 고정한 새 request로만 CR3d2c 복구를 요청한다.
- 복구 허용 최종 판정은 child/repository가 다시 수행한다. output manifest가 남은 불확실한 실행은 화면이 우회하지 않는다.

## 수명과 정리

- 창 닫기는 자신의 cancel 파일을 쓰고 블로킹하지 않으며 숨은 상태에서 polling으로 종료 결과를 소비할 수 있다.
- 앱 종료는 3초 정상 종료 기회 후 기존 process manager 순서를 따르고, 해당 창의 UUID 임시 파일만 삭제한다.

## 검증

- `test_research_final_dialog.py`: 8개 통과.
- CR3d3a CLI·순차 검증 화면·독립 비교 화면·campaign 화면 인접 회귀: 51개 통과.
- 전체 `test_research*.py`: 588개 통과, 396.362초.

## 남은 범위

- 실행 중 candidate별 진행 snapshot은 CR3d3a 계약에 없으므로 현재 화면은 종료 결과만 표시한다.
- final 결과를 새 개발 판단에 사용할 때의 `EXPOSED_DEVELOPMENT` 명시 기록과 후속 가설 경계는 다음 단계다.
- 자동 최종평가, 앱 재시작 영속 재개, RUNNING orphan 회수, NAS/API/계좌·주문은 이 단계에서 바꾸지 않았다.

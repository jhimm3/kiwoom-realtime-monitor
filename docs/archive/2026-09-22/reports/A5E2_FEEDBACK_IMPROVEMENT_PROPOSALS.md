> **과거 기록** · 원래 경로: `reports/A5E2_FEEDBACK_IMPROVEMENT_PROPOSALS.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# A5e2 — 등록된 한 파라미터 개선안 revision

기준일: 2026-09-16. 로컬 구현·회귀 완료, NAS 누적 배포 전.

## 목적

적격 A5e1 기계 복기를 전략을 바로 변경하는 명령으로 쓰지 않고, 기존 CR4와 같은 등록 Family와
명시 허용값 안의 반증 가능한 후보로 바꾼다.

## 입력과 출력

- 입력: 적격 `journal_feedback_review/v1`, 등록 Family와 factor allowlist, 정규화된 기준 전략,
  파라미터별 허용 정수값, seed, 최대 제안 수.
- 출력: 0개 이상의 `journal_feedback_improvement/v1` 불변 revision.
- 각 제안은 기준 전략과 정확히 한 파라미터만 다르다.
- 허용값 밖의 값, 등록되지 않은 파라미터, 정규화로 사라지는 값은 거절한다.

seed는 bounded 후보의 순서만 바꾼다. 같은 review·기준 전략·허용 정책·변경 내용은 seed와 무관하게
같은 proposal ID를 갖는다.

## 의미

POSITIVE review는 추가 개선 반증 후보, NEGATIVE review는 회복 후보, FLAT review는 차이 판별 후보라는
rationale만 기록한다. 어느 방향이 실제 개선인지 단정하지 않으며 재시뮬레이션 전에는 전략으로 채택하지 않는다.

## 저장과 보호

기존 중앙 generic store의 비공개 `execution_feedback_improvement_proposals` 컬렉션을 사용한다.
원본 review가 먼저 저장돼 있어야 하며 계좌·전략·evidence·동결시각·rationale을 다시 확인한다.
사용자 복기, 수동 유형, 기준 전략, 연구 queue와 주문 경로는 변경하지 않는다.

## 검증

- A5e2·A5e1·A5d 및 CR4 가설·전진평가·체결 대조·projection 회귀 49개 통과.
- 한 필드 변경, seed 독립 ID 집합, 부적격/표본 부족 review 차단, 미등록 파라미터,
  내용 변조, 원본 review 없는 저장과 멱등 저장을 확인했다.

## 다음

명시적으로 채택된 proposal을 기존 전략과 분리된 새 불변 버전으로 만들고 같은 proposal을 한 번만
CR3 개발 재검증 대기열에 등록한다. 채택은 모의 또는 실전 주문 활성화를 뜻하지 않는다.

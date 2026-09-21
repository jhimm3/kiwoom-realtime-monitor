> **과거 기록** · 원래 경로: `reports/A5D_FEEDBACK_EVIDENCE.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# A5d 비용 포함 FeedbackEvidence

완료일: 2026-09-16

## 목적

중앙 모의 실행 원장과 계좌별 매매일지의 확인된 결과를 다음 연구가 재현할 수 있는 불변 문서로
고정한다. 사용자 복기나 기존 전략 원본을 수정하지 않으며, 결과를 본 뒤 고른 자료와 사후 근거가
새 전략의 독립 검증 근거로 섞이지 않게 한다.

## 입력

- A5c `TradeFillReconciliation`: 상세 우선 단일 체결과 주문별 품질 상태
- 계좌별 kt00015 `DailyTradeCost`
- canonical mock `AccountScope`
- 평가 시작·종료·근거 기준·동결 시각
- 전략 ref, 선택 run과 선택 선언 시각
- 해당 체결과 연결된 연구 링크
- 연구가 개발 자료만 사용했는지 또는 최종 결과에 이미 노출됐는지

## 확정 손익 조건

회차의 모든 주문이 `EXACT` 또는 `DETAIL_ONLY`이고, 매수·매도에 필요한 실제 비용이 존재하며,
포지션이 닫힌 경우에만 총손익·broker 비용·순손익을 기록한다. 다음 중 하나라도 있으면 해당 회차와
전체 합계의 확정 손익은 `null`이다.

- `SUMMARY_ONLY`, `PARTIAL`, `CONFLICT`
- kt00015 비용 누락
- 열린 포지션 또는 원가를 알 수 없는 매도

broker 순손익은 체결 총손익에서 실제 비용을 한 번만 차감한다. 기존 forward 보고서가 이 값을 다시
차감하지 않는 계약을 유지한다.

## 선택 편향과 근거 시점

- run을 평가 시작 전에 선언: `PREDECLARED`
- 평가 시작 뒤 선언: `POST_HOC`
- 선언 시각 없음: `UNVERIFIED`
- run을 고르지 않은 전체 계좌 자료: `ALL_ACCOUNT_TRADES`

연구 링크는 해당 체결의 source event, broker execution, run 또는 decision과 연결되는 것만 사용한다.
관련 없는 링크는 `AT_EXECUTION` 근거가 될 수 없다. 링크가 모두 당시 가용 근거면 `AT_EXECUTION`,
사후 근거가 하나라도 있으면 `POST_TRADE_INCLUDED`, 확인할 수 없으면 `UNVERIFIED`다.

`DEVELOPMENT_ONLY` 자료만 전략 피드백 적격이 될 수 있다. 최종 검증 결과를 이미 본
`FINAL_EXPOSED` 문서는 기록은 보존하지만 같은 전략 개선의 독립 근거로 재사용하지 않는다.

## 저장과 연결

- 버전: `journal_feedback_evidence/v1`
- 중앙 컬렉션: `execution_feedback_evidence`
- owner: `strategy_ref`
- key: 전체 문서 내용 hash인 `evidence_id`

동일 문서는 멱등 저장되고 같은 ID의 다른 내용은 거절한다. 이 컬렉션은 공개 콘텐츠 API allowlist에
추가하지 않으며 기존 generic central document 저장소를 재사용한다. 별도 DB migration과 NAS build
변경은 없다.

완결·사전선택·PIT 안전·개발 전용 조건을 모두 만족한 문서만 기존 `ForwardEvidence`로 변환한다.
프로필의 전략·계좌·환경·평가기간이 다르면 변환을 거절한다.

## 검증

- 정확 매수·매도에서 총손익 10원, 실제 비용 3원, 순손익 7원으로 한 번 계산
- 부분 체결 및 비용 누락의 확정 손익 차단
- 사후 run 선택, 사후 근거, 최종 결과 노출의 적격 차단
- 다른 계좌 연구 링크 거절
- 관련 없는 연구 링크의 PIT 안전 주장 차단
- 내용 ID round-trip, 변조 거절, 중앙 문서 멱등 저장
- 적격 문서만 ForwardEvidence 변환
- 직접 A5d/forward/A5c 회귀 31개, 전체 forward 6개, 전체 trade 120개 통과

## 다음 단계

피드백에서 만든 기계 복기와 전략 개선 제안을 사용자 원본과 분리된 revision으로 저장한다. 사용자가
채택하거나 자동 정책이 허용한 변경은 기존 전략을 덮지 않는 새 버전으로 만들고 CR3 재검증 대기열에
연결한다.

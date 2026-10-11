# 2026-10-11 문서 정리 기록

기준: 제품 2.1.0 · 로컬 main/HEAD `89fd6fda19f1f5bc9c7a62695d767f783a81fa79`. 변경·이동 대상 문서의 기존 미커밋 내용까지 원문 ZIP에 보존했다. 코드·테스트의 기존 작업은 유지했다. NAS·원본 DB·수집기·녹화 예약은 변경하지 않았다.

## 이동·승계

| 원래 경로 | 이동 이유 | 보존본 / 현재 승계 |
|---|---|---|
| `AUDIT_REPORT.md` | 9월 22일 문서 정리 결과. 현행 불일치와 후속은 OPEN_ITEMS로 승계 | [보존본](reports/AUDIT_REPORT_20260922.md) · [승계](../../OPEN_ITEMS.md) |
| `docs/api-contract/2026-08-14_0B_주식체결_검증.md` | 8월 14일 API 실측 기록. 현행 API 계약과 구분해 보존 | [보존본](api-contract/2026-08-14_0B_주식체결_검증.md) · [승계](../../../API_CONTRACT.md) |
| `docs/api-contract/2026-08-14_ka00198_순위_검증.md` | 8월 14일 API 실측 기록. 현행 API 계약과 구분해 보존 | [보존본](api-contract/2026-08-14_ka00198_순위_검증.md) · [승계](../../../API_CONTRACT.md) |
| `docs/api-contract/2026-08-14_고가_기준_검증.md` | 8월 14일 API 실측 기록. 현행 API 계약과 구분해 보존 | [보존본](api-contract/2026-08-14_고가_기준_검증.md) · [승계](../../../API_CONTRACT.md) |
| `docs/api-contract/2026-08-14_기본정보_및_거래대금_검증.md` | 8월 14일 API 실측 기록. 현행 API 계약과 구분해 보존 | [보존본](api-contract/2026-08-14_기본정보_및_거래대금_검증.md) · [승계](../../../API_CONTRACT.md) |
| `docs/api-contract/2026-08-14_인증_검증.md` | 8월 14일 API 실측 기록. 현행 API 계약과 구분해 보존 | [보존본](api-contract/2026-08-14_인증_검증.md) · [승계](../../../API_CONTRACT.md) |
| `docs/DOCUMENTATION_VALIDATION.md` | 이전 문서 정리의 검증 수치. 이번 검증 기록과 분리 | [보존본](reports/DOCUMENTATION_VALIDATION_20260922.md) · [승계](../../DOCUMENTATION_VALIDATION.md) |
| `docs/MAIN_WINDOW_APP_CONTROLLER_PLAN.md` | A-D 구현 완료 계획과 당시 검증. 현행 책임은 모듈 지도, 운영 잔여는 OPEN_ITEMS | [보존본](reports/MAIN_WINDOW_APP_CONTROLLER_PLAN.md) · [승계](../../../MODULE_MAP.md) |
| `docs/MAIN_WINDOW_CONNECTION_BASELINE.json` | 완료한 AppController 작업의 변경 전 정적 증거 | [보존본](evidence/MAIN_WINDOW_CONNECTION_BASELINE.json) · [승계](../../../MODULE_MAP.md) |
| `docs/MAIN_WINDOW_CONNECTION_COMPARISON.json` | 완료한 AppController 작업의 변경 후 정적 증거 | [보존본](evidence/MAIN_WINDOW_CONNECTION_COMPARISON.json) · [승계](../../../MODULE_MAP.md) |
| `docs/REGRESSION_COVERAGE_AUDIT.md` | 이전 미등록 260개 보호 근거. 테스트 삭제 근거로 사용하지 않고 증거 보존 | [보존본](reports/REGRESSION_COVERAGE_AUDIT.md) · [승계](../../TEST_FINAL_QUALITY_AUDIT.md) |
| `docs/SERVER_LIFESPAN_FAILURE_PLAN.md` | 서버 수명 A+B 구현 완료. Windows rename 원인과 운영 검증은 OPEN_ITEMS에 유지 | [보존본](reports/SERVER_LIFESPAN_FAILURE_PLAN.md) · [승계](../../OPEN_ITEMS.md) |
| `docs/TEST_CI_TIER_PROPOSAL.json` | 430개 파일을 대상으로 한 이전 제안 snapshot. 현행 tests/ci_groups.json과 분리 | [보존본](evidence/TEST_CI_TIER_PROPOSAL.json) · [승계](../../../tests/ci_groups.json) |
| `docs/TEST_CONTRACT_RECONCILIATION_20261009.md` | 완료된 10월 9일 fixture·계약 보완 결과 | [보존본](reports/TEST_CONTRACT_RECONCILIATION_20261009.md) · [승계](../../TEST_FINAL_QUALITY_AUDIT.md) |
| `docs/TEST_DEPENDENCY_AUDIT.md` | 409개 테스트의 이전 조사 기준선. 최신 CI 계약과 현재 결과로 통합 안내 | [보존본](reports/TEST_DEPENDENCY_AUDIT.md) · [승계](../../TEST_FINAL_QUALITY_AUDIT.md) |
| `docs/TEST_DUPLICATION_CI_FOLLOWUP.md` | 단계별 CI 편입과 중복 감사 완료 기록. 현재 등록은 실행 manifest/catalog | [보존본](reports/TEST_DUPLICATION_CI_FOLLOWUP.md) · [승계](../../TEST_FINAL_QUALITY_AUDIT.md) |

## 긴 이력을 분리한 문서

| 현행 | 이전 전문 |
|---|---|
| [CURRENT_STATUS.md](../../CURRENT_STATUS.md) | [2026-10-11까지의 기록](history/CURRENT_STATUS_through_20261011.md) |
| [OPEN_ITEMS.md](../../OPEN_ITEMS.md) | [2026-10-11까지의 기록](history/OPEN_ITEMS_through_20261011.md) |
| [MODULE_MAP.md](../../../MODULE_MAP.md) | [2026-10-11까지의 기록](history/MODULE_MAP_through_20261011.md) |
| [DEPENDENCY_REFACTOR_VERIFICATION_PLAN.md](../../DEPENDENCY_REFACTOR_VERIFICATION_PLAN.md) | [2026-10-11까지의 기록](history/DEPENDENCY_REFACTOR_VERIFICATION_PLAN_through_20261011.md) |

## 유지한 문서와 원본

API/DB/과거자료/연구/주문 계약, 진단 운영·원인 Replay 설계, 진행 중인 과거뉴스/수집 계획은 서로 다른 계약을 담으므로 유지했다. DB baseline/after/approval/catalog JSON과 공식 자료 색인·법적 고지도 유지한다. 영어 공개 HTML 2개는 한국어 정본으로 안내하며 주소는 유지한다. 테스트 파일 자체를 삭제하거나 이전 등록/통과 수치를 새 전체 결과로 바꾸지 않았다.

[문서별 전체 분류](document-inventory.json)는 정리 전 269개 문서/참조 원장을 빠짐없이 기록한다. [원문 ZIP](original-documents.zip)은 변경·이동한 54개 파일을 원래 경로와 바이트 그대로 보존한다. 현행·과거 설명문과 비교표는 한글화했으며 코드·식별자·공식 자료·라이선스는 원래 언어를 유지했다. 읽기용 아카이브는 안내·링크·언어를 갱신했으므로 원문 바이트 검증은 ZIP을 기준으로 한다.

[이번 검증](../../DOCUMENTATION_VALIDATION.md) · [현재 문서 안내](../../README.md) · [남은 작업](../../OPEN_ITEMS.md).

## 추가 통합

- `docs/NAS_DIAGNOSTIC_API_DESIGN.md` → [원래 설계](reports/NAS_DIAGNOSTIC_API_DESIGN.md) / [운영·API·제어 통합](../../NAS_RUNTIME_DIAGNOSTICS.md). 중복 계약을 합치고 작성 당시 근거는 보존했다.
- `docs/DIAGNOSTIC_CONTROL_DESIGN_REVIEW.md` → [원래 설계](reports/DIAGNOSTIC_CONTROL_DESIGN_REVIEW.md) / [운영·API·제어 통합](../../NAS_RUNTIME_DIAGNOSTICS.md). 중복 계약을 합치고 작성 당시 근거는 보존했다.
- NAS 진단 운영의 [이전 측정·CLI 전문](history/NAS_RUNTIME_DIAGNOSTICS_through_20261011.md)도 별도 보존했다. API·상태·보호 수신/주문/저장·보고서·동시성/복구의 고유 조건은 통합 문서에 유지한다.

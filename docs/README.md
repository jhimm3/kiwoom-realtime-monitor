# 현재 문서 안내

**현재 구조 정본(2026-10-23):** 기존 기능 전체의 재평가·목표 책임·재사용/부분 교체·승인 후 전환 순서는 [앱 전체 목표 구조](architecture/app-target-20261023.md)에 있다. 현행 아키텍처·R3 원장·미래 AI Trading 설계와 구분하며 현재는 설계 승인 대기다. 이번 평가는 오류·복구·기본 관측·보안·UX·개발/배포/복원 등 14개 품질 영역을 포함한다. 병목이 아직 해결되지 않았으므로 NAS 녹화·리플레이·정밀 진단은 현재 유지·사용한다. 이번 목표는 과거 뉴스·대신증권(CREON)의 일회성 수집/적재 도구까지 포함해 언제든 삭제 가능한 독립 경계를 만드는 것이며, 도구·운영 작업·적재 자료를 지금 삭제/중단하지 않는다. 자체 로컬 LLM 구축·학습·서빙 계획은 취소한다.

정리일: **2026-10-11 · 제품 2.1.0**. 작업 위치·소스·배포·검증의 정본은 [현재 상태](CURRENT_STATUS.md#current-summary)다. 단계별 보고서의 과거 ‘다음 작업’은 현재 우선순위나 실행 권한이 아니다.

## 처음 읽을 문서

| 목적 | 정본 |
|---|---|
| 실행 소스·배포 release·검증 결과 | [현재 상태](CURRENT_STATUS.md) |
| 미완료·운영 확인·사용자 보류 | [남은 작업](OPEN_ITEMS.md) |
| 실행 비용·구조·기능 연결 감사와 다음 리팩토링 | [R3 백로그](../REFACTOR_BACKLOG.md) — “다음”은 현재/다음 ID와 관련 코드부터 확인 |
| 새 기능·수정의 책임/계약·비용·연결 검사 | [R3 기능별 책임 계약](architecture/r3-ownership.md#필요한-기능군부터-읽기) — 관련 기능군만 선택, 전체 감사 반복 없음 |
| 제품 방향과 개발 순서 | [개발 로드맵](../FUTURE_DEVELOPMENT_ROADMAP.md) |
| 전체 목표 설계·재조립/신규 개발 비교 | [AI Trading Intelligence 전체 설계](AI_TRADING_SYSTEM_REDESIGN.md) — 선택을 위한 설계안, 현재 구현과 구분 |
| 원래 종합 명세와 현재 개발 방향 대조 | [2026-10-14 종합 명세 대조 감사](AI_TRADING_INTEGRATION_ALIGNMENT_AUDIT_20261014.md) — 정책별 자료·가격·모델 지원·순자산·비상정지와 미완료 범위 |
| 시스템 책임과 수정 진입점 | [현재 아키텍처](../ARCHITECTURE_CURRENT.md) · [모듈 지도](../MODULE_MAP.md) |
| 작업 규칙 | 정본 [AGENTS](../AGENTS.md) · [개발 불변 규칙](../DEVELOPMENT_GUARDRAILS.md) · [모델 정책 요약](MODEL_HANDOFF_POLICY.md): GPT-6.1 Sol 기본, Luna는 네 조건을 모두 만족하는 저위험 작업만 허용, 실질적인 상위 판단에 Astra, Sol High 유지 선호 |

## 계약과 데이터

| 영역 | 문서 |
|---|---|
| 공개 API·저장·시간/출처 | [API 계약](../API_CONTRACT.md) · [DB 스키마](../DB_SCHEMA.md) · [과거 데이터 계약](../HISTORICAL_DATA_CONTRACT.md) |
| 요청→저장→소비자 | [시장 데이터 흐름](MARKET_DATA_FLOW.md) · [DB 관계도·사전](database/README.md) · [선택형 지도](database/atlas.html) |
| TOP20 장후 재계산 설계·구현 경계 | [장중 집계와 장후 분봉 재계산](TOP20_AFTER_CLOSE_RECONCILIATION.md) — 로컬 구현·과거일 읽기 대조, NAS·PG gate 대기 |
| 실시간 기존 구독 유지 | [차등 구독·승인·녹화 계약](INCREMENTAL_REALTIME_SUBSCRIPTION_DESIGN.md) — 로컬 구현·관련 회귀, NAS 전용 검증/운영 반영 대기 |
| 과거 자료와 테마/학습 입력 | [과거 자료 확보](HISTORICAL_BACKFILL_PLAN.md) · [PC 완성 과거뉴스 archive 계약](PREPARED_NEWS_ARCHIVE_DESIGN_REVIEW.md) |
| 연구·평가·모의 실행 | [연구 요청 형식](RESEARCH_REQUEST_FORMAT.md) · [전진 평가](FORWARD_EVALUATION_CONTRACT.md) · [키움 모의 실행](KIWOOM_MOCK_EXECUTION_CONTRACT.md) |
| 첫 모의 후보의 미채택 기준·final 범위 | [검토용 초안](MOCK_CANDIDATE_CRITERIA_PROPOSAL.md) — 실제 기준 파일·후보 승인과 구분 |
| 백업·복원 | [Google Drive 엄격 복원](GOOGLE_DRIVE_RESTORE_DESIGN.md) |

DB 관계도의 NAS 카탈로그는 **2026-10-01 실측 snapshot**이다. 최신 운영 스키마를 이번에 재조회한 자료가 아니며 코드 계약·실제 배포와 구분한다.

## 검증·DB·진단

| 목적 | 문서 |
|---|---|
| 기능 보존 절차와 현재 검증 공백 | [기능 보존 검증](DEPENDENCY_REFACTOR_VERIFICATION_PLAN.md) · [CI 계약과 최종 테스트 감사](TEST_FINAL_QUALITY_AUDIT.md) |
| 감사 이후 변경분의 테스트 관리 | [증분 검토·원장·실행/CI 확인](INCREMENTAL_TEST_REVIEW.md) |
| DB 연결·transaction 보존 | [DB 리팩터링 기준선](db_refactoring/README.md) · [공통 DB 접근/관측](COMMON_DB_ACCESS_OBSERVABILITY_REVIEW.md) |
| writer 등록과 저장 경로 | [저장 경로 감사](KIWOOM_STORAGE_WRITE_AUDIT.md) |
| 부하 후보와 외부 입력 수명 | [전체 부하 감사](WHOLE_APP_LOAD_OPTIMIZATION_REVIEW.md) · [외부 입력 lifecycle 감사](EXTERNAL_INPUT_LIFECYCLE_AUDIT.md) |
| 동일 사건·초기 상태의 반복 실험 | [기록 workload 실험 계약](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md) · [경량 trace/부분 replay 계약](DB_REPLAY_TRACE_DESIGN.md) |
| 실제 진단 사용·API·제어 경계 | [NAS 진단 운영·API·하위 제어 통합 계약](NAS_RUNTIME_DIAGNOSTICS.md) |

기준선 JSON·승인 원장·DB 카탈로그는 감사 도구와 계약 근거다. 이름이나 생성일만으로 삭제하지 않는다. 경량 trace·부분 replay와 전체 원인 Replay의 완료 조건도 서로 다르다.

## 운영·사용

- [NAS 설치·운영](../deploy/synology/README.md) · [NAS 제한 운영 명령](NAS_OPERATOR_COMMANDS.md) — 개발 종료 뒤 권한 회수 잔여 포함.
- [패키징·업데이트](UPDATE_PACKAGING.md) · [API 발급](API_발급_가이드.md) · [OCR 모델 설치](OCR_모델_직접_설치.md) · [강의 전략팩](AI_강의_전략팩_적용_가이드.md).
- [2.1.0 릴리즈 노트](RELEASE_NOTES_v2.1.0.md) · [변경 기록](../CHANGELOG.md).
- [키움 공식 자료 로컬 색인](reference/키움_REST_API_로컬_텍스트_색인.md).

## 과거 기록과 유지 방법

- [아카이브 전체 안내](archive/README.md) · [2026-10-11 이동·통합 목록](archive/2026-10-11/README.md) · [이번 문서 검증](DOCUMENTATION_VALIDATION.md).
- 완료된 AppController/서버 수명 계획, 이전 테스트 조사/CI 편입 보고서, 8월 API 검증은 아카이브에 보존했다. 운영 미확인과 원인 미확정 항목은 OPEN_ITEMS에 남겼다.
- 상태·우선순위·책임·필드/단위·운영 절차는 각각 해당 정본 한 곳에 갱신한다. 같은 완료 기록을 여러 정본에 계속 덧붙이지 않는다.
- 변경 시 날짜·소스·검증 환경을 적고 미확인을 완료로 올리지 않는다. 이전 전문과 근거는 날짜별 아카이브에 남기고 링크를 함께 갱신한다.

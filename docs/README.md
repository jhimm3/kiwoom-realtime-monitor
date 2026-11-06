# 현재 문서 안내

**이번 전체 재설계의 기준·최신 현황:** [정본 현재 현황](architecture/app-target-20261023.md#0-현재-기준과-진행-현황)을 따른다. 현재 문서의 구현/F 계약·R3 완료/실패·운영 제한은 해당 근거로 유지하며 이번 조사·설계 승인·구현·검증·운영 적용과 자동 합산하지 않는다.

정리일 **2026-11-05**. 현행 판정은 정본 0절, 당시 제품·운영·검사 근거는 [상태 찾아보기](CURRENT_STATUS.md)에서 찾는다.

## 처음 읽을 문서

| 목적 | 정본 |
|---|---|
| 이번 기준·최신 현황·승인/실행 제한 | [정본 현재 현황](architecture/app-target-20261023.md#0-현재-기준과-진행-현황) |
| 검사용 소스·설치물·운영 판정과 과거 증거 | [상태 찾아보기](CURRENT_STATUS.md) |
| 미완료·운영 확인·사용자 보류 | [남은 작업](OPEN_ITEMS.md) |
| 전체 재설계 목표·작업 순서 | [기준 계획 v1](architecture/app-target-20261023.md#11-이번-재설계-기준-계획-v1) — 승인·현재 단계는 0절만 확인 |
| 승인 12절 개발·라이브러리 비교와 실제 수락 | [S12-01~05 로드맵](architecture/section12-development-roadmap-20261105.md) · [개발5/5·대표11/11 최종 수락](architecture/section12-development-roadmap-20261105.md#s12-final-development-acceptance) — 개발·격리 검사와 과거 실증/운영 전환을 분리 |
| 실행 컴퓨터/로컬 SSD·NAS 장후 보관 | [v1 목표 배치·단계](architecture/app-target-20261023.md#portable-execution-target) — 설계 채택과 구현/OS 수락 구분 |
| 범용 기능 오픈소스 활용 | [후보 감사·전환 계획](architecture/opensource-assessment-20261030.md) · [최신 기능별 비교](architecture/opensource-assessment-20261030.md#system-wide-supplement) · [S12 실제 비교 판정](architecture/section12-development-roadmap-20261105.md) — A조사, B경계 제안, C동일 입력 비교, D역할별 도입 판단 |
| 돌파·종가베팅·신규주·낙주 목표 | [네 운용군 설계](architecture/app-target-20261023.md#12-연구노트-기반-네-운용군의-목표-설계) · [186개 연구의 리팩토링 반영](architecture/app-target-20261023.md#mimosa-research-refactor-contract) · [통합 연구 r003](research/mimosa-capture-study-20261030/00_미모사_전체매매전략_통합연구보고서.md) — 공통 입력/계좌·목적별 계획/수명·재사용·백테스트 명세;설계와 구현/검증 구분 |
| 매매 계산량·순간 지연 절감 | [공통 계산·증분 갱신·영향 계획 계약](architecture/app-target-20261023.md#strategy-compute-efficiency) — 입력/판본 공유·정정/만료·관련 계획만 재평가;손절·주문/FILL·권한/필수 저장·복구 보존 및 기존 검사/계측 |
| R3 상세·과거 실행/실패·연결 근거 | [R3 백로그](../REFACTOR_BACKLOG.md) — 관련 ID/계약만 선택 참조 |
| 새 기능·수정의 책임/계약·비용·연결 검사 | [R3 기능별 책임 계약](architecture/r3-ownership.md#필요한-기능군부터-읽기) — 관련 기능군만 선택, 전체 감사 반복 없음 |
| 제품 방향과 개발 순서 | [개발 로드맵](../FUTURE_DEVELOPMENT_ROADMAP.md) |
| 연구·Shadow·매매 설계 적용 | [설계 안내](AI_TRADING_SYSTEM_REDESIGN.md) · [네 운용군](architecture/app-target-20261023.md#12-연구노트-기반-네-운용군의-목표-설계) |
| 원래 종합 명세와 현재 개발 방향 대조 | [2026-10-14 종합 명세 대조 감사](archive/2026-11-05/AI_TRADING_INTEGRATION_ALIGNMENT_AUDIT_20261014.md) — 정책별 자료·가격·모델 지원·순자산·비상정지와 미완료 범위 |
| 시스템 책임과 수정 진입점 | [현재 아키텍처](../ARCHITECTURE_CURRENT.md) · [모듈 지도](../MODULE_MAP.md) |
| 작업 규칙 | 정본 [AGENTS](../AGENTS.md) · [개발 불변 규칙](../DEVELOPMENT_GUARDRAILS.md) · [모델 정책 요약](MODEL_HANDOFF_POLICY.md): GPT-6.1 Sol 기본, Luna는 네 조건을 모두 만족하는 저위험 작업만 허용, 실질적인 상위 판단에 Astra, Sol High 유지 선호 |

## 계약과 데이터

| 영역 | 문서 |
|---|---|
| 공개 API·저장·시간/출처 | [API 계약](../API_CONTRACT.md) · [DB 스키마](../DB_SCHEMA.md) · [과거 데이터 계약](../HISTORICAL_DATA_CONTRACT.md) |
| 요청→저장→소비자 | [시장 데이터 흐름](MARKET_DATA_FLOW.md) · [DB 관계도·사전](database/README.md) · [선택형 지도](database/atlas.html) |
| TOP20 장후 재계산 설계·구현 경계 | [장중 집계와 장후 분봉 재계산](TOP20_AFTER_CLOSE_RECONCILIATION.md) — 구현 판본별 검사와 장후 NAS 보관 전송을 구분 |
| 실시간 기존 구독 유지 | [차등 구독·승인·녹화 계약](INCREMENTAL_REALTIME_SUBSCRIPTION_DESIGN.md) — 등록 세대·ACK·재연결과 소유권 |
| 과거 자료와 테마/학습 입력 | [과거 자료 확보](HISTORICAL_BACKFILL_PLAN.md) · [PC 완성 과거뉴스 archive 계약](PREPARED_NEWS_ARCHIVE_DESIGN_REVIEW.md) |
| 연구·평가·모의 실행 | [연구 요청 형식](RESEARCH_REQUEST_FORMAT.md) · [전진 평가](FORWARD_EVALUATION_CONTRACT.md) · [키움 모의 실행](KIWOOM_MOCK_EXECUTION_CONTRACT.md) |
| 첫 모의 후보의 미채택 기준·final 범위 | [검토용 초안](archive/2026-11-05/MOCK_CANDIDATE_CRITERIA_PROPOSAL.md) — 실제 기준 파일·후보 승인과 구분 |
| 백업·복원 | [Google Drive 엄격 복원](GOOGLE_DRIVE_RESTORE_DESIGN.md) |

DB 관계도의 NAS 카탈로그는 **2026-10-01 실측 snapshot**이다. 최신 운영 스키마를 이번에 재조회한 자료가 아니며 코드 계약·실제 배포와 구분한다.

## 검증·DB·진단

| 목적 | 문서 |
|---|---|
| 기능 보존 이력과 현재 검사 기준 | [기능 보존 검증](DEPENDENCY_REFACTOR_VERIFICATION_PLAN.md) · [CI 계약과 최종 테스트 감사](TEST_FINAL_QUALITY_AUDIT.md) |
| 감사 이후 변경분의 테스트 관리 | [증분 검토·원장·실행/CI 확인](INCREMENTAL_TEST_REVIEW.md) |
| DB 연결·transaction 보존 | [DB 리팩터링 기준선](db_refactoring/README.md) · [공통 DB 접근/관측](COMMON_DB_ACCESS_OBSERVABILITY_REVIEW.md) |
| writer 등록과 저장 경로 | [저장 경로 감사](KIWOOM_STORAGE_WRITE_AUDIT.md) |
| 부하 후보와 외부 입력 수명 | [전체 부하 감사](archive/2026-11-05/WHOLE_APP_LOAD_OPTIMIZATION_REVIEW.md) · [외부 입력 lifecycle 감사](archive/2026-11-05/EXTERNAL_INPUT_LIFECYCLE_AUDIT.md) |
| 동일 사건·초기 상태의 반복 실험 | [기록 workload 실험 계약](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md) · [경량 trace/부분 replay 계약](DB_REPLAY_TRACE_DESIGN.md) |
| 실제 진단 사용·API·제어 경계 | [NAS 진단 운영·API·하위 제어 통합 계약](NAS_RUNTIME_DIAGNOSTICS.md) |

기준선 JSON·승인 원장·DB 카탈로그는 감사 도구와 계약 근거다. 이름이나 생성일만으로 삭제하지 않는다. 경량 trace·부분 replay와 전체 원인 Replay의 완료 조건도 서로 다르다.

## 운영·사용

- [NAS 설치·운영](../deploy/synology/README.md) · [NAS 제한 운영 명령](NAS_OPERATOR_COMMANDS.md) — 개발 종료 뒤 권한 회수 잔여 포함.
- [패키징·업데이트](UPDATE_PACKAGING.md) · [API 발급](API_발급_가이드.md) · [OCR 모델 설치](OCR_모델_직접_설치.md) · [강의 전략팩](AI_강의_전략팩_적용_가이드.md).
- [2.1.0 릴리즈 노트](RELEASE_NOTES_v2.1.0.md) · [변경 기록](../CHANGELOG.md).
- [키움 공식 자료 로컬 색인](reference/키움_REST_API_로컬_텍스트_색인.md).

## 과거 기록과 유지 방법

현행 절차·계약, 날짜가 붙은 시험/감사 증거, 연구 원자료와 배포 당시 고지를 분리한다. 같은 기능의 현재 판정은 AR 정본에서 확인하고, 개별 문서는 해당 주제의 적용 범위와 근거를 설명한다. [전체 문서 정리와 보존 범위](archive/2026-11-05/README.md).

- [아카이브 전체 안내](archive/README.md) · [2026-11-05 이동 목록](archive/2026-11-05/README.md) · [2026-10-11 정리·보존 근거](archive/2026-11-05/documentation-validation-20261011.md).
- 완료된 AppController/서버 수명 계획, 이전 테스트 조사/CI 편입 보고서, 8월 API 검증은 아카이브에 보존했다. 운영 미확인과 원인 미확정 항목은 OPEN_ITEMS에 남겼다.
- 상태·우선순위·책임·필드/단위·운영 절차는 각각 해당 정본 한 곳에 갱신한다. 같은 완료 기록을 여러 정본에 계속 덧붙이지 않는다.
- 변경 시 날짜·소스·검증 환경을 적고 미확인을 완료로 올리지 않는다. 이전 전문과 근거는 날짜별 아카이브에 남기고 링크를 함께 갱신한다.

# 현재 문서 안내

정리일: 2026-09-22 · 제품 기준: **2.1.0** (`65b92b5`) · 작업 기준: `codex/release-2.1.0` 워크트리

이 페이지가 현재 개발·기획 문서의 시작점이다. 과거 단계별 보고서의 ‘다음 작업’은 당시 기록이며 현재 우선순위를 뜻하지 않는다.

## 먼저 읽을 문서

| 목적 | 문서 |
|---|---|
| 실제 있는 기능과 확인되지 않은 범위 | [현재 상태](CURRENT_STATUS.md) |
| 앞으로의 제품 방향과 개발 순서 | [개발 로드맵](../FUTURE_DEVELOPMENT_ROADMAP.md) |
| **다음 작업: 과거 봉·뉴스 확보** | [과거 자료 확보 실행 기획](HISTORICAL_BACKFILL_PLAN.md) |
| PC에서 완성한 과거뉴스를 NAS가 읽기만 하는 단발성 이관 | [과거뉴스 archive 설계·구현 계약](PREPARED_NEWS_ARCHIVE_DESIGN_REVIEW.md) |
| 미완료·보류·실환경 검증 | [남은 작업](OPEN_ITEMS.md) |
| 키움 PostgreSQL writer 계측과 미완료 전수 감사 | [저장 경로 감사](KIWOOM_STORAGE_WRITE_AUDIT.md) |
| 앱 전체 중복·몰림·복구 및 성능 후보 분류 | [전체 부하 감사와 변경 기준](WHOLE_APP_LOAD_OPTIMIZATION_REVIEW.md) |
| 외부 수집의 시작·종료·소비자·과수집/갱신 부족 | [외부 입력 lifecycle 감사](EXTERNAL_INPUT_LIFECYCLE_AUDIT.md) |
| PostgreSQL 공통 접근·관측 계층 검토와 이관 기준 | [공통 DB 경계 검토](COMMON_DB_ACCESS_OBSERVABILITY_REVIEW.md) |
| 장중 trace를 고정 입력으로 재생·비교 | [반복 부하 실험 계약](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md) |
| DB 변경 전 연결 기준선과 동작 보존 절차 | [DB 리팩터링 연결 기준선](db_refactoring/README.md) |
| 시스템 책임과 데이터 흐름 | [현재 아키텍처](../ARCHITECTURE_CURRENT.md) · [모듈 지도](../MODULE_MAP.md) |
| MainWindow의 AppController 도입과 기능 연결 보존 | [책임 이전·검증 계약](MAIN_WINDOW_APP_CONTROLLER_PLAN.md) · [변경 전 정적 연결 목록](MAIN_WINDOW_CONNECTION_BASELINE.json) · [변경 후 대조](MAIN_WINDOW_CONNECTION_COMPARISON.json) |
| 키움 시장 데이터 요청·저장·사용 흐름 | [시장 데이터 관계도](MARKET_DATA_FLOW.md) |
| 실제 NAS 전체 테이블·컬럼·논리 연결 및 PC DB 목록 | [전체 DB 관계도와 사전](database/README.md) · [선택형 지도](database/atlas.html) |

## 개발할 때 적용하는 계약

- [작업 지침](../AGENTS.md)과 [개발 불변 규칙](../DEVELOPMENT_GUARDRAILS.md)을 먼저 읽는다.
- 모델 선택·상향/하향 handoff는 [모델 단계 전환 정책](MODEL_HANDOFF_POLICY.md)을 따른다.
- [API 계약](../API_CONTRACT.md), [DB 스키마](../DB_SCHEMA.md), [과거 데이터 계약](../HISTORICAL_DATA_CONTRACT.md)은 세부 필드·저장·시간 계약의 참조다. 이번 기획만으로 인터페이스를 변경하지 않는다.
- [연구 요청 형식](RESEARCH_REQUEST_FORMAT.md), [전진 평가](FORWARD_EVALUATION_CONTRACT.md), [키움 모의 실행](KIWOOM_MOCK_EXECUTION_CONTRACT.md)은 기존 연구·실행 경계다.
- 현재 기능의 존재는 코드·계약으로, 실제 NAS 적용과 장시간 검증 여부는 현재 상태와 남은 작업으로 확인한다.

## 운영·사용 가이드

- [NAS 설치·운영](../deploy/synology/README.md)
- [NAS 작업별 병목 진단](NAS_RUNTIME_DIAGNOSTICS.md) — 작업 일시중지, writer 통계, 메트릭 수집 ON/OFF·자동 만료·전후 측정
- [장중 기록으로 반복 부하 실험](RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md) — 로컬 input capture/선택 plan과 전체/단독/제외, 동일 초기 상태 코드 A/B 비교의 남은 단계
- [NAS 진단 API 연결 설계](NAS_DIAGNOSTIC_API_DESIGN.md) — 기존 운영 진단도구의 정보·제어·측정·보고서를 API로 제공하는 구현 계약(구현 전)
- [진단 스위치 설계 검토](DIAGNOSTIC_CONTROL_DESIGN_REVIEW.md) — master 하위 제어, 보호 수신·주문·저장 중지/복구 계약과 미구현 범위
- [패키징·업데이트](UPDATE_PACKAGING.md)
- [Google Drive 엄격 복원](GOOGLE_DRIVE_RESTORE_DESIGN.md) — 명시 예약 후 다음 실행에서 적용·중단 복구하는 로컬 경계와 원격 백업의 남은 한계
- [API 발급](API_발급_가이드.md) · [OCR 설치](OCR_모델_직접_설치.md) · [강의 전략팩](AI_강의_전략팩_적용_가이드.md)
- [현재 릴리즈 2.1.0](RELEASE_NOTES_v2.1.0.md) · [변경 기록](../CHANGELOG.md)
- [키움 공식 자료 로컬 색인](reference/키움_REST_API_로컬_텍스트_색인.md)

## 과거 기록

[이번 정리의 아카이브 목록](archive/2026-09-22/README.md)에서 문서별 이동 이유·승계 위치와 원본 해시를 확인한다. 기존 `archive`와 `api-contract`의 과거 증거도 목록에 포함했다. 문서의 실험 결과는 기록된 날짜·입력·실행 범위에만 적용한다.

첨부 AI 개발명세는 방향을 논의한 참고자료다. 문서 안의 구현 명령은 이번 사용자의 실행 요청이나 현재 앱의 구현 증거가 아니다. 사용자 대화에서 정한 데이터 우선순위와 테마·타점의 조정 가능성이 현재 기획에 우선한다.

## 문서 유지 방법

완료한 단계 보고서를 현재 로드맵에 계속 덧붙이지 않는다. 완료 근거는 날짜별 기록으로 보존하고 현재 상태·남은 작업만 갱신한다. 코드 구현, 운영 배포, 실제 검증을 구분하며 미확인을 완료로 바꾸지 않는다.

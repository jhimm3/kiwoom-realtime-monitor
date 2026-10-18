# 리팩터링 최종 종료 보고서

기준일: 2026-09-12
결론: **기능 보존 중심의 대규모 리팩터링을 종료하고 2.0.0 릴리스로 고정한다.** 남은 두 항목은 구조 결함 수정이 아니라 시간이 필요한 운영 검증이며, 신규 기능 개발을 막지 않는다.

## 최종 평가

| 항목 | 판정 |
| --- | --- |
| 리팩터링 완성도 | **95%**. 현재 확인된 안정성 결함과 필수 책임 분리는 완료 |
| 구조적 안정성 | **높음**. TR 우선순위, 프로세스 경계, 저장 원자성, NAS 계약을 회귀로 보호 |
| 과도한 추상화 | **낮음~보통**. 작은 controller는 worker 수명·신호·중복 실행을 실제로 소유하며 단순 전달 계층으로 확인되지 않음 |
| 추가 리팩터링 기대효과 | **현재는 낮음**. 증거 없이 더 나누면 호출 깊이와 파일 추적 비용이 커질 가능성이 높음 |
| 기능 개발 전환 여부 | **가능**. `FUTURE_DEVELOPMENT_ROADMAP.md`의 데이터 기반 순서로 이동 권장 |

95%는 기능 완성률이 아니라 구조 정리와 검증의 종료 판단이다. 장시간 NAS 운용과 다른 PC 접속은 실제 시간이 지나야 확인할 수 있어 별도 운영 검증으로 남긴다.

## 생성·갱신한 개발보조 문서

- `ARCHITECTURE_CURRENT.md`: 현재 실제 시스템과 데이터 흐름
- `MODULE_MAP.md`: 기능별 담당 파일과 먼저 볼 테스트
- `API_CONTRACT.md`: NAS REST/WebSocket 계약
- `DB_SCHEMA.md`: 로컬·중앙 DB, 마이그레이션, Raw/파생/사용자 원본 구분
- `DEVELOPMENT_GUARDRAILS.md`, `AGENTS.md`: TR 중앙화, 최소 수정, 증거 기반 버그 수정, 문서 갱신 규칙
- `AUDIT_REPORT.md`: 기능개발서와 구현 불일치, 구조 위험, 과추상화 판정
- `HISTORICAL_DATA_CONTRACT.md`: 과거 시장 재현용 시각·출처·결측 계약
- `FUTURE_DEVELOPMENT_ROADMAP.md`: NAS 자료 기반 시뮬레이션과 향후 기능 순서
- `REFACTORING_CLOSEOUT_PLAN.md`: 단계별 완료·보류 단일 원장
- `CHANGELOG.md`: 실제 변경 기록

## 검증 결과

- 핵심 회귀: **496개 통과** (`240 + 256`, 두 프로세스 묶음 모두 종료 코드 0)
- MainWindow·설정 GUI: **26개 통과**
- 뉴스 프로세스·창: **17개 통과**
- Windows 2.0.0 창 모드 패키지: mock 로컬 서버 시작·부모 종료 감지·정상 종료 코드 0 확인
- NAS 실행 빌드: `2026.09.12-shared-backup-contract-v1`
- `/health`, 인증 API, WebSocket ready/pong: 통과
- PostgreSQL 실제 통합: 중앙 스키마 v3, query cache, realtime, minute/daily bars, metadata, snapshots, documents, external bars 왕복 및 rollback 통과
- 실제 장애전환: NAS 정상 → 서버 중단 → 로컬 키움 `ka00198` 20종목 → NAS 재시작 → 중앙 경로 20종목 복귀 통과
- 마켓스테이트: 최근 100건에서 잘못된 `T88:88` 키 **0건**
- 사용자 데이터: NAS `.env`, `postgres-data`, `server-data` 보존 확인

## 실제 코드 변경 범위

상세 파일별 책임은 `MODULE_MAP.md`를 단일 지도처럼 사용한다. 주요 변경 경계는 다음과 같다.

- `presentation/main_window.py`: 순위 실행, 실시간 구독, 후속 worker 수명, 표 계산을 독립 정책/controller로 이동하고 Qt 화면 반영만 유지
- `journal_process.py`: 체결·봉·분석·전략·스냅샷 저장/조회 조정을 application/persistence 경계로 이동
- `central_server/*`: 중앙 TR broker, 실시간 수집, 독립 TOP20, PostgreSQL/SQLite 저장, 메타데이터, outbox, 외부시장 자료와 API 계약 정리
- `infrastructure/persistence/*`: 명시적 스키마 마이그레이션, 트랜잭션, 관측 메타데이터와 저장소 책임 분리
- `presentation/*_worker_controller.py`: QThread 생성·중복 실행·신호·종료 수명 경계 고정
- `scripts/run_core_regression.ps1`, `check_nas_operational.py`, `check_postgres_integration.py`, `check_failover_operational.py`: 반복 가능한 회귀·운영 검증 경로 마련

기존 화면·분석 문구·저장 의미를 바꾸기 위한 리팩터링은 하지 않았다. 동작 변경은 로그·DB·테스트로 확인된 결함의 최소 수정과 사용자가 별도로 요청한 기능에 한정했다.

## 기능개발서와 실제 구현의 남은 차이

- 과거 문서의 단일 `monitor.db` 설명과 달리 현재는 메인·뉴스·매매일지 SQLite 및 NAS PostgreSQL로 나뉜다. 현재 사실은 새 구조 문서를 우선한다.
- NAS PostgreSQL은 중앙 누적 원본이지만 앱은 오프라인·장애전환을 위해 로컬 SQLite 캐시와 병합을 유지한다. “PostgreSQL만 유일 원본”이라는 미래 설명은 현재 구현으로 간주하지 않는다.
- `personal_server`, `local_server` 내부 이름과 사용자 화면의 `NAS` 표현은 호환 데이터 때문에 내부 식별자를 유지한다.
- README/패키지 버전과 1.1.20 이후 누적 기능의 정식 릴리스 노트 정리는 실제 릴리스 작업에서 수행한다.

## 남은 구조적 위험과 판정

| 우선순위 | 대상 | 현재 판정 | 다시 손댈 조건 |
| --- | --- | --- | --- |
| 1 | `presentation/main_window.py` | 화면 조립은 크지만 추가 Manager는 이득이 불명확하여 유지 | UI 결함·변경이 다시 집중되거나 독립 책임이 확인될 때 |
| 2 | `journal_process.py` | 남은 책임은 주로 위젯 상태와 사용자 동작 순서이므로 유지 | 동일 orchestration 결함이 반복되거나 GUI 회귀에서 병목 확인 시 |
| 3 | `presentation/stock_news_window.py` | 선택 복원과 신호 조정이 남았으나 현 테스트 범위에서 안정 | 실제 종료/선택/중복 실행 결함이 재현될 때 |
| 4 | 중앙 콘텐츠 동기화 조정 | 프로세스별 정책 차이 때문에 완전 통합하지 않음 | 주기·충돌 규칙 결함이 여러 sync 모듈에 반복될 때 |
| 5 | `central_server/database.py` | 공통 codec 밖의 연결·placeholder·SQL은 DB 방언 경계로 유지 | 동일 CRUD 결함이 SQLite/PostgreSQL에서 반복될 때 |

다음에 하나만 구조적으로 정리한다면 **실제 결함 또는 프로파일 근거가 생긴 뒤 `stock_news_window.py`의 선택·worker 조정 경계**를 검토한다. 현재는 추가 리팩터링을 시작하지 않는다.

## 시작·조회 성능 결론

- 실제 DB 복제본에서 bootstrap 진입→첫 화면 표시는 약 `0.7258초 → 0.0744초`로 줄었다. 일봉 250개 보존 정리를 첫 화면 뒤로 이동한 효과가 확인됐다.
- 매매일지 315묶음 반복 조회는 `0.4356초 → 0.0048초`로 줄었다.
- Manager/Service/Controller 객체 수나 import 계층 자체가 확인된 주요 병목은 아니었다.
- 뉴스 프로세스가 메인 DB의 실시간 변경을 콘텐츠 변경으로 오인해 약 1.8만 건을 반복 동기화하던 피드백 루프를 제거했다.
- 성능 때문에 현재 구조를 되돌릴 수준의 근거는 없다.

## 후속 운영 검증과 완료 기준

아래는 리팩터링 종료를 막지 않으며 운영하면서 증거를 누적한다.

1. **NAS 장시간 운용**: 한 거래일 이상 실행 후 시작/종료 시점의 메모리·DB 크기와 순위/TOP20 최신 시각을 비교한다. 비정상 메모리 지속 증가, DB 저장 중단, 수집 공백이 없으면 통과다.
2. **다른 PC 접속**: 별도 PC에서 인증 API, WebSocket ready/pong, 순위·TOP20 조회를 실행하고 기존 PC와 동시 사용해도 오류가 없으면 통과다.
3. **정식 릴리스**: 버전 확정 때 README 사용자 기능 설명, 패키지 버전, 릴리스 노트와 GitHub 릴리스를 한 작업으로 맞춘다.

## 종료 결론

확인된 안정성 결함의 최소 수정, 핵심 책임 분리, 과추상화 감사, 전체 회귀, NAS 실제 배포, PostgreSQL 경계, 장애전환까지 완료했다. 따라서 이 시점부터 구조 정리를 기본 작업으로 계속하지 않고, 새 기능은 기존 계약과 테스트를 이용해 작은 독립 단위로 개발한다.

# 현재 앱과 검증 상태

확인 기준: 2026-09-23 · **현재 제품 릴리즈 2.1.0** · 로컬 `main`에는 2.1.0 이후 검증·문서 변경이 포함됨

현재 작업 소스는 `C:/Users/pc-1/Documents/ChatGPT/kiwoom-realtime-monitor`, 브랜치는 `main`이다. 이전 `e0b9` 워크트리의 검증된 변경과 현재 문서를 이 폴더에 fast-forward로 합쳤다. 테스트 실행기의 source root와 interpreter/data 위치는 별개다.

[2.1.0](https://github.com/jhimm3/kiwoom-realtime-monitor/releases/tag/v2.1.0), [2.0.0](https://github.com/jhimm3/kiwoom-realtime-monitor/releases/tag/v2.0.0), [1.1.20](https://github.com/jhimm3/kiwoom-realtime-monitor/releases/tag/v1.1.20) GitHub 릴리즈와 로컬 태그·현재 코드를 대조했다. 로컬 `main`의 후속 작업은 새 GitHub 릴리즈나 배포를 뜻하지 않는다.

## 구현된 기반

| 영역 | 현재 존재하는 기능 | 남겨 둘 구분 |
|---|---|---|
| 시세·NAS | 앱 독립 순위·시장·뉴스·계좌 수집, 봉/관측 revision, 직접 연결·장애전환 | 구현 존재와 실제 NAS 최신 이미지·누적 범위는 별도 |
| 고해상도 관측 | 수신 체결 기반 1초 OHLCV·대금·건수, VI·hot cohort·상한가 기록 | 전 종목 원시 틱/호가와 과거 공백 복원은 아님 |
| 뉴스 | 기사/본문/AI/사건 이력, 작업·cursor·원문 정제·추출 요약 | 현재 검색 API 경로와 증권 사이트 과거 수집은 별개 |
| 테마 | 프로필, 편집·병합·분리, 프로필별 이름 결정 원장, LLM 근거 제안·사용자 검토, 백업·NAS 동기화·시점별 스냅샷 | 실제 기사 품질 평가와 과거 사건 일괄 검토는 후속 |
| 타점 | KRX 돌파·눌림 재가속, 후보 감시, 판단·근거 저장 | 사용자 학습과 사례로 개선할 잠정 정책 |
| 연구 | 동결 export/bundle, 재생·Paper 체결/비용, 제한 탐색·지속 campaign·자원 제어 | 실제 대규모/24시간 검증과 미래 날짜 자동 확장은 별도 |
| 평가 | 독립 시간/종목 구간, 순차 검증, final 잠금·접근·복구·노출 원장 | 전체시장 일반화·외부 DB/수동 열람까지 미사용 증명은 아님 |
| 가설·일지 | 자동 후속 가설, 상세체결 projection/대조, 피드백·개선 제안·채택·재검증 | 연구 성공·수익성 보장을 뜻하지 않음 |
| 모의 실행 | 후보 게시·admission·위험/복구 gate·계좌 owner·runner/supervisor/UI | 제한 모의·실브로커 장중/장시간 검증은 별도 |
| 인증 | NAS vault, 복수 계좌 프로필·인증 교체·서버 시세 담당 선택 API | PC 시세 담당 선택 UI는 미연결. 직접/fallback 신원 결합의 남은 A4B와 구분 |

현재 연구 DB 계약은 v23, 중앙 인증 활성화 원장은 schema v19 계열이다. 세부 테이블과 호환 의미는 [DB 스키마](../DB_SCHEMA.md)를 따른다. 이전 문서의 v12/v17 등은 해당 단계 설명이며 현재 전체 구현 수준을 뜻하지 않는다.

## 운영 기록에서 확인된 것과 이번에 확인하지 않은 것

기존 보고서에는 PostgreSQL schema19 왕복·rollback 검증, HTTPS/WSS 연결·앱 주소 전환, 자동 모의운용 fixture/fake-cycle 검증 기록이 있다. 따라서 과거 단계의 ‘PostgreSQL/HTTPS/runner 미구현’을 현재 상태로 반복하지 않는다.

TOP20 화면의 최신 순위 조회는 목표 회차의 종목코드·종목명이 모두 채워진 20행을 NAS가 검증한 직후 메모리 projection을 사용한다. 따라서 키움 조회 뒤 PostgreSQL 스냅샷 저장이 느려져도 앱 표시는 그 저장을 기다리지 않는다. 이전 회차나 부분 응답은 공개하지 않고, 과거·연구 조회는 계속 저장 완료본만 사용한다. 2026-09-22 NAS 실행본 `2026.09.22-top20-live-projection-v1`에서 `/health.status=ok`, 중앙 실시간 `READY`, 최신 TOP20 20행과 `persistence_state=persisted`를 확인했다.

`2026.09.22-theme-suggestion-review-v1` 소스는 2026-09-22 19:46에 NAS로 동기화했고 추적 파일 957개의 SHA-256 일치와 운영 `.env`, `postgres-data`, `server-data`, `server-secrets` 제외를 확인했다. 기존 파일은 `X:\kiwoom-monitor-backups\20260922-194611-theme-suggestion-review-v1`에 보존했다. 컨테이너 재빌드와 새 `/health.server_build` 확인은 아직 하지 않았으므로 실행 중인 NAS 서버 기준은 위에서 확인한 `2026.09.22-top20-live-projection-v1`이다. 장시간 수집·다른 PC 동시 이용·실브로커 모의 검증은 [남은 작업](OPEN_ITEMS.md)에서 관리한다.

## 새 방향에서 아직 해야 하는 것

과거자료 운영 DB `data/historical_intelligence.sqlite3`를 만들었다. CREON 삼성전자 연속조회는 1분봉 190,102개(2024-08-29 이후)와 그 이전 5분봉 57,710개(2021-08-11~2024-08-28)를 겹치지 않게 저장했고, 5분봉 시각은 구간 종료시각으로 확인했다. 네이버 날짜 지정 검색은 원응답·기사 관계·원문 URL별 확인 시도와 원문 발행시각을 저장한다. 발행시각 확보, 원문 차단, 기사 없음, 시각 없음, 제목 불일치 등을 별도 상태로 남기며 발행시각을 확인한 기사만 현재 학습 적격으로 표시한다.

NAS의 `stock_aliases` 1,164행을 사용해 94,750개 후보 종목·일의 당시 상호를 선택하고, 이전 대화에서 정한 상호변경일 ±14일에는 구·신 이름을 함께 검색하도록 95,090개 재개 가능 뉴스 작업을 만들었다. 2026-09-22에는 DB와 대신 원응답을 `deploy/synology/server-data/historical-intelligence/v1/runs/20260921T185214Z-04bb4a3f5d43`에 불변 스냅샷으로 게시했고, 로컬 검증 복사본의 SHA-256 일치와 SQLite `integrity_check=ok`를 확인했다. NAS 파일을 네트워크에서 실행 중인 SQLite로 직접 열지 않는다. 다른 후보 종목으로 시세 수집 확대와 뉴스 작업 95,090개의 실제 소진·도달기간 확인은 남아 있다.

테마는 기존 프로필 안에 `alias`, `split_to`, `keep_separate` 결정을 저장한다. 사용자가 테마를 병합하거나 이름을 바꾸면 이전 표현이 대표명으로 해석되고, 분리하면 이후 같은 복합 이름을 가져와도 분리된 테마들로 확장된다. 분리된 두 테마는 별도 결정 전까지 다시 별칭으로 합칠 수 없다. 뉴스 AI는 여러 종목을 묶을 기사 근거가 있을 때만 원시 테마명·확신도·근거를 별도 후보로 저장한다. 테마 관리의 `AI 테마 제안 검토`는 기사 제목·발행시각·원문과 활성 프로필 결정을 함께 보여 주고 승인·거절 전에는 종목 테마를 바꾸지 않는다. 사용자가 적용 이름을 고쳐 승인하면 그 별칭 결정도 프로필에 남아 이후 같은 표현에 재사용된다. 같은 창의 뒤 행에서 이름을 수정하지 않았다면 앞 행에서 확정한 최신 별칭을 저장 시점에 다시 적용해 이전 이름으로 되돌리지 않는다. 후보와 검토 상태·기사 문맥은 프로필 복사, 테마 파일 백업과 NAS 전체 메타데이터 스냅샷에 포함된다. 실제 기사에 대한 제안 품질·의미별 병합 정확도와 과거 사건 일괄 후보 생성은 아직 검증하지 않았다.

외부 자료를 기존 strict TOP20 재생에 섞지 않기 위해 `historical_reconstruction/v1` 입력 계약과 hash 검증 loader를 추가했다. 후보는 `posthoc_candidate_days/v1`, `not_contemporaneous_top20=true`로 고정하고 후보 생성시각을 알 수 없으므로 export 시각을 `available_at`으로 쓴다. 봉과 뉴스는 실제 수집 관측시각을 유지하며, 종목·일에 1분봉이 있으면 1분만 선택하고 없을 때만 5분을 선택한다. 5분을 1분으로 확장하지 않는다. 첫 최종 로컬 표본 `data/research/historical-reconstruction/2026-09-18-initial-v2`는 후보 50개, 현재 확보된 1분봉 종목 2개, 미확보 48개, 봉 762행, 뉴스 관계 3,497행을 고정했다. 뉴스 관계 중 발행시각 확인 2,056개와 차단 1,295개, 시각 없음 74개 등 제외 상태도 manifest에 따로 집계한다. 이는 입력 연결 표본이며 기존 전략 평가나 학습 완료를 뜻하지 않는다.

후보일과 결과 구간을 분리한 복원본 `data/research/historical-reconstruction/2026-09-18-through-2026-09-21-v1`도 만들었다. 어댑터는 후보 50개를 `historical_candidate_population`으로 유지하고, 수집 작업이 완료된 000150·005930의 다음 거래일 1분봉 각 381개만 `data/research/historical-strategy-input/2026-09-18-v1`에 넣었다. 연구 clock은 2026-09-21 실제 봉 종료시각을 쓰되 원자료가 실제 확보된 2026-09-22 수집시각은 각 payload의 `source_available_at`에 남긴다. 기존 두 전략 Family의 가격 Factor는 이 입력을 실행할 수 있지만 당시 TOP20 자료가 아니므로 rank persistence Factor는 runner에서 거부한다. 현재 정책을 최종값으로 정하지 않았으므로 이 단계에서는 실제 성과 비교 결과를 만들지 않았다.

다기간 어댑터는 여러 후보일을 한 동결 입력에 넣되 각 사례의 결과를 후보 DB에서 확인한 바로 다음 거래일까지로 제한한다. 이 경계로 멀리 떨어진 후보일 사이의 봉을 한 사례에 계속 포함하거나 같은 결과 봉을 여러 사례 성과로 중복시키지 않는다. 첫 다기간 표본 `multi-period-2024-2026-v1`은 2024-12-24→12-26, 2025-05-29→05-30, 2026-01-14→01-15 세 사례와 연구 관측 7,567개를 포함하며, 각 원천·파생 manifest와 파일 hash를 일반 권한 로더로 재검증했다. 수집 진행 중 만든 불변 표본이므로 전체 후보 일반화나 시간순 TRAIN/VALIDATION/OOS 성과 완료를 뜻하지 않는다.

같은 표본의 `historical_chronological_split_plan/v1`은 사례를 통째로 시간순 배정해 2024 사례를 TRAIN, 2025 사례를 VALIDATION, 2026 사례를 OOS로 고정했다. 계획은 파생 데이터셋 ID와 revision hash에 결합되고 각 fold의 마지막 15:30 봉을 포함하는 미포함 종료 경계를 사용한다. OOS 상태는 `SEALED`, 결과 포함은 false이며 이 단계에서는 전략 실행·성과 조회·후보 선택을 하지 않았다.

분할 계획에서 `historical_development_inputs/v1` TRAIN·VALIDATION 입력만 별도 동결했다. TRAIN은 후보군 1개와 1분봉 2,239개, VALIDATION은 직전 후보군 seed와 새 후보군 2개 및 1분봉 2,661개다. 두 입력 모두 원본 역사 모집단 provenance와 source availability 제한을 유지하고 품질 판정 `PASS`·revision 재현성 `VERIFIED`를 확인했다. 패키지는 OOS 입력·관측·결과를 포함하지 않으며 아직 실제 전략 성과 비교를 실행하지 않았다.

이 개발 입력으로 기존 돌파·눌림 재가속 Family의 고정 TRAIN/VALIDATION 구조 실행도 완료했다. 투영 입력의 원천 `historical_reconstruction_strategy/v1` 표시를 runner가 함께 읽고 재생 커서가 `historical_candidate_population`을 소비하도록 보완했으며, 순위 지속성 Factor 차단도 투영 뒤 유지된다. 결과는 `data/research/historical-development-results/multi-period-2024-2026-v1`에 있다. TRAIN 돌파/눌림은 각각 후보 109/3건이었지만 전부 `next_tradable_bar_gap`으로 검열됐고, VALIDATION은 돌파 102건 중 1건만 체결·종료(-28원), 눌림 3건은 전부 검열됐다. 이는 수집 진행 중인 희소 표본의 구조 점검 결과이며 전략 우열·수익성 근거가 아니다. 파라미터는 잠정값, 비용은 브로커 미검증 개발 추정값이고 OOS는 계속 봉인 상태다.

같은 개발 입력의 재실행 준비도 검사를 추가했다. 현재 TRAIN은 후보 50개 중 분봉 7개·연속 1분쌍 6개, VALIDATION은 분봉과 연속쌍 모두 7개라 `BLOCKED`다. 기본 요청 생성은 후보 전부가 분봉과 최소 한 연속 1분쌍을 가질 때만 허용하며, 희소 입력의 구조 점검은 `--allow-partial`을 명시해야 한다. 후보 원장에는 `00499K` 같은 영문 포함 6자리 단축코드가 91개 있었고 숫자 전용 필터 때문에 대신 작업에서 빠진 사실도 확인했다. CREON bridge와 작업 생성을 영문 포함 6자리로 확장하고 현재 원장에 91개를 증분 등록했다.

LLM 학습과 단순 추론을 구분하기 위해 `historical_learning_cases/v1` 준비 계약을 추가했다. 사후 후보 선정, 복원 뉴스 제목·검색 요약, 아직 생성하지 않은 AI 해석, 다음 거래일 결과 라벨을 분리하며 strict 시점 재생이나 모델 학습 완료로 표시하지 않는다. 최종 첫 불변 표본 `data/research/historical-learning-cases/2026-09-18-v2`는 50사례, 뉴스 근거가 있는 사례 22개, 결과 봉이 있는 사례 2개다. 결과 없는 48사례와 차단·시각 미확인·후보일 뒤 발행 관계를 그대로 제외 원장에 남겼다. 현재 `semantic_relevance_reviewed=false`, `ai_interpretation_generated=false`, `model_weight_training_ready=false`다. 같은 데이터셋을 NAS `server-data/historical-intelligence/v1/learning-cases/historical-learning-cases-548aae706fe48133557c3ed398cfb1985fa5a80c1212b2daa623113ada877290`에 불변 게시했고 `latest.json`을 별도 갱신했으며 두 파일의 SHA-256 일치를 확인했다.
이 사례에서 `historical_news_review_queue/v1` 대기열도 만들었다. 2,035개 종목-기사 관계를 공급자·언론사·기사 식별자로 합쳐 고유 기사 1,422개로 줄였고, 종목별 기존 비AI 판정으로 우선 검토 105개를 표시했다. 규칙 판정은 정답이 아니며 전체 항목은 `pending`, `human_review_complete=false`, `llm_used=false`, `model_weight_training_ready=false`다. 로컬 불변 산출물은 `data/research/historical-news-review-queues/2026-09-18-v1`에 있다. 같은 파일을 NAS `server-data/historical-intelligence/v1/news-review-queues/historical-news-review-queue-578f89c206d709720184aa753649dc3840f949a066819b476fc27ce576311805`에 불변 게시하고 `latest.json`을 갱신했으며 두 파일의 SHA-256 일치를 확인했다.

`historical_news_review_workflow.py`로 우선 검토 105행을 `data/research/historical-news-review-work/2026-09-18-priority-v1.csv`에 내보냈다. 이 CSV는 편집 작업본이며 정답 원장이 아니다. 가져오기는 원본 대기열 ID와 파일 hash를 확인하고 관련 판정의 사건 ID, 테마 사용 시 프로필 이름, 검토자와 timezone 포함 검토시각을 요구한다. 검증된 행만 `historical_news_review_decisions/v1` 불변 결과가 되며 그 결과도 `model_weight_training_ready=false`다. 현재 입력된 사람 판정은 0건이다. 워크플로 소스는 `X:\kiwoom-monitor-backups\20260922-213000-human-news-review-workflow-v1` 백업 뒤 NAS와 동기화했고 추적 파일 966개의 SHA-256 불일치는 0개다. 오프라인 연구 CLI이므로 컨테이너 재빌드는 필요하지 않다.

같은 작업표는 앱의 전략 연구 창에서 `과거 뉴스 검토`로 연다. 화면은 최신 불변 대기열에 결합된 작업표만 읽고, 관련·무관·보류, 사건 ID, 기존 테마 프로필 이름과 테마명, 근거 메모를 행 단위로 원자 저장한다. `검토 결과 동결` 전 작업표는 계속 비권위 편집본이며 현재 사람 판정 0건 상태는 바뀌지 않았다.

과거 뉴스 검토 화면까지 포함한 추적 파일 968개는 NAS 프로젝트와 SHA-256 불일치 0개로 동기화했다. 기존 NAS 파일 966개는 `X:\kiwoom-monitor-backups\20260923-001448-historical-news-review-ui-v1`에 보존했고 운영 `.env`의 SHA-256은 전후 동일하다. `postgres-data`, `server-data`, `server-secrets`는 복사 대상에 포함하지 않았다. 서버 동작은 바뀌지 않아 컨테이너 재빌드는 필요하지 않다.

사람 판정 뒤의 사건 누수를 막는 `historical_news_event_split/v1` 계약도 구현했다. 관련 기사만 `canonical_event_id` 단위로 묶고 사건의 최초 발행시각을 timezone-aware 값으로 비교해 TRAIN·VALIDATION·봉인 OOS에 통째로 배정한다. 결정 산출물은 원본 대기열의 기사 발행시각·정밀도·시각 출처를 보존한다. 현재 사람 판정이 0건이므로 실제 분할 산출물은 만들지 않았고 모델 학습 준비 상태도 계속 false다.

사건 분할 코드까지 포함한 추적 파일 971개는 NAS 프로젝트와 SHA-256 불일치 0개로 동기화했다. 기존 NAS 파일 968개는 `X:\kiwoom-monitor-backups\20260923-003116-historical-news-event-split-v1`에 보존했다. 운영 `.env` 해시는 이전 확인값과 같고 `postgres-data`, `server-data`, `server-secrets`는 복사 대상에서 제외했다.

전략 연구의 과거 뉴스 검토 화면에서 `사건 분할 계획`을 직접 실행할 수 있다. 최신 불변 판정에 관련 사건이 최소 3개 있어야 하며 사용자가 TRAIN·VALIDATION 사건 수를 고르면 나머지 최소 1개를 OOS로 봉인한다. 현재 불변 판정 결과가 0개라 실제 계획은 생성하지 않았다.

앱 사건 분할 실행까지 포함한 추적 파일 971개는 NAS와 SHA-256 불일치 0개로 다시 동기화했다. 직전 NAS 파일 971개는 `X:\kiwoom-monitor-backups\20260923-004208-news-event-split-ui-v1`에 보존했고 운영 데이터·비밀 경로는 제외했다.

사건 분할 뒤 RAG·미세조정 비교가 공통으로 사용할 `historical_news_development_inputs/v1`도 구현했다. 이 계약은 TRAIN·VALIDATION에 배정된 관련 기사만 제목·검색 요약·발행시각 입력과 사람 확정 사건·테마 target으로 투영하고 OOS payload를 완전히 제외한다. 파일 hash, 결정 dataset ID, 분할 plan ID, 구간 간 사건 중복을 로드 때 다시 확인한다. 현재 판정과 분할이 0건이므로 실제 개발 입력은 만들지 않았다.

과거 뉴스 검토 화면의 `개발 입력 생성`은 최신 불변 사람 판정과 정확히 일치하는 가장 최근 사건 분할을 찾아 위 공통 입력을 불변 저장한다. 같은 산출물은 덮어쓰지 않고, 일치하는 분할이 없으면 먼저 사건 분할을 만들도록 안내한다.

앱 개발 입력 생성까지 포함한 추적 파일 974개는 NAS와 SHA-256 불일치 0개로 동기화했다. 직전 NAS 파일 974개는 `X:\kiwoom-monitor-backups\20260923-010929-news-development-inputs-ui-v1`에 보존했고 운영 데이터·비밀 경로는 제외했다.

공통 개발 입력 계약까지 포함한 추적 파일 974개는 NAS와 SHA-256 불일치 0개로 동기화했다. 직전 NAS 파일 968개는 `X:\kiwoom-monitor-backups\20260923-005948-historical-news-development-inputs-v1`에 보존했고 운영 데이터·비밀 경로는 제외했다.

수집 진행률은 NAS `deploy/synology/server-data/historical-intelligence/v1/STATUS.md`와 `status.json`에 게시한다. 발행시각이 검증된 과거 기사는 기존 인증된 `news_article` content 경로로 10작업마다 증분 전송한다. 운영 중 100작업마다 게시하는 상태는 작업 원장만 빠르게 집계하고, 17GB대 기사·원문시도 전체 집계는 최종 스냅샷 때 수행한다. 과거 뉴스 검색은 요청 시작 간격 0.5초를 유지하는 4개 작업자로 페이지 응답 대기를 겹친다. 검색과 원문 확인도 파이프라인으로 겹치고, 언론사 원문은 같은 도메인에 한 요청만 허용한 16개 작업자로 병렬 처리한다. 밀도가 50% 이상인 같은 종목·검색어·월의 대기 일자는 날짜 범위 검색으로 묶는다. 완료 표본의 일평균 페이지 수로 한 묶음을 예상 80페이지 이하로 제한하고, 표본이 없으면 일 2페이지로 계산한다. 평균 50페이지 이상인 조합은 일별 작업을 유지하며, 실제 100페이지에 도달한 범위는 날짜를 나눠 재개한다. 범위 결과의 검색 목록 날짜 또는 확인된 원문 `published_at`으로 기존 일별 원장에 다시 귀속하고 날짜를 확인할 수 없는 결과는 임의 날짜에 넣지 않는다. 네이버 검색이 HTTP 403/429를 반환하면 현재 작업을 `pending`으로 되돌리고 60초 후 자동 재개한다. NAS 운영 DB에서 `naver_historical_web`/`historical_backfill` revision으로 저장된 뒤 기존 BODY 작업기가 원문을 처리한다. 별도 역사 SQLite 스냅샷 자체를 운영 뉴스 DB로 열거나 덮어쓰지 않는다.

로컬 수집기 생존 여부는 프로젝트 루트의 `수집기_모니터.cmd`를 실행해 확인하고 정지한 수집기를 다시 시작할 수 있다. CLI 확인은 `scripts/show_historical_collectors.ps1`을 유지한다. 장시간 단일 종목은 종목·페이지·기사 또는 분봉 단계 heartbeat가 계속 갱신되면 정상 처리 중이다. 뉴스와 대신 수집기는 공용 SQLite writer 충돌을 기다린 뒤 저장하며, 대신 시작 시 대형 분봉 집계 동안 writer transaction을 유지하지 않는다. 네트워크를 쓸 수 없는 실행 세션은 작업 시도를 소진하지 않고 즉시 종료하며, 영문 포함 6자리 단축코드도 뉴스 수집 대상으로 허용한다.

기존 후보 DB는 읽기 전용으로 확인했다. 94,750개 후보 종목·일, 5,910,806개 일봉, 4,562,200개 분봉이 있고 분봉 작업 완료 중 81,901건은 0행이었다. 실제 확보 범위와 다음 작업은 [과거 자료 확보 기획](HISTORICAL_BACKFILL_PLAN.md)에 적었다.

## 근거를 찾는 위치

[현재 아키텍처](../ARCHITECTURE_CURRENT.md), [모듈 지도](../MODULE_MAP.md), [API 계약](../API_CONTRACT.md), [과거 데이터 계약](../HISTORICAL_DATA_CONTRACT.md)을 먼저 읽는다. 단계별 완료·테스트 수치는 [아카이브 문서 목록](archive/2026-09-22/README.md)에서 확인한다. 과거 테스트 수치를 이번 작업의 실행 결과로 인용하지 않는다.

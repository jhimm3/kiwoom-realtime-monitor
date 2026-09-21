# 현재 앱과 검증 상태

확인 기준: 2026-09-22 · **현재 제품 릴리즈 2.1.0** · 로컬 `main`에는 2.1.0 이후 검증·문서 변경이 포함됨

현재 작업 소스는 `C:/Users/pc-1/Documents/ChatGPT/kiwoom-realtime-monitor`, 브랜치는 `main`이다. 이전 `e0b9` 워크트리의 검증된 변경과 현재 문서를 이 폴더에 fast-forward로 합쳤다. 테스트 실행기의 source root와 interpreter/data 위치는 별개다.

[2.1.0](https://github.com/jhimm3/kiwoom-realtime-monitor/releases/tag/v2.1.0), [2.0.0](https://github.com/jhimm3/kiwoom-realtime-monitor/releases/tag/v2.0.0), [1.1.20](https://github.com/jhimm3/kiwoom-realtime-monitor/releases/tag/v1.1.20) GitHub 릴리즈와 로컬 태그·현재 코드를 대조했다. 로컬 `main`의 후속 작업은 새 GitHub 릴리즈나 배포를 뜻하지 않는다.

## 구현된 기반

| 영역 | 현재 존재하는 기능 | 남겨 둘 구분 |
|---|---|---|
| 시세·NAS | 앱 독립 순위·시장·뉴스·계좌 수집, 봉/관측 revision, 직접 연결·장애전환 | 구현 존재와 실제 NAS 최신 이미지·누적 범위는 별도 |
| 고해상도 관측 | 수신 체결 기반 1초 OHLCV·대금·건수, VI·hot cohort·상한가 기록 | 전 종목 원시 틱/호가와 과거 공백 복원은 아님 |
| 뉴스 | 기사/본문/AI/사건 이력, 작업·cursor·원문 정제·추출 요약 | 현재 검색 API 경로와 증권 사이트 과거 수집은 별개 |
| 테마 | 프로필, 편집·병합·분리, 백업·NAS 동기화·시점별 스냅샷 | 종목명 별칭과 의미상 같은 테마 별칭은 별개 |
| 타점 | KRX 돌파·눌림 재가속, 후보 감시, 판단·근거 저장 | 사용자 학습과 사례로 개선할 잠정 정책 |
| 연구 | 동결 export/bundle, 재생·Paper 체결/비용, 제한 탐색·지속 campaign·자원 제어 | 실제 대규모/24시간 검증과 미래 날짜 자동 확장은 별도 |
| 평가 | 독립 시간/종목 구간, 순차 검증, final 잠금·접근·복구·노출 원장 | 전체시장 일반화·외부 DB/수동 열람까지 미사용 증명은 아님 |
| 가설·일지 | 자동 후속 가설, 상세체결 projection/대조, 피드백·개선 제안·채택·재검증 | 연구 성공·수익성 보장을 뜻하지 않음 |
| 모의 실행 | 후보 게시·admission·위험/복구 gate·계좌 owner·runner/supervisor/UI | 제한 모의·실브로커 장중/장시간 검증은 별도 |
| 인증 | NAS vault, 복수 계좌 프로필·인증 교체·서버 시세 담당 선택 API | PC 시세 담당 선택 UI는 미연결. 직접/fallback 신원 결합의 남은 A4B와 구분 |

현재 연구 DB 계약은 v23, 중앙 인증 활성화 원장은 schema v19 계열이다. 세부 테이블과 호환 의미는 [DB 스키마](../DB_SCHEMA.md)를 따른다. 이전 문서의 v12/v17 등은 해당 단계 설명이며 현재 전체 구현 수준을 뜻하지 않는다.

## 운영 기록에서 확인된 것과 이번에 확인하지 않은 것

기존 보고서에는 PostgreSQL schema19 왕복·rollback 검증, HTTPS/WSS 연결·앱 주소 전환, 자동 모의운용 fixture/fake-cycle 검증 기록이 있다. 따라서 과거 단계의 ‘PostgreSQL/HTTPS/runner 미구현’을 현재 상태로 반복하지 않는다.

반면 최신 `market-cap-reference-v1` 소스 동기화 기록만으로 NAS 컨테이너 이미지·`/health` 반영까지 완료했다고 할 수 없다. 이번 작업은 실제 NAS 접속·배포·키 교체·주문을 수행하지 않았다. 장시간 수집·다른 PC 동시 이용·실브로커 모의 검증은 [남은 작업](OPEN_ITEMS.md)에서 관리한다.

## 새 방향에서 아직 해야 하는 것

과거자료 운영 DB `data/historical_intelligence.sqlite3`를 만들었다. CREON 삼성전자 연속조회는 1분봉 190,102개(2024-08-29 이후)와 그 이전 5분봉 57,710개(2021-08-11~2024-08-28)를 겹치지 않게 저장했고, 5분봉 시각은 구간 종료시각으로 확인했다. 네이버 날짜 지정 검색은 원응답·기사 관계·원문 URL별 확인 시도와 원문 발행시각을 저장한다. 발행시각 확보, 원문 차단, 기사 없음, 시각 없음, 제목 불일치 등을 별도 상태로 남기며 발행시각을 확인한 기사만 현재 학습 적격으로 표시한다.

NAS의 `stock_aliases` 1,164행을 사용해 94,750개 후보 종목·일의 당시 상호를 선택하고, 이전 대화에서 정한 상호변경일 ±14일에는 구·신 이름을 함께 검색하도록 95,090개 재개 가능 뉴스 작업을 만들었다. 2026-09-22에는 DB와 대신 원응답을 `deploy/synology/server-data/historical-intelligence/v1/runs/20260921T185214Z-04bb4a3f5d43`에 불변 스냅샷으로 게시했고, 로컬 검증 복사본의 SHA-256 일치와 SQLite `integrity_check=ok`를 확인했다. NAS 파일을 네트워크에서 실행 중인 SQLite로 직접 열지 않는다. 다른 후보 종목으로 시세 수집 확대, 뉴스 작업 95,090개의 실제 소진·도달기간 확인, 테마의 의미별 대표명/별칭과 사용자 결정 유지, 필요에 따른 로컬 LLM·학습은 아직 완료하지 않았다.

외부 자료를 기존 strict TOP20 재생에 섞지 않기 위해 `historical_reconstruction/v1` 입력 계약과 hash 검증 loader를 추가했다. 후보는 `posthoc_candidate_days/v1`, `not_contemporaneous_top20=true`로 고정하고 후보 생성시각을 알 수 없으므로 export 시각을 `available_at`으로 쓴다. 봉과 뉴스는 실제 수집 관측시각을 유지하며, 종목·일에 1분봉이 있으면 1분만 선택하고 없을 때만 5분을 선택한다. 5분을 1분으로 확장하지 않는다. 첫 최종 로컬 표본 `data/research/historical-reconstruction/2026-09-18-initial-v2`는 후보 50개, 현재 확보된 1분봉 종목 2개, 미확보 48개, 봉 762행, 뉴스 관계 3,497행을 고정했다. 뉴스 관계 중 발행시각 확인 2,056개와 차단 1,295개, 시각 없음 74개 등 제외 상태도 manifest에 따로 집계한다. 이는 입력 연결 표본이며 기존 전략 평가나 학습 완료를 뜻하지 않는다.

수집 진행률은 NAS `deploy/synology/server-data/historical-intelligence/v1/STATUS.md`와 `status.json`에 게시한다. 발행시각이 검증된 과거 기사는 기존 인증된 `news_article` content 경로로 증분 전송하며, NAS 운영 DB에서 `naver_historical_web`/`historical_backfill` revision으로 저장된 뒤 기존 BODY 작업기가 원문을 처리한다. 별도 역사 SQLite 스냅샷 자체를 운영 뉴스 DB로 열거나 덮어쓰지 않는다.

기존 후보 DB는 읽기 전용으로 확인했다. 94,750개 후보 종목·일, 5,910,806개 일봉, 4,562,200개 분봉이 있고 분봉 작업 완료 중 81,901건은 0행이었다. 실제 확보 범위와 다음 작업은 [과거 자료 확보 기획](HISTORICAL_BACKFILL_PLAN.md)에 적었다.

## 근거를 찾는 위치

[현재 아키텍처](../ARCHITECTURE_CURRENT.md), [모듈 지도](../MODULE_MAP.md), [API 계약](../API_CONTRACT.md), [과거 데이터 계약](../HISTORICAL_DATA_CONTRACT.md)을 먼저 읽는다. 단계별 완료·테스트 수치는 [아카이브 문서 목록](archive/2026-09-22/README.md)에서 확인한다. 과거 테스트 수치를 이번 작업의 실행 결과로 인용하지 않는다.

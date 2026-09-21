# 현재 앱과 검증 상태

확인 기준: 2026-09-22 · **릴리즈 2.1.0** · tag/HEAD `65b92b572e8faaf8df8a010f06861d055038ee3e`

작업 소스는 `C:/Users/pc-1/.codex/worktrees/e0b9/kiwoom-realtime-monitor`, 브랜치는 `codex/release-2.1.0`이다. 오래된 원본 작업폴더의 README 버전을 현재 앱 버전으로 사용하지 않는다. 테스트 실행기의 source root와 interpreter/data 위치는 별개다.

[2.1.0](https://github.com/jhimm3/kiwoom-realtime-monitor/releases/tag/v2.1.0), [2.0.0](https://github.com/jhimm3/kiwoom-realtime-monitor/releases/tag/v2.0.0), [1.1.20](https://github.com/jhimm3/kiwoom-realtime-monitor/releases/tag/v1.1.20) GitHub 릴리즈와 로컬 태그·현재 코드를 대조했다. 워크트리에는 매매일지 프로세스/worker/테스트와 CHANGELOG의 미커밋 변경이 있으며 이번 문서 정리가 그 변경의 검증·배포를 뜻하지 않는다.

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

대신 1분 약 2년/5분 약 5년 실제 확보, 네이버 증권 사이트 과거 뉴스 경로·본문·도달기간 확인, 외부 역사 자료의 앱 입력 연결, 테마의 의미별 대표명/별칭과 사용자 결정 유지, 필요에 따른 로컬 LLM·학습이 남아 있다. 이 기능을 문서만으로 구현 완료로 표시하지 않는다.

기존 후보 DB는 읽기 전용으로 확인했다. 94,750개 후보 종목·일, 5,910,806개 일봉, 4,562,200개 분봉이 있고 분봉 작업 완료 중 81,901건은 0행이었다. 실제 확보 범위와 다음 작업은 [과거 자료 확보 기획](HISTORICAL_BACKFILL_PLAN.md)에 적었다.

## 근거를 찾는 위치

[현재 아키텍처](../ARCHITECTURE_CURRENT.md), [모듈 지도](../MODULE_MAP.md), [API 계약](../API_CONTRACT.md), [과거 데이터 계약](../HISTORICAL_DATA_CONTRACT.md)을 먼저 읽는다. 단계별 완료·테스트 수치는 [아카이브 문서 목록](archive/2026-09-22/README.md)에서 확인한다. 과거 테스트 수치를 이번 작업의 실행 결과로 인용하지 않는다.

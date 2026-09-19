# R5d2c — 공통 뉴스 검색어 운영 UI와 수집 경계

2026-09-15. 로컬 구현 완료, NAS 동기화/배포 전.
누적 build `2026.09.15-runtime-credentials-r5-news-query-operations-v1`.

## 구현 범위

기존 NAS 연결 설정의 운영 폼에서 공통 뉴스 수집 ON/OFF·검색어 목록·검색어별 주기를 편집한다.
서버의 기존 news_query_set_enabled/news_query_set/news_query_set_refresh_seconds와 같은 operations
부분 PUT/worker/expected_revision CAS를 재사용했다. 새 endpoint나 서버 필드 계약 변경은 없다.
세 필드가 없는 구 NAS에는 입력을 비활성화하고 추가 필드를 전송하지 않는다.

검색어는 한 줄에 하나씩 입력한다. 앞뒤 공백/빈 줄/중복은 제거하되 '수주 계약'처럼 내부 공백은
유지한다. 정규화 후 최대 50개이고 UI ON은 최소 한 개를 요구한다. OFF에서는 빈 목록도 가능하다.
주기는 기존 60~86400초 범위다. 기존 등록 종목 뉴스 주기 및 선택 종목 60초 freshness와 구분한다.
공통 검색어는 NAS 운영값이며 PC 직접 뉴스 키/설정에 복사하지 않는다.
폼 항목 증가로 작은 화면에서 저장 버튼이 밀리지 않도록 NAS 폼만 스크롤하고 Save/Cancel을 밖에 유지했다.
설정 API 접속/뉴스 조회 I/O는 기존 worker에서 실행한다. 키움 TR/순위 우선순위 경로는 변경하지 않았다.

## 재현한 문제와 최소 수정

가짜 공급자의 첫 query 통신을 대기시킨 뒤 OFF로 바꾸어도 다음 query가 실행됐고,
300초 수집 후 60초로 줄여도 이전 next_schedule_at 때문에 새 주기 수집이 건너뛰어졌다.
두 회귀를 수정 전 실패로 확인했다 (`tmp/r5d2c-before.log`).

기존 QuerySetNewsCollector의 run_once에서 같은 enabled/queries/poll 정책을 확인하고,
접수한 검색어의 owned 페이지/저장은 완료하되 변경 뒤 낡은 정책의 다음 검색어를 시작하지 않는다.
다음 기존 정기 loop가 새 정책을 사용한다. 주기 변경이 즉시 네트워크 수집 명령을 만들지는 않는다.

정상 완료 cursor의 예약은 저장된 last_success + 현재 poll_seconds로 판정해 단축/연장을 적용한다.
실패 backoff, 부분 페이지 next_start와 명시 next_schedule_at=0은 기존 의미를 유지한다.
cursor를 삭제하거나 재기록하는 별도 경로/상태/manager를 만들지 않았다.
현재 접수한 기사/본문/분류 작업·기존 source cursor·요청 사용량·일일 상한을 보존한다.
검색어를 제거했다 다시 추가하면 이전 cursor를 재사용한다. 서버 재기동에서는 저장값이 ENV 기본값보다 우선한다.

## 검증

- 신규 10개: 수집 중 OFF/현재 접수 완료/나머지 query 중단, 주기 단축·연장,
  오류 backoff 보존, cursor 로드 중 query 교체와 제거/재추가 위치 보존,
  실제 로컬 API의 동일 수집기/기사/cursor/사용량/CAS/인증/입력 범위/재기동 저장값,
  구 NAS 필드 제외/한 줄 정규화, 입력 오류 전 I/O 차단, 작은 창 폼 스크롤/저장 버튼,
  실제 worker/client의 변경 필드만 PUT/CAS/PC 키 보존.
- 수정 직후 뉴스 source/Qt 관련 회귀 34개 통과. 가짜 공급자/임시 SQLite/offscreen Qt만 사용했다.
- 최종 뉴스/네이버·DART owner/작업/Qt/운영 client/서버/DB/조건검색/해외시세 관련 회귀
  205개 모두 통과, 종료 코드 0 (`tmp/r5d2c-regression.log`). 신규 10개를 포함한 개수다.
- Python 4개 구문/공백·누적 build 3곳 일치 확인. 가짜 상태의 일반/검색어/작은 창 화면을
  렌더링해 입력 가독성과 스크롤/Save 배치를 확인했다.
- 실제 NAS/PostgreSQL/공급자 API/사용자 DB는 변경하지 않았다.

## 다음

R0~R5 로컬 구현 완료. 다음은 R6 실전 인증 교체로 REST/단일 WebSocket/순위 우선순위·집계
상태와 실제 수신 공백을 보존한다. R7 누적 NAS 동기화/배포 및 실환경 검증은 미완료다.
중간 NAS 동기화·재빌드 없음. 모델 에스컬레이션 없음.

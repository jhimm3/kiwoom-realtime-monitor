# 현재 구현 아키텍처

로컬 작업 소스에서 확인한 진입점과 소유권을 정리한다. 최신 검증·설치·운영 적용 판정은 [AR 정본 0절](docs/architecture/app-target-20261023.md#0-현재-기준과-진행-현황)에서 각각 확인한다.

## 실행 단위와 데이터 흐름

| 경계 | 진입점 | 읽을 범위 |
| --- | --- | --- |
| PC 기동·조회 | [코드](src/kiwoom_monitor/bootstrap.py) | 전체 앱과 조회 전용 경로를 나눈다. 조회 전용의 자료 위치·역할을 기동 인자로 검증한다. |
| PC가 소유하는 서버 | [코드](src/kiwoom_monitor/infrastructure/central_server_process.py) | 로컬 서버 모드의 자식 시작·종료 결과를 관리한다. 원격 연결은 PC 소유 자식과 수명이 다르다. |
| 서버 조립 | [코드](src/kiwoom_monitor/central_server/app.py) | 서버 설정에서 수집·저장·뉴스·실행 경로를 조립한다. 변경 시 실제 역할 설정과 호출자를 확인한다. |
| 실시간 입력·순위 | [코드](src/kiwoom_monitor/central_server/realtime_collector.py) | 실시간 입력의 수집·RAM 경계를 소유한다. [TOP20 조립](src/kiwoom_monitor/central_server/autonomous_top20.py)와 저장·소비를 확인한다. |
| 뉴스 | [코드](src/kiwoom_monitor/central_server/news_jobs.py) | 실행 작업의 진행·수명을 맡는다. 기사·본문·판단 판본의 수락은 기존 저장 경계에서 확인한다. |
| 연구 | [코드](src/kiwoom_monitor/research_process.py) | 연구 프로세스 진입점이다. 동결 입력과 결과 수락을 주문 권한에서 분리한다. |
| 주문·체결 | [코드](src/kiwoom_monitor/application/order_lifecycle.py) | 주문 사건·저장·복구 계약을 보호한다. [모의 Runner](src/kiwoom_monitor/central_server/mock_automation_runner.py)의 수명과 계좌 실행 권한은 별도 경계다. |

저장 수락·transaction·재조회는 [DB 스키마](DB_SCHEMA.md)와 [API 계약](API_CONTRACT.md)에서, 호출자와 실패·복구는 [기능별 책임 계약](docs/architecture/r3-ownership.md)과 [모듈 지도](MODULE_MAP.md)에서 확인한다.

## 목표 배치와 실제 전환

[선택 실행 컴퓨터와 NAS 보관](docs/architecture/app-target-20261023.md#portable-execution-target)가 승인된 목표다. 운영체제별 설치, 장후 전송, 운영 전환 수락은 정본의 해당 경계에 기록한다. 실제 전환은 원본·대상과 기준시각을 확정한 뒤 진행한다.

[이전 전문](docs/archive/2026-11-05/ARCHITECTURE_CURRENT.md)에는 초기 구조와 판본별 구현·검증 근거를 보존했다.

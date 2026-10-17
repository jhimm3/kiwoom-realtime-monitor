# 뉴스·장전 시황과 시장 관찰의 메인 통합

2026-10-15 · 기준 main `93883dff76171398d858e9c9f5c3cfcf7fd115d2`

사용자 요청에 따라 main의 기존 작업을 보존하면서 ed9d 뉴스/ChatGPT 구독/장전 v4와 a5af 시장 관찰/기록시각 변경을 통합한다. NAS 배포·설정 변경·실제 계좌 전송은 이번 작업 범위가 아니다. 원본 워크트리를 초기화하거나 내용을 덮어쓰지 않는다.

## 입력 보존과 통합 방식

- 원본 main의 기존 미커밋/신규10파일, ed9d57파일, a5af16파일의 HEAD·변경 목록·원본 bytes/hash·patch·index 지문을 먼저 보존했다. 실제 data/.env/DB/credential 파일은 통합 입력에 포함하지 않았다.
- 두 워크트리의 출발점은 `50a70c9ce6cdf20915fde27f2d1f47becb82eadf`이고 main은 더 진행돼 있다. 각 워크트리의 출발점 대비 차이만 main 작업본 위에 3-way로 적용했다. 예전 워크트리 전체 파일로 최신 main을 교체하지 않았다.
- `market_theme_shadow_monitor._snapshot`의 완료 분봉 포함 조건(main A03)과 TOP20 관찰 범위(a5af)를 같은 기존 snapshot 호출에 함께 유지했다. 새 호출 owner/저장 경계/매매 정책을 만들지 않았다.
- 공통 문서3개의 독립 추가 절을 함께 보존했다. 서버 app에는 aware KST 시계 주입과 장전 시황/구독 서비스 시작·종료·인증 경로가 함께 들어간다.
- main의 position_target/mock_automation_risk/order_lifecycle/research_evaluation/realtime_collector/minute_bars/mock_automation_runner7파일은 기준 main HEAD와 LF 내용이 동일함을 확인했다. main의 다른 진행 중인 사건 복구·돌파 수정과 index 상태도 보존한다.

## 검증 범위

최종 통합 후보의 Python 1,063파일 구문 검사, diff 공백 검사, unit461/integration27 catalog, 신규 unit8모듈 등록 검사를 통과했다. 뉴스 API 공식 프로필106건/9worker는 skip0·시작/종료 LF 지문 동일로 통과했다. 중앙 서버·NAS 구독97건과 RAM 기록 크기 제한6건의 재검사도 통과했다.

초기 시장 관찰·A03·서버·분봉·포지션 합동422건에서는 실패10/오류7을 기록했다. 원인은 새 부가 관측을 고려하지 않은 기록 상한 fixture, 새 서비스/라우트/진단 필드/공급자 오류문을 반영하지 않은 계약 기대값, 그리고 실제 계정 선택 요청의 지연 annotation 해석 오류였다. 이 실행을 전체 통과로 표시하지 않는다. 마지막 항목은 실제 OpenAPI 생성 실패를 재현하고 `chatgpt_sessions.py`의 postponed annotations만 제거해 수정했으며, 본문 스키마 생성과 잘못된 필드/추가 필드의422 거부 회귀를 추가했다. 나머지는 기존 불변 fixture를 보존하고 새 계약 fixture의 정확한 부가 항목과 수명 순서, 기존 결정 ID·1MiB 상한을 검사하도록 보완했다. 위97건과6건은 해당 실패 항목을 포함한다. 변경되지 않은 시장/A03/분봉/포지션의 앞선 성공 검사는 다시 실행하지 않았다. 실제 계좌·NAS/PG·실시장 정확도 수락은 별도다.

최종 공식 프로필은 `data/diagnostics/main-integration-20261015/news-api-contract/run.json`, 서버/기록 검사와 보존 확인은 같은 폴더의 검증 자료를 따른다. 원래 실패 로그와 입력 패치·정확한 main 이전 파일 백업은 ed9d 작업폴더의 동일 진단 경로에 보존한다.

최종 통합 결과와 실행 검사 수·동일 LF 지문·main 기존 변경/index 보존은 `data/diagnostics/main-integration-20261015`의 보존 자료를 따른다. 이 작업에서 commit/push/NAS 배포를 수행한 것으로 표시하지 않는다. 전체 all-local과 외부 CI는 commit/배포 전 별도 필요한 검사이며, 과거 소스의 통과 기록을 현재 결과로 재사용하지 않는다.

## 여전히 남은 운영 연결

장전 JSON v4의 실제 Market State/C1 adapter, 종목/사건/관계 revision 매핑, 장중 가설 검증·소비 만료는 미연결이다. main 통합만으로 이 기능을 완료 처리하지 않는다. 나스닥/WTI 기존 수집 비활성화는 사용자 결정 후속이며 실제 NAS 설정은 아직 변경하지 않았다. NAS 구독 runtime/계정 연결과 새 통합 배포본·예약07:55 검증, 정상 시각 자료를 통한 시장 관찰 수락이 남는다. 예전 뉴스 전용 설치 준비본을 그대로 통합 배포본으로 사용하지 않는다.

[장전 계약](morning-market-brief-alignment.md) · [시장 관찰](market-context.md)

## NAS 통합 검증 후속 — 2026-10-15

후속 사용자 `하자` 요청으로 통합 후보의 NAS 격리 검증과 런타임 설치 준비를 진행한다. v3 후보의 selected198/storage114는 통과했고 운영 source/DB/설정/예약은 유지했다. 테스트 구성과 DB 감사 원장 보완, 최초 실패·Windows 전체 회귀·관리자 인증 및 실제 적용 경계는 [통합 NAS 검증 원장](nas-integrated-validation-20261015.md)에서 이어 기록한다. 앞 절의 미검증·미배포 설명은 통합 작업 종료 당시 근거다.

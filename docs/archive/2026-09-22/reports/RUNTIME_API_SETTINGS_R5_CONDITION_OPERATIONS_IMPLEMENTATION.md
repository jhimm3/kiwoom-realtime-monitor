> **과거 기록** · 원래 경로: `reports/RUNTIME_API_SETTINGS_R5_CONDITION_OPERATIONS_IMPLEMENTATION.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# R5d2b — 조건검색 운영 런타임 변경

2026-09-15. 로컬 구현 완료, NAS 동기화/배포 전.
누적 build `2026.09.15-runtime-credentials-r5-condition-operations-v1`.

## 변경 범위와 확인된 원인

기존 조건검색은 부팅 설정을 사용하고 WebSocket 연결 시 조건식 목록/등록을 실행했다.
새 운영 입력을 단순 대입하면 등록된 seq와 설정 이름이 달라지고, 접수 queue의 신호가 저장될 때
현재 이름을 읽어 과거 신호에 새 조건명을 붙일 수 있다. queue 접수 → 정책 변경 → 저장 순서를
가짜 실행으로 재현하고 접수 당시 seq/name을 함께 보존했다.

기존 NAS 운영 화면에 조건검색 추적 ON/OFF, 정확한 조건명, 이름에 포함된 문자열을 추가했다.
기존 operations의 부분 PUT/인증/revision CAS를 사용한다. 정확한 이름이 우선하고 문자열 선택은
유일하게 일치할 때만 가능하다. ON에서 선택값이 모두 비면 저장 전에 거절한다.
실전 broker/시장 이벤트 서비스가 없는 NAS에는 지원 불가 flag를 반환하고 새 필드를 거절한다.
구 NAS의 필드가 없으면 PC 입력을 비활성화하고 전송하지 않는다.

## 등록과 보존 계약

동일 market_events 인스턴스와 기존 단일 WebSocket 수신 loop에서 목록/등록/해제를 처리한다.
새 WebSocket·manager·범용 owner·SQL 테이블을 추가하지 않았고 HTTP handler는 상류 응답을 기다리지 않는다.
새 조건 전체 초기 페이지가 확인되기 전에는 이전 활성 조건을 유지한다.
초기 결과와 interleaved 실시간 신호를 최대 5,000건 보류하고 초기 결과 다음 실시간 신호 순서로
적용한다. 이 순서로 후속 초기 페이지의 I가 먼저 도착한 실시간 D를 덮어쓰는 문제를 막는다.
전체 보류분이 저장 queue에 들어갈 수 있는지 전환 전 확인한다.

등록 성공 뒤 이전 조건을 CNSRCLR로 해제하고 ACK를 확인한다. 실패한 후보도 해제한다.
등록/해제 오류·15초 timeout·누락/반복 cursor·잘못된 형식·queue 초과는 복구 필요로 공개한다.
요청 중 정책이 다시 바뀌면 낡은 목록/후보를 적용하지 않고 정리 후 최신 정책을 등록한다.
runtime 변경 뒤 seq 없는 초기 응답은 잘못된 조건에 귀속하지 않고 timeout 처리한다.
같은 영속 revision으로 명시 재저장하면 다시 시도한다. hot-cohorts 조회도 현재 메모리 runtime 상태를 합친다.

OFF는 신규 조건 신호를 즉시 무시하며 상류 등록 해제를 요청한다. 이미 접수한 신호/기존 코호트,
당일 및 다음 관측 거래일 보존, VI와 체결 수집/구독은 유지한다. 다시 ON하면 동일 서비스로 등록한다.
저장 설정은 재기동 시 ENV 기본값보다 우선한다. 저장 실패는 이전 실행/버전을 유지하며,
저장 후 setter 실패와 나중의 상류 등록 실패는 저장 완료와 실행 복구 필요를 구분한다.
operations ACTIVE는 정책 전달 완료이고 condition_status ACTIVE는 실제 등록 완료다.

프로토콜 근거는 [키움증권 공식 조건검색 문서](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/kiwoom_docs/%EC%A1%B0%EA%B1%B4%EA%B2%80%EC%83%89.md)의
ka10173/ka10174 조회 응답과 REAL 02, CNSRCLR seq/결과코드다. 2026-09-15 공식 저장소 문서의
검색 색인 내용을 확인했다. 실제 NAS ACK·순간 동시 등록 허용 및 공급자 응답 지연은 R7에서 확인한다.

## 검증 및 남은 범위

- 신규 17개: 전체 페이지/실시간 순서, 기존 코호트 보존, 조건명 누락/모호함, 형식 오류,
  등록 실패·명시 retry, 잘못된/누락 seq, timeout 무한 재시도 방지, OFF/ON 서비스 보존,
  낡은 목록/후보, 누락/반복 cursor, queue 전체 수용, 접수 문맥, 해제 ACK 실패,
  실제 로컬 API 인증/strict 필드/CAS/저장 실패/적용 실패/재시작 보존, 구 NAS/Qt 상태.
- 최종 관련 회귀 172개 통과, 종료 코드 0 (`tmp/r5d2b-regression.log`). 추가로 동일 가짜
  WebSocket 실제 수신 loop의 정책 적용/연결 및 구독 교체 없음과 최신 runtime 진단 조회를 검증해
  신규 경계 17개 모두 통과했다 (`tmp/r5d2b-final-boundaries.log`). 중복 실행을 성공 개수로 합산하지 않는다.
- Python 6개 구문/공백과 누적 build 3곳 일치 확인. 자동 조회와 local mirror 저장을 막은
  가짜 상태 설정창을 offscreen으로 렌더링해 조건 입력과 실제 활성 조건 표시를 확인했다.
- 실제 NAS/PostgreSQL/공급자 API/사용자 DB는 변경하지 않았다. 실환경 검증은 미완료다.

다음은 R5d2c 공통 뉴스 query-set 운영 UI다. 기존 서버 API는 구현돼 있으나 PC 뉴스/NAS 설정에
query-set 입력이 없어 기존 부분 PUT/CAS에 연결한다. R6 실전 인증 owner·R7 누적 배포/실환경도 남았다.
중간 NAS 동기화·재빌드 없음. 모델 에스컬레이션 없음.

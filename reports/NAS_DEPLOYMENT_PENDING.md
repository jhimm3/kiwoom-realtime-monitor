# NAS 배포 및 잔여 검증 상태

## 2026-09-16 누적 빌드 — 순위·신고가·기본정보 성능 보완

- 배포 후보 build: `2026.09.16-ranking-metadata-freshness-v1`.
- 현재 워크트리의 계좌·연구·자동 모의운용 안전 보완 등 누적 변경 전체와 함께, 30초 순위 경계 예약, 신규 추적 종목 신고가용 일봉 준비, 당일 `ka10001` 최신성 검사를 포함한다.
- 뉴스 경로는 이번 후속 작업에서 추가 변경하지 않았다. 기존 누적 뉴스 변경은 워크트리에 이미 포함된 상태 그대로 빌드된다.
- 관련 중앙 서버 회귀 118개와 누적 핵심 회귀 1,131개(634+497), 전체 Python 문법 검사가 통과했다.
- Git 관리·신규 파일 862개를 `X:\kiwoom-monitor`에 누적 동기화했고 SHA-256 불일치는 0개다. 기존 763개 파일은 `X:\kiwoom-monitor-backups\20260916-223901-ranking-metadata-freshness-v1`에 보존했으며 운영 `.env`, `postgres-data`, `server-data`는 유지했다.
- 이미지 빌드·컨테이너 재생성·`/health.server_build` 확인은 아직 남아 있다.

## 2026-09-16 R7c2 HTTPS 초기 연결 완료

- 사용자 재적용 후 HTTPS 8443 R7 build/health·DB·프로필 메타데이터·WSS ready(101) 통과.
- 필수값 없는 JSON 검사가 INVALID_CREDENTIAL_REQUEST로 거부됨: HTTPS secure boundary 통과 확인.
  실제 공급자 키 전송/프로필 생성/교체/주문은 없었다.
- 앱 저장 NAS 주소도 HTTPS 8443으로 변경 완료, 기존 접속 토큰/다른 설정은 보존·검증했다.
- 남음: 앱 재시작 후 실제 화면 확인, 장중 다계좌/REG/교체 관측 간격, PostgreSQL 통합/실제 Linux 권한.
- 이번 HTTPS 설정으로 추가 이미지 재빌드는 필요 없다. 이전 절은 진행 당시 이력이다.

## 2026-09-16 R7c1 사용자 승인 후 프록시 신뢰 초기 설정

- 사용자 서버 로그에서 실제 peer 172.23.0.1 확인, 명시 승인 후 NAS env에 해당 IP 한 줄만 추가 완료.
- 기존 다른 바이트/키 유지, 로컬 DPAPI 암호화 복구본의 복원 일치 검증 완료. 평문 백업은 만들지 않았다.
- 기존 실행 서버는 아직 HTTPS_REQUIRED다. server 컨테이너 설정 재적용/재생성 필요(이미지 빌드 불필요).
- WSS 404도 남아 있으며 WebSocket 헤더/Upgrade 전달 확인 필요. 기존 HTTPS DB/metadata 읽기는 정상.
- 서버/이미지/DB/앱 주소/키 교체/주문은 이 단계에서 실행하지 않았다.

## 2026-09-16 런타임 인증 R7c HTTPS 8443 확인

- DSM의 기존 443을 유지하고 사용자가 API 프록시를 8443에 설정했다.
- HTTPS 8443의 엄격한 TLS/R7 build/health·인증 capability·프로필 메타데이터·DB 읽기 통과.
- WSS는 404: 역방향 프록시 WebSocket 헤더/경로 확인 필요.
- 변경 불가능한 필수값 없는 JSON transport 검사에서 HTTPS_REQUIRED: 실제 proxy peer IP 신뢰 설정 필요.
  공개 health marker 요청의 서버 접근 로그 IP를 먼저 확인하며 추정 IP/와일드카드는 사용하지 않는다.
- 앱 주소/실제 키/env/주문은 변경하지 않았다. 상세는 R7 보고서를 따른다.

## 2026-09-16 런타임 인증 R7b 시작 후 확인

- 새 실행 build `2026.09.16-runtime-credentials-r7-deploy-v1`, health=ok 확인.
- 중앙 DB 읽기/인증 프로필 메타데이터 7개/구현 capability/앱 WebSocket ready 통과.
- 순위 저장 회차 01:41:30 → 01:42:00 진행 확인. 현재 체결은 정상 거래시간 대기다.
- 사용자 확인: HTTPS 역방향 프록시 미설정. `https://mfactory.duckdns.org/health`는 TLS 통과/nginx 404다.
  프록시 계획은 HTTPS 해당 호스트:443 → HTTP 127.0.0.1:8787, WebSocket/인증서 연결이다.
- 남음: HTTPS API/WS 및 실제 peer 신뢰, 장중 다계좌/REG/교체 관측 간격, PostgreSQL 통합/실제 Linux 권한.
- 기본 배포 확인 완료이며 R7 전체 운용 완료는 아니다. 상세는 R7 보고서를 따른다.

## 2026-09-16 런타임 인증 R7a 누적 배포 준비

- 이번 누적 build `2026.09.16-runtime-credentials-r7-deploy-v1`, app/compose/Dockerfile 세 곳 일치.
- Docker context의 NAS 백업/실제 env/인증 제외를 보완하고 cryptography 직접 설치·secret 영속 마운트를 확인했다.
- 관련 회귀 368개 통과·exit=0(Windows POSIX 권한 1개 skip). 실제 권한은 Linux/NAS에서 확인한다.
- NAS 실제 /health는 `2026.09.15-shadow-candidate-settings-v1`, status=ok다. 새 이미지 검증 전이다.
- X: 경로를 사용자 세션에서 확인하고 전체 Git 작업본/새 파일의 hash manifest로 비교했다.
  전체 763개 검증·변경 135개 동기화 완료(기존 57개 원본 백업/새 78개), 동일 628개.
  백업: `X:\kiwoom-monitor\.codex-backups\runtime-credentials-r7-20260916-012631`.
- `.env`, postgres-data, server-data, server-secrets와 NAS 전용 파일은 삭제/덮어쓰지 않는다.
- 기존 registry/HMAC은 준비돼 있으며 앱 NAS 주소는 HTTP/신뢰 프록시 미설정이다.
  새 인증키 관리는 HTTPS/신뢰 프록시 설치 후 확인한다. 데이터/키 값은 변경하지 않았다.
- 제공된 `https://192.168.0.5`는 인증서 이름 불일치로 TLS 검사가 실패했다. API 라우팅은 미확인이다.
- 동기화 후에도 실행 이미지는 구 build다. 다음: 사용자 이미지 재빌드→새 health/운영 검증.
- 이후 사용자 화면에서 R7 이미지 빌드 성공/DB healthy를 확인했다. 서버 시작은 server-secrets 폴더 부재로 실패했다.
  NAS 경로 확인 후 빈 폴더 생성 완료. 이미지 재빌드 없이 기존 프로젝트를 다시 시작하고 health를 확인한다.
- 정정된 `https://mfactory.duckdns.org`는 TLS 통과/health 404다. 서버 시작 후 라우팅을 재확인한다.
- 상세: [R7a 배포 준비 보고서](RUNTIME_API_SETTINGS_R7_DEPLOYMENT_PREPARATION.md).

## 2026-09-16 런타임 인증 R6c2 계획된 재연결

- 최신 누적 build `2026.09.16-runtime-credentials-r6-planned-reconnect-v1`, 세 곳 일치.
- collector의 최대 30초 계획된 재연결 상태를 health/capability/WS와 PC worker에 연결했다.
- PC는 기존 순위표를 유지하고 계획된 상류 오류만 유예한다. 반복 통지는 deadline을 늘리지 않는다.
- 모든 REG 승인/재개 시작 실패/만료/종료는 대기를 끝내며 거래시간/구독 대기는 거짓 장애를 만들지 않는다.
- paused TR 대기는 일반 API 오류로 분리하여 로컬 TR을 만들지 않는다. 기존 NAS DB 우선/REST 우선순위는 유지한다.
- 교체 전후 허용 0B 관측 간격을 UTC 시각·최신 상태·NAS/PC 로그·PC 문구에 기록한다.
- 넓은 인접 회귀 251개와 capability/PC 표시 최종 회귀 87개 통과·exit=0. 모두 fake API/WS·임시 DB다.
- 재개 시작 실패 보완 후 최종 연결/계좌/역할 회귀 189개 통과·exit=0. 최종 신규 회귀 17개.
- AST 7개·공백 16개·build 3곳 일치 확인 완료.
- **NAS 동기화/재빌드 전**. 다음은 R7 누적 소스 검증·백업·동기화·사용자 재빌드·실환경 검증이다.
- 상세: [R6c2 계획된 재연결 보고서](RUNTIME_API_SETTINGS_R6_PLANNED_RECONNECT_IMPLEMENTATION.md).

## 2026-09-16 런타임 인증 R6c1 PC 실전계좌 입력

- PC NAS 설정에 별도 실전 관리 버튼과 프로필 추가/키 확인·적용·비활성화/계좌 조회 ON/OFF를 연결했다.
- 모의주문 토글은 실전 화면에서 숨기고 설정 적용과 실제 REG 승인을 분리 표시한다.
- 최종 인접 회귀 124개 통과·exit=0. 가짜 API/WS·임시 DB만 사용했다.
- PC/client 변경만 있으므로 서버 build는 `2026.09.16-runtime-credentials-r6-real-account-realtime-v1` 그대로다.
- **NAS 소스 동기화 전**. R6c2 계획된 재연결/기존 순위표 유지 후 R7에서 누적 동기화·재빌드한다.
- 상세: [R6c1 PC 실전 입력 보고서](RUNTIME_API_SETTINGS_R6_PC_REAL_INPUT_IMPLEMENTATION.md).

## 2026-09-16 런타임 인증 R6b3c2b 실전 계좌 WebSocket 연계

- 최신 누적 build `2026.09.16-runtime-credentials-r6-real-account-realtime-v1`.
- 담당 00/04는 기존 시장 WS에서 분배하고 비담당은 자기 계좌 전용 WS를 쓴다.
- verified scope/binding/generation·실전 event 전용 저장·0.5초 복구 병합/30초 backup·실제 token/socket/저장 drain을 연결했다.
- 비담당 이벤트도 기존 hub/daily 매수 편입에 합치고 담당 이벤트를 중복 발행하지 않는다.
- pending 저장 실패/overflow는 상태로 공개한다. RAM 대기는 강제 종료의 영속 outbox가 아니다.
- 첫 관련 회귀 84개/인접 회귀 260개 모두 통과·exit=0. AST 9개/build 3곳/소스 공백 통과.
- 최종 소스 회귀 82개, socket 종료 실패 fence를 포함한 최종 회귀 95개 모두 통과·exit=0. 신규 WS/owner 회귀는 17개다.
- **NAS 소스 동기화 전**이다. 실제 복수 토큰/WS 승인·수신/API/사용자 DB/PostgreSQL은 검증·변경하지 않았다.
- 다음 PC 입력/계획된 재연결 표시(R6c), 이후 R7 누적 배포/실환경까지 중간 재빌드를 요청하지 않는다.
- 상세: [R6b3c2b 보고서](RUNTIME_API_SETTINGS_R6_REAL_ACCOUNT_REALTIME_IMPLEMENTATION.md).

## 2026-09-16 런타임 인증 R6b3c2a 자동 실전 REST 수집/저장/운영 설정

- 최신 누적 build `2026.09.16-runtime-credentials-r6-real-account-monitor-v1`.
- 계좌별 30초 자동 REST 수집/실전 전용 저장/HTTPS ON-OFF를 기존 owner/broker에 연결했다.
- 저장은 verified scope/현재 binding/profile/설정 revision을 같은 SQLite/PG 트랜잭션에서 확인한다. mock 원장은 변경하지 않는다.
- OFF/ON은 시장 collector/순위를 재연결하지 않으며 키/역할/종료는 실제 조회·저장을 drain한다.
- 적용 revision은 REST 정책이고 monitor_status.mode=rest_poll로 실제 성공 관측과 구분한다.
- 인접 회귀 219개 실행(218 통과·Windows POSIX 권한 1 skip), 실패 0·exit=0. AST 7개/build 3곳/diff 공백 통과.
- 최종 소스 회귀 84개 모두 통과·exit=0. 신규 자동 수집/운영 설정 회귀는 14개다.
- PostgreSQL 배포 후 검사 스크립트는 임시 실전 자료를 rollback으로 검사한다. 실제 PostgreSQL/NAS/API/사용자 DB는 아직 검증/변경하지 않았다.
- **NAS 소스 동기화 전**이다. 다음 계좌 WS 연계(R6b3c2b), PC 표시(R6c), R7 누적 배포/실환경까지 중간 재빌드를 요청하지 않는다.
- 상세: [R6b3c2a 보고서](RUNTIME_API_SETTINGS_R6_REAL_ACCOUNT_MONITOR_IMPLEMENTATION.md).

## 2026-09-16 런타임 인증 R6b3c1 실전 read-only 복구 조회

- 최신 누적 build `2026.09.16-runtime-credentials-r6-real-account-reads-v1`. 실전 reader/단일 소유 read task/실제 전체 조회 drain을 로컬 구현했다.
- 주문 통합 조회와 두 venue 잔고를 중복 합산하지 않는 파서를 공유하며 real/mock 환경 검사와 mock 실행 원장 경계를 유지한다.
- 기존 중앙/계좌 broker를 사용하고 일반 v1 무신원 계좌 복구 조회를 차단한다. 자동 monitor/실전 DB 저장/계좌 WS/운영 PUT은 아직 없다.
- 인접 회귀 342개 실행(341 통과·Windows POSIX 권한 1 skip), 실패 0·exit=0. 이후 누락 계좌 테이블 검사를 포함한 최종 관련 회귀 51개 통과·exit=0.
- **NAS 소스 동기화 전**이다. 실제 키/계좌/API/WS/앱/사용자 DB/PostgreSQL을 사용·변경하지 않았다. R7까지 중간 배포/재빌드를 요청하지 않는다.
- 다음 R6b3c2 자동 실전 수집/환경별 저장·WS 분배/계좌 운영 PUT. R6c PC 표시와 R7 누적 배포/실환경도 남았다.
- 상세: [R6b3c1 보고서](RUNTIME_API_SETTINGS_R6_REAL_ACCOUNT_READS_IMPLEMENTATION.md).

## 2026-09-15~16 런타임 인증 R6b3b2 실제 시세 담당 전환

- 최신 누적 build `2026.09.15-runtime-credentials-r6-market-role-switch-v1`. 서버 역할 전환/CAS/PUT/재시작 복원을 로컬 구현했다.
- 기존 중앙 broker/단일 시장 collector/집계를 유지하며 두 계좌 client와 routing을 교환한다. 기본계좌/v2 대상·계좌 binding/cursor·물리 요청 한도를 보존한다.
- 저장 전 실패는 복구, 저장 후/불명확/복구 실패는 두 계좌 조회/시장 연결을 차단하고 저장된 담당을 재시작으로 복구한다. 적용 revision은 routing/재연결 시작이며 REG 준비 완료는 별도다.
- 인접 회귀 306개 실행(305 통과·Windows POSIX 권한 1 skip), 실패 0·exit=0. 이후 인증 변경 중단/첫 재개 frame 보완의 최종 관련 회귀 64개 통과·exit=0.
- **NAS 소스 동기화 전**이며 실제 키/계좌/API/WS/앱/사용자 DB/PostgreSQL은 변경하지 않았다. R7까지 중간 배포/재빌드를 요청하지 않는다.
- 다음 R6b3c 실전계좌 실시간 수집/운영 PUT. R6c PC 입력/계획된 재연결 표시·R7 누적 배포/실환경도 남았다.
- 상세: [R6b3b2 보고서](RUNTIME_API_SETTINGS_R6_MARKET_ROLE_SWITCH_IMPLEMENTATION.md).

## 2026-09-15 런타임 인증 R6b3b1 담당 전환 독점 구간

- 최신 누적 build `2026.09.15-runtime-credentials-r6-market-role-barrier-v1`. 담당 전환 독점 구간/직전 재검증을 로컬 구현했다.
- 실제 전환과 credential 후보는 상호 배제한다. 취소/예외 해제·종료 대기를 제공하며 현재 역할·binding·활성 profile·적용된 vault revision·동일 context를 확인한다.
- 인접 회귀 293개 실행(292 통과·Windows POSIX 권한 1 skip), 실패 0·exit=0. 이후 종료 중 시작/비활성 검사를 포함한 최종 관련 회귀 43개 통과·exit=0.
- 실제 역할 저장/REST·WS 전환 호출자와 공개 PUT은 아직 없다. R6b3b2에서 연결한다. 실전계좌 실시간 수집/운영, R6c PC 표시와 R7 누적 배포/실환경도 남았다.
- **NAS 소스 동기화 전**이다. 실제 키/계좌/API/사용자 DB/PostgreSQL을 변경하지 않았다. R7까지 중간 배포/재빌드를 요청하지 않는다.
- 상세: [R6b3b1 보고서](RUNTIME_API_SETTINGS_R6_MARKET_ROLE_BARRIER_IMPLEMENTATION.md).

## 2026-09-15 런타임 인증 R6b3a 시세 역할 저장·담당 보호

- 최신 누적 build `2026.09.15-runtime-credentials-r6-market-profile-settings-v1`. R6b3a는 로컬 저장 기반만 완료했다.
- 시세 담당과 불변 기본계좌 역할을 분리하고 revision CAS·최신 binding·활성 계좌 검증, 담당 disable/연결 해제 보호를 추가했다. 인증된 GET만 제공하며 applied_revision=null이다.
- 관련 테스트 58개 통과. 인접 회귀 283개 실행(282 통과·Windows POSIX 권한 1 skip), 실패 0·exit=0.
- **NAS 소스 동기화 전**이다. 실제 인증키/계좌/API/주문/사용자 DB/PostgreSQL은 변경하지 않았다. R7 전 중간 배포/재빌드를 요청하지 않는다.
- 다음은 실제 담당 전환 owner/PUT·재기동 반영과 실전계좌 실시간 수집/운영 제어다. R6c PC 표시·R7 누적 배포/실환경 검증도 남았다.
- 상세: [R6b3a 보고서](RUNTIME_API_SETTINGS_R6_MARKET_PROFILE_SETTINGS_IMPLEMENTATION.md).

## 2026-09-15 런타임 인증 R6b2 실전 owner/API·복수 계좌 과거 조회

- 최신 누적 build `2026.09.15-runtime-credentials-r6-real-owner-v1`. R0~R5·R6a·R6b1·R6b2 인증/과거 조회 로컬 완료.
- 시세 담당의 기존 단일 collector/client/broker와 추가 실전계좌 read-only broker/query를 구분해 기존 HTTPS credential/v3 계좌 API에 연결했다. keyless 등록·실패 후 명시 후보 검증·계좌 lock·기존 run/scope/cursor/disable 이력을 보호한다.
- 회귀 266개 실행(265 통과·Windows POSIX 권한 1 skip), 실패 0·exit=0, Python 6개 구문/공백·build 3곳 일치 확인.
- **NAS 소스 동기화 전**이며 실제 인증키/API/주문/사용자 DB/PostgreSQL은 변경하지 않았다. R7까지 중간 재빌드를 요청하지 않는다.
- 다음 R6b3 시세 역할 CAS·실전계좌 실시간 수집/운영 제어. R6c PC 입력/계획된 재연결과 R7 누적 동기화/배포/실환경 검증도 남았다.
- 상세: [R6b2 보고서](RUNTIME_API_SETTINGS_R6_REAL_CREDENTIAL_OWNER_IMPLEMENTATION.md).

## 2026-09-15 런타임 인증 R6b1 실전계좌 활성화 저장

- 최신 누적 build `2026.09.15-runtime-credentials-r6-real-account-claims-v1`. R0~R5·R6a·R6b1 로컬 완료.
- 실전/모의 scope의 계좌 설정 claim을 binding/활성화와 함께 저장하며 중복 연결 실패는 전체 rollback한다. 기존 설정·disable 이력·완료 replay 이후의 새 profile을 보존한다.
- 계좌 설정 27개 통과. 인접 회귀 214개 실행(213 통과·Windows POSIX 권한 1 skip), 실패 0, 종료 코드 0. PostgreSQL 검사 절차 5개 추가 및 임시 SQLite 시뮬레이션 확인.
- **NAS 소스 동기화 전**이다. 실제 PostgreSQL/키움 API/인증키/사용자 DB를 변경하지 않았다. R7까지 중간 재빌드를 요청하지 않는다.
- 다음은 R6b2 실제 실전 owner/API·시세 담당/추가 실전계좌 분리다. R6c PC 입력/계획된 재연결 표시와 R7 누적 동기화/배포/실환경 검증도 남았다.
- 상세: [R6b1 보고서](RUNTIME_API_SETTINGS_R6_REAL_ACCOUNT_CLAIMS_IMPLEMENTATION.md).

## 2026-09-15 런타임 인증 R6a 실전 재연결 기반

- 최신 누적 build `2026.09.15-runtime-credentials-r6-reconnect-barriers-v1`. R0~R5와 R6a 로컬 완료.
- 기존 collector의 pause/drain/resume·generation fence·실제 socket/token/owned 저장/장 마감 완료, 같은 source 최초 REG baseline/관측 연속성 재설정과 계좌 query BUSY/drain/선택적 cursor 폐기를 추가했다.
- 저장 waiter 취소와 접수 장 마감 취소 때문에 물리 완료 전에 종료가 완료되는 문제를 수정 전 재현/보완했다. 기존 실패 대기분·초/분봉 accumulator·허브·REST 순위 우선순위와 독립 real/mock 한도를 유지한다.
- 신규 11개를 포함한 관련 회귀 235개 모두 통과, 종료 코드 0. Python 4개 구문/공백·누적 build 3곳 일치 확인 완료.
- **실제 실전 키 적용 hook/API/UI에는 아직 연결하지 않았다. NAS 소스 동기화 전**이며 실제 NAS/PostgreSQL/키움 API/사용자 DB/인증키는 변경하지 않았다. R7까지 중간 재빌드를 요청하지 않는다.
- 다음은 R6b 실전 owner/검증 candidate·계좌 binding·활성 receipt와 실제 key 교체다. R6c PC 입력/계획된 재연결 표시·R7 누적 배포/실환경도 남았다. 상세: [R6a 보고서](RUNTIME_API_SETTINGS_R6_RECONNECT_BARRIERS_IMPLEMENTATION.md).

## 2026-09-15 런타임 인증 R5d2c 공통 뉴스 검색 운영

- 최신 누적 build `2026.09.15-runtime-credentials-r5-news-query-operations-v1`. R0~R5 로컬 완료.
- NAS 설정에서 기존 공통 뉴스 수집 ON/OFF·검색어·주기를 편집한다. worker/부분 PUT/CAS·구 NAS 호환을 유지하며 입력 정규화/최대 50개/UI ON 빈 목록을 검사한다. 작은 창에서 NAS 폼만 스크롤하고 저장 버튼을 유지한다.
- 기존 collector의 수집 중 정책 변경/주기 적용 문제 두 가지를 수정 전 재현했다. 접수 query는 완료하고 낡은 정책의 다음 query는 중단한다. 정상 완료 last_success 기반으로 새 주기를 사용하며 오류 backoff/부분 페이지/기사/cursor/예산을 보존한다.
- 신규 10개를 포함한 최종 관련 회귀 205개 모두 통과, 종료 코드 0. Python 4개 구문/공백·누적 build 3곳 일치·가짜 상태 일반/작은 창 배치 확인 완료.
- **NAS 소스 동기화 전**이다. 실제 NAS/PostgreSQL/공급자 API/사용자 DB는 변경하지 않았다. R7까지 중간 재빌드를 요청하지 않는다.
- 다음은 R6 실전 인증 교체. R7 누적 배포/실환경도 남았다. 상세: [R5d2c 보고서](RUNTIME_API_SETTINGS_R5_NEWS_QUERY_OPERATIONS_IMPLEMENTATION.md).

## 2026-09-15 런타임 인증 R5d2b 조건검색 운영

- 최신 누적 build `2026.09.15-runtime-credentials-r5-condition-operations-v1`. R5d2b 로컬 완료.
- 기존 NAS 운영 화면/operations에서 조건검색 추적 ON/OFF·정확한 이름/substring을 변경한다. 같은 시장 이벤트 서비스와 단일 수신 loop에서 전체 초기 결과 검증/새 조건 전환/이전 등록 해제 ACK를 처리한다.
- 실패한 후보/시간초과/낡은 응답/페이지·queue 오류를 부분 적용하지 않고 기존 활성식과 코호트·VI/체결 수명을 유지한다. OFF도 기존 자료를 삭제하지 않는다. late 실패와 설정 setter 실패는 복구 필요로 공개하고 같은 영속 revision을 명시 재저장한다.
- 관련 회귀 172개와 추가 수신 loop/진단 검증을 포함한 신규 경계 17개 통과, 종료 코드 0. Python 6개 구문/공백·누적 build 3곳 일치·가짜 상태 설정창 배치를 확인했다.
- **NAS 소스 동기화 전**이다. 실제 NAS/PostgreSQL/공급자/사용자 DB를 변경하지 않았다. R7까지 중간 재빌드는 요청하지 않는다.
- 다음은 R5d2c 공통 뉴스 검색어 운영 UI. R6 실전 owner·R7 누적 배포/실환경, 실제 상류 등록/해제 ACK와 잠깐의 두 조건 등록 허용 확인이 남았다. 상세: [R5d2b 보고서](RUNTIME_API_SETTINGS_R5_CONDITION_OPERATIONS_IMPLEMENTATION.md).

## 2026-09-15 런타임 인증 R5d2a 해외시세 운영

- 최신 누적 build `2026.09.15-runtime-credentials-r5-external-operations-v1`. R5d2a 로컬 완료.
- 기존 operations에서 해외 지연 시세 ON/OFF·주기·자동 월물 전환·확인 횟수를 실행 중 변경한다. 초기 OFF에도 같은 수집기를 조립하며 저장 OFF는 ENV ON보다 우선한다.
- 수집/설정 변경 owned task·실제 thread/최종 저장 drain을 연결했고 봉/roll 이력/일봉 기준 cache를 보존한다. 구 NAS 입력 호환과 복구 필요 UI를 유지한다. 초기 symbol/자동 roll은 유지하며 수동 상품/활성 월물 변경은 미포함이다.
- 신규 10개·최종 관련 회귀 125개 모두 통과, 종료 코드 0. Python 4개 구문/공백·누적 build 3곳 일치·가짜 상태 화면 배치 확인 완료.
- **NAS 소스 동기화 전**이다. 실제 NAS/PostgreSQL/외부 공급자/사용자 DB를 변경하지 않았다. R7까지 중간 재빌드는 요청하지 않는다.
- 다음은 R5d2b 조건검색/기타 운영 확대. R6 실전 owner·R7 누적 배포/실환경도 남았다. 상세: [R5d2a 보고서](RUNTIME_API_SETTINGS_R5_EXTERNAL_OPERATIONS_IMPLEMENTATION.md).

## 2026-09-15 런타임 인증 R5d1 공급자 PC 입력 화면

- R5d1 로컬 완료. NAS 설정에서 네이버/DART/AI 키를 준비·명시 적용·비활성화할 수 있는 메뉴를 연결했다. 기존 모의계좌 UI/조회/주문 계약은 유지한다.
- global 화면은 계좌 UI/I/O를 제외하며 고정 프로필·공급자·revision·operation·계좌 null을 확인한다. 키는 password 입력/전달 직전·종료 비움이며 PC 설정/DB/미러/백업에 저장하지 않는다.
- 공급자/NAS별 메모리 pending 요청을 재사용하고 timeout 후 같은 operation을 확인한다. AI ACTIVE를 인증 성공으로 표시하지 않는다.
- 서버/API/DB/build 변경 없음. 최신 NAS 누적 build는 R5c `2026.09.15-runtime-credentials-r5-ai-rotation-v1`이다. **NAS 소스 동기화 전**이며 R7까지 중간 재빌드는 요청하지 않는다.
- 신규 11개 및 최종 관련 회귀 163개 모두 통과, 종료 코드 0. 다섯 폼의 실제 로컬 서버 경로를 가짜 공급자로 검증했으며 실제 NAS/유료 API/사용자 키·DB는 변경하지 않았다.
- 다음은 R5d2 기존 운영값 확대. R6 실전 owner·R7 누적 배포/실환경도 남았다. 상세: [R5d1 보고서](RUNTIME_API_SETTINGS_R5_PROVIDER_UI_IMPLEMENTATION.md).

## 2026-09-15 런타임 인증 R5c AI 키 교체

- 최신 누적 build: `2026.09.15-runtime-credentials-r5-ai-rotation-v1`. R0~R4·R5a/R5b/R5c 로컬 완료.
- 세 AI 공급자 고정 global 프로필·keyless service/작업기·유료 검증 없는 UNVERIFIED prepare/명시 apply를 연결했다. ACTIVE는 키 적용 완료이며 실제 분석 검증은 runtime_validation으로 구분한다.
- 접수 snapshot과 공급자별 실제 준비/대기/분석/최종 저장 drain을 사용하고 HTTP waiter 취소/서버 종료를 보호한다. 캐시/사용량/상한/품질 버전과 다른 공급자 경계를 유지한다.
- **NAS 소스 동기화 전**이다. 다음은 R5d 공급자 UI/운영 확대. R6 실전 owner·R7 누적 배포/실환경도 남았다. 중간 재빌드는 요청하지 않는다.
- 상세: [R5c 보고서](RUNTIME_API_SETTINGS_R5_AI_IMPLEMENTATION.md). 신규 17개 및 누적 관련 회귀 351개: 350 통과, Windows POSIX 권한 검사 1개 생략, 종료 코드 0.
- 실제 유료 AI API/NAS/PostgreSQL·사용자 키/DB는 변경하지 않았다. 실제 인증 성공은 설치 후 명시 분석으로 확인한다. build 세 파일의 표기를 함께 갱신했다.

## 2026-09-15 런타임 인증 R5b DART 키 교체

- 최신 누적 build: `2026.09.15-runtime-credentials-r5-dart-rotation-v1`. R0~R4·R5a/R5b 로컬 완료.
- 고정 DART 기본 프로필과 회사코드 캐시를 건드리지 않는 검증을 연결했다. 종목 수집은 두 client/운영 enabled를 접수 시 고정하고 실제 저장까지 drain한다. 독립 pause로 동시 provider 교체를 보호하고 NAVER query-set는 DART 교체 중 계속한다.
- cache Path/정상 조회의 30일 갱신 정책·기사/cursor/작업/NAVER 사용량·운영 토글을 보존한다. commit 이후 실패는 DART만 부재/복구 필요 상태로 두며 예전 env 키로 우회하지 않는다. DB migration/분류 품질 변경 없음.
- **NAS 소스 동기화 전**이다. 다음은 R5c AI. 공급자 UI/운영 확대·R6 실전 owner·R7 누적 배포/실환경도 남았다. 중간 재빌드는 요청하지 않는다.
- 상세: [R5b 보고서](RUNTIME_API_SETTINGS_R5_DART_IMPLEMENTATION.md). 관련 회귀 334개: 333 통과, Windows POSIX 권한 검사 1개 생략, 종료 코드 0. 최종 DART 18개도 모두 통과했다.
- Python 문법 7개 파일·관련 diff/신규 파일 공백 검사 통과, build 세 곳 일치. 가짜 API/임시 데이터 검증이며 실제 NAS TLS/PostgreSQL/Linux·DART key/IP/응답·장중 검증은 미완료다.

## 2026-09-15 런타임 인증 R5a 네이버 키 교체

- 최신 누적 build: `2026.09.15-runtime-credentials-r5-naver-rotation-v1`. R0~R4·R5a 로컬 완료.
- 네이버 기본 프로필/후보 검증/비활성화를 기존 runtime API에 연결하고 종목별·공통 검색을 실제 페이지/저장 drain 뒤 함께 교체한다. 기사·cursor·작업·사용량 보존, 검증 HTTP 예산 합산, keyless 첫 활성화를 지원한다.
- commit 시작 이후 실패는 예전 키를 복원하지 않으며 NAVER 부재/복구 필요 상태로 다른 공급자·캐시·뉴스 후속 작업의 경계를 유지한다. DB migration·원문/요약/분류 규칙 변경 없음.
- **NAS 소스 동기화 전**이다. 다음은 R5b DART, AI·공급자 PC 입력 UI/운영 확대·R6 실전 owner·R7 누적 배포와 실환경 검증도 남았다. 중간 재빌드는 요청하지 않는다.
- 상세 계약/검증: [R5a 보고서](RUNTIME_API_SETTINGS_R5_NAVER_IMPLEMENTATION.md). 관련 회귀 316개: 315 통과, Windows POSIX 권한 검사 1개 생략, 종료 코드 0. 최종 취소 완료 객체 정리 보완 뒤 네이버/뉴스/서버 112개도 모두 통과했다.
- Python 문법 6개 파일·관련 diff/신규 파일 공백 검사 통과, build 세 곳 일치. 가짜 API/임시 DB 검증이며 실제 NAS TLS/PostgreSQL/Linux·네이버 인증/장중 검증은 미완료다.

## 2026-09-15 런타임 인증 R4b 선택 계좌 PC 연결

- 최신 누적 build: `2026.09.15-runtime-credentials-r4-selected-client-v1`. R0~R4 누적 로컬 완료.
- 인증 v3 조회 가능 계좌 목록과 PC 일지 선택/worker binding 고정·빈 결과/늦은 응답 차단, 선택 모의 명령 client를 연결했다. 새 주문 UI/실제 자동주문 완료를 뜻하지 않는다.
- 기존 기본 v2 계좌/시장자료 fallback는 유지하고 명시 NAS 계좌는 PC 단일 프로필로 우회하지 않는다. DB migration 없음.
- **NAS 소스 동기화 전**이다. R5 뉴스·R6 실전 owner·R7 NAS 누적 동기화/실환경 검증이 남았고 중간 재빌드는 대기한다.
- 상세 계약/검증: [R4b 보고서](RUNTIME_API_SETTINGS_R4_SELECTED_ACCOUNT_IMPLEMENTATION.md).
- 관련 회귀 388개: 387 통과, Windows POSIX 권한 1개 생략. 마지막 일지/선택 계좌 44개도 전부 통과, 종료 코드 0. 실제 API/주문 없는 검증이다.

## 2026-09-15 런타임 인증 R4a PC 모의계좌 관리 화면

- R4a 로컬 구현 완료: NAS 모의 프로필 추가·키/계좌 확인·명시 적용·비활성화·계좌 조회/수동 주문 허용 UI와 HTTPS client.
- PC 키 설정/DB/미러에 NAS 키를 쓰지 않으며 같은 NAS의 창 재열기는 안전한 요청 상태를 공유한다.
- 서버 소스/build/DB 변경 없음. 최신 누적 NAS build는 아래 R3g이며 **NAS 소스 동기화 전**이다. R7까지 중간 재빌드 대기.
- R4b 매매일지 선택 계좌/PC context가 다음이다. R5/R6/R7과 실제 NAS HTTPS/PostgreSQL 검증은 남아 있다.
- 상세 계약/검증: [R4a 보고서](RUNTIME_API_SETTINGS_R4_NAS_UI_IMPLEMENTATION.md).
- 관련 회귀 253개 실행: 252 통과, Windows POSIX 권한 1개 생략, 종료 코드 0. 신규 client/UI/로컬 서버 통합 24개 포함. 문법 6개 파일·관련 diff 공백 검사 통과.

## 2026-09-15 런타임 인증 R3g 선택 계좌 API

- 최신 누적 소스 build: `2026.09.15-runtime-credentials-r3-scoped-accounts-v1`. R0~R3f 누적.
- 명시 scope/profile/binding 버전의 v3 계좌 조회와 v2 선택 모의 주문·GET·취소를 연결했다. 기존 기본 계좌 v1/v2는 유지한다.
- 계좌별 cursor·실제 페이지 종료 대기·응답 전 context 검증, 계좌/run별 요청 ID와 UNKNOWN 재전송 방지를 확인했다.
- R3 서버 기능 로컬 완료. 다음은 R4 입력/계좌 선택 UI와 명시 context 전달이다. R5 news/R6 real owner는 남았다.
- **NAS 소스 동기화 전**이다. R7까지 중간 재빌드를 요청하지 않는다. 실제 NAS PostgreSQL·Linux·TLS·중단/복구·장중 응답은 미검증이다.
- 관련 회귀 336개 실행: 335 통과, Windows POSIX 권한 1개 생략, 종료 코드 0. 실제 키·네트워크·주문 없는 fake/임시 DB 검증이다.
- 계약/완료 기준: [R3g 보고서](RUNTIME_API_SETTINGS_R3_SCOPED_ACCOUNTS_IMPLEMENTATION.md).
- 최종 Python 문법 9개 파일·관련 diff 공백 검사 통과, 서버/Compose/Dockerfile build 세 곳 일치.

## 2026-09-15 런타임 인증 R3f 계좌 제어

- 최신 누적 소스 build: `2026.09.15-runtime-credentials-r3-account-controls-v1`. R0~R3e 누적.
- 모의 키 disable prepare/apply·명시 tombstone·기존 binding 보존과 실제 계좌 설정 PUT을 연결했다.
- 실제 작업 종료→CAS 저장→새 bundle 준비 뒤 applied_revision을 공개한다. 저장 후 실패는 닫고 동일 설정으로 복구한다.
- **NAS 소스 동기화 전**이다. R7까지 중간 재빌드를 요청하지 않는다. 실제 NAS PostgreSQL·Linux 권한·TLS·중단/복구는 R7 대기다.
- R3 진행 중이다. 다음은 scoped 계좌 조회/주문 API, 이후 R4 입력 UI다. 새 사용자용 토글/키 입력 UI는 아직 없다.
- 계약과 완료 기준은 [R3f 보고서](RUNTIME_API_SETTINGS_R3_ACCOUNT_CONTROLS_IMPLEMENTATION.md)를 따른다.
- 관련 회귀 323개 실행: 322 통과, Windows POSIX 권한 1개 생략, 종료 코드 0. 마지막 startup receipt 보완 후 owner 30개도 전부 통과했다. 실제 키·네트워크·주문 없는 임시 DB/vault 검증이다.
- 최종 문법 9개 파일·diff 공백 검사 통과, build 세 곳 일치. 실제 PostgreSQL disable/binding/설정 원자 저장·replay 검사는 통합 스크립트에 추가했고 NAS 실행은 R7 대기다.

## 2026-09-15 런타임 인증 R3e 실제 모의 owner

- 최신 누적 소스 build: `2026.09.15-runtime-credentials-r3-mock-owner-v1`. R0·R1·R2·R3a~d 포함.
- 실제 모의 인증 hooks와 복수 계좌 실행 owner를 연결했다. 같은 계좌 키 교체는 client/broker·조회 한도 이력·계좌 UUID/run을 보존하고 새 계좌는 주문 OFF로 시작한다.
- 실제 초기 계좌 읽기·임대·설정 revision 확인 뒤 ACTIVE를 공개한다. commit 후 실패는 이전 토큰을 자동 재개하지 않고 복구가 필요한 상태로 닫는다.
- 공통 인증·계좌 읽기·WS token 동시 작업과 bootstrap은 각각 최대 2개다. 실전 시세 broker와 모의 계좌 broker는 분리한다.
- 기본 v1 주문 계좌는 `nas-mock-default`를 유지하고 초기 boot/교체 중에는 요청을 거절한다. main mock 시세 profile은 별도로 예약한다.
- 최종 로컬 회귀 310개 실행: 309 통과, POSIX 권한 1개 Windows에서 생략, 종료 코드 0. 실제 키·공급자 네트워크·주문 없이 fake transport와 임시 DB/vault로 확인했다.
- **NAS 소스 동기화 전**이다. R7까지 중간 재빌드를 요청하지 않는다. 실제 NAS PostgreSQL·Linux 권한·TLS·중단/복구 검증은 R7 대기다.
- R3는 진행 중이다. 모의 비활성화·설정 PUT·계좌별 scoped API와 입력 UI는 아직 후속이다. 실제 모의 owner가 확인한 settings GET의 applied_revision은 이제 반환한다.
- 구현 범위와 계약: [R3e 보고서](RUNTIME_API_SETTINGS_R3_MOCK_OWNER_IMPLEMENTATION.md).

## 2026-09-15 런타임 인증 R3d 계좌 설정 CAS

- 최신 누적 소스 build: `2026.09.15-runtime-credentials-r3-account-settings-v1`. R0·R1·R2·R3a/b/c 포함.
- verified scope별 운영 설정·revision CAS·profile/binding 검증과 인증 GET을 구현했다.
- 모의 binding/활성화/초기 계좌 설정을 같은 트랜잭션으로 저장하고 중복 active profile은 롤백한다.
  초기 주문 OFF를 저장하고 동일 profile 갱신과 완료 replay는 설정을 보존한다.
- **NAS 소스 동기화 전**이다. R7까지 중간 재빌드를 요청하지 않는다.
- 최종 로컬 회귀 291개 실행: 290 통과, POSIX 권한 1개 Windows에서 생략, 종료 코드 0.
  관련 Python 문법/diff 공백 검사 통과, 서버/Compose/Dockerfile build 3곳 일치.
- 실제 복수 계좌 owner·키 교체 hooks·설정 PUT·scoped API는 후속 R3다.
  저장 토글은 아직 실제 적용 상태가 아니며 GET applied_revision은 null이다.
- PostgreSQL CAS/replay/중복 rollback 통합 검사를 추가했으며 실제 NAS 실행은 R7 대기다.

## 2026-09-15 런타임 인증 R3c 계좌 bundle

- 최신 누적 소스 build: `2026.09.15-runtime-credentials-r3-bundle-v1`. R0·R1·R2·R3a·R3b 포함.
- 기존 모의 연결을 계좌/run/profile 고정 bundle로 연결하고 owned start/close·연결별 신원·
  요청별 gateway를 고정했다. 인증·계좌 불일치·monitor 시작 실패는 모의 기능만 닫는다.
- R3는 진행 중이다. 실제 복수 계좌 활성화 owner·키 교체 hooks·계좌 설정 CAS·scoped API는 후속이다.
- **NAS 소스 동기화 전**이다. R7까지 중간 재빌드를 요청하지 않는다.
- 최종 로컬 회귀 274개 실행: 273 통과, POSIX 권한 1개 Windows에서 생략, 종료 코드 0.
- 관련 diff 공백 검사 통과, 서버/Compose/Dockerfile build 3곳 일치.
- 실제 NAS PostgreSQL·Linux 권한·TLS·중단/복구 운용 검증은 R7 대기다.

## 2026-09-15 런타임 인증 R3b 모니터 종료·DB 소유권

- 최신 누적 소스 build: `2026.09.15-runtime-credentials-r3-monitor-fence-v1`. R0·R1·R2·R3a 포함.
- monitor/WS 실제 종료·owned task·queue drain·독립 heartbeat·운영 repository의 DB owner/scope
  트랜잭션 검사 구현. 늦은 성공 응답이 기존 SUBMISSION_UNKNOWN을 덮지 않도록 검증한다.
- R3는 진행 중이다. 실제 키 교체 owner·복수 계좌 bundle·scoped API·startup/health는 후속이다.
- **NAS 소스 동기화 전**이다. R7까지 중간 재빌드를 요청하지 않는다.
- PostgreSQL owned write 검증을 통합 스크립트에 추가했으며 실제 NAS 실행은 R7 대기다.
- 최종 로컬 회귀 264개 실행: 263 통과, POSIX 권한 1개 Windows에서 생략, 종료 코드 0.
  변경 Python 문법·공백 검사 통과, 서버/Compose/Dockerfile build 3곳 일치.

## 2026-09-15 런타임 인증 R3a 모의 명령 장벽

- 최신 누적 소스 build: `2026.09.15-runtime-credentials-r3-command-barrier-v1`. R0·R1·R2 포함.
- gateway 실제 명령 수명·접수 gate·drain, runtime 동기 종료·독립 heartbeat·신규 주문 OFF,
  현재 account/run/owner에 한정한 임대 해제를 로컬 구현했다.
- R3 진행 중이다. 실제 모의계좌 키 교체·bundle·monitor/WS drain·DB 늦은 쓰기 fence·scoped API는
  후속 부분이며 credential owner는 아직 운영 경로에 등록하지 않았다.
- **NAS 소스 동기화 전**이다. R7까지 중간 재빌드를 요청하지 않는다.
- 실제 NAS PostgreSQL 임대 해제 검증은 통합 스크립트에 추가했고 R7 실행 대기다.
- 최종 로컬 회귀 250개 실행: 249 통과, POSIX 권한 1개 Windows에서 생략, 종료 코드 0.
  변경 Python 문법·공백 검사 통과, 서버/Compose/Dockerfile build 3곳 일치.

## 2026-09-15 런타임 인증 R2b 공통 준비·적용 API

- 최신 누적 소스 build: `2026.09.15-runtime-credentials-r2-api-v1`. R0·R1·R2a를 포함한다.
- profile/operation API, TTL·상한·멱등성·HTTPS·safe body 처리와 vault→DB→runtime 상태 조정을 구현했다.
- 공통 R2 로컬 완료. 실제 공급자 owner는 R3/R5/R6 연결 전이며 현재 prepare는 503으로 거절한다.
  키를 실제 운영 연결에 적용하지 않았다. 입력 UI·복수 계좌 runtime도 아직 다음 단계다.
- 이 변경도 **NAS 소스 동기화 전**이다. R7까지 중간 재빌드를 요청하지 않는다.
- 실제 NAS PostgreSQL·Linux 권한·trusted proxy IP/TLS·프로세스 중단은 R7 검증 대상이다.
- 다음은 R3 모의계좌 owner와 계좌별 runtime 연결이다.
- 최종 로컬 회귀 210개 실행: 209 통과, POSIX 권한 1개 Windows에서 생략, 종료 코드 0.
  변경 Python 문법·공백 검사 통과, 서버/Compose/Dockerfile build 3곳 일치.

## 2026-09-15 런타임 인증 R2a 공통 REST 장벽

- 최신 누적 소스 build: `2026.09.15-runtime-credentials-r2-barrier-v1`. R0·R1을 포함한다.
- 후보 검증·실제 HTTP/DB/token drain·취소 보호·캐시 세대 기반을 추가했다.
  외부 준비/적용 API·계좌 runtime은 아직 연결 전이며 키 변경을 운영에 적용하지 않았다.
- 이 변경도 **NAS 소스 동기화 전**이다. R7까지 중간 재빌드를 요청하지 않는다.
- R2 진행 중: 다음은 R2b operation/profile API·TTL·idempotency·HTTPS와 commit 조정이다.
- 최종 로컬 회귀 192개 실행: 191 통과, POSIX 권한 1개 생략, 종료 코드 0.

## 2026-09-15 런타임 인증 설계 R1 로컬 구현

- 최신 누적 소스 build: `2026.09.15-runtime-credentials-r1-v1`. R0 변경을 포함한다.
- 전용 vault·초기 env 이관·기동 합성·중앙 v19 프로필/활성화 원장 구현.
  실행 중 키 변경 API, 신규 계좌 runtime, 앱 입력 UI는 아직 구현 전이다.
- **R0·R1 모두 NAS 소스 동기화 전**이다. R7까지 중간 재빌드를 요청하지 않는다.
- Compose는 `./server-secrets:/app/secrets`, 고정 secret 경로와 암호화 의존성을 추가했다.
  코드 동기화에서는 `server-secrets`도 `.env`/`postgres-data`/`server-data`처럼 보존한다.
  Git·Docker context·일반 백업 제외 대상이며 접근 제한 복구 백업만 별도 보관한다.
- 로컬 회귀 167개 실행: 166개 통과, POSIX 권한 1개 Windows에서 생략, 종료 코드 0.
- 실제 NAS PostgreSQL·Linux 권한은 미실행이다. R7 이미지에서 기존
  `check_postgres_integration.py`의 credential 추가 검증을 함께 실행한다.

## 2026-09-15 런타임 인증 설계 R0 로컬 구현

- 최신 누적 소스 build는 `2026.09.15-runtime-settings-r0-v1`이다. 서버/Compose/Dockerfile 세 곳을 갱신했다.
- 설정 부분 저장, revision 충돌, 저장 실패 보존, 적용 실패 상태/재적용과 NAS·뉴스 설정창
  백그라운드 조회·저장을 구현했다. 키 교체/새 계좌 연결은 아직 구현하지 않았다.
- 이 R0 변경은 **NAS 소스 동기화 전**이다. 앞선 Shadow 동기화 사실과 구분한다.
  구현 단계별 중간 재빌드를 요청하지 않고 R7 누적 동기화·재빌드에서 반영한다.
  현재 NAS 운영 이미지 반영 여부는 이번 작업에서 확인하지 않았다.
- 관련 회귀 87개 통과(종료 코드 0), 수정 파일 문법/공백 검사 통과.

## 2026-09-15 Shadow 후보 런타임 설정 배포 대기

- 누적 배포 후보 build는 `2026.09.15-shadow-candidate-settings-v1`이다. 기존에는 `SHADOW_CANDIDATE_ENABLED`와 전략 JSON을 서버 기동 때만 읽어 NAS 재시작 없이는 후보 감지를 바꿀 수 없었다. 새 운영 설정은 on/off, 검증된 전략 객체, poll 간격, 순위 자료 유효시간을 중앙 DB에 보존하며 PUT 성공 시 서버는 계속 둔 채 후보 task만 즉시 시작·중지·교체한다. 앱의 Shadow 창에서 주요 전략 조건과 작동 여부를 편집하고 상태를 한국어로 확인한다. 메인 글자 버튼 둘은 `30초 간격` 왼쪽의 14px 색상 네모로 축소했다. 관련 서버·GUI 회귀 76개와 최종 전환 집중 회귀 10개가 통과했다. 변경 17개를 `X:\kiwoom-monitor`에 동기화했고 SHA-256 불일치는 0개, 백업은 `X:\kiwoom-monitor-backups\20260915-062533-shadow-candidate-settings-v1`에 보존했다. 이미지 재빌드, `/health.server_build`와 실제 on/off 상태 확인이 남아 있다.

## 2026-09-15 상한가 기준 구간 동기화 배포 대기

- 누적 build `2026.09.15-upper-limit-basis-v1` 배포를 완료했다. 추적 종목의 키움 `0g` 상한가·하한가·기준가를 같은 실시간 연결에서 구독해 `stock_price_references` 최신 문서와 앱 이벤트로 전달한다. 장후 0B 등락률 기준이 0.00%로 전환됐는데 이전 `upl_pric`과 현재가가 같은 모순 조합은 상한가 강조와 새 상한가 사실에서 제외한다. 누적 변경 38개를 `X:\kiwoom-monitor`에 동기화했고 기존 파일은 `X:\kiwoom-monitor-backups\20260915-055650-upper-limit-basis-v1`에 보존했으며 SHA-256 불일치는 0개다. 재빌드 뒤 `/health.server_build`, 인증 API, NAS WebSocket ready/pong을 확인했다. 재기동 직후 005930의 `stock_price_references`는 0건이며 `0g`는 변경 이벤트이므로 다음 실제 기준 전환 때 최초 저장과 장후 0.00% 강조 해제를 운영 확인한다.

## 2026-09-15 매매일지 전체일 분봉 확정 계약 v2 배포 대기

- 누적 배포 후보 build는 `2026.09.15-journal-minute-coverage-v2`다. 실제 사용자 일지 DB에서 금호전기(001210) 2026-09-14 자료가 09:04~10:42의 99개뿐인데도 `after_close_confirmed`·`확정`으로 기록된 사실을 확인했다. 같은 시각 NAS는 아직 장후 분봉 보완 중이었고, 현재 중앙 DB에는 09:00~19:59의 620개 KRX/COMBINED 봉이 있다.
- 원인은 앱이 중앙에서 대상일 봉을 하나라도 받으면 전체일 완료로 간주한 것이었다. 새 중앙 응답은 별도 coverage 문서의 `window_closed=true`와 `session_finalized=true`를 확인해 `coverage.complete`를 제공하며, 앱은 이 근거가 있을 때만 전체일 확정한다. 기존 시행일 이후 조기 종료 확정 기록도 조회 시 `일부`로 자동 교정되어 다음 보완 대상이 된다. v1 재빌드 실측에서 620개·19:59까지 반환되지만 구형 `kind=minute` 문서를 NAS가 건너뛰어 `coverage.complete=false`인 사실을 확인했다. v2는 이 구형 문서를 다시 조회해 새 완료 문서로 승격한다.
- v2 관련 회귀 150개가 통과했다. v2 누적 빌드 입력 253개를 `X:\kiwoom-monitor`에 다시 동기화했고 SHA-256 불일치는 0개다. v1 파일은 `X:\kiwoom-monitor-backups\20260915-052221-journal-minute-coverage-v2`에 백업했으며 운영 `.env`, `postgres-data`, `server-data`를 보존했다. v2 이미지 재빌드와 앱의 금호전기 19:59·`확정` 운영 확인이 남아 있다.

## 2026-09-15 뉴스 정제·저장 필터·비AI 요약 누적 배포 대기

- 현재 누적 배포 후보 build는 `2026.09.15-ranking-startup-latency-v1`이다. NAS `ka00198` 24시간 30초 수집에 더해 NAS 재시작 직후 현재 회차를 즉시 수집한다. NAS→키움 및 앱→NAS의 직전 `dt/tm`·부분 응답은 0.25초 2회→0.5초 2회→이후 0.75초로 제한 재확인한다. 실제 운영 DB에서 `02:02:30`은 20행 중 정상 9행, `02:03:30`은 정상 10행, `02:04:30`은 정상 3행뿐인 키움 갱신 중 응답이 저장된 사실을 확인했다. 새 빌드는 이런 빈 자리 응답을 NAS에서 재조회하고 미완성 회차를 저장하지 않는다. 앱도 NAS 중앙 DB를 같은 간격으로 재확인하므로 NAS가 2~3초 뒤 저장한 완성본을 해당 회차 안에서 바로 반영한다. 앱은 최신 완성 회차를 끝내 받지 못하면 직전 자료를 새 결과로 적용하지 않고 이미 표시된 정상 표를 유지하며 다음 회차까지 `API: 재조회`로 표시하지 않는다. 배포 후 임의의 비경계 시각에 NAS를 재시작해 다음 `:00/:30` 전에 현재 회차 snapshot이 생기는지, 저장 snapshot 20행에 빈 코드·이름이 없는지, 앱이 부분 응답 뒤 완성본을 같은 회차에 받는지 확인한다.
- 2026-09-15 01:49 재빌드·기동 뒤 `/health.status=ok`, `server_build=2026.09.15-ranking-snapshot-freshness-v1`를 확인했다. 야간 `01:49:30`과 `01:50:00` 스냅샷이 새로 저장됐으며, 앱은 `01:50:00` 회차에서 직전 `01:49:30`을 단계형 간격으로 재확인한 뒤 `01:50:04.474`에 `01:50:00` 자료를 받아 19종목 순위 변동을 반영했다. 정확 타이머 적용 뒤 `:59→:00` 동일 회차 중복 예약은 나타나지 않았다.
- `2026.09.15-ranking-startup-latency-v1` 수정 파일 15개, 부분 응답 보완 6개, 앱→NAS 이중 재확인 최종 보완 7개를 `X:\kiwoom-monitor`에 동기화했고 각 동기화의 SHA-256 불일치는 0개다. 기존 NAS 파일 백업은 `X:\kiwoom-monitor-backups\20260915-020737-ranking-startup-latency-v1`, `X:\kiwoom-monitor-backups\20260915-021500-partial-ranking-final`, `X:\kiwoom-monitor-backups\20260915-023000-ranking-double-poll-final`에 보존했다. 이번 복사는 명시한 소스·테스트·문서·빌드 파일만 대상으로 하며 운영 `.env`와 데이터 디렉터리는 대상에 포함하지 않았다. 새 이미지 재빌드와 `/health.server_build` 확인은 남아 있다.
- 누적 배포 후보 build는 `2026.09.15-news-cleaning-summary-v3`다. v1 이미지 기동 때 모의 키움 토큰 발급 실패가 선택 기능인 모의계좌 신원 확인에서 전 서버를 종료시키는 경로를 확인했다. v2부터 이 경우 중앙 시세·뉴스 서버를 계속 기동하고 모의계좌 조회·주문만 비활성화하며, 실제 확인 계좌와 `MOCK_ACCOUNT_REF`가 다른 신원 불일치는 계속 기동을 거부한다. v3는 토큰 거절의 키움 `return_code`와 `return_msg`를 비밀값 없이 로그와 health의 `mock_account_error`에 보존한다.
- 2026-09-15 모의 토큰 거절의 운영 원인은 사용자가 신청한 모의투자 1개월 이용 기간 만료로 확정했다. 동일 증상에서는 모의투자 참가 기간을 먼저 확인하고, 재신청 전까지 `mock_account_available=false`와 모의 주문 503은 정상적인 부분 비활성 상태로 취급한다.
- 원문 정제기 v7, 제목뿐인 기사·명백한 비증권 검색 잡음의 신규 저장 차단, 원문 최대 3문장 추출 요약과 읽기 좋은 본문 문단 표시를 포함한다.
- 오프라인 body revision 49,835건을 전수 검사했다. 정제 후 사용 가능한 fulltext 46,318건에서 원문 밖 문장 생성과 알려진 포털 꼬리 선택은 0건이었고, 안전한 문장이 없는 114건은 요약을 생략했다. 상세 결과는 `NEWS_EXTRACTIVE_SUMMARY_AUDIT_20260915.md`에 있다.
- NAS 누적 소스 동기화를 완료했다. 최초 기존 소스 백업은 `X:\kiwoom-monitor-backups\20260915-010159-news-cleaning-summary-v1`이다. v1 기동 실패본은 `X:\kiwoom-monitor-backups\20260915-011200-news-summary-v1-startup-failure`에 추가 백업한 뒤 v2를 동기화했다. 변경 핵심 파일의 SHA-256 불일치는 0개였고 `.env`, `postgres-data`, `server-data`를 보존했다. 중앙 서버·설정·계좌 신원·모의계좌 회귀 78개가 통과했다. v2 이미지 빌드, 컨테이너 재생성, `/health.server_build` 확인은 아직 남아 있다.

## 2026-09-14 KRX 애프터 신설 — S1~S6/A4b 누적 NAS 배포·기본 검증 완료

- 08:55~09:00 페일오버 원인 확인: NAS HTTP와 뉴스 동기화는 계속 정상이었지만 KRX 사전 구독 직후 중앙 키움 WebSocket 처리에서 `NoneType is not iterable`이 발생했다. 원본 프레임은 저장되지 않았으나 같은 시각의 코드 경로에서 `REAL data=null`을 입력하면 동일 예외가 재현됐다. 모든 실시간 parser·시장 이벤트·KRX 관측이 null data를 빈 batch로 무시한다.
- 앱 지연 보완: NAS 당일 분봉 DB 우선 읽기, 완료 일봉 아카이브 유지, 기본정보·NXT 최신 중앙 문서 우선 반환을 적용했다. 중앙 행이 전혀 없거나 연결을 쓸 수 없을 때만 `ka10080` 보완을 허용한다. 대량 분봉 로컬 저장은 `MarketCacheWriter`로 이동했다. 비GUI 인접 93개, MainWindow 23개, 클라이언트 조립·보완 스케줄 28개 등 144개 회귀와 Python compileall이 정상 종료했다.
- 후속 책임 이전: 새 TOP20 편입 종목의 기본정보·NXT 여부·등장 전 KRX/NXT 분봉을 NAS가 중앙 문서/`through_entry` 표식 확인 후 한 번 준비한다. 앱의 기본 `5` 순위와 당일 분봉·확정 일봉은 중앙 저장 API를 우선한다.
- 현재 누적 배포 후보 build는 `2026.09.14-news-reaction-classification-v1`이다. 키움 실전 토큰은 여러 WebSocket을 동시에 유지하지 못하므로 중앙 실시간은 한 연결을 쓴다. 전체 TOP20·당일/익일 15% 코호트의 0B 체결을 우선 보장하되 일반 NXT 코호트는 SOR 통합 코드로 등록한다. `0B`와 `0w`는 각각 독립된 200개 한도로 계산하며, 남는 0B venue 상세와 0w는 TOP20, 실제 매수 편입 종목, 나머지 앱 요청 순으로 배정한다. `_AL`은 SOR 원본으로 저장하고 통합 차트는 SOR 한 벌 또는 KRX+NXT 한 벌만 사용한다. 분봉 보완 때 두 방식의 거래대금 차이 추이를 저장하며 비교 API는 KRX+NXT 완전 비교 통계와 단일 거래소 부분 건수를 구분한다. NAS↔로컬 전환은 각 경로 첫 누적값을 기준점으로만 사용한다. 뉴스 분류는 종목·시장 가격 반응을 제외하되 환율·유가·금리·기업 실적 전망의 변화는 보존한다. REG/REMOVE는 0.25초 간격으로 보낸다. 배포 후 `/health.server_build`, 30초 순위 snapshot 증가, 중앙 실시간 `REG` 승인, 통합 분봉과 거래대금 비교 기록·요약, 최근 뉴스 재분류를 확인한다.
- 2026-09-14 16:48 NAS 이미지 빌드·컨테이너 재생성을 완료했다. `/health`는 `status=ok`, `server_build=2026.09.14-ranking-priority-news-body-v1`를 반환했고, 기본 순위 `subject=5`가 `16:48:00`, `16:49:00`, `16:49:30`으로 자동 증가했다. 재시작 뒤 첫 순위에서 멈추던 현상은 재현되지 않았다. 동기화 전 백업은 `X:\kiwoom-monitor-backups\20260914-164427-ranking-priority-news-body-v1`이며 246개 `src` 파일의 NAS SHA-256 불일치는 0개였다.
- 실제 계좌 매수 체결 종목도 중앙 `00` 알림에서 계좌번호 없이 별도 기록하여, 다음 순위 주기부터 NAS 구독·기본정보·분봉·수급·역사적 신고가 및 장후 확정 보완 대상에 포함한다. TOP20 membership과 지수 구성은 그대로 유지한다.
- 매매일지의 오늘+직전 거래일 최초 차트는 새 중앙 DB 전용 `GET /api/v1/market/recent-minute-bars`를 사용한다. 정상 NAS 연결 중 앱이 `ka10080`을 요청하던 마지막 확인 경로를 제거했다.
- v3는 TOP20 후보 외국인/기관·프로그램·신고가·역사적 신고가, 1~5 순위 기준, 장후 시장지수 차트의 생산 책임을 NAS로 옮긴다. 앱은 중앙 저장값만 읽고, 계좌 과거 체결·비용과 명시적 직접 연결 모드를 제외하면 자동 키움 TR을 만들지 않는다.
- [변경 대상과 Sol 단계별 계획](KRX_NXT_SESSION_CHANGE_20260914_REVIEW.md)을 작성했다. KRX 정규장 09~15:30/장후 시간외종가는 유지하고 16~20 애프터를 추가한다. 현재 NAS/직접 WS는 15:30 이후 NXT 전용이므로 새 KRX 체결 수집이 필요하다.
- 구독만 늘리기 전에 기존 정규장 연구/shadow에 애프터 봉이 자동 유입되지 않도록 보호하고, 일봉·전체 거래일 확정·cohort 종료를 구별한다. 키움/NXT 상세 phase와 모의환경 저녁장 지원은 별도 확인 대상이다.
- 사용자 추가 공지에 따라 KRX 미체결 이월 금지와 NXT VI/거래 재개 단일가의 수집·연구·주문 수명 계약을 감사 문서에 반영했다. NXT 주문까지 15:30에 일괄 만료하지 않으며 실제 주문 종료·자금 해제는 broker 대조로 확인한다.
- S1 로컬 구현 완료: 시행일별 공통 세션 정책과 `krx-regular/v1` 연구·D4 shadow 보호를 추가했다. 집중·인접 회귀 66개, 최종 집중 회귀 18개가 통과했다. 실제 NAS/PC의 16~20 KRX 구독은 S2이며 아직 배포·재빌드 대상이 아니다.
- 추가 화면자료로 차트용 전체일 일봉은 시행일부터 20:00 기준·익일 반영이고 `정규장만 보기`는 별도 범위임을 확인했다. NXT VI 2분 단일가의 예상체결가·매수/매도 잔량 연구를 위해 전수 호가 대신 VI 발생 추적 종목의 구간 한정 1초 snapshot을 S2b로 추가했다. 호가 유형/FID 실확인 전에는 활성화하지 않는다.
- S2a 로컬 구현 완료: NAS 중앙 수집과 PC 직접·fallback이 같은 venue별 정책으로 KRX 애프터와 기존 NXT 구독을 갱신한다. 종목 집합이 같아도 세션 경계에서 정책 서명이 바뀌면 재구독하며 15:30 정규장 종가와 20:00 관측일 종료를 분리했다. 집중·인접 회귀 118개와 독립 핵심 회귀 54개가 통과했다. S2b VI 호가는 아직 미구현이다.
- S3 로컬 구현 완료: 시행일부터 KRX 전체일 분봉·차트용 일봉은 20:00 종료 후 20:05에 보완하고, 15:30 정규장 종가와 완료 상태를 분리했다. 조회 성공과 대상일 실제 봉을 함께 확인하므로 19:59 무체결은 누락으로 만들지 않으며 조회 실패·빈 응답은 완료 처리하지 않는다. Sol 집중·인접 회귀 119개와 독립 핵심 회귀 50개가 통과했다. NAS 배포·build ID는 아직 변경하지 않았다.
- S4 로컬 구현 완료: 원 체결의 계좌 scope·venue·시각과 schedule revision을 `trade-analysis/v2`에 고정했다. 시행일 이후 KRX 종가 단일가, 장후 시간외종가 주문·체결, KRX 애프터를 구분하고 venue 결측과 NXT 동적 phase는 추측하지 않는다. 차트는 15:30 정규장 종가와 20:00 전체일 최종가를 분리하며 자동조회·비용 정산 대기 문구도 새 거래일에 맞췄다. 집중·인접 회귀와 독립 핵심 회귀 78개가 통과했다. NAS 배포·build ID는 아직 변경하지 않았다.
- S5 로컬 구현 완료: 연구 범위를 `krx-regular/v1`, `krx-after/v1`, `krx-full-day/v1`으로 식별하고 요청·RunSpec·검색·D4 checkpoint·forward evidence에 연결했다. 전체일도 15:30~16:00 고정가 구간을 제외하며 Factor와 가상 pending 주문은 16:00에 새 구간으로 시작한다. 세션 공백·거래일 변경·근거 없는 단일가/VI 봉은 next-open 체결이나 결과 완료로 만들지 않는다. 기존 profile 누락 요청의 run/hash는 보존했다. Sol 집중 93개·전체 연구 91개·인접 48개와 독립 핵심 회귀 60개가 통과했다. NAS 배포·build ID는 아직 변경하지 않았다.
- S6 로컬 구현 완료: broker-backed 모의 신규 주문은 KRX `09:00~15:20` 수동 LIMIT를 허용한다. 키움 모의투자가 KRX 전용이라는 공개 범위와 새 KRX 애프터 제도를 실제 응답으로 대조하기 위해, 인증된 수동 주문에 한해 `16:00~20:00` KRX LIMIT도 `manual-mock-krx-after-limit-probe/v1` 근거로 broker까지 보낸다. 이는 애프터 지원 확정이 아니며 접수·거절 응답으로 판정한다. NXT·시장가·15:20~16:00 신규 주문은 계속 거절한다. 취소·broker 대조·재연결 복구·늦은 체결은 시간 gate에서 제외했으며 시각만으로 잔량을 종료하거나 16시에 자동 이월·재주문하지 않는다. 실제 모의 주문·PostgreSQL·장중 관측은 아직 확인하지 않았다.
- 누적 build `2026.09.13-session-journal-sync-v1`의 NAS 이미지 빌드·기동을 완료했다. `/health.status=ok`, `account_query_v2=true`, `journal_v2_sync=true`, `journal_news_links_v2=true`, WebSocket ready/pong을 확인했다. PostgreSQL schema v18 검사기는 A4b 실제 HTTP 왕복·scope·source provenance·tombstone을 포함한 전 항목과 rollback이 모두 true였고, `kt00007` account-query도 verified scope로 HTTP 200을 반환했다.
- 중앙 콘텐츠 증분·동일문서 방어의 다음 누적 배포 후보는 `2026.09.14-content-sync-idempotency-v1`로 확정했다. NAS 소스는 2026-09-14 누적 동기화했으며 실행 이미지 반영 여부는 `/health.server_build`로 별도 확인한다. 기존 배포 이미지의 위 검증 기록은 유지한다.
- 장중 화면 지연 후속: 메인 Qt 스레드에서 매초 수행하던 분봉·현재가·당일고가 SQLite 저장을 전용 단일 writer로 옮겼다. 수정 전 WM_NULL p99 45.93ms·15ms 초과 35/1000회에서 수정 후 재시작 최종 측정 p99 6.04ms·15ms 초과 1/600회로 감소했고 실시간 순위 주기는 유지했다. 계좌 범위 체결 스냅샷의 v2 키·보충 저장 scope 누락도 고쳐 체결마다 반복되던 5회 실패 재시도를 제거했다. 전체 핵심 회귀는 962개(`539+423`)를 모두 검증했으며, 현재 PC의 손상된 venv console launcher 때문에 자식 실행 경로 1개는 실제 앱과 같은 venv `pythonw.exe`로 분리 실행해 통과했다. 재시작 뒤 관련 저장 오류는 0건이다. 이 항목의 제품 변경은 데스크톱 실행 경로이므로 NAS 서버 이미지 재빌드 사유는 아니다.
- 2026-09-14 10:51 확인한 NAS 실행본 `/health`는 정상이나 `server_build=2026.09.13-session-journal-sync-v1`이다. 동기화한 `2026.09.14-content-sync-idempotency-v1` 서버 변경을 실행본에 반영하려면 새 이미지 빌드·컨테이너 재생성이 남아 있다.
- 데스크톱 테스트 바로가기는 이전 worktree e7f7 경로를 가리키던 것이 원인이었고 e0b9의 `scripts/run_test_app_with_data.py`로 수정해 실행을 확인했다. runner의 저장소 루트 import 보완 후 메인·뉴스·일지가 모두 실행됐고 central sync와 ranking 20개가 정상 동작했다. 전체 핵심 회귀는 952개(`536+416`)가 통과했다. 뉴스 7일 수집은 source 9개, raw 290,598건, unique revision 15,069건, duplicate 275,529건, request 2,906건, truncation history 10건, error 0건이었다. queue는 completed 47,529건·failed 5건이고 본문은 fulltext 14,574건·summary_only 490건·body_failed 5건이다. 저장 GLOBAL 뉴스 검색 1건 전후 외부 요청과 job 수는 증가하지 않았다.
- 실제 일지에서 legacy-unassigned review 로드 직후 autosave 예외를 확인해 `_review_dirty` 최소 수정과 legacy scope의 v1 저장 계약으로 해결했고 관련 회귀 53개가 통과했다. 실제 사용자 DB 복사본의 일지 창을 약 1분 실행한 결과 exit 0, 새 `journal-crash.log` 없음, 원본 DB와 crash log 변경 없음이었다. 현재 e0b9 테스트 앱은 재기동 상태를 유지하며 news child도 정상이다. 이 수정은 desktop journal·runner·테스트·문서에만 영향을 주고 현재 NAS server 런타임 동작은 바꾸지 않는다. `src/journal_process.py`는 NAS 소스에 동기화해 다음 이미지에 포함한다.
- 매매일지 계좌 선택 지연은 UI thread에서 query와 요약·상세 렌더, backfill, enrichment를 한 번에 실행한 것이 원인이었고 query 자체는 8~10ms였다. 20ms debounce, 동일 scope 재조회 생략, core render 뒤 후속 갱신 0ms deferred 처리를 적용했다. 실제 데이터 offscreen 계측에서 account handler는 194~247ms에서 0.2~0.6ms로, 최대 event-loop pause는 258ms에서 141.9ms로 줄었다. 관련 회귀 66개가 통과했다. 이 데스크톱 UI 수정 때문에 현재 NAS 서버 이미지를 다시 빌드할 필요는 없다.
- NAS server PID 1 CPU는 최초 44.2% 뒤 6.8%·6.8%·8.4%, DSM 전체 CPU는 6~13%, 메모리는 40%였다. API 메모리는 process 124,563,456 bytes/container 159,633,408 bytes, DB는 1,168,610,995 bytes였다. 일요일이라 live VI/cohort/upper-limit 0건과 condition `NOT_OBSERVED`는 거래시간 미관측이며 candidate 비활성은 의도한 설정이다.
- 다음 거래시간 검증: 08시 venue 구독·second row, live 00/04 계좌 지문, VI/condition/`ka10054`, cohort lifecycle, 09:00~15:20 및 16:00~20:00 모의 KRX 1주 LIMIT, 15:20~15:30 KRX 종가 단일가, 15:30~15:40 NXT 주문접수와 KRX 장후종가 주문접수, 15:40 NXT 접속매매·KRX 장후종가 체결, 16:00 KRX 합류, SOR 주문의 실제 전송 venue, 20:05 finalization, 장시간 저장 증가 및 다른 PC 접속. 영웅문에서 SOR가 프리에는 NXT, 정규장에는 SOR로 자동 표시됐던 사용자 관측을 기준선으로 두고 애프터도 15:40 NXT→16:00 SOR 전환 여부를 실제 주문확인 결과로 기록한다.

## A4b 계좌 경계 재검토 — 0a~0c 누적 배포·기본 검증 완료

- 누적 배포 build ID: `2026.09.13-session-journal-sync-v1`. NAS 이미지 빌드·기동과 기본 런타임 검증을 완료했다.
- 0a 로컬 구현 완료: 일지→메인→뉴스창 relay가 요청 당시 origin/canonical scope를 보존하고, scope 없는 구 명령만 legacy로 처리한다. merge/split도 실제 fill의 두 scope를 검증해 DB와 v2 export에 명시 저장한다. Sol 집중 69개·DB/export 인접 49개와 독립 핵심 회귀 44개가 통과했다.
- 0b 로컬 구현 완료: 서버가 v1 `journal_sync_states`를 허용하고 뉴스 v2는 독립 capability로 협상한다. 0b 단계에서는 0c 전이라 새 flag를 false로 유지했다. 현재는 0c 계약과 검증이 완료되어 서버가 true를 광고한다. 구 NAS optional 404는 보류 진단으로 남기면서 일반 기사·테마를 계속 처리하고, 일지 삭제 상태 404는 merge/upload를 보류한다. 401/500/timeout은 실패로 구별했다. NAS 소스 동기화·build ID·이미지는 변경하지 않았다.
- 0c 로컬 구현 완료: news v3/journal v8, 뉴스 tombstone, origin 기반 v2 hash key, legacy source owner/content hash 원장과 v1/v2 scope 충돌 차단을 반영했다. 중앙 서버는 검증 입력 계약과 함께 `journal_news_links_v2=true`를 광고한다. NAS 파일과 build ID는 변경하지 않았다.
- 직전 집중 회귀 90개/44개와 전체 879개(`494+385`) 통과는 당시 범위의 기록이다. 새 재현 결과가 그 테스트의 누락 경로를 확인했으며 실제 두 PC 동기화 완료 근거로 사용하지 않는다.
- [A4b 구현 순서](A4B_DIRECT_WEBSOCKET_SCOPE_REVIEW.md)의 다음 단계는 1의 로컬 신원 저장과 고정 자격 수명이다. build ID와 NAS 파일은 아직 변경하지 않았다.
- 직접 계좌 자동 결합은 인증된 HTTPS 대조로 확정했다. 현재 PC의 서버 URL은 HTTP이고 NAS HTTPS 제공 여부는 미확인이다. 해당 기능은 HTTPS 종단과 인증서 확인 후 활성화한다. NAS HMAC 키·registry와 기존 `.env`, `postgres-data`, `server-data`를 보존한다.
- `/health.server_build`, capability, PostgreSQL v18 왕복과 앱 실제 경로는 확인했다. 실제 두 PC 간 계좌별 동기화와 위 거래시간 항목은 후속 운영 검증으로 남는다.

## A4a 실시간 계좌 scope 배포 완료

- 로컬 준비 빌드: `2026.09.13-a4-realtime-account-scope-v1`. NAS main/mock 00·04의 9201 지문 검증, 익명 scope envelope, 계좌별 일지 선택과 `journal_v2_*` 동기화 capability를 포함한다.
- 전체 핵심 회귀 874개(`494+380`)가 통과했다. 2026-09-13 NAS 소스 동기화·이미지 빌드·기동 후 `/health.server_build=2026.09.13-a4-realtime-account-scope-v1`, 인증 API, WebSocket ready/pong, `account_query_v2=true`, `journal_v2_sync=true`를 확인했다. 실제 `kt00007`은 `nas-real-default`의 익명 UUID와 binding revision 1로 완료됐고 원본 계좌번호 필드를 반환하지 않았다. mock은 `ka00001` 등록 ref와 시작 시 검증 ref가 일치해 서버가 정상 기동했다. 실제 장중 00/04 일치·불일치 격리와 PostgreSQL `journal_v2_*` 문서 왕복은 다음 거래시간 운영 확인에 남긴다.

## A3 계좌 조회 배포 대기

- 로컬 준비 빌드: `2026.09.13-a3-account-query-v1`. main Kiwoom `ka00001` 신원 검증과 `/api/v2/kiwoom/account-query` 세션, `account_query_v2` capability가 추가됐다.
- NAS 적용 전 `.env`에 32바이트 이상의 기존 `ACCOUNT_IDENTITY_HMAC_KEY`와 `ACCOUNT_IDENTITY_REGISTRY_ENABLED=1`이 필요하다. 값 자체는 저장소·일반 로그에 기록하지 않는다.
- 전체 핵심 회귀 870개(`492+378`)가 통과했다. NAS 소스 동기화·이미지 빌드·기동·`/health.server_build`·인증 capability·실제 2페이지 응답·PostgreSQL 확인 전까지 NAS 적용 완료로 보지 않는다.

## 이번 후속 변경 — 뉴스 저장자료 활용

- 현재 NAS 실행 확인(2026-09-13): `2026.09.13-o1-manual-mock-orders-v1` 컨테이너를 06:27 생성·06:28 시작했고 `/health.status=ok`, `server_build` 일치를 다시 확인했다. 이전 실행에서 인증 capabilities·resource 진단, WebSocket ready/pong과 안전한 미존재 주문 조회 HTTP 404도 통과했으며 실제 주문은 전송하지 않았다.
- PostgreSQL 검사기 후속 검증 완료: NAS 소스의 수정본은 `X:\kiwoom-monitor-backups\20260913-061605-postgres-checker-key-fix` 백업 뒤 SHA-256 일치로 동기화됐지만, 같은 이미지 태그로 재생성된 컨테이너에는 이전 검사기가 남아 있음을 `minute_observation_key` 부재로 확인했다. 제품 저장 경로나 운영 DB 결함은 아니며, 실행 중 컨테이너의 검사 파일만 NAS 소스와 같은 분봉 observation key 계약으로 일시 보정했다. 문법 검사와 PostgreSQL schema v16의 query cache, 실시간·1초·분봉·일봉, observation/export, D5 테마, N1/N2/N3 뉴스, VI/cohort/상한가, shadow 후보, mock execution 원장 CRUD·rollback이 모두 `true`로 통과했다. 영구 NAS 소스는 이미 수정돼 있으며 다음 새 태그 이미지 빌드부터 보정된 검사기가 그대로 포함된다.
- O1 수동 모의주문 NAS 동기화: `2026.09.13-o1-manual-mock-orders-v1`. 별도 기능 플래그와 인증 요청을 모두 통과한 경우에만 KRX 지정가 제출·상태·취소 API가 열린다. 전송·취소 직전에 모의계좌 네 조회를 다시 수행하고, 같은 run의 `request_id`를 멱등키로 사용해 중복 broker 전송을 막는다. 모의 주문은 기존 모의 client의 초당 1회 제한을 공유하며 실전 초당 5회 client에는 편입되지 않는다. 후보·전략 자동주문은 연결하지 않았다. 집중 회귀 83개와 전체 핵심 회귀 823개(`458+365`)가 통과했다. 기존 NAS 소스와 `.env`는 `X:\kiwoom-monitor-backups\20260913-060343-o1-separated-mock-client-v1`에 백업했고, `src` 240개와 명시 빌드 입력 8개의 SHA-256 mismatch 0개로 동기화했다. `deploy/synology/.env`의 기존 값과 `postgres-data`, `server-data`를 보존한 채 주문 플래그만 활성화했다. 이미지 빌드·기동과 안전한 미존재 주문 조회까지 확인했으며, 다음 KRX 세션의 1주 제한 주문 왕복이 남아 있다.
- O1 계좌 모니터 누적 배포: `2026.09.13-o1-separated-mock-client-v1`. 실전 조회와 모의투자 조회는 각각 초당 5회와 초당 1회의 별도 App Key/App Secret·REST client·broker queue·WebSocket을 사용한다. 모의계좌는 최초 `ka10075/ka10076/kt00018/kt00001` 복구, 비공개 00 상세체결 대조, 04·접수·취소·재연결 후 전체 재조회를 수행한다. 04 예수금을 주문가능금액으로 쓰거나 계좌 원문을 일반 WebSocket에 공개하지 않으며 주문 transport도 연결하지 않았다. 관련 회귀 96개와 전체 핵심 회귀 817개(`452+365`)가 통과했다. NAS의 기존 빌드 입력은 `X:\kiwoom-monitor-backups\20260913-053315-o1-mock-execution-v1`에 백업했고, `src` 240개와 명시 빌드 입력 8개의 SHA-256 mismatch 0개로 동기화했다. `.env`, `postgres-data`, `server-data`를 보존한 채 이미지 빌드·시작을 완료했다. `/health.server_build`, 인증 capabilities·resource 진단, WebSocket ready/pong이 통과했다. 보존된 NAS `.env`에는 모의 App Key/App Secret·익명 account ref·run ID와 활성화 플래그가 모두 설정됐다. 컨테이너 재생성 뒤 uptime 49초로 새 설정 반영을 확인했으며, 현재 일요일이므로 평일 모의계좌 00/04 실수신과 실제 복구 응답 확인은 다음 KRX 세션에 남는다.
- O2a 로컬 작업본: mock 전진평가의 동결 프로파일, V1/O1 근거 조립, 데이터/시스템/성과 gate, 불변 중앙 내부 문서 저장을 구현했다. 서버 시작·API·DB schema와 주문 경로는 바꾸지 않았다. `broker_mock_validated` 저장에는 같은 전략의 저장된 최종 PASSED 보고서가 필요하다. 집중 회귀 32개와 전체 핵심 회귀 794개(`429+365`)가 통과했다. 현재 NAS 실행본 `2026.09.13-o1-mock-execution-v1`에는 이 후속 로컬 모듈이 아직 동기화되지 않았다.
- O1 서버 작업본: `2026.09.13-o1-mock-execution-v1`, 중앙 스키마 v16. mock/KRX 주문 계약, 단발 전송 경계, 응답 유실·부분체결·취소 경합 상태기계와 중앙 execution 원장을 추가했다. 서버 시작 경로에는 transport/runtime을 연결하지 않아 배포만으로 주문이 발생하지 않는다. 집중 회귀 65개와 전체 핵심 회귀 784개(`419+365`)가 통과했다. 누적 NAS 빌드 입력 동기화와 사용자 이미지 빌드·기동을 완료했고 PostgreSQL v16 전체 CRUD·rollback 검사는 아직 하지 않았다.
- O1 누적 NAS 동기화: 기존 빌드 입력은 `X:\kiwoom-monitor-backups\20260913-040209-o1-mock-execution-v1`에 백업했다. 전체 `src` 235개와 명시 빌드 입력 8개의 SHA-256 mismatch는 0개이며 `.env`, `postgres-data`, `server-data`를 보존했다. NAS의 앱 빌드 값과 Compose 이미지 태그는 모두 `2026.09.13-o1-mock-execution-v1`이다.
- O1 실행 확인: `/health`가 `status=ok`, `server_build=2026.09.13-o1-mock-execution-v1`를 반환했다. 인증 capabilities·resource 진단과 WebSocket ready/pong이 통과했다. 확인 시 서버 프로세스 메모리는 약 84MiB, 중앙 DB는 약 697MiB였다. 컨테이너 내부 PostgreSQL 검사기는 SSH 계정/로컬 Docker 실행 경로가 없어 별도 실행이 남아 있다.
- 로컬 뉴스창: 저장 본문·공급계약 규칙 근거의 선택 조회를 구현하고 관련 회귀 및 임시 API 연결을 확인했다. 테스트 앱은 현재 워크트리 소스를 사용하므로 뉴스 보조 프로세스까지 다시 시작하면 이 UI를 읽는다.
- 서버 작업본: `2026.09.13-d4-shadow-candidates-v1`, 중앙 스키마 v15. D3 Factor/Family를 저장된 TOP20·strict KRX 완료봉 sequence에 연결하는 주문 없는 shadow 후보 원장, 재시작 checkpoint, 인증 cursor API와 앱 후보 창을 추가했다. 생성은 전략 JSON·후보군 최신성 값을 명시하기 전까지 기본 OFF다. 아래의 정상 운영 확인 기록은 이전 실행본에 대한 기록이다.
- D4 집중 회귀 99개와 전체 핵심 회귀 677개(`324+353`)가 통과했다. 후보 1회 발행, 재시작 중복 방지, stale 후보군 차단, SQLite 불변 원장/cursor, API 인증·OFF 품질, 앱 첫 무음 동기화·동일 event 무알림을 포함한다. PostgreSQL 검사기에는 v15 state/decision/candidate 왕복을 추가했으나 실제 NAS 실행 결과는 아직 없다.
- D3a 관련 회귀 92개와 전체 핵심 회귀 644개(`292+352`)가 통과했다. PostgreSQL 검사기는 분봉 delta 동일 operation 재시도 검증을 포함하지만 실제 NAS PostgreSQL 결과는 재빌드 뒤 확인해야 한다.
- D2 집중 회귀 77개와 전체 핵심 회귀 639개(`287+352`)가 통과했고 두 핵심 묶음 모두 종료 코드 0이었다. 5,001개 동일시각 기록, 추출 중 신규 commit 격리, API 인증·page, 가상 시계 재생을 포함한다. PostgreSQL 검사기는 D1 원장과 D2 export page round-trip·rollback을 포함하지만 실제 NAS PostgreSQL 결과는 재빌드 뒤 확인해야 한다.
- v11 종목 뉴스 병합 관련 회귀 103개가 통과했다. 병목 수정 후 뉴스 source·서비스·작업·서버 API 관련 회귀 65개와 전체 핵심 회귀 624개(`273+351`)가 통과했고 두 핵심 묶음 모두 종료 코드 0이었다. 관련 테스트와 핵심 테스트는 겹치므로 합산하지 않는다.
- 검증: 관련 회귀 77개, root 전체 핵심 회귀 612개(`270+342`) 통과. 두 핵심 묶음 모두 종료 코드 0. 별도 임시 API 연결과 수정 전후 재현, v9→v10 기존 관측 보존도 확인했다. 서로 겹치는 관련/핵심 테스트 수를 합산하지 않는다.
- 핵심 회귀에 뒤이어 추가된 정식 v9→v10 fixture 테스트 1개도 별도 실행으로 통과했다. 그 사이 제품 코드는 변경되지 않았다.
- 실제 PostgreSQL: 검사기를 새 동작에 맞춰 보완했지만 이번 v10/v11 및 동시 BODY/target 처리의 실환경 검사는 실행하지 않았다. SQLite 검증과 PostgreSQL 코드 검토를 실제 PostgreSQL 실행 결과로 해석하면 안 된다.
- `n3-stock-news-v1`, 50ms 간격의 `news-job-pacing-v1`, 초당 1건과 비동기 health를 적용한 `news-worker-fairness-v1` 모두 실환경에서 단일 코어 CPU 99%대와 내부·외부 `/health` 시간 초과가 재현됐다. PostgreSQL은 정상이고 앱의 조회 성공은 NAS가 아니라 로컬 Kiwoom 장애전환 결과다. `server-stack-diagnostic-v1`의 스택은 메인 이벤트 루프가 `news_sources._targets → _exact_company_name → re.compile`에서 KRX 회사명 패턴을 반복 생성 중임을 확인했다. 수정본은 전체 종목에 대한 정규식 생성을 피하고 이름별 패턴을 재사용하며 페이지 계산을 worker thread에서 수행한다.
- 활용 범위와 실제 수집 장애의 재현 근거는 `reports/ADDED_DATA_USAGE_REVIEW.md`에 기록했다.

## 직전 배포 확인 기록

- 상태: **`2026.09.12-vi-hot-cohort-v1` NAS 소스 동기화·사용자 빌드·기동 및 기본 API 확인 완료, PostgreSQL 실통합·Kiwoom 실시간 검증 대기**
- 직전 배포의 서버 빌드/이미지: `2026.09.12-vi-hot-cohort-v1` / `kiwoom-monitor-server:2026.09.12-vi-hot-cohort-v1`
- 로컬 검증: 시장 이벤트 관련 회귀 89개와 전체 핵심 회귀 580개(`270+310`) 통과. 두 핵심 회귀 묶음 모두 종료 코드 0
- 누적 배포 내용: 중앙 DB v2 메타데이터 표와 v3 시장상태 특수시각 보정, 순위·TOP20 지수·시장 상태·조회/실시간 분봉·일봉의 값/메타데이터 동시 저장, coverage 진단 API, 빈/오래된 차트 캐시 보완, 실제 0B 거래대금 보호, 공식 KRX 카탈로그 시장 분류, TOP20 디스크 outbox와 PostgreSQL 실통합 검사기. 일반 기존 행은 추정 보정하지 않고 확인된 `T88:88` 행만 `saved_at` 서울 시각으로 이전
- NAS 파일 동기화: `2026.09.12-vi-hot-cohort-v1` 소스 56개 동기화 완료, SHA-256 mismatch 0. `.env`, `postgres-data`, `server-data` 보존 확인. 배포 직전 백업은 `X:\kiwoom-monitor-backups\20260912-191839-vi-hot-cohort-v1`
- NAS 실행본 반영 여부: 사용자가 Container Manager에서 `kiwoom-monitor-server:2026.09.12-vi-hot-cohort-v1` 빌드를 완료했다. `http://192.168.0.5:8787/health`가 `status=ok`, `server_build=2026.09.12-vi-hot-cohort-v1`를 반환했다. 인증 `themes/history`, 뉴스 article/event history, `news/sources`, `market/events?kind=cohort`가 모두 HTTP 200을 반환했다. cohort 조건 상태는 `NOT_OBSERVED`이며 Kiwoom 점검 및 현재 KRX 비수집 시간과 일치한다. v9 전용 market events 조회가 200을 반환해 v9 migration 적용도 확인했다.
- PostgreSQL 실제 통합: 중앙 스키마 v3, query cache, realtime, minute/daily bars, observation metadata, dataset snapshots, documents, external bars 왕복과 rollback 모두 통과
- 실제 장애전환: 동일 조회 클라이언트에서 NAS 정상 조회 후 서버 컨테이너를 중지해 이 PC의 키움 API로 `ka00198` 20종목을 수신했고, 서버 재시작 뒤 NAS 조회로 자동 복귀해 다시 20종목을 수신함
- 보존 확인: 중앙 DB 약 283MB와 `deploy/synology/.env`, `postgres-data`, `server-data` 유지
- 남은 검증: 서버 컨테이너의 실제 PostgreSQL checker로 D5/N1/N2/N3 및 v9 VI/cohort/상한가 CRUD·parity·rollback을 확인해야 한다. 실제 NAVER query_set의 7일 품질과 저장량·CPU·메모리, 기존 watchlist/순위 지연도 측정해야 한다. Kiwoom이 현재 점검 중이므로 실제 1h/CNSRLST/CNSRREQ/ka10054와 다음 관측 KRX 세션 종료 만료는 점검 종료 뒤 확인한다.

## 다음 실환경 검증

1. **완료(2026-09-13):** 서버 컨테이너에서 PostgreSQL schema v16 전체 검사기를 실행해 1초 집계, D5 테마, N1 article/body/AI/job, N2 event/membership, N3 source/target/budget, VI/cohort/상한가, shadow 후보, mock execution 원장과 최종 rollback이 모두 `true`임을 확인했다.
2. 공급계약 테스트 뉴스가 수락된 뒤 최신 `news_article` projection이 즉시 보이고, BODY와 RULE job이 `COMPLETED` 및 실제 body/event revision 참조를 남기는지 확인한다. 자동 AI를 켠 환경에서는 AI job, immutable AI revision, `news_ai` 최신 projection과 같은 요청의 일일 사용량도 함께 확인한다.
3. 다음 거래일 08:00 이후 실제 1초 체결 행과 종목별 초 집계를 확인하고, 장시간 운용 뒤 저장량·메모리·디스크·queue 및 순위/TOP20/1분봉 수집 연속성을 비교한다.
4. 다른 PC에서 같은 NAS API 인증 조회와 WebSocket 연결을 확인한다.
5. `/api/v1/news/sources?days=7`에서 각 기본 query의 `last_success`, cursor, raw/unique/duplicate, `truncated/error`, request budget, body/RULE/job 상태를 확인하고 이를 시장 전체 전수 coverage로 해석하지 않는다.
6. `/api/v1/news/search`에서 confirmed GLOBAL 기사가 정확한 종목 목록에 보이고, unresolved·ambiguous·타종목은 제외되며 같은 identity의 owner 기사가 우선하는지 확인한다. 이 조회 전후 NAVER 요청 예산과 BODY/RULE/AI job 수가 목록 병합 때문에 늘지 않는지 확인한다.
7. Kiwoom 점검 종료 뒤 `/api/v1/market/events?kind=cohort`에서 조건 이름이 정확히 하나 선택되고 coverage가 KRX인지 확인한다. 1h 발동/해제와 ka10054 보완이 중복 없이 남는지, 편입 종목의 0B가 TOP20 중복 때 한 번만 집계되는지, NXT 가능 종목이 NXT 구독에 포함되는지 확인한다.
8. 편입 세션 종료에는 cohort가 유지되고 다음 실제 관측 KRX 세션 종료에 만료되는지 확인한다. `upl_pric`과 0B 근거가 없는 과거 상한가를 확정하지 않으며 어떤 주문도 생성되지 않는지 확인한다.

후속 기능에서 NAS 서버 코드가 더 바뀌면 이 문서의 빌드 값과 배포 범위를 계속 갱신한다.

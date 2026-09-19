# R7a — 런타임 인증 누적 NAS 배포 준비

작성일: 2026-09-16. source sync와 이미지 배포/운영 검증은 구분한다.
이번 누적 build: `2026.09.16-runtime-credentials-r7-deploy-v1`.

## 준비 및 확인 사실

- 사용자 세션의 `X:\kiwoom-monitor` 실제 경로와 src/deploy 프로젝트 표식을 확인했다.
- 실제 NAS `/health`는 status=ok, `2026.09.15-shadow-candidate-settings-v1`이다.
  새 이미지 재빌드/health 일치 검증 전이다.
- 동기화 전 NAS compose도 구 build였다. R0~R6c의 누적 변경을 기존 파일/새 파일 모두 포함해 동기화했다.
- 기존 Docker context에는 `.codex-backups` 제외가 없었다. 실제 NAS에 이 폴더가 존재함을 확인했고,
  백업/실제 env/secret 폴더 제외를 추가했다. 이 변경 때문에 최종 R7 태그를 세 곳에 갱신했다.
- Dockerfile은 `--no-deps` 설치 이후 cryptography를 직접 설치한다.
  compose는 기존 DB/data를 유지하고 server-secrets를 `/app/secrets`에 영속 마운트한다.
- 기존 신원 registry/HMAC 설정은 준비돼 있다. 비밀값은 출력/보고서/코드 백업에 넣지 않았다.
- 앱 NAS 주소는 HTTP이며 신뢰 프록시는 미설정이다. 새 인증 관리에는 HTTPS/신뢰 프록시 준비가 필요하다.
  이를 해결하기 위해 보안 검증을 끄거나 실제 env/인증키를 임의 변경하지 않는다.
- 사용자가 제공한 `https://192.168.0.5`에 인증키 없는 `/health` 요청을 보냈지만,
  엄격한 TLS 검사에서 `RemoteCertificateNameMismatch`로 실패했다. IP와 인증서 이름 불일치는 확인됐고,
  이 HTTPS 주소가 NAS API로 라우팅되는지는 미확인이다. 인증서와 일치하는 주소/프록시 연결 확인이 남았다.
- 사용자가 주소를 `https://mfactory.duckdns.org`로 정정했다. 공개 `/health`는 TLS 검사를 통과하고
  HTTP 404를 반환했다. 서버 시작 전 확인이므로 시작 후 API 라우팅을 다시 검증한다.

## 동기화 계약

전체 Git tracked와 새 untracked 파일을 기준으로 local source의 SHA256 manifest를 만든다.
NAS 저장소 루트에 삭제형 mirror를 사용하지 않는다. 기존 release/검사 결과/백업 등 NAS 전용 파일도 유지한다.
최초 비교는 762개/약 32.4 MiB이며 134개 변경(기존 57개/새 77개), 나머지 628개는 같았다.
보고서 갱신 후 최종 파일 수/변경 수는 아래 실제 동기화 결과로 기록한다.

백업 위치는 `X:\kiwoom-monitor\.codex-backups\runtime-credentials-r7-20260916-012631`이다.
원본이 있는 변경 파일은 복사 전에 같은 상대 경로로 보관하고 백업 hash를 확인한다.
복사 전 source/현재 NAS hash를 재확인하고 복사 후 각 파일/전체 manifest와 build 세 곳을 검사한다.
`.env`는 배포 전후 hash가 같은지 확인한다. postgres-data/server-data/server-secrets는
전체 경로 구성요소에서 제외하므로 동기화/일반 코드 백업에 포함되지 않는다.
실행 중 DB가 자체 갱신되는 것까지 멈췄다는 뜻은 아니며 서버 재시작/주문은 수행하지 않는다.

동기화 목록/결과는 local `tmp/r7-nas-source-manifest.json`, `tmp/r7-nas-dry-run.json`,
`tmp/r7-nas-sync-result.json`과 NAS 코드 백업의 sync receipt에 남긴다. 이 자료에는 비밀값이 없다.

## 검증

`tmp/r7-deploy-regression.log`: 배포 계약 관련 368개, 151.657초, exit=0, OK(skipped=1).
Windows의 POSIX 권한 검사 1개만 skip했다(`test_credential_store.py`). 실제 Linux/NAS 권한 검증은 남아 있다.
서버 설정/API/우선 REST/collector/계좌 신원/모의 monitor/주문 수명/뉴스/AI/source collection/
PC 설정/credential vault·runtime·barrier/실전·모의·공급자 owner/PC 관리 통합/계획 재연결을 포함한다.
모두 fake API/WS와 임시 DB/vault 테스트이며 실제 키움 주문/키/사용자 DB/NAS 재빌드를 사용하지 않았다.

## 실제 동기화 결과

- 2026-09-16 01:33:40 KST 최초 누적 동기화 완료: 전체 763개 hash 확인,
  변경 135개(기존 57개 원본 백업/새 파일 78개), 동일 628개.
- 백업 receipt: `sync-835e1870-cde8-457f-a656-a67ba1448820.json`.
  위 백업 경로에서 최초 원본을 보존하며 후속 결과 문서도 같은 계약으로 동기화한다.
- `.env` 전후 동일, 데이터/secret 폴더는 대상에서 제외했다. 삭제형 mirror와 서버 재시작은 하지 않았다.
- AST 281개, manifest 763개, build 세 곳, Docker context/cryptography/secret mount 사전 검사 통과.
- 동기화 후 읽기 전용 build gate는 여전히 구 실행 이미지의 health=ok를 확인하고
  `build_pending`으로 중단했다. 새 인증 API 호출/실환경 검증을 먼저 실행하지 않았다.

소스 동기화 완료이며 이미지 재빌드/실행 완료는 아니다.

사용자 빌드 화면에서 R7 이미지 빌드/태깅 성공과 DB healthy를 확인했으나 서버 시작은
`deploy/synology/server-secrets` 원본 폴더 부재로 bind mount 실패했다.
NAS 실제 폴더 부재를 확인하고 검증된 경로에 빈 폴더를 생성했다. 기존 데이터/키는 변경하지 않았다.
Dockerfile/compose 변경 없이 기존 프로젝트 재시작이 가능하다. 준비 문서에 최초 폴더 생성 조건을 추가했다.
당시 서버 실행 health와 실제 vault Linux 권한은 미검증이었다. 이후 시작 확인 결과는 아래에 기록한다.

## R7b — 사용자 시작 후 읽기 전용 운영 확인

2026-09-16 01:40~42 KST 기존 앱의 NAS 설정/접속 토큰을 메모리에서만 사용해 확인했다.

- `/health`: status=ok, 실제 build가 `2026.09.16-runtime-credentials-r7-deploy-v1`과 일치한다.
- capability: runtime_credentials_v1, multi_account_query_v3, account_contexts_v3,
  scoped_mock_orders_v2, planned_reconnect_v1 등 구현 capability가 활성화돼 있다.
- 인증 메타데이터 조회 성공: 프로필 7개, 실전·모의·네이버·DART·OpenAI·Gemini·Claude 지원 표시.
  키/계좌 신원/개별 프로필 내용은 출력하지 않았다. 이 조회 성공이 각 공급자 키의 유효성 검증은 아니다.
- 중앙 문서 DB 읽기/앱용 WebSocket ready 성공. 실제 키움 상류 REG/체결 성공을 뜻하지 않는다.
- 체결 상태 WAITING_MARKET, paused=false, shutdown=false, observation_expected=false.
  현재 거래시간 밖이며 이 상태를 장애로 보고하지 않는다.
- 순위 저장 회차가 01:41:30에서 01:42:00으로 진행했다. 24시간 순위 수집은 체결 대기와 별개로 동작한다.
- 정정 HTTPS 주소의 TLS 검사는 통과하지만 `/health`는 nginx HTML 404이며 FastAPI의 404가 아니다.
  사용자가 역방향 프록시 미설정을 확인했다. 내부 API 정상과 HTTPS 미연결을 구분한다.
- HTTPS 프록시 계획: `mfactory.duckdns.org:443` HTTPS → `127.0.0.1:8787` HTTP,
  WebSocket 헤더/해당 도메인 인증서 연결. 설정 후 public health/wss와 실제 프록시 peer 신뢰를 확인한다.
  임의 trusted IP/전체 네트워크 허용/검증 우회/실제 env 변경은 하지 않았다.

결과는 local `tmp/r7-after-build-result.json`, `tmp/r7-rank-after-start-result.json`에 남겼다.
이미지 배포/기본 읽기 확인은 완료했으며 R7 전체 운용 검증은 미완료다.

## R7c — 8443 HTTPS 프록시 확인

사용자는 443을 DSM에 사용 중이라고 확인했고 NAS API용 별도 8443 프록시를 설정했다.
`https://mfactory.duckdns.org:8443/health`는 엄격한 인증서 검사 후 R7 build/health=ok다.
동일 HTTPS의 인증 capability/프로필 메타데이터/DB 읽기도 성공했다.
앱 주소 파일은 아직 기존 HTTP이며 임의로 변경하지 않았다.

- WSS `/api/v1/realtime`은 HTTP 404다. 내부 WS ready와 구분하며 현재 프록시의 WebSocket 헤더/경로 확인이 필요하다.
- 필수 request_id/label이 없는 `{}`만 프로필 생성 경로에 보내 transport 검사를 확인했다.
  secure 검사가 본문/저장보다 앞서며 `HTTPS_REQUIRED`로 거부했다. 실제 프로필 생성/키 준비/변경은 불가능한 요청이다.
- 현재 credential trusted proxy는 미설정이다. 서버는 uvicorn proxy_headers=False와
  정확한 peer IP 및 단일 X-Forwarded-Proto=https 검증을 유지한다.
  프록시의 실제 접근 로그 IP를 확인한 뒤 해당 IP만 초기 설정한다. gateway IP 추정/와일드카드 허용은 하지 않는다.
- `/health?r7_proxy_peer=20260916` 공개 요청으로 기존 접근 로그에서 peer를 찾을 수 있게 했다.
  HTTPS upstream 전체 성공으로 보고하지 않으며 보안 검증을 끄지 않는다.

결과는 `tmp/r7-https-after-proxy-result.json`이다. 현재 비밀값 전송/실제 env/주문/앱 연결 설정 변경은 없다.

## R7c1 — 실제 프록시 peer 확인/초기 설정 승인 대기

사용자 제공 서버 로그 `kiwoom-monitor-server-1 (1).html`에서 health marker의 peer `172.23.0.1`/200,
일반 HTTP GET realtime/404, 빈 JSON credential 검사/426을 확인했다. 원본 로그의 비밀값은 출력하지 않았다.
NAS env의 trusted proxy 항목은 0개다. 리뷰 가능한 수정안은 기존 다른 바이트를 보존하고
`CREDENTIAL_TRUSTED_PROXIES=172.23.0.1` 한 줄만 추가하는 것이다.
이 설정은 해당 Docker gateway에서 전달된 단일 X-Forwarded-Proto=https를 신뢰하는 보안 경계다.
키 변경/주문 권한 활성화가 아니며 이미지 빌드 대신 서버 컨테이너 설정 재적용/재생성이 필요하다.

실행 전 자동 승인 검토가 영속 credential trusted-proxy 설정과 민감 env 복구 백업 변경을 거부했다.
사유: 사용자 IP 확인은 정확한 보안 설정 변경에 대한 명시 승인이 아니라는 판단이다.
따라서 실제 env/백업/서비스는 변경하지 않았다. 우회 실행하지 않으며 사용자 명시 승인 후에만 적용한다.
준비 스크립트는 기존 env 복구본을 일반 코드 백업에 넣지 않고 CurrentUser DPAPI로 로컬 tmp에 암호화하여
복구 바이트 일치 검증 후 한 줄을 추가한다. 승인 전 해당 스크립트는 실행되지 않았다.

### 사용자 명시 승인 후 초기 설정 기록

사용자가 `응`으로 위 정확한 한 줄과 암호화 복구본을 승인한 후 제한 밖 검토도 승인됐다.
검증된 NAS `deploy/synology/.env`에 `CREDENTIAL_TRUSTED_PROXIES=172.23.0.1`만 추가했다.
기존 다른 바이트는 보존했고 CurrentUser DPAPI 복구본의 복원 일치를 메모리에서 확인했다.
복구본은 로컬 ignored tmp에 암호화 파일만 보관하며 일반 코드/DB 백업에 넣지 않았다.
키/계좌/DB/앱 주소/실제 주문/서버 실행 상태는 변경하지 않았다.

적용 후 기존 실행 컨테이너의 HTTPS 검사는 여전히 HTTPS_REQUIRED, WSS 404이며 DB/metadata는 정상이다.
compose는 env를 컨테이너 생성 때 읽으므로 `.env` 변경만으로 실행 환경이 갱신되지 않는다.
이미지 재빌드 없이 server 컨테이너를 설정 재적용/재생성한 뒤 검증해야 한다. 단순 restart는 부족하다.
NAS 호스트의 배포 디렉터리에서 사용하는 재적용 명령은
`docker compose up -d --no-build --force-recreate server`이다. 서버 컨테이너 내부에서 실행하는 명령은 아니다.
현재 환경에 NAS 호스트 Docker 실행 수단이 없어 명령을 실행하지 않았다.
프록시의 WebSocket 헤더/Upgrade 전달 확인도 별도로 남아 있다.

## R7c2 — 재적용 후 HTTPS/WSS·신뢰 검증 완료

사용자가 프로젝트 재적용과 프록시 헤더 확인 완료를 알린 후 동일 HTTPS 8443에서 다시 검사했다.
R7 build/health=ok·DB/프로필 메타데이터 읽기·runtime_credentials capability·WSS ready(101)가 모두 통과했다.
필수값 없는 `{}` transport 검사는 HTTPS_REQUIRED가 아닌 INVALID_CREDENTIAL_REQUEST를 반환했다.
이는 secure boundary를 통과한 뒤 본문 필수값 검사가 안전하게 거부한 것이며 실제 프로필/키/주문 변경은 없다.

기존 앱 사용자 `data_source.json`의 mode=personal_server/이전 내부 NAS 주소를 확인하고
server_url만 `https://mfactory.duckdns.org:8443`으로 변경했다. 접속 토큰과 다른 JSON 필드/형식은 보존했다.
저장 전 기존 바이트 불변, 저장 후 예상 JSON/바이트 일치를 검증했다. 복구용 기록에는 이전/새 URL만 남긴다.
앱이 켜져 있으면 재시작해 새 주소를 읽어야 하며 이 단계에서 앱 프로세스를 종료하지 않았다.

검증 결과는 `tmp/r7-https-after-proxy-result.json`, `tmp/r7-app-https-setting-result.json`이다.
HTTPS 초기 설치/프록시 신뢰/앱 주소 전환은 완료했다. 실제 공급자 키 유효성/장중 다계좌 REG/교체 간격,
PostgreSQL 통합과 실제 Linux 권한 검증까지 완료했다는 뜻은 아니다. 이미지 추가 재빌드는 필요 없다.

## 사용자 재빌드와 후속 운영 검증

동기화 완료 안내 후 Container Manager의 기존 프로젝트에서 새 서버 이미지를 빌드/시작한다.
database/server 상태와 `/health.server_build`가 이번 R7 태그인지 먼저 확인한다.
이전 이미지가 그대로면 운영 검증으로 넘어가거나 배포 완료라고 보고하지 않는다.
그 다음 인증된 capability/profile 상태/DB schema/WS ready와 실제 REG 준비/계좌별 수신을 확인한다.
PostgreSQL 회귀는 기존 임시 검사 helper로 수행하고 실제 주문을 실행하지 않는다.
기존 순위 갱신/빈 응답/다계좌 분리/HTTP·WS 단절·복구/계획된 교체/실제 관측 간격도 확인한다.
새 키 준비·적용은 HTTPS/신뢰 프록시 조건을 충족한 주소에서만 검증한다.

사용자 이미지 재빌드/시작과 새 health 일치·DB 읽기·앱 WS·순위 회차 진행은 확인했다.
HTTPS 프록시/신뢰 설정과 앱 저장 주소 전환은 R7c2에서 확인 완료했다.
실환경 다중 토큰/REG/관측 간격, PostgreSQL 통합·Linux 권한 검증은 남아 있다.
모델 에스컬레이션 없음.

# 시놀로지 개인 중앙 서버 설치

이 구성은 Container Manager에서 서버와 PostgreSQL을 함께 실행한다. PostgreSQL 포트는 외부에 공개하지 않고 앱은 중앙 서버의 8787 포트에만 접속한다.

## 준비

- DSM 패키지 센터에서 `Container Manager`를 설치한다.
- File Station에서 `/volume1/docker/kiwoom-monitor` 폴더를 만든다.
- 이 저장소 전체를 위 폴더에 복사한다. `deploy/synology` 폴더만 따로 복사하면 빌드할 수 없다.

## 설치

1. `/volume1/docker/kiwoom-monitor/deploy/synology/.env.example`을 같은 폴더의 `.env`로 복사한다.
2. `.env`의 `POSTGRES_PASSWORD`와 `MONITOR_SERVER_ACCESS_TOKEN`을 서로 다른 긴 영문·숫자 값으로 교체한다. DB 비밀번호에는 URL 예약문자를 넣지 않는 것이 안전하다.
3. 기본 포트 8787을 바꾸려면 `KIWOOM_MONITOR_PORT`를 1~65535 사이의 사용하지 않는 포트로 지정한다. DSM이 사용하는 일반적인 `SERVER_PORT` 이름은 쓰지 않는다.
4. `KIWOOM_APP_KEY`, `KIWOOM_SECRET_KEY`를 입력한다. 뉴스·DART·AI를 서버에서 사용할 때만 해당 키도 입력한다.
5. Container Manager에서 `프로젝트` → `생성`을 누른다.
6. 프로젝트 이름은 `kiwoom-monitor`, 경로는 `/volume1/docker/kiwoom-monitor/deploy/synology`로 지정하고 기존 `docker-compose.yml`을 사용한다.
7. 빌드 및 시작을 누른 뒤 `database`와 `server` 컨테이너가 모두 `healthy`인지 확인한다.

같은 네트워크의 PC 브라우저에서 `http://NAS주소:KIWOOM_MONITOR_PORT/health`를 열어 `status`가 `ok`인지 확인한다. 이 주소는 공개 상태 확인용이며 실제 데이터 API는 토큰 인증을 요구한다.

`postgres-data`에는 PostgreSQL 원본이, `server-data`에는 DART 캐시 등 서버 파일이 남는다. 컨테이너를 업데이트해도 두 폴더는 삭제하지 않는다.

### PostgreSQL 실제 통합 검증

누적 배포 뒤 Container Manager의 프로젝트 터미널에서 서버 컨테이너에 대해 아래 명령을 한 번 실행한다.

```sh
python /app/scripts/check_postgres_integration.py
```

`status: ok`, 현재 `schema_version`, `checks`의 모든 값, `rollback: true`가 모두 나와야 한다. 검사는 쿼리 캐시·실시간 최신값·분봉·일봉·관측 메타데이터·데이터셋·문서·외부 봉을 왕복한다. 검증용 행은 성공·실패 여부와 관계없이 마지막에 삭제하므로 운영 자료로 누적되지 않는다. PostgreSQL 5432 포트를 외부에 노출할 필요는 없다.

## 앱 연결

앱의 `연결` 탭에서 `NAS로 연결`을 고르고 NAS 연결 설정에 주소와 `MONITOR_SERVER_ACCESS_TOKEN` 값을 입력한다. 외부 접속은 NAS 포트를 인터넷에 직접 공개하기보다 Tailscale이나 VPN을 권장한다.

- 같은 집/사무실 네트워크: `http://NAS내부IP:8787`
- 외부 접속: Tailscale을 설치한 뒤 `http://NAS의-Tailscale-IP:8787`
- 연결 초기에는 `로컬 자동 전환`을 켜 두면 NAS 장애 때 기존 로컬 키움 연결로 돌아간다.

NAS 연결 설정의 `서버 사용량`에서 앱 프로세스 메모리, 서버 컨테이너 메모리, PostgreSQL DB 크기와 NAS 저장 볼륨 사용량을 확인할 수 있다. PostgreSQL 컨테이너 자체 메모리는 Docker 소켓을 서버에 제공하지 않으므로 Container Manager에서 확인한다.

## HTTPS 외부 접속

8787 같은 내부 서버 포트를 인터넷에 직접 열지 말고 DSM 역방향 프록시가 HTTPS를 종료하도록 구성한다.

1. DSM `제어판 → 외부 액세스 → DDNS`에서 사용할 호스트 이름을 만들고 Let's Encrypt 인증서를 발급한다.
2. DSM `제어판 → 로그인 포털 → 고급 → 역방향 프록시`에서 규칙을 만든다.
   - 소스: `HTTPS`, 만든 호스트 이름, 외부 포트(권장 443)
   - 대상: `HTTP`, `127.0.0.1`, `.env`의 `KIWOOM_MONITOR_PORT`
3. 역방향 프록시의 사용자 지정 헤더에서 `WebSocket`을 추가한다. 실시간 순위 연결에 필요하다.
4. 인증서 설정에서 해당 호스트 이름에 발급한 인증서를 연결한다.
5. 공유기의 외부 포트는 NAS의 HTTPS 소스 포트로만 전달하고 `KIWOOM_MONITOR_PORT`는 외부에 전달하지 않는다.
6. 앱 NAS 주소에는 `https://호스트이름`을 입력한다. 외부 포트가 443이 아니면 `https://호스트이름:외부포트`로 입력한다. 앱은 실시간 연결을 자동으로 `wss://`로 바꾼다.

공개 인터넷 대신 개인 장치에서만 쓸 경우에는 Tailscale/VPN이 공격 표면이 더 작다. 어느 방식을 쓰더라도 긴 `MONITOR_SERVER_ACCESS_TOKEN`은 유지한다.

## 배포 점검

개발 PC에서 다음 형식으로 점검한다.

```powershell
kiwoom-monitor-check --url https://서버주소 --token 접속토큰
```

성공 결과는 상태 확인, 토큰 인증, DB 읽기, 실시간 WebSocket 네 항목이 모두 `true`로 나온다. 키움 장이 닫혀 있어도 WebSocket 서버 연결 자체는 확인할 수 있다.

저장 연속성과 메모리·DB 증가를 같은 형식으로 기록하거나 다른 PC에서 접속을 검증할 때는 저장소의 검사기를 쓴다.

```powershell
$env:KIWOOM_MONITOR_SERVER_URL='https://서버주소'
$env:MONITOR_SERVER_ACCESS_TOKEN='접속토큰'
python scripts/check_nas_operational.py
python scripts/check_nas_operational.py --observe-seconds 3600 --poll-seconds 60
```

두 번째 명령은 한 시간 동안 메모리, DB 크기, 최신 ranking/TOP20 키를 표본으로 남긴다. 다른 PC에서도 첫 번째 명령이 성공하면 토큰 인증 API와 WebSocket 경로가 실제로 접근 가능하다는 근거가 된다. NAS 단절→로컬 전환은 앱 로그의 활성 경로와 설정 화면 표시, NAS 복구 뒤 `central` 복귀를 함께 확인한다.

## 백업

Container Manager 프로젝트를 중지하지 않고 PostgreSQL의 `pg_dump` 백업을 정기적으로 만든다. 복구 시험이 끝난 백업만 유효한 백업으로 간주한다. 앱의 로컬 DB는 장애 중 임시 캐시와 아직 중앙에 전송되지 않은 변경을 위해 그대로 유지한다.

## 업데이트

업데이트 전 DB 백업을 만든 뒤 이미지를 다시 빌드한다. 새 서버의 `/health`와 배포 점검이 모두 성공한 다음 앱의 개인 서버 모드를 사용한다. 실패하면 앱의 페일오버를 켜거나 데이터 연결 방식을 로컬 직접 연결로 돌릴 수 있다.

### 서버 이미지를 반드시 다시 빌드하는 경우

다음 파일 중 하나라도 바뀌면 컨테이너의 단순 `중지 → 시작`이나 `다시 시작`만으로는 반영되지 않는다. Container Manager 프로젝트에서 **서버 빌드가 포함된 재빌드**를 실행해야 한다.

- `src/kiwoom_monitor/central_server/` 및 서버가 가져다 쓰는 `src/kiwoom_monitor/` 소스
- `pyproject.toml`, `Dockerfile`, `.dockerignore`
- `deploy/synology/docker-compose.yml`
- 서버 Python 패키지나 실행 명령

### 서버 코드 변경 시 빌드 번호 갱신 — 필수

NAS 서버 동작이 바뀌는 코드 수정은 재빌드 안내만 남기고 끝내면 안 된다. **코드 변경과 같은 작업에서 새 빌드 번호를 만들고 아래 세 파일을 함께 수정해야 한다.**

1. `src/kiwoom_monitor/central_server/app.py`의 `SERVER_BUILD`
2. `deploy/synology/docker-compose.yml`의 `server.image`
3. `deploy/synology/server.Dockerfile`에서 최신 소스를 검사하는 빌드 번호 문자열

세 값은 `2026.09.10-schema-ledger-v1`처럼 완전히 같아야 한다. 형식은 `YYYY.MM.DD-변경명-vN`을 사용한다. 서버 코드가 바뀌었는데 기존 이미지 태그를 그대로 재사용하지 않는다. 그래야 Container Manager의 이미지 목록과 `/health` 응답만 보고도 새 코드가 실제로 배포됐는지 구분할 수 있다.

AI 또는 개발자는 NAS 서버 코드 변경을 완료했다고 보고하기 전에 다음 체크를 모두 통과해야 한다.

- [ ] 새 `SERVER_BUILD`를 정했다.
- [ ] Compose 이미지 태그를 같은 값으로 바꿨다.
- [ ] Dockerfile 검증 문자열을 같은 값으로 바꿨다.
- [ ] 관련 서버 회귀 테스트를 통과했다.
- [ ] NAS에 저장소 전체를 동기화했다.
- [ ] Container Manager에서 **빌드**를 실행했다.
- [ ] `/health`의 `server_build`가 새 값과 일치한다.

마지막 두 항목을 사용자가 직접 수행하기로 한 경우에는 “코드 준비 완료”로만 보고하고, 실제 NAS 반영 완료라고 표현하지 않는다.

권장 순서는 다음과 같다.

1. 위 세 파일의 빌드 번호가 모두 같은지 확인한다.
2. NAS의 프로젝트 폴더에 저장소 전체를 동기화한다. 일부 파일만 복사하지 않는다.
3. `.env`, `postgres-data`, `server-data`는 덮어쓰거나 삭제하지 않는다.
4. Container Manager의 `kiwoom-monitor` 프로젝트에서 **빌드**를 실행해 서버 이미지를 다시 만든다.
5. 프로젝트를 **시작**하고 `database`와 `server`가 모두 정상인지 확인한다.
6. `http://NAS주소:KIWOOM_MONITOR_PORT/health`의 `server_build`가 이번 변경에서 정한 값과 정확히 같은지 확인한다.
7. 인증이 필요한 배포 점검도 실행해 DB와 WebSocket 연결을 확인한다.

이미지 삭제는 정상적인 소스 변경 때 필수 절차가 아니다. 빌드 캐시나 동일 태그 때문에 새 소스가 들어가지 않은 것이 확인된 경우에만 서버 이미지를 정리한 뒤 다시 빌드한다. PostgreSQL 데이터 폴더는 이미지와 무관하므로 삭제하지 않는다.

재빌드 후에도 예전 코드 오류가 반복되면 먼저 NAS의 `src/kiwoom_monitor` 전체가 개발 PC와 같은지 확인한다. `deploy/synology`만 복사하거나 변경 파일만 골라 복사하면 새 코드가 참조하는 모듈이 이미지에서 누락될 수 있다.

## 개발 도구에서 X:가 보이지 않을 때

이 PC의 사용자 세션에는 NAS 공유가 `X:`로 연결되어 있으며 저장소 위치는 `X:\kiwoom-monitor`다. Codex의 기본 명령은 별도 제한 계정에서 실행되므로, 사용자에게는 X:가 정상 연결되어 있어도 기본 명령의 `Get-PSDrive`, `net use`, `Test-Path X:\`에서는 나타나지 않을 수 있다.

이 결과는 NAS 연결 해제나 공유 권한 오류를 뜻하지 않는다. 다음 순서로 판정한다.

1. 기본 명령에서 X:가 안 보이면 연결 실패라고 보고하지 않는다.
2. 승인된 제한 밖 읽기 전용 명령으로 `X:\kiwoom-monitor`를 다시 확인한다.
3. 실행 계정이 `desktop-ts9efil\pc-1`인지 확인한다.
4. 실제 NAS의 Compose 이미지 태그, `SERVER_BUILD`, Dockerfile 검증 문자열을 작업본과 비교한다.
5. 쓰기가 필요할 때만 대상 파일과 백업 위치를 명시하여 승인된 실행으로 복사한다.

사용자가 Windows에서 X:를 이미 볼 수 있다면 별도 권한 설정이나 재연결은 필요하지 않다. 승인 창은 NAS의 공유 권한을 부여하는 창이 아니라, Codex 명령을 사용자 세션 권한으로 한 번 실행하도록 허용하는 절차다.

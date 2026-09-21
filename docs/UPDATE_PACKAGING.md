# Windows 빌드·설치·부분 업데이트 배포

[현재 구현·검증 상태](CURRENT_STATUS.md) · [남은 확인 사항](OPEN_ITEMS.md)

최초 설치는 전체 설치 파일(`KiwoomMonitor-Setup-<버전>.exe`)로 한다. 이후 버전은
이전 배포 폴더와 새 배포 폴더를 비교하여 바뀐 파일만 ZIP으로 배포한다.

## 앱 빌드와 업데이트 도우미

릴리즈 버전은 `pyproject.toml`, `src/kiwoom_monitor/__init__.py`,
`src/kiwoom_monitor/presentation/app_metadata.py`의 `APP_VERSION`,
`installer/KiwoomMonitor.iss`의 버전과 출력 파일명에 동일하게 반영한다.
관련 테스트와 `compileall`을 통과한 작업본에서 공식 배포본을 만든다.

검증된 `resources/UpdateHelper.exe`가 있으면 재사용한다. 도우미가 없거나
`src/kiwoom_monitor/update_helper.py`, Python, PyInstaller, 도우미 의존성 또는
서명 설정이 바뀐 경우에만 아래 명령으로 다시 만든다. 이 도우미는 사용자 앱에서
PowerShell 대신 앱 종료 대기·파일 교체·재실행을 수행한다.

```powershell
# 업데이트 도우미 재빌드가 필요한 경우에만 실행
.\.venv\Scripts\pyinstaller.exe --clean --noconfirm --onefile --windowed `
  --name UpdateHelper --distpath resources --workpath build\UpdateHelper `
  --specpath build\UpdateHelper src\kiwoom_monitor\update_helper.py
```

공식 릴리즈의 앱 본체는 매번 `--clean`으로 폴더형 배포본을 만들고,
새 배포본으로 Inno Setup 설치 파일을 생성한다.

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm KiwoomMonitor.spec
& 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' installer\KiwoomMonitor.iss
```

생성물은 `dist/KiwoomMonitor/`와
`dist/installer/KiwoomMonitor-Setup-<버전>.exe`다.
도우미를 재사용했다면 새 배포 폴더의
`_internal/update_helper/UpdateHelper.exe` 해시가 직전 검증본과 같은지 확인한다.

| 자산 | 처리 |
| --- | --- |
| UpdateHelper | 위 재빌드 조건이 없으면 검증본 재사용 |
| OCR 모델·기본 알림음·아이콘 | 해당 자산이 바뀔 때 갱신 |
| THIRD_PARTY_LICENSES.txt·licenses/ | 포함 패키지·버전·모델이 바뀔 때 고지 갱신 |
| 직전 버전 전체 배포 폴더 | 변경하지 않고 보관하여 부분 업데이트 비교 기준으로 사용 |
| 앱 배포 폴더·설치 파일·업데이트 ZIP·SHA-256 | 공식 릴리즈마다 새로 생성 |

재사용은 자산을 새로 생성하거나 내려받지 않는다는 뜻이다. 필요한 자산은 새 앱
배포 폴더와 설치 파일에도 포함한다. 개발 확인용 캐시 빌드와 공식 릴리즈의 clean
빌드를 구분한다.

## 부분 업데이트 만들기

아래는 `1.1.1 → 1.1.2`의 **과거 버전을 사용한 명령 형식 예시**다.
실제 배포에서는 보관된 이전 배포 폴더와 새 버전 번호로 바꾼다.

```powershell
.\.venv\Scripts\python.exe scripts\create_update_package.py `
  --previous release\1.1.1 `
  --current dist\KiwoomMonitor `
  --version 1.1.2 `
  --output release
```

생성되는 `KiwoomMonitor-Update-1.1.2.zip`에는 변경 파일과 `update_manifest.json`이
포함된다. OCR 모델처럼 변경되지 않은 큰 파일은 ZIP에 들어가지 않는다. 함께 생성되는
`KiwoomMonitor-Update-1.1.2.zip.sha256`도 반드시 같은 GitHub Release 자산으로 올린다.
현재 앱은 ZIP 다운로드 후 SHA-256 값을 비교하며, 일치하지 않거나 검증 파일이 없으면 자동
업데이트를 적용하지 않는다.

GitHub Release에는 전체 설치 파일, 부분 업데이트 ZIP, SHA-256 검증 파일을 함께 올린다.
앱은 검증 파일이 있는 부분 업데이트 ZIP만 자동으로 적용하며, 없을 때는 릴리즈 페이지를 연다.

`Program Files`에 설치한 경우 파일 교체에는 Windows 관리자 권한 확인이 한 번 필요하다.

## 배포 전 확인

- 설치 파일과 ZIP에 `api.env`, `google_drive_client.json`, `google_drive_token.dat`,
  사용자 SQLite DB, 계좌 binding, 로그 또는 서버 비밀 저장소가 없는지 확인한다.
- 제3자 고지 묶음 `THIRD_PARTY_LICENSES.txt`와 `licenses/`가 설치본에 포함되는지 확인한다.
  의존성 변경 시 `scripts/generate_third_party_notices.ps1`로 고지를 갱신한다.
- ZIP의 `zipfile.testzip()` 결과와 생성된 SHA-256을 확인하고 전체 설치 및 이전 버전에서의
  업데이트를 확인한다.
- 자동 업데이트는 설치된 앱보다 큰 버전의 릴리즈를 대상으로 한다. 같은 버전 자산을
  교체한 경우 이미 같은 버전인 앱에서는 자동 감지하지 않으므로 설치 파일을 수동 실행한다.
- 업데이트 실패 지점은 진행 창과
  `%LocalAppData%\KiwoomMonitor\updates\apply_update.log`에서 확인한다.
- `release/`, `dist/`, `build/`는 Git에 올리지 않고 배포 대상 결과물만 GitHub Release 자산으로 올린다.

설치 위치와 사용자 데이터 위치를 구분한다. 설치본의 사용자 데이터는
`%LocalAppData%\KiwoomMonitor\data`이며 앱 파일 교체 과정에서 삭제하지 않는다.
새 릴리즈의 전체 배포 폴더는 다음 부분 업데이트의 비교 기준으로 보관한다.

## NAS 서버 변경은 별도 배포

Windows 설치본을 만들었다고 NAS 서버가 갱신되는 것은 아니다. NAS가 사용하는 소스·
의존성·Dockerfile·Compose가 바뀌면 `SERVER_BUILD`, Compose 이미지 태그,
Dockerfile 검증 문자열을 같은 새 값으로 올리고 이미지를 다시 빌드한다.
`.env`, `postgres-data`, `server-data`, `server-secrets`는 코드 동기화 중 보존한다.
실행 서버의 `/health.server_build`가 새 값과 일치하고 운영 점검이 끝나야 NAS 반영
완료로 보고한다. 아직 배포하지 않았다면 코드·배포본 준비 완료와 실제 반영을 구분한다.
자세한 절차는 [시놀로지 서버 설치·업데이트](../deploy/synology/README.md)를 따른다.

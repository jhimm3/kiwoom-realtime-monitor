# 부분 업데이트 배포

최초 설치는 전체 설치 파일(`KiwoomMonitor-Setup-<버전>.exe`)로 한다. 이후 버전은
이전 배포 폴더와 새 배포 폴더를 비교하여 바뀐 파일만 ZIP으로 배포한다.

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
앱은 ZIP 다운로드 후 SHA-256 값을 비교하며, 일치하지 않거나 검증 파일이 없으면 자동
업데이트를 적용하지 않는다.

GitHub Release에는 전체 설치 파일, 부분 업데이트 ZIP, SHA-256 검증 파일을 함께 올린다.
앱은 검증 파일이 있는 부분 업데이트 ZIP만 자동으로 적용하며, 없을 때는 릴리즈 페이지를 연다.

`Program Files`에 설치한 경우 파일 교체에는 Windows 관리자 권한 확인이 한 번 필요하다.

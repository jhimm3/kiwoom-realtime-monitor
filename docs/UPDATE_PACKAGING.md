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
포함된다. OCR 모델처럼 변경되지 않은 큰 파일은 ZIP에 들어가지 않는다.

GitHub Release에는 전체 설치 파일과 부분 업데이트 ZIP을 함께 올린다. 앱은 부분
업데이트 ZIP이 있을 때 그것을 우선 내려받고, 없을 때만 전체 설치 파일 페이지를 연다.

`Program Files`에 설치한 경우 파일 교체에는 Windows 관리자 권한 확인이 한 번 필요하다.

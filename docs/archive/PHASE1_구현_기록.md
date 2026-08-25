# Phase 1 구현 기록

범위: API 미연결 Windows GUI 셸, SQLite 설정 저장, 로그 기반, 빈 메인 테이블.

## 구현됨

- PySide6 메인 창과 기본 테이블
- SQLite 최초 마이그레이션: `settings`, `column_settings`
- 갱신 주기(10·20·30·60초) 저장 대화상자
- 사용자 로컬 앱 데이터 폴더의 SQLite·로그 저장
- SQLite 설정 저장 단위 테스트

## 의도적으로 미구현

- 키움 REST API/토큰/WebSocket 연결
- 실시간 순위·시세·거래강도 계산
- Excel 테마 관리
- 열 순서·표시/숨김·크기 저장 UI
- 배포용 EXE

## 실행 방법

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m kiwoom_monitor
```

실행 데이터는 `%LOCALAPPDATA%\KiwoomRealtimeMonitor`에 저장되며 GitHub에 올라가지 않는다.

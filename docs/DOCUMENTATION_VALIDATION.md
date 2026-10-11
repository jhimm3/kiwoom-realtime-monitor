# 2026-10-11 문서 정리·통합 검증

대상: 제품 2.1.0 · 로컬 `main` / HEAD `89fd6fda19f1f5bc9c7a62695d767f783a81fa79`. 로컬 `origin/main`도 같은 SHA이며 원격 fetch는 하지 않았다. 기존 미커밋 제품 작업본을 보존했다.

## 정리 결과

- 정리 전 **269개** tracked Markdown·docs JSON/HTML/텍스트 참조를 파일별 분류했다. 기존 아카이브와 제3자 고지를 포함하며 미분류는 없다.
- 완료 계획·이전 테스트 감사·8월 API 검증 및 부속 증거 **16개**를 아카이브로 옮겼다. 진단 API·스위치 설계 **2개**는 NAS 진단 운영 문서에 통합하고 원래 설계를 아카이브로 옮겼다. 총 **18개**다.
- CURRENT_STATUS, OPEN_ITEMS, MODULE_MAP, 기능 보존 검증 계획 및 진단 운영의 누적 이력 **5개**를 분리했다. OPEN_ITEMS의 **37개 D/O/R ID**와 완료 조건·사용자 보류를 유지했다.
- 진단 운영·API·하위 제어를 한 문서에서 찾게 하고, 기존 인증·TTL/CAS·상태/ACK·보호 수신/주문/저장·보고서·동시성/복구 계약을 유지했다. durable 인계 등 미완료 확장은 구현 완료로 표시하지 않는다.
- 문서 안내와 아키텍처의 오래된 작업 기준·미구현 표현을 정정했다. 테마 결정 원장·과거뉴스 reader/API/조회 화면의 구현된 경로를 반영했다. 기록일의 수량·배포·실측과 현행 계약을 구분했다.
- API/DB/시간·출처/연구/주문 계약과 진행 중인 원인 Replay·수집/과거뉴스 계약, DB baseline/after/approval/catalog, 공개 페이지·공식 자료·법적 고지는 보존했다.
- 현행 문서와 과거 아카이브의 외국어 설명·제목·비교표를 한글화했다. 코드·필드명·SQL·공식 자료·라이선스는 원래 표기를 유지했다. 영어 공개 페이지 2개는 한국어 정본으로 안내하도록 통합하고 기존 주소를 유지했다.

## 보존·검증

| 검사 | 결과 |
|---|---|
| 변경·이동 전 원문 | 54개 파일을 원래 경로·바이트로 ZIP 보존. ZIP CRC와 항목별 SHA-256 일치 |
| 기존 제품 소스·테스트·설정 등 | 시작 당시 비문서 파일 1,193개 중 1,189개 SHA-256 일치. 작업 중 테스트 파일 4개의 별도 변경을 감지했으며 문서 정리 도구가 쓰지 않은 파일로 구분해 보존 |
| 이동한 JSON 증거 | 원문과 바이트 일치, JSON parsing 통과 |
| 현행·아카이브 Markdown | 251개 문서의 상대 링크 3,067개·앵커·코드펜스 짝 검사. 확인된 끊김 0개 |
| 모듈 지도 코드 경로 | 실제 파일 존재 확인. 과거 실행 수치는 아카이브에 보존 |
| 테스트 등록 검사 | `scripts/run_regression.py --check-test-catalog`: unit 427 / integration 25, exit 0. 제품 테스트 실행 수가 아님 |
| diff 형식 | `git diff --check` 통과 |

원문과 이동·통합 이유는 [전체 목록](archive/2026-10-11/document-inventory.json), [읽기용 아카이브](archive/2026-10-11/README.md), [원문 ZIP](archive/2026-10-11/original-documents.zip)에 있다. 읽기용 보존본은 안내·상대 링크를 갱신했으므로 바이트 일치는 ZIP 항목을 기준으로 한다. 기존 실패 기록을 후속 통과로 덮지 않았다.

별도 변경을 감지한 파일은 `tests/unit/test_central_server_app.py`, `test_mock_automation_specification.py`, `test_run_regression.py`, `test_storage_boundary_concurrency.py`다. 이 문서 작업에서 해당 파일을 수정하거나 되돌리지 않았다.

## 검증 범위의 한계

이번에 실행한 검증은 문서·보존·등록 검사다. 앱 전체 회귀·수동 UI·실제 NAS/PostgreSQL·수집 진행률·외부 URL을 새로 검증하지 않았다. CURRENT_STATUS의 제품 테스트 수치와 운영 결과는 기존 기록의 범위를 유지하며 이번 실행 결과로 보고하지 않는다. 제품 코드·사용자 DB·NAS·수집기·월요일 녹화 예약·Git 커밋/push는 변경하지 않았다.

과거 기록이 참조하는 `data/research/historical-development-inputs/monthly-2024-09-to-2026-01-v1/manifest.json` 한 경로는 파일 접근 권한 제한으로 존재 여부를 재검증하지 못했다. 기존 해결 경로를 보존했고 확인된 끊긴 링크로 취급하지 않는다. 제3자 고지와 변경하지 않은 문서의 기존 공백은 보존했다.

[현재 문서 안내](README.md) · [현재 상태](CURRENT_STATUS.md) · [남은 작업](OPEN_ITEMS.md). 9월 22일 검증 수치는 [당시 기록](archive/2026-10-11/reports/DOCUMENTATION_VALIDATION_20260922.md)에 있다.

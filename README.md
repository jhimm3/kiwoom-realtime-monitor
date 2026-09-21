# 키움 실시간 종목순위·테마·거래강도 모니터

키움 REST·WebSocket `0B` 기반의 Windows 상위 20종목 모니터입니다. 실시간 순위·현재가·기간 거래대금·거래강도와 최고가 근접 알림, NXT 표시, 테마·뉴스·자동 매매일지, TOP20 지수를 제공합니다. 저장된 자료로 전략 연구와 Shadow 후보 검증을 반복하고, 검증된 후보를 계좌별 안전 gate를 거쳐 모의운용할 수 있습니다. 선택형 개인 NAS 서버는 앱이 꺼져도 시장·뉴스·계좌 자료를 수집하고 여러 PC에 같은 설정과 분석 자료를 제공하며, NAS를 쓰지 않는 로컬 모드도 그대로 지원합니다.

## 현재 버전

- **2.1.0**
- [GitHub 릴리즈](https://github.com/jhimm3/kiwoom-realtime-monitor/releases/tag/v2.1.0)
- [소개 페이지](docs/index.html) · [개인정보처리방침](docs/privacy.html)

## 문서와 개발 방향

- **[현재 문서 안내](docs/README.md)** — 기능·기획·계약·운영 문서를 찾는 시작점
- [현재 앱 상태](docs/CURRENT_STATUS.md) · [개발 로드맵](FUTURE_DEVELOPMENT_ROADMAP.md)
- [다음 작업: 대신 과거 봉·네이버 증권 뉴스 확보](docs/HISTORICAL_BACKFILL_PLAN.md)
- [남은 작업과 보류](docs/OPEN_ITEMS.md) · [현재 아키텍처](ARCHITECTURE_CURRENT.md) · [모듈 지도](MODULE_MAP.md)
- [작업 지침](AGENTS.md) · [개발 불변 규칙](DEVELOPMENT_GUARDRAILS.md)

## 변경 내역

- [현재 2.1.0 릴리즈 노트](docs/RELEASE_NOTES_v2.1.0.md)
- [누적 변경 기록](CHANGELOG.md)
- [이전 릴리즈·완료 보고서·구형 명세](docs/archive/2026-09-22/README.md)

## 저작권 및 제3자 고지

앱 자체는 `Copyright 2026 크니. All rights reserved.`로 보호됩니다. 포함 라이브러리·OCR 모델·알림 음성·설치 도구에 대한 고지는 [THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt)를 참조하세요. 설치본에는 패키지별 원문 고지 파일을 담은 `licenses/` 폴더도 함께 포함됩니다.

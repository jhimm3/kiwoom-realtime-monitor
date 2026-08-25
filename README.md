# 키움 실시간 종목순위·테마·거래강도 모니터

키움 REST·WebSocket `0B` 기반의 Windows 상위 20종목 모니터입니다. 실시간 순위·현재가·기간 거래대금·거래강도와 최고가 근접 알림, NXT 표시, 테마 관리, Google Drive 선택 동기화, 설치·부분 업데이트를 제공합니다.

## 현재 버전

- **1.1.10**
- [GitHub 릴리즈](https://github.com/jhimm3/kiwoom-realtime-monitor/releases/tag/v1.1.10)
- [소개 페이지](docs/index.html) · [개인정보처리방침](docs/privacy.html)

## 변경 내역

- [1.1.10](docs/RELEASE_NOTES_v1.1.10.md) — KRX+NXT 합산 최고가·직전 거래대금·분봉 보완, 최근 730개 분봉 연속조회
- [1.1.9](docs/RELEASE_NOTES_v1.1.9.md) — PowerShell 없이 전용 업데이트 도우미 EXE로 부분 업데이트 적용
- [1.1.8](docs/RELEASE_NOTES_v1.1.8.md) — 부분 자동 업데이트 SHA-256 검증, 설정 백업 첨부파일 복원 안전성 강화, 설치본 제3자 고지 포함
- [1.1.7](docs/RELEASE_NOTES_v1.1.7.md) — `ka10081` 일봉 직접 거래대금·30일 캐시, 순위 변동 표시 시간 기본값 0초
- [1.1.6](docs/RELEASE_NOTES_v1.1.6.md) — 자동 업데이트 도우미 안정화
- [1.1.5](docs/RELEASE_NOTES_v1.1.5.md) · [1.1.4](docs/RELEASE_NOTES_v1.1.4.md) — 업데이트·아이콘 개선
- [1.1.3](docs/RELEASE_NOTES_v1.1.3.md) · [1.1.2](docs/RELEASE_NOTES_v1.1.2.md) · [1.1.1](docs/RELEASE_NOTES_v1.1.1.md) · [1.1.0](docs/RELEASE_NOTES_v1.1.0.md)

## 개발 문서

- [AI 인수인계 개발실행서 v3.3](docs/AI_인수인계_개발실행서_v3.3.md) — 현재 구조·설정·API·빌드·배포의 기준 문서
- [기술 명세서](docs/키움_실시간_모니터_기술명세서_v2.0.md)
- [OCR 모델 직접 설치](docs/OCR_모델_직접_설치.md)

## 저작권 및 제3자 고지

앱 자체는 `Copyright 2026 크니. All rights reserved.`로 보호됩니다. 포함 라이브러리·OCR 모델·알림 음성·설치 도구에 대한 고지는 [THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt)를 참조하세요. 설치본에는 패키지별 원문 고지 파일을 담은 `licenses/` 폴더도 함께 포함됩니다.

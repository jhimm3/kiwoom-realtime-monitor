# 2026-10-13 전체 설계 재구성 전 기록

사용자가 전체 설계와 재조립/신규 개발 비교를 요청한 시점의 로드맵을 원문 보존했다.

- [변경 전 로드맵 전문](roadmap-before-system-redesign.md): 당시 루트 `FUTURE_DEVELOPMENT_ROADMAP.md`의 바이트를 그대로 복사했다. 내부 상대 링크는 당시 저장소 루트 기준이며, 과거 진행 수량·미커밋 표현·“다음 작업”은 해당 기록 시점의 상태다.
- [현재 개발 순서](../../../FUTURE_DEVELOPMENT_ROADMAP.md)
- [전체 설계와 비교 판단](../../AI_TRADING_SYSTEM_REDESIGN.md)
- [사용자가 추가한 작은 모듈형 모놀리스 참고안 원문](user-proposed-modular-monolith.txt): 첨부 텍스트를 바이트 그대로 보존했다. 새 프로젝트 개발 결정이 아니라 설계 비교 참고안이다.
- [현재 실행·검증 상태](../../CURRENT_STATUS.md)
- [R2 이전 활성 QueryStore 소비 계약 기준선](query-store-consumers-before-r2.json): 불변 승인 근거 API 1개/소비 지점 2개를 추가하기 전 JSON 바이트를 보존했다. 검토한 새 계약은 현행 감사 입력에 반영하며 이 사본은 과거 비교 근거다.
- [R3 native 입력 이전 QueryStore 기준선](query-store-consumers-before-r3-native-inputs.json): bounded 1초봉 read 1개/입력 bridge 소비 3지점 추가 전 기준선. 기존 계약/forwarding은 보존하고 현행 감사에 새 delta만 반영했다.

이 아카이브는 새 실행 권한이나 현재 개발 순서를 뜻하지 않는다. 소스·검사·운영 DB는 이동/삭제하지 않았다.

- [공통 Shadow 페이지 전달 이전 QueryStore 기준선](query-store-consumers-before-shared-shadow-page.json): 기존 native safe-prefix page read 소비 1지점을 추가하기 전 JSON 원문. API/signature·기존 소비/forwarding은 유지하고 동일 store owner의 새 연결만 검토했다.

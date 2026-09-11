# NAS 배포 및 잔여 검증 상태

- 상태: **`2026.09.12-shared-backup-contract-v1` 누적 배포 완료, 잔여 실환경 검증 대기**
- 작업본의 현재 서버 빌드: `2026.09.12-shared-backup-contract-v1`
- 누적 배포 내용: 중앙 DB v2 메타데이터 표와 v3 시장상태 특수시각 보정, 순위·TOP20 지수·시장 상태·조회/실시간 분봉·일봉의 값/메타데이터 동시 저장, coverage 진단 API, 빈/오래된 차트 캐시 보완, 실제 0B 거래대금 보호, 공식 KRX 카탈로그 시장 분류, TOP20 디스크 outbox와 PostgreSQL 실통합 검사기. 일반 기존 행은 추정 보정하지 않고 확인된 `T88:88` 행만 `saved_at` 서울 시각으로 이전
- NAS 파일 동기화: 완료. 핵심 8개 파일 SHA-256 일치 및 `.env`, `postgres-data`, `server-data` 보존 확인
- NAS 실행본 반영 여부: `2026.09.12-shared-backup-contract-v1` 이미지를 새로 빌드해 실행했으며 `/health.server_build`, 인증 콘텐츠 API, WebSocket ready/pong을 확인함. 중앙 DB v3 적용 뒤 최근 마켓스테이트 100건의 `T88:88`은 0건
- PostgreSQL 실제 통합: 중앙 스키마 v3, query cache, realtime, minute/daily bars, observation metadata, dataset snapshots, documents, external bars 왕복과 rollback 모두 통과
- 실제 장애전환: 동일 조회 클라이언트에서 NAS 정상 조회 후 서버 컨테이너를 중지해 이 PC의 키움 API로 `ka00198` 20종목을 수신했고, 서버 재시작 뒤 NAS 조회로 자동 복귀해 다시 20종목을 수신함
- 보존 확인: 중앙 DB 약 283MB와 `deploy/synology/.env`, `postgres-data`, `server-data` 유지
- 남은 검증: 장시간 수집 연속성과 다른 PC 접속. PostgreSQL 통합 및 NAS 단절→로컬 장애전환→NAS 복구는 완료

## 다음 실환경 검증

1. 장시간 운용 뒤 메모리·디스크·순위/TOP20 수집 시각의 연속성을 비교한다.
2. 다른 PC에서 같은 NAS API 인증 조회와 WebSocket 연결을 확인한다.

후속 기능에서 NAS 서버 코드가 더 바뀌면 이 문서의 빌드 값과 배포 범위를 계속 갱신한다. 현재 빌드의 배포 및 필수 통합 검증은 완료됐다.

# R5d2a — 해외 지연 시세 운영 변경

2026-09-15. 로컬 구현 완료, NAS 동기화/배포 전.
누적 build `2026.09.15-runtime-credentials-r5-external-operations-v1`.

## 구현 범위

기존 NAS 운영 설정에서 해외 지연 시세 수집 ON/OFF, 수집 주기, 자동 월물 전환, 확인 횟수를
컨테이너 재시작 없이 변경한다. 기존 operations 부분 PUT/revision/applied_revision/CAS를 사용한다.
ENV는 신규 필드 최초 초기값이고 저장 OFF는 다음 기동의 ENV ON보다 우선한다.
초기 symbol이 있으면 OFF 설치에도 같은 수집기를 준비하며 background는 ON에서만 시작한다.
상품 symbol/활성 월물 수동 변경은 이번 입력에 포함하지 않고 기존 자동 roll 정책을 유지한다.

## 확인된 경계와 최소 보완

기존 close는 정기 loop를 취소하고 끝냈다. collect_once의 to_thread fetch는 취소 뒤에도
계속될 수 있고, loop 취소는 봉/roll/status 저장 완료를 뜻하지 않았다.
기존 수집기에 단일 owned cycle을 추가해 caller/loop 취소 뒤에도 실제 수집과 최종 저장을 완료한다.
같은 cycle은 공유하되 일봉이 필요한 요청은 intraday-only 완료를 일봉 완료로 재사용하지 않는다.

설정 변경도 같은 수집기의 owned task/잠금에서 직렬 처리한다. 기존 cycle을 drain한 뒤
새 주기/전환 정책을 적용하고 ON은 같은 인스턴스를 재개한다. HTTP waiter 취소 뒤에도 처리하며
shutdown은 pending 변경과 실제 cycle을 기다린다. 캐시·봉·roll 이력·이전 일봉 기준을 초기화하지 않는다.
새 manager/service/wrapper/SQL 테이블을 추가하지 않았다. 호출 경로는 기존 API → 기존 수집기다.

저장 전 실패는 기존 실행/버전을 유지한다. 저장 후 적용 실패는 복구 필요로 공개하고,
같은 revision 명시 재저장으로 적용을 확인한다. PC 화면도 저장과 실행 적용을 구분한다.
구 NAS 응답에 새 필드가 없으면 controls를 비활성화하며 default 추가 필드를 보내지 않는다.
기존 AI/뉴스/Shadow 설정과 시장자료·순위 우선순위 경로는 유지한다.

## 검증

- 신규 10개: 수집 waiter 취소/최종 저장 drain, 설정 waiter 취소/실제 완료,
  shutdown/pending 변경, OFF→ON cache/roll/봉 보존, 일봉 미수집 cycle 재사용 금지,
  실제 로컬 API OFF→ON/저장 OFF 우선, 인증/CAS/strict 입력/빈 symbol,
  저장 실패/실행 적용 실패와 명시 복구, 구 NAS 입력 호환/복구 필요 UI.
- 변경 직후 외부시세/서버/PC 운영 설정 회귀 66개 통과. 최종 관련 회귀 125개 모두 통과,
  종료 코드 0 (`tmp/r5d2-regression.log`). 외부시세/서버/운영 client/설정 허브/공급자 관리/DB를 포함한다.
- Python 4개 구문/공백 정상, tracked diff 공백 검사 정상, 누적 build 표기 3곳 일치.
  자동 조회를 막은 가짜 상태의 NAS 운영 설정 화면을 offscreen으로 렌더링해 배치를 확인했다.
- 가짜 선물 시세·임시 SQLite·offscreen Qt 검증만 수행했다.
  실제 NAS/PostgreSQL/외부 공급자/사용자 DB는 변경하지 않았다.

## 다음

R5d2b 조건검색/기타 운영값 확대가 남았다. 조건식 교체는 기존 코호트 수명과 WebSocket
재등록/이전 응답을 검증해 연결한다. R6 실전 인증 owner·R7 누적 배포/실환경도 남았다.
중간 NAS 동기화·재빌드 없음. 모델 에스컬레이션 없음.

# CR2c1 — PC 연구 작업자 영속 복구

2026-09-16. CR2b2 누적 소스를 유지한 후속 구현. 이번 범위는 작업자 수명/복구이며 새 자료 자동 등록이나 보관 정책은 포함하지 않는다.

## 변경 이유와 책임

기존 GUI는 작업자 종료를 화면에 표시했지만 실패 이력·재시도 시각은 저장하지 않았다.
기존 연구 Repository/worker/dialog에 수명 원장을 연결했다. 별도 Manager/범용 스케줄러/서버 API는 추가하지 않았다.
기능 이해 경로는 기존 dialog → process/repository이며 queue에는 순수 타이머 지연 계산만 추가했다.

연구 DB v12 `campaign_worker_recovery`는 두 테이블을 추가한다.
`research_campaign_workers`는 현재 소유권/상태/연속 실패/재시도 시각을, `research_campaign_worker_attempts`는 세대별 실행 이력을 저장한다.
기존 campaign/job/budget 행과 완료 trial/report는 바꾸지 않는다. DB downgrade는 지원하지 않는다.

## 입출력 계약

| 동작 | 조건과 결과 |
| --- | --- |
| claim | RUNNING 의도 + 재시도 시각 경과 + 활성 lease 없음 → 단조 증가 generation/owner/30초 lease와 attempt를 한 트랜잭션으로 생성 |
| busy | 활성 owner가 있으면 새 프로세스를 만들지 않음. GUI는 lease 이후 다시 확인 |
| heartbeat | worker가 약 1초마다 자신을 갱신. 일시정지/owner 변경/만료면 False로 실행 중단 |
| search job 실행 시작/결과 commit | 기존 search/campaign fence에 worker owner/generation/live lease를 함께 확인. 소유권을 잃은 결과는 확정하지 않음 |
| 실패 | 시작 파일/디렉터리/프로세스 오류, 비정상 종료(예상하지 못한 exit 0 포함), 미확인 lease 만료 → 해당 attempt에 한 번만 FAILED 기록 |
| 재시도 | 시작 때 고정한 campaign policy revision의 backoff/상한 사용. worker/job 실패 횟수는 별도 |
| 기본 정책 | 첫 실패 30초, 두 번째 60초 대기. 연속 3회 실패 시 NEEDS_ATTENTION으로 자동 재시도 중단 |
| 생존 회복 | 시작 후 60초 이상 정상 lease 갱신 시 연속 실패 횟수만 0으로 초기화. 과거 attempt는 보존 |
| 예상 종료 | 취소 marker/PAUSED/STOPPED/창 숨김/앱 종료 → EXPECTED_EXIT, 실패 증가 없음. 오류가 함께 발생해도 종료 의도를 확인 |
| 앱 복원 | 실패/재시도 시각/격리 상태를 유지. 기존 선택 index는 계속 DB/결과 경로 위치만 저장 |
| 명시 재시도 | 사용자가 시작/재개를 누르면 FAILED/NEEDS_ATTENTION을 초기화하고 재시도. 활성 owner는 강제 대체하지 않음 |

GUI는 single-shot 타이머로 저장된 lease/retry 시각을 기다린다. 최단 1초, 목표 시각에 100ms 여유를 둔다.
창 숨김/일시정지/중지/앱 종료 시 복구 타이머를 멈춘다. RUNNING 의도와 현재 프로세스 생존은 같은 값이 아니다.
재시도 대기 중 유한 연구를 수동 실행하면 기존 단일 프로세스 경계를 유지한다.
유한 실행 완료/결과 읽기 실패/시작 실패 후 캠페인 복구를 다시 예약한다.
유한 연구의 연속 재개를 사용 중이면 해당 실행을 먼저 이어가고, 연속 재개 해제/완료 후 캠페인 복구를 예약한다.
worker CLI는 직접 claim하거나 GUI가 예약한 `--worker-token`/`--worker-generation` 쌍을 받는다.
부모/자식이 같은 종료를 보고해도 한 번만 처리하며 오래된 세대 응답은 새 worker를 변경하지 않는다.

## 검증

- 임시 연구 DB/fixture/offscreen Qt를 사용했다. 실제 사용자 DB/NAS/API/자격증명/주문은 사용하지 않았다.
- 초기 기존 campaign 회귀 72개 통과. 새 worker/UI 집중 회귀 37개 통과.
- 동시 claim, DB 재시작 후 backoff, 실패 상한/명시 재시도, 중복/오래된 종료 응답, 만료의 단회 기록을 검증했다.
- 60초 생존 회복, 정책/의도 구분, claim 쓰기 실패 rollback, v11 campaign/job/예산 전체 행 보존을 검증했다.
- 실제 시뮬레이션 후 worker 소유권을 제거해 heartbeat 전 결과 commit도 거부함을 확인했다.
- GUI 시작 오류/디렉터리 오류/중복 owner/예상하지 못한 exit 0/숨김 종료/앱 복원/수동 재시도를 검증했다.
- 유한 연구 완료·결과 읽기 실패 뒤 캠페인 복구 예약이 유지되는 전환 회귀를 추가했다.
- 최종 전체 연구 회귀 **199개 / 84.220초 / OK / 프로세스 exit 0**. `tmp/cr2c1-regression.log`에 결과를 남겼다.
- 변경 Python 9개 AST/공백 확인과 관련 `git diff --check`를 통과했다. 테스트 로그에 skip/오류/자식 출력 읽기 예외는 없었다.

## 남은 범위

CR2c2 관련 새 watermark 입력 선택/자동 등록, 디스크 cap/참조 보호/보관 원장이 남는다.
CR3 검증 분리, CR4 새 가설 생성, A5 계좌별 일지 피드백, O2-M 자동 모의운영은 이번 단계가 아니다.
실제 PC/NAS 규모의 24시간 부하와 OS 강제 종료 후 현장 복원은 후속 운영 검증이다.
이번 변경은 PC 연구 경로다. NAS 동기화/재빌드는 하지 않았다. 모델 에스컬레이션 없음.

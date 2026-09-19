# CR3a4b — 독립 개발 구간의 화면 캠페인 등록

2026-09-16. CR3a4a의 별도 등록 CLI를 기존 ResearchDialog 버튼에 연결한다.
원본·유효 실행 spec/연구 DB v17/기존 v1 등록은 유지한다. 실제 사용자 앱/DB/NAS에는 이번 작업을 실행하지 않았다.

## 목적과 최소 변경

‘캠페인에 요청 등록’ 버튼이 독립 개발 구간을 기존 AuxiliaryProcessManager로 등록한다.
새 Manager/Service/스레드/DB migration은 없다. 단일 관리자에 registration 상태와 완료 처리만 추가한다.
GUI → 기존 research_process 등록 → 기존 data_source/repository의 책임을 유지한다.
새 repository.load_campaign_job은 완료 확인에 해당 composite key 한 행만 읽는다.
GUI에서 전체 export/bundle이나 누적 전체 job 목록을 읽지 않는다.

## 입출력과 수명 계약

- 작은 JSON을 기존 parser로 검증하고 parsed 정책/절대 경로로 등록 request snapshot을 만든다.
- UUID별 snapshot/result/cancel 파일을 사용한다. 기존 finite/campaign 결과·취소 파일과 섞지 않는다.
- 새 campaign은 PAUSED로 만들고 selection을 child 실행 전에 보존한다. 등록은 trial/주문을 실행하지 않는다.
- 기존 campaign은 같은 DB/결과 폴더만 사용한다. 활성 worker/미처리 등록이 있으면 다른 등록/연구를 시작하지 않는다.
- 입력 JSON 변경/삭제/선택 변경으로 진행 중 snapshot을 바꾸지 않는다.
- 기존 poll timer는 프로세스 종료를 비동기로 확인한다. 성공은 native exit 0 + registration kind + campaign/job/experiment ID + 저장 source evidence가 맞아야 표시한다.
- 현재 예산 revision을 제외한 원본 evidence를 대조해 동일 과학적 요청의 예산 변경을 별개 실험으로 오인하지 않는다.
- 잘못된 결과/다른 ID/결과 파일 읽기 실패/native 오류는 확인 필요로 표시한다. 이미 commit된 DB job은 삭제하지 않는다.
- 취소/창 숨김은 해당 등록 회차의 cancel 파일만 보낸다. 창 숨김은 process wait/kill을 하지 않는다.
- 취소 전에 등록이 commit되면 완료 사실을 표시한다. 앱 종료는 기존 graceful stop을 사용한다.
- 완료/취소/실행 실패 뒤 해당 UUID의 소유 임시 파일만 정리한다. 원본 JSON/export/DB/campaign 선택은 보존한다.
- 등록 중 시작/재개/정지/예산/재시도/자료 설정 호출은 거부한다. 결과를 소비하기 전 child가 종료됐더라도 새 작업을 시작하지 않는다.

## 재현한 문제와 수정

첫 인접 회귀 56개에서 실패 3개를 확인했다.
하나는 JSON tuple→list 정규화를 그대로 비교한 새 테스트 오류였다. JSON 의미로 비교하도록 수정했다.
나머지 둘은 등록 예외 문구가 _schedule_worker_retry의 metadata refresh에 덮이는 같은 경로였다.
refresh를 먼저 하고 오류 문구를 나중에 표시하도록 호출 순서를 수정했다.

별도 테스트에서 registration 결과를 소비하기 전 _set_campaign_state('RUNNING')이 임시 campaign을 활성화했다.
`tmp/cr3a4b-state-before.log`에서 실패를 확인한 뒤 registration 동안 설정 callback을 거부하도록 보완했다.
기존 user intent/worker lease/자동 재개 정책 자체는 변경하지 않는다.

## 검증

새 offscreen Qt 계약 회귀 13개:

- 버튼 클릭은 paused 메타데이터와 고정 요청만 만들며 GUI의 heavy loader를 호출하지 않는다.
- 성공 후 원본 request 삭제/캠페인 복원, table 보존, 단일 job 조회, 소유 임시 파일 정리.
- 중복 클릭/child 종료 미처리/늦은 상태 callback/다른 연구 시작 차단.
- 해당 회차 취소/비동기 창 숨김/commit 뒤 취소/앱 stop과 원본 보존.
- 실행 실패/native 오류/다른 결과 ID 대조와 예외 문구 유지.
- 재등록은 별도 파일을 사용하며 같은 job 하나를 재사용한다.
- 임시 실제 child가 load를 지연하는 동안 Qt heartbeat가 계속 처리되고 native exit 0으로 paused 등록을 완료한다. trial은 생성하지 않는다.

인접 회귀: **57개 / 45.688초 / OK / native exit 0**.
로그 `tmp/cr3a4b-focus-final.log`, 종료 코드 `tmp/cr3a4b-focus-final-exit.txt`.
전체 누적 회귀: **368개 / 230.503초 / OK / native exit 0**.
로그 `tmp/cr3a4b-regression.log`, 종료 코드 `tmp/cr3a4b-regression-exit.txt`.
새 13개와 기존 연구 실행/평가/품질/보고/split/검색/queue/repository/resource/bundle/replay,
campaign worker/budget/source/NAS 준비/용량/임시 정리/복구와 offscreen Qt 회귀를 함께 검증했다.
변경 파일 10개의 AST/공백과 대상 git diff --check도 종료 코드 0이다.

## 다음과 확인하지 않은 범위

다음 CR3b1은 여러 독립 개발 구간의 검증/집계 계약이다. 구간별 표본/결측/실패/편중을 유지하고
독립 초기 현금의 손익/최대 낙폭을 공통 현금의 연속 포트폴리오처럼 해석하지 않는다.
종목 분할/최종 접근 원장/자동 최종평가/새 날짜 이동/자동 가설/계좌 주문/일지 피드백은 후속이다.
실제 NAS 전체 규모/24시간 운전/장중 순위와 동시 사용의 성능은 이번 fixture로 완료 판정하지 않는다.
비정상 강제 종료로 남은 임시 파일의 장기 정리는 후속 운영 검증 대상이다. source 원본이나 완료 작업을 소급 지우지 않는다.
NAS 재빌드는 필요 없다. 사용자 앱 재시작 후 해당 워크트리 구현이 반영된다.

모델 에스컬레이션: 없음.

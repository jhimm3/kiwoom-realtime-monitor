> **과거 기록** · 원래 경로: `reports/CR3C3_GROUPED_VALIDATION_DIALOG.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# CR3c3 — 종목 그룹×시간 순차 검증 화면

2026-09-16. 기존 순차 검증 창에 그룹 v2 실행/취소/진행·결과·같은 snapshot 이어 실행을 연결했다.
누적 PC 소스 변경이며 NAS 소스/이미지, DB v17, API, 계좌·실제 주문 경로는 변경하지 않는다.

## 목적과 수정 범위

하나의 고정 전략을 여러 종목 그룹과 시간 구간에서 검증하고 그룹별 결과를 화면에서 확인한다.
기존 DevelopmentValidationDialog와 research_process의 CLI/file 경계를 재사용한다.
화면 구현은 기존 presentation/research_dialog.py에 두고, 검증 중 재현된 Windows 파일 충돌은 기존 research_process.py의 결과 발행 함수에서 수정한다.
별도 실행기/Manager/Service/DB 계층을 만들지 않는다.
사용자 입력 → 기존 parser → 정규 snapshot → 기존 child → 기존 executor/DB → bounded 결과 파일 → 두 표 순서다.
GUI는 연구 DB와 동결 시장자료를 열지 않는다. 전체 원본의 검증·실행·집계는 기존 child에서 한다.

## 입력과 사용

전략 연구 → 여러 구간 순차 검증 → 검증 파일 선택 → 검증 실행.
기존 independent_development_validation/v1과 그룹 v2를 모두 지원한다.
[요청 계약](../snapshots/docs/RESEARCH_REQUEST_FORMAT.md)과 [CLI/실행 계약](CR3C2_SYMBOL_WINDOW_VALIDATION.md)을 따른다.
이 단계는 요청 파일 편집기나 전략 자동 생성기를 추가하지 않는다.

정규 요청을 최초 실행 성공 시 immutable DTO로 보관한다. '같은 요청 이어서 실행'은 이 snapshot을 다시 실행한다.
선택 파일의 이후 수정은 이어 실행에 적용하지 않는다. 새 요청은 '검증 실행'으로 파싱한다.
완료 cache/취소 재개/atomic claim/실패·소유자 확인/시간·메모리·CPU guard는 기존 실행기의 계약을 유지한다.
snapshot은 이 창의 수명 안에서 보관하며 앱 재시작 뒤 자동 복구나 자동 반복은 추가하지 않는다.

## 화면 출력과 검증

- 단계 표: 시간 fold 먼저/bucket 다음으로 모든 최대 200개 요청을 표시한다. 기간(KST)/역할, 실행 상태, 표본 판정, 해당 구간 손익/MDD, 이유를 포함한다.
- 그룹 번호: bucket 0은 그룹 1, bucket 1은 그룹 2다. 실제 bucket은 그룹 표 tooltip에 남긴다. 선택되지 않은 bucket은 표시하지 않는다.
- 그룹 표: 요청/실행 완료/미시작 수와 식별된 실행의 적격/양수 구간, 손익 중앙값, 최악 구간 MDD, 표본 판정을 분리한다.
- 그룹 간 손익/MDD는 합산하지 않는다. 중앙값은 0.5원 단위도 보존한다. 거래가 없는 그룹은 실행 완료와 적격 0/N/A를 함께 표시한다.
- 그룹 comparison COMPLETE는 식별된 결과가 적격이라는 뜻이다. 전체 요청 완료는 모든 단계의 COMPLETED/CACHED 여부로 판단한다.
- 기존 시간 v1 결과로 돌아오면 그룹 표를 비우고 숨긴다.

250ms polling은 최대 16 MiB의 결과 파일만 읽는다. 실행 중의 이전 정상 표를 보존하고,
요청 version/hash 정책/전체 단계 수/순서·step_key·기간/IDs/구현 hash와 그룹별 counts·IDs·비교 scope를 확인한다.
그룹별 표본 수·중앙값/최악 MDD도 확인한다. 모든 단계/그룹 표시 값을 준비한 뒤 두 표를 갱신한다.
잘못된 결과나 정상 종료 코드와 결과 status의 불일치, 종료 시 RUNNING 잔존을 성공으로 표시하지 않는다.

취소는 이 작업의 cancel 파일로 전달한다. 숨긴 창에서도 polling을 계속하고 부모 종료는 기존 child 정리를 따른다.
native exit를 아직 polling하지 않은 작업에는 중복 실행을 허용하지 않는다. 임시 파일 정리는 자신의 세 파일만 대상으로 한다.

## 테스트

새 tests/unit/test_research_symbol_validation_dialog.py의 11개 계약 테스트:
실제 엔진/두 그룹·네 단계, 거래 없는 그룹, 완료 cache와 수정 원본에 영향받지 않는 snapshot 재개,
실행 전 취소와 재개, 최대 200단계 표시, 그룹→시간 v1 호환, matrix/그룹 범위·수·통계 위조 시 두 표 보존,
부분 비교와 전체 완료의 구별, 중앙값 소수 보존, 중복 실행/숨김 수명, native 0의 RUNNING 잔존 차단,
실제 hidden child 실행 중 Qt heartbeat 유지와 native 종료를 확인한다.

기존 C2의 화면 미지원 테스트는 그룹 요청을 실제 정규 snapshot으로 위임하고 GUI DB/원본 입력 접근이 없음을 확인하도록 갱신했다.
새 계약 11개/16.301초 OK, native exit 0. 그룹/기존 화면/CLI 묶음 48개/43.600초 OK, native exit 0.
수정 후 화면/CLI/파일 발행 묶음 55개/44.925초 OK, native exit 0.
수정 후 실제 child 두 종류 각 10회, 총 20개/53.674초 OK, native exit 0.
전체 연구 핵심 회귀 494개/329.718초 OK, native exit 0. tmp/cr3c3-regression-final.log와 tmp/cr3c3-regression-final-exit.txt에 남겼다.
정적 AST/공백 검사 13개 파일과 git diff --check 통과.

## Windows 결과 파일 충돌 재현과 최소 수정

첫 묶음에서 실제 hidden child가 native 1로 종료했고 첫 전체 489개 회귀에서는 기존 시간 검증이 native 0/PARTIAL로 종료했다.
실제 프로세스 두 종류를 각각 10회 실행해 20회 중 3회 실패를 재현했다. 캡처한 단계 이유는
GUI가 결과 파일을 읽는 동안 child의 temporary.replace(target)가 실패하는 PermissionError/WinError 5였다.
RUNNING 발행 시 실패하면 기존 executor가 해당 구간을 FAILED로 저장하고, 최종 발행 실패는 프로세스 오류가 된다.
전략/자료 적격과 무관한 파일 읽기·교체 충돌임을 tmp/cr3c3-child-repeated.log에 남겼다.

기존 _write_result에서 Windows 오류 5/32/33인 PermissionError에 한해 동일 임시 파일 rename을 최대 10회 재시도한다.
간격은 25ms, 최대 대기는 225ms이며 child에서만 기다린다. JSON은 한 번 만들고 항상 원자 교체한다.
기존 정상 파일을 지우거나 직접 덮어쓰지 않는다. 계속 실패하면 원래 예외를 다시 발생시키고 임시 파일을 정리한다.
그 외 I/O 오류·다른 OS 경로는 재시도하지 않는다. 검증 엔진/주문/키움 TR 재실행은 추가하지 않는다.

test_research_result_publication.py의 5개 회귀는 실제 Windows 읽기 핸들 충돌/해제,
일시 충돌의 rename만 재시도, 지속 충돌의 상한/이전 파일 보존/임시 파일 정리, 무관한 오류의 즉시 실패를 확인한다.
기존 실제 child 테스트도 마지막 진행 결과의 단계 이유를 assertion에 포함하도록 보강했다.
수정 후 동일 실제 프로세스 반복 20회가 모두 통과했다. 실제 NAS/장시간 운영 검증으로 확장해 주장하지 않는다.

## 남은 범위

다음 CR3d는 최종 평가 접근 원장과 개발에 노출된 평가 구간의 재사용 방지 계약이다.
그룹별 coverage/편중, 최종 입력 격리·자동 평가, 새 날짜 확장/자동 가설/24시간 반복·영속 복구는 후속이다.
shared 시장 context를 유지하므로 이 화면 연결이 미사용 최종 종목 holdout이나 종목별 자료 완전성 보장을 추가하지 않는다.
실제 NAS 전체 규모 장시간 운용·사용자 화면 검증은 수행하지 않았다. 모델 에스컬레이션 없음.

# 모의 전진평가 계약

기준: **2.1.0**, 2026-09-22 문서 정리. 이 문서는 동결 전진평가·피드백·별도 자동 모의운용 연결의 상세 계약이다.
구현·배포·운영 확인 범위는 [현재 상태](CURRENT_STATUS.md), 미완료·보류 작업은 [남은 작업](OPEN_ITEMS.md)을 따른다.
A5 피드백과 O2-M0/Me1~Me3의 runner·제어 API·UI는 구현되어 있다. 코드 연결, NAS 적용, 실제 모의계좌·장시간 검증은 각각 구분한다.

## TRAIN 개발 표본 확대 준비

### 확대 입력의 제한된 순차 요청

복원 CLI `--strategy-outcomes-only`는 사후 후보 원본을 보존하면서 뉴스와 선정일 봉을 읽지 않고 결과일의 실제 공급자 봉만 동결한다. 일반 복원 호출의 기본 동작은 유지한다. 기존 주식·최소 연속쌍 투영과 불변 loader를 적용한 날짜 묶음에만 `prepare_historical_baseline_requests --train-expansion`을 사용한다.

요청 생성에는 `--projection`, `--prior-selection`, `--baseline-request`, `--selection-dates`, `--development-source`가 필요하다. 확대 계획이 기존 투영/월별 선택에서 재현되는지 확인하고 선언된 순서의 연속 2~20일만 허용한다. 기존 일괄 검증의 최소 2일과 최대 20일을 유지하며 날짜마다 기존 `development_partition/v2`로 포지션·현금을 초기화한다. 마지막 홀수 날짜는 직전 묶음에 합쳐 누락하거나 복제하지 않는다. 기존 OOS 없는 돌파 정규장 청산 기준 요청에서 전략·비용·평가 정책을 계승하며 별도 `historical_training_batch_preparation/v1`에 확대 hash·기준 요청 hash·자료 ID/revision·최소 입력 점검·실제 과학적 파일 hash를 결합한다. 현재 원장/불변 입력 안의 출력, 임의 날짜 선별·조건 변경·부분 허용을 거절한다.

신규 묶음은 기존 `independent_development_validation/v1` runner의 순차 fresh engine·atomic claim·완료 캐시를 재사용한다. 전체 코호트 준비 완료와 첫 고정 묶음의 기능 시험, 전체 기간 손익 평가 완료는 별도로 보고한다. 일별 손익 합계를 연속 운용 계좌의 수익률/낙폭으로 해석하지 않는다. 실제 비용·종일 완전성·독립 검증·후보 기준/final·Shadow 승격은 이 준비와 기능 시험으로 승인하지 않는다.

`plan_historical_monthly_case_selection --expand-train-selection <기존 월별 계획>`은 기존 TRAIN 월의 모든 후보일을 가격·성과와 무관하게 고정한다. 투영 SHA-256, 월별 계획 파일 SHA-256·plan ID, 달력 첫 후보일·TRAIN/VALIDATION 역할, 날짜 순서와 제외 수량을 대조한다. 첫 VALIDATION 선택일 이상인 결과일은 경계 제외 원장에 남기며 기존 TRAIN 사례를 전부 유지한다. 날짜를 수익성에 따라 선별하거나 기존 검증을 TRAIN으로 옮기지 않는다.

산출물 `historical_train_case_expansion/v1`은 준비 원장이다. `source_readiness_verified=false`, `runner_requests_created=false`를 유지하며 현재 원본 대조는 별도 원장에 결합한다. 기존 월별 일별 요청 생성기의 2~20사례 계약으로 이 확대 원장을 그대로 실행할 수 없으며, 전체 날짜를 유지하는 제한된 순차 TRAIN 연결과 불변 가격 입력 hash·투영 대조를 별도로 검증해야 한다. 최소 연속쌍 감사는 종일 완전성·실시간 TOP20 모집단·독립 holdout·미사용 final·후보 적격성 승인이 아니다.

저장된 `daily_trade_costs`는 기존 `TradeCostService`의 정산 조회 → 계좌별 저장 경계에 속한다. 실전 계좌 지정 기록과 `legacy/unknown` 미배정 기록을 섞어 비용률을 추정하지 않는다. 계좌 식별자를 내보내지 않은 실전 범위별 금액 가중 관측 비율도 주문 매체·할인·슬리피지와 과거 개발 기간의 실제 비용을 확인하기 전에는 후보 비용으로 채택하지 않는다. 현재 후보의 실제 계좌/매체 선택과 기존 연구 비용 모델은 이 준비 단계에서 변경하지 않는다.

O2a는 전략을 실제 주문에 연결하는 기능이 아니라, 앞으로 들어오는 자료에서 동결된 전략을 같은 기준으로 평가하기 위한 증거 경계다. O2a 평가 계약은 mock 환경만 허용하며, 프로파일·보고서 저장만으로 주문 실행기나 서버 시작 경로를 켜지 않는다. 주문 실행은 아래 별도 O2-M 운용·시작 계약을 따른다.

## 동결 프로파일

`ForwardEvaluationSpec`은 다음 값을 평가 시작 전에 고정한다.

- 전략 참조, Family 버전, Factor 버전 목록, 주문 정책 버전
- 주 데이터 경로 `nas` 또는 `direct`
- mock 계좌 참조
- 시작·종료 시각
- 데이터, 시스템, 성과 통과 기준
- 명시적인 연구 `session_profile`과 그 시간표·연속성·단일가 정책

전체 내용으로 `profile_id`를 계산하므로 기간이나 기준 또는 session profile 하나를
바꾸면 새 프로파일이다. S5 이전 저장 문서는 필드 누락 상태의 기존 ID로 읽지만 새
프로파일은 `krx-regular/v1`, `krx-after/v1`, `krx-full-day/v1` 중 하나를 명시한다.
이 값은 연구 가능 범위이며 키움 mock 주문 지원을 뜻하지 않는다. 기준은 임의 기본값을 채우지 않는다. 값이 `TBD`인 기준이 하나라도 있으면 보고서는 `BLOCKED`이며 승격 근거가 될 수 없다.

## 입력 계약

`forward_evidence_from_sources`는 기존 V1 대조 통계와 O1 execution event를 읽어 다음 항목을 만든다.

- 데이터: 비교 가능 관측 수, 경로 coverage, p95 도착 차이, 누락 수
- 시스템: submission unknown, 주문 거절, 취소 실패/응답 유실, 재접속, 잔고 불일치, NO_TRADE
- 성과: 활동일, 완료 거래, mock broker 순손익, broker가 이미 반영한 비용, 별도 누락 비용, MDD, 노출

같은 event ID는 한 번만 센다. `broker_net_pnl_won`은 broker가 반영한 비용 이후 값으로 취급한다. `broker_reported_cost_won`은 설명용이며 다시 차감하지 않는다. 수수료·슬리피지 등 별도로 빠진 비용만 `additional_unmodeled_cost_won`으로 한 번 차감한다.

A5a부터 중앙 event 입력은 검증된 mock 계좌 scope와 `accepted_sequence` 커서로 읽는다.
`source_event_id`가 중복 방지 기준이며 run ID는 전략 실행 계보다. A5b의 상세 FILL projection은
aggregate를 수량/누락 근거로만 분리하고 계좌·거래일·주문·체결 ID로 상세 행을 멱등 저장한다.
A5c는 kt00007 요약과 상세 FILL을 대조하며 겹친 요약과 상세를 독립 거래로 더하지 않는다.
A5d의 `journal_feedback_evidence/v1`은 kt00015 비용, 계좌·기간·전략·선택 run/선언 시각,
체결 품질, 당시 근거의 PIT 상태와 최종 검증 노출 상태를 동결한다. 부분·충돌·요약 전용 체결,
비용 누락·열린 포지션에는 확정 순손익을 만들지 않는다.

`forward_evidence_from_feedback`은 계좌·환경·전략·기간이 profile과 일치하고 피드백 적격성이
확인된 evidence만 기존 `ForwardEvidence`로 변환한다. 데이터 대조 통계와 실행 event는 별도 입력이며,
추가 누락 비용·MDD·노출도 호출자가 근거를 갖춰 제공한다. 원장만 읽어 모든 성과를 자동 확정하는 계약은 아니다.
A5e는 별도 기계 복기·등록 파라미터 개선 제안을 보존하고, 명시 채택 시 새 전략 버전과 기존 개발
campaign의 재검증 요청으로 연결한다. 사용자 복기와 원 전략을 덮어쓰거나 final 평가를 자동 재사용하지 않는다.

## 출력과 승격

- `BLOCKED`: 기준이 `TBD`
- `PENDING`: 기간 또는 최소 표본이 아직 성숙하지 않음
- `FAILED`: 최종 최소 기준 미달, 근거 누락, 또는 최대 장애 한도 초과
- `PASSED`: 모든 기준이 정해졌고 종료된 기간의 모든 gate 통과

최대 허용치 위반은 기간 중에도 즉시 `FAILED`로 표시한다. `PASSED`는 `broker_mock_validated`로 옮길 수 있는 근거일 뿐 자동 stage 변경이나 주문을 만들지 않는다. stage 변경은 별도 불변 revision이며 한 단계씩만 허용한다. `broker_mock_validated` revision을 저장하려면 같은 전략의 저장된 최종 `PASSED` 보고서 ID를 근거로 제시해야 한다. `approved_for_live`는 O2b의 별도 계좌·위험·복구·사용자 승인 경계가 없으면 생성할 수 없다.

## 저장

기존 중앙 `central_documents`를 다음 내부 컬렉션으로 사용한다.

- `execution_forward_profiles`
- `execution_forward_reports`
- `execution_strategy_stage_revisions`

이 컬렉션은 공개 콘텐츠 API allowlist에 포함하지 않는다. key는 내용 hash이며 같은 key에 다른 문서를 저장하려 하면 실패한다. 별도 중앙 스키마 변경은 없다.

## 평가 계약의 한계

- 평가 profile·보고서·PASSED만으로 mock 주문 실행기를 시작하지 않음. 별도 O2-M 시작 경계는 구현되어 있음
- 운영 기준값 자동 선택
- 보고서 통과에 따른 자동 전략 교체
- 실거래 adapter, 실계좌 설정, live 주문
- mock 체결률·대기열·시장 충격을 실거래와 같다고 간주하는 평가

## O2-Ma 자동 모의운용 준비 명세

`mock_automation_operating_spec/v1`은 최종 평가 후보 package/result hash와 final batch/run, 현재 검증된 mock 계좌
binding, 이 문서의 동결 profile과 운용 한도를 하나의 content ID로 묶는다. 동시 전략·포지션,
최대 자금·일일 손실, 데이터 공백·unknown·재접속·잔고 불일치, 지원 거래소/session/data path,
후보 교체·중지·복구 정책 중 하나라도 `TBD`이면 `BLOCKED`다.

현재 O1 구현과 연결 가능한 범위는 단일 전략, 단일 포지션, KRX 정규장 지정가다. 명세가
`READY`여도 저장만으로 runtime claim이나 주문 transport를 켜지 않는다. 후보 입장·lease·broker
복구와 실제 Decision 전달은 아래 O2-M 실행 경계에 연결되어 있으며 각 단계에서 다시 검증한다.

O2-Mb 입장은 최신 binding과 실제 SHADOW revision을 다시 확인하고, 명세의 final batch 안에
candidate hash가 한 번만 존재하며 execution과 research run이 모두 같은 result hash로 완료됐는지
대조한다. 통과하면 spec당 admission을 먼저 남기고 기존 O1의 계좌 단일 lease를 얻는다. lease가
다른 run에 있으면 receipt를 만들지 않는다. 성공한 runtime도 신규 주문은 비활성 상태다.

O2-Mc 복구 gate는 account lease를 다시 확인한 뒤 broker 미체결·체결·잔고·주문가능금액 전체
복구를 검사한다. 기존 주문·포지션·예약자금은 `wait_until_flat`과 충돌하므로 차단한다. 검증된
당일 손익이 없거나 데이터 공백을 알 수 없는 경우도 추정값으로 통과시키지 않는다. 통과 판정은
`CLEARED_ORDERS_DISABLED`이며 후속 주문 gate를 자동으로 열지 않는다.

O2-Md와 M0의 지속 gate는 위 통과 뒤의 각 action Decision을 별도로 검사한다. 현재 binding·lease,
서버 시각·forward 평가기간·KRX 정규 연속장, 입력/계좌 freshness, 최신 risk snapshot ID/revision,
자금·손실·장애 한도와 기존 O1 비종결 intent를 확인한다. ENTER/EXIT·terminal 주문·대사 진행 상태를
구분하며, EXIT는 같은 run의 확인된 보유에서 미체결 매도를 뺀 수량만 허용한다.
승인된 같은 Decision은 결정적 intent 하나로 연결되고 재호출은 기존 intent를 읽는다.
계좌의 영속 RUNNING/STOPPED control revision을 intent 저장 트랜잭션에서 다시 검사한다.
중지 저장 실패 시 메모리 주문 gate도 닫고, 명시 재개는 중지 이후 새 broker 대사를 요구한다.

## O2-Me1~Me3 현재 연결

- Me1은 동결 final 후보 package와 사전 동결 eligibility policy/result receipt를 전용 인증 경계로 게시한다.
  final `COMPLETED`만으로 입장하지 않으며 content hash·등록 Family·구현 hash·final 계보·현재 mock binding을 대조한다.
- Me2는 상세 체결·broker 실제 비용·복구 잔고를 계좌별 FIFO로 대조해 불변 risk snapshot과 단조 reconciliation revision을 만든다.
  비용·체결·잔고가 맞지 않으면 손익을 0으로 추정하지 않는다. 운용 gate는 출처 문자열만으로 이 근거를 대신하지 않는다.
  비용 요청만 실패한 경우 네 계좌 복구 조회의 성공 근거를 유지하고 `COST_QUERY_UNAVAILABLE`과 미확인 손익을 게시한다.
  이전에 확인된 0원 손익이나 캐시 비용으로 신규 진입을 열지 않는다. 같은 run의 상세 체결·잔고·미체결 수량이 대조된 경우에는 기존 manage-only EXIT 검사를 적용한다. 계좌 복구 자체의 실패·잘못된 비용 내용은 이 처리로 숨기지 않는다. 비용 조회가 다시 성공해야 비용 가용성을 복원한다.
- 계좌 단일 owner의 수동/자동 bundle 전환은 flat broker 상태를 확인하고 기존 callback·monitor 작업을 drain한 뒤 lease를 넘긴다.
  자동 모드에서도 조회·명시 취소·대사는 유지하며 수동 신규 주문은 conflict로 거절한다.
- Me3 runner는 중앙 관측 cursor와 O1 상세 체결을 소비하고 supervisor는 저장 RUNNING 의도를 복원한다.
  복원 실패는 신규 주문을 닫는다. 인증 시작·중지·재개·상태 API와 PC 모의 자동운용 창이 이 경계를 사용한다.
  PC는 서버의 `MANUAL`/`AUTOMATIC` 값과 영속 control을 기준으로 제어한다. 상태 조회 실패는 수동·미시작 상태가 아니며, 실패 후 이전 화면 상태로 시작·재개를 열지 않는다. 새 상태 조회에 성공해야 제어를 복원한다. 서버 복원 오류는 새 시작을 막지만 확인된 기존 RUNNING의 중지와 STOPPED의 명시 복구 경로를 없애지 않는다.
- READY 게시 화면은 ELIGIBLE 후보, 같은 설정의 실제 Shadow 근거, 현재 mock binding,
  동결 Forward 기준과 운용 한도를 확인한다. READY 게시와 자동운용 시작은 별도다.
- Shadow monitor 자체에는 주문권한이 없다. 자동운용에서도 같은 Decision의 중복 전송을 막는 기존 O1 원장을 사용한다.

이 연결의 로컬 통합·반복 검증 기록은 실제 브로커 전진 성과나 현재 NAS 배포 상태를 증명하지 않는다.
실제 PostgreSQL·제한 모의계좌·장시간 동시 운용의 확인 범위는 [현재 상태](CURRENT_STATUS.md)와 [남은 작업](OPEN_ITEMS.md)에 남긴다.

### 독립 시계 청산·미체결 관리 — 2026-10-12

후보 설정에 `krx_regular_before_auction/v1`이 명시된 자동 runner는 분봉이 없는 회차와 bootstrap/legacy 복구 중에도 기존 runtime의 서버 시계와 상세 BUY 체결을 확인한다. 보유시간 만료, 진입 거래일의 청산 여유·연속장 끝, 기존 intent의 `expires_at`을 판정한다. 관측 cursor·봉 시각·보유 상태를 시계에 맞춰 합성하지 않는다. 신규 ENTER 직전에도 실제 서버 시각의 진입 예산을 검사한다.

청산이 필요하고 활성 주문이 없으면 보유종목의 실제 완료봉 참조와 새 계좌 위험 원장을 대조한다. 가격 근거의 `bar_end`와 `available_at` 모두 미래가 아니며 운용 명세의 `maximum_data_gap_seconds` 이내여야 한다. 최근에 도착한 과거 봉은 현재 가격으로 쓰지 않는다. 신뢰할 수 있는 bootstrap/복구 완료 frames만 사용하며, Decision·원본 가격 참조를 저장한 뒤 기존 recovery/Decision gate와 결정적 O1 intent 경로로 단 한 번의 KRX LIMIT를 제출한다. broker 조회 동안 가격이 오래되거나 마감 경계를 넘으면 제출을 보장하지 않는다.

- 청산 시점의 매수 미체결은 새 broker 주문 근거로 확인한 뒤 취소를 요청하고, 기존 유효한 매도 미체결은 체결·대사를 기다린다. 주문 유효시간 만료·연속장 끝·가격/계좌 근거 불확실은 먼저 영속 STOPPED와 닫힌 주문 gate로 전환한다.
- clock 중지 뒤 알려진 ACCEPTED/PARTIALLY_FILLED만 기존 gateway의 새 계좌 조회를 거쳐 취소한다. `SUBMISSION_UNKNOWN`과 `CANCEL_PENDING`은 기존 monitor 대사에 남긴다. 취소 시도 ID는 transport 전에 저장하며, 거절·응답 불명·재시작만으로 자동 재시도하지 않는다. 취소 직전 체결되면 terminal 원장을 사용하고 취소를 전송하지 않는다.
- `execution_mock_automation_clock_current`는 계좌별 현재 시계 판단·가격 snapshot·Decision·취소 시도·미해결 intent·중지 revision을 보존한다. 부분 bootstrap의 기존 checkpoint를 덮어쓰지 않는다. 종료는 시작한 시계의 broker 작업·native 저장을 drain한다.
- 취소 접수는 청산 체결이 아니다. 실제 상세 FILL만 보유 수량과 cooldown을 바꾸고, 늦은 체결도 중지 상태에서 계속 반영한다. 같은 RUNNING의 후속 실제 포지션에는 별도 시계 Decision을 만든다.
- 재시작은 영속 중지를 유지한다. 기존 명시 재개 API의 중지 이후 새 broker recovery 검사를 통과해야 다시 판단한다. 이전 진입 거래일의 마감 미청산을 다음 날 신규 보유로 바꾸거나 애프터 주문으로 자동 이월하지 않는다. 중지 함수는 명세·실행 ID와 선택적인 예상 control revision을 확인해 교체된 운용이나 동시 사용자 중지를 덮어쓰지 않는다.

사용자의 기존 긴급 중지는 신규 주문 차단 계약을 유지한다. 이 자동 취소 정책은 위 시간 정책을 선택한 clock 중지에만 적용한다. 로컬 가짜 브로커의 통과는 실제 모의계좌의 조회 지연·가격 신선도·마감 전 체결 성공을 보증하지 않는다. 과학적 연구 구현 hash와 앞선 64회 개발 결과는 변경하지 않았으며, 그 분봉 평가를 수신 단절 시계 경로의 검증으로 재사용하지 않는다.

### PC 후보 파일 준비

[후보 준비 CLI](../scripts/prepare_mock_automation_candidate.py)는 기존 `final_candidate/v1`과 `prepare_mock_automation_candidate_publication()`을 사용한다. 네트워크 호출·최종 평가 실행·NAS 게시·주문은 수행하지 않는다.

| 명령 | 입력과 결과 |
|---|---|
| `freeze-policy` | `--request` 고정 `single_run` 연구 요청, `--strategy-ref`, `--output` JSON. 현재 전략·비용·과학적 구현 hash에 적격 기준과 현재 UTC 시각을 결합한다. `--minimum-closed-trades`, `--minimum-active-days`, `--minimum-net-realized-pnl-won`, `--maximum-drawdown-ppm` 중 생략한 값은 미정이며 출력 상태는 `BLOCKED`다. 모두 지정한 `FROZEN`은 기준 파일 생성 완료이지 전략 적격성·주문 승인 판정이 아니다. |
| `prepare-publication` | `--request`, `--policy` 동결 기준 파일, `--account-ref`, `--final-batch-id`, `--final-run-id`, `--output` JSON. 기존 연구 DB를 읽기 전용으로 열어 완료 final 원장·run·보고서를 대조하고 package/policy/receipt 세 문서를 출력한다. 원장이나 계보가 없으면 파일을 만들지 않는다. 미정·사후 동결·성과 기준 미달은 기존 receipt의 `BLOCKED` 이유를 그대로 보존한다. |
| `inspect-preparation` | `--development-root` 고정 일별 개발 결과 폴더, 선택 `--family`(기본 돌파)·`--strategy-ref`·적격 기준 4개·`--final-start`/`--final-end`·비용/공백 참고 파일. `--request`가 있으면 기존 파서로 고정 요청의 후보 설정을 대조하되 해당 자료·DB를 읽지 않는다. 개발 계획·요청·검증된 개발 입력만 조회하고 JSON을 출력한다. 미정 값을 채우거나 파일·기준·final 원장을 만들지 않는다. |

`inspect-preparation`과 PC `후보·final 동결 준비 점검`은 같은 함수를 사용한다. 모든 개발 fold의 **실제 끝(제외) 시각** 뒤의 시간순 final 범위만 검토하며 시간대 누락·역전·개발 겹침은 입력 관측을 읽기 전에 거절한다. 기존 eligibility policy 계약으로 기준의 타입·범위를 검사하지만 임시 검사용 시각·policy ID를 동결 근거로 반환하지 않는다. 과학적 후보 hash는 기존 계약상 날짜·입력 경로를 제외하므로, 고정 요청의 설정 일치와 final 입력/기간 승인은 다르다. 둘 다 시각이 있어야 범위 대조 완료를 표시하며 비용 유효기간 초과와 현재 모의 Shadow가 미지원인 눌림은 확인 이유로 남긴다. 실제 비용·종일 완전성·개발 성과 승인·final 자료/미사용 원장·같은 설정 Shadow는 항상 별도 미검증 상태다. 보고서는 `REVIEW_REQUIRED`이며 기존 동결·final 실행/입장 gate를 대체하지 않는다.

2026-10-12 사용자 요청의 [기준·final 범위 초안](MOCK_CANDIDATE_CRITERIA_PROPOSAL.md)은 별도 검토 자료다. 제안 값을 UI·CLI 기본값이나 실제 policy로 자동 채우지 않고, 채택되지 않은 상태에서는 기존 미정 값을 유지한다.

준비 기능의 새 검사는 기존 등록된 `test_research_final_execution`과 `test_mock_automation_dialog` 모듈에 포함했다. 최종 전체 catalog 확인은 별도 뉴스 작업의 `tests.unit.test_seed_news_jobs_from_nas_rankings` 미등록 한 개로 실패했으며, 이 준비 기능의 관련 통과를 전체 CI 성공으로 보고하지 않는다.

CLI는 동결 시각을 소급 지정하는 인자를 제공하지 않는다. 동일 후보·기준의 재호출은 원래 파일과 시각을 재사용하며, 기준·전략·비용·코드가 달라진 동일 출력 파일은 교체하지 않는다. 완성된 임시 파일을 같은 디렉터리의 hard link로 원자 공개해 경쟁 작성자가 기존 파일을 덮어쓰지 못하게 한다. 공개 실패 시 임시 파일을 정리하고 오류를 반환한다. 출력은 JSON이며 동결 연구 입력 디렉터리 안에는 쓸 수 없다. 후보 파일 생성은 PC 화면의 READY 명세 작성·서버 admission을 대체하지 않는다.

### PC 화면의 후보 준비·게시

`모의 자동운용 → 후보 준비·게시`에서 고정 연구 요청·기준 파일·전략 이름과 적격 기준 4개를 입력해 기준 파일을 동결한다. 생략한 기준은 미정으로 유지하며 임의 기본값을 넣지 않는다. 별도 final 평가가 완료되면 같은 연구 요청·기준 파일과 완료 batch/run 참조로 후보 게시 파일을 준비한다. 기존 게시 파일을 선택해 검토하는 경로도 제공한다.

검토 화면은 전략 조건·동결 기준·final 참조·관측 결과·연구 비용 모델을 표시한다. `ELIGIBLE`만 NAS 게시 버튼을 열고 입력 변경·작업 실패는 이전 검토를 무효화한다. 파일 작업은 기존 CLI 함수를 작업 스레드에서 사용하며 final 평가나 OOS 데이터 재생을 실행하지 않는다. 게시 파일 읽기는 4 MiB에서 제한하고, 파싱 뒤 기존 세 문서의 256 KiB 게시 상한과 package/policy/receipt hash·재계산 판정·현재 과학적 구현 hash·선택 계좌를 대조한다.

같은 창의 `기존 개발 비교 결과` 폴더와 `돌파·눌림 개발 결과 확인`으로 네 고정 TRAIN·VALIDATION 실행을 확인한다. 요청 분할을 먼저 검사하고 기존 연구 DB를 `read_only=True`로 연다. 결과 파일은 실행 참조만 사용하고 실제 판단·성과는 native 원장의 기존 독립 개발 비교에서 읽는다. 요청과 실행의 전략·비용·분할·세션 불일치 및 OOS 참조는 거절하고, 두 Family의 같은 기간·입력·비용·구현 조건 차이는 별도로 표시한다. 누락 native 보고서는 기존 `INVALID` 판정으로 남긴다. 개발 기준 충족은 모의 후보 적격 판정이 아니며, 조회는 검토한 후보의 게시 상태를 무효화하고 미정 기준을 채우지 않는다. 입력 관측·final 보고서·주문·NAS API는 읽거나 실행하지 않는다.

명령줄에서도 `python -m scripts.summarize_historical_baseline_results --root <고정 결과 폴더> --inspect-development`로 같은 읽기 전용 JSON을 출력한다. 이 옵션은 기존 `summary.json`·`SUMMARY.md`를 갱신하지 않는다. 기존 요약 생성 명령과 별도 경로다.

개발 조회의 `position_observations`는 검증한 TRAIN·VALIDATION 실행의 기존 execution event만 읽어 종료 미청산의 진입 intent·종목·수량·마지막 보유종목 관측·청산 요청·청산 검열 사유를 추적한다. 시각은 시간대를 정규화하며 원본 봉의 존재나 체결 가능성으로 확장해 해석하지 않는다. 종료 미청산 기록이 없는 것은 원장의 해당 기록 부재를 뜻하며 실제 계좌가 flat이라는 증거가 아니다. 진입 참조가 없으면 미확인으로 표시한다. 진단은 기존 순실현손익·개발 판정·후보 적격 기준을 변경하지 않는다.

계속 보유·단일 포지션 계약에서 보유 종목의 후속 체결 근거가 없는 월별 사례는 다음 후보군의 진입까지 막을 수 있다. 이를 해결한다며 관측하지 않은 가격으로 강제 청산하거나 날짜마다 자금·상태를 묵시적으로 초기화하지 않는다. 일별 연구가 필요하면 기존 독립 개발 분할의 새 엔진·초기 자금 계약으로 기간을 명시하고, 독립 결과를 연속 계좌 수익률로 합치지 않는다. 모의·실전 운용의 보유 정책 변경은 별도 결정이다.

### 일별 개발 원장의 손실 조회

`scripts/summarize_historical_baseline_results.py --root <일별 결과 폴더> --inspect-daily-losses`는 고정 일별 계획과 두 Family의 완료 요청 참조를 검사한 뒤 연구 DB를 읽기 전용으로 조회한다. native 독립 비교·run·판단·체결을 대조하고 전략·실행 비용·평가 정책 전체·세션·일별 입력과 기간의 일치를 확인한다. 기존 FIFO 닫힌 거래 계산을 재사용하며 각 거래의 진입 판단, 청산 판단 또는 위험 청산 사유, 체결가 손익, 양쪽 수수료, 매도세를 추적한다. 비용·순손익 불일치와 모호한 참조·부분 비용 배분은 거절한다. 슬리피지는 이미 체결가에 포함되므로 다시 차감하지 않는다. 미청산은 기존 검열로 남기며 일별 독립 금액 합계를 연속 계좌 수익률로 해석하지 않는다. JSON 출력만 수행하고 원본 재생·파일 갱신·OOS·후보 판정·주문은 수행하지 않는다.

### 돌파 판단·체결 가격 기여 진단

일별 원장에 대한 추가 진단 `--inspect-breakout-losses`는 기존 요청·native 원장 대조를 재사용하고 현재 과학적 구현과 다른 돌파 결과를 거절한다. 기존 `--inspect-daily-losses` 출력은 유지한다. 닫힌 거래의 양쪽 판단 가격·수량·시간대와 실제 체결가/금액을 확인해 아래 원화 항등식을 계산한다.

`체결가 손익 = 진입 판단→체결 기여 + 두 판단 사이 가격 이동 + 청산 판단→체결 기여 + 위험 청산 미분해 잔여`

진입 기여는 `진입 판단 가격×수량−매수 체결 금액`, 청산 기여는 `매도 체결 금액−청산 판단 가격×수량`이다. 위험 체결에는 청산 판단을 만들어내지 않고 거래별 청산 신호·관련 기여를 `null`, 진입 이후 남은 체결가 손익을 미분해 잔여로 남긴다. 집계의 두 판단 관련 금액은 청산 판단이 있는 거래만의 합계이며 분해 가능/미확인 건수를 함께 제공한다. 다음 봉 가격 차이와 모델 슬리피지가 결합된 판단→체결 차이를 지연·슬리피지 단독 원인으로 해석하지 않는다. 수수료·세금은 기존 금액을 한 번만 차감한다.

청산 사유는 복수 사유의 조합 단위로, 진입 시간은 사전 고정 KST 반개방 구간 09–10·10–12·12–14·14–15:20과 구간 밖으로 집계한다. 각 차원의 거래·손익 합계가 전체와 일치하며 수익 시간대 선별·전략 변경·후보 판정은 하지 않는다. 고정 주수의 원화 기여는 동일 투자금 비교가 아니다. 실제 원장의 [한국어 진단](../data/research/historical-development-results/daily-2024-09-to-2025-12-session-exit-60s-v2/BREAKOUT_PRICE_ATTRIBUTION.md)과 불변 JSON은 별도 로컬 산출물이다. 132건 VALIDATION의 체결가 손실 19,109원, 수수료 4,056원, 세금 27,806원, 순손실 50,971원을 재대조했다. 125건 보유 만료는 순손실 48,779원이고 판단 사이 가격 이동 -9,195원·청산 판단→체결 -8,177원이다. 이는 보유 10분이 원인이라는 증명이나 개선 성과가 아니다. TRAIN에서 보유 중 가격 경로·돌파 강도·체결 차이를 추가 확인하고 소수 사전 고정 가설을 비교할 근거로만 사용한다. 실제 비용·입력 완전성·final·Shadow는 미승인이다.

### TRAIN 돌파 보유 경로 진단

`--inspect-training-paths`는 기존 고정 계획·요청의 OOS 배제와 native 대조를 재사용하되 TRAIN 돌파 run만 조회한다. VALIDATION/눌림 성과 원장을 읽지 않으며, 기존 동결 개발 전체 바이트의 무결성·범위 검증과 TRAIN 성과 조회를 구분한다. 기존 일별 손실 조회의 기본 출력은 유지한다. 각 TRAIN의 기존 독립 분할을 다시 투영해 native dataset/revision과 대조하고 기존 완료봉 replay·rolling_high_breakout/v1·정수 비용/슬리피지 계산을 재사용한다. 진입 판단 가격·가용 시각과 양쪽 체결 revision/시각/가격의 불일치는 거절한다.

진입 후 1·3·5·10분의 정확한 완료봉만 관측하며 실제 종료 뒤 가격과 위험 청산 봉은 제외한다. 위험 청산 봉 안의 순서는 알 수 없으므로 마지막 전체 봉의 가격을 청산 전에 알았다고 가정하지 않는다. 미관측 시각·일찍 종료된 거래·위험 청산 시각은 각각 별도 상태와 `null`로 남긴다. 시점마다 실제 보유한 표본이 달라지고 종가는 가상 체결가가 아니므로 짧은 보유시간의 손익으로 환산하지 않는다. 사전 고정 돌파 거리 구간 0–10·10–25·25–50·50bp 이상은 기술적 집계이며 최적 진입 조건이나 후보 선택 결과가 아니다. 실제 TRAIN 396건의 [경로 진단](../data/research/historical-development-results/daily-2024-09-to-2025-12-session-exit-60s-v2/TRAINING_BREAKOUT_PATHS.md)에서 179건이 양수 종가 관측 후 최종 순손실, 379건은 전체 보유 관측창의 봉 수가 맞았다. 모델 슬리피지와 분리한 다음 봉 시가 기여는 진입 +8,956원·정상 청산 +266원이며 설정 5bp 모델 슬리피지는 진입 -15,483원·정상 청산 -15,188원이다. 실제 브로커 측정값·손실의 단독 원인·반사실 재실행으로 해석하지 않는다.

### 사전 고정 TRAIN 개발 가설 비교

경로 진단 후 실행 전 불변 `fixed-plan.json`과 두 기존 표준 개발 요청을 고정해 같은 TRAIN 12일에서 5분 보유/10bp 돌파 여유를 각각 12회 평가했다. 기존 기준 12회는 읽기 전용 재사용하고 새 24회는 별도 연구 DB에 저장했다. [고정 가설 비교](../data/research/historical-development-results/breakout-training-hypotheses-20261012-v1/TRAINING_COMPARISON.md)는 native 요청/전략·비용·평가/구현·날짜별 dataset/revision·거래/손익과 기준 파일 SHA를 대조한다. 기준 -82,706원, 5분 -188,900원, 10bp -62,750원의 닫힌 거래 합계이며 독립 날짜의 계좌 수익률이 아니다. 보유 변경은 진입 시간 예산·점유·재진입도 바꾼다. 모두 종료 미청산 0일이나 수익성을 확인하지 못했고 모든 날짜·가설을 보존했다. VALIDATION은 과거 진단에 이미 사용됐다는 제한을 유지하며 이번에는 성과를 조회하거나 OOS/final을 실행하지 않았다. 모델 비용·자료 완전성·대표성·미사용 final·Shadow가 미승인인 상태에서 개발 손실 감소를 후보 적격으로 승격하지 않는다. 실제 비용과 더 넓은 사전 고정 개발 표본의 진입 조건 검증이 다음 범위다.

### 확대 TRAIN 완료 원장의 타점 진단

`scripts/inspect_expanded_training_baseline.py --root <완료 기준 폴더> --output <별도 진단 폴더>`는 완료 평가 index·준비 원장·평가 계획에 묶인 모든 TRAIN 날짜와 요청을 확인한다. 기존 FIFO 가격 분해·완료봉 경로 함수를 재사용하며 새 성과 실험을 실행하지 않는다. native 전략/비용/평가·논리 결과/출력·독립 분할 dataset/revision·체결 원장과 종료 상태를 다시 대조한다. 날짜별 분할 입력을 한 묶음씩 로드하고 RSS 1024MB·CPU 50% 기존 자원 guard를 적용한다. 분봉 공백·늦은 가용 시각·위험 청산 봉·조기 종료는 미확인/별도 상태로 유지하고 실제 청산 뒤 가격을 사용하지 않는다.

돌파 거리 경계 0·10·25·50bp와 관측 1·3·5·10분은 기존 진단의 고정 구간이다. 진입 시간은 거래일별 실제 개장 시각 이후 0·60·180·300분의 반개방 구간으로 나눠 지연 개장도 처리한다. 청산 사유는 조합별 한 거래로 세며 모든 월/날짜를 보존한다. DB·입력·기준 보고서의 전후 SHA를 확인하고 별도 불변 JSON만 작성한다. 관측 가격을 가상 청산 체결가로 사용하거나 구간별 손익을 새 전략 성과로 승계하지 않는다.

239일 진단 뒤 돌파 여유 25bp 하나의 비교 요청 119개를 기존 native 파서로 검증해 사전 고정했다. 원래 0bp 기준·10분 보유·비용·수량·초기 자본·세션·자료·독립 상태 정책을 보존한다. 초기 현금 100만원은 각 날짜의 새 독립 시작값이며 수량은 1주다. 실제 투자 비중이나 연속 계좌 수익률을 뜻하지 않는다. 비교는 현재 미실행이며 기존 DB 크기×1.25 추정과 여유 20GiB를 실행 직전/완료 묶음 경계에서 확인한다. 뉴스 수집 증가와 용량 중단은 별도 운영 상태이고 용량 미통과를 성과 실패·후보 부적격·전체 완료로 처리하지 않는다. 실제 비용/입력 완전성·기준 채택·독립 검증/final·Shadow는 별도 승인 근거가 필요하다.

### 기능 개발 우선과 완료 연구의 냉장 보관 — 2026-10-12

사용자 결정으로 추가 타점 비교는 보류하고 기존 모의운용의 주문·체결·청산·복구 연결을 확인한다. 25bp 계획 자체는 불변 보존하되 별도 comparison-disposition.json의 DEFERRED를 먼저 확인한다. 완료 연구의 큰 상세 DB·중복 입력은 NAS 아카이브로 옮기고 작은 완료 원장·요청·manifest·타점 진단을 PC에 유지했다. 원래 실행이 완료됐다는 과거 근거와 현재 raw 입력/DB를 새로 감사했다는 사실은 구분한다. 원본 재생·새 조건 실행·native 상세 감사는 복원 영수증의 ZIP/파일 SHA와 기존 입력/과학적 hash를 다시 통과해야 한다. 기능 시험의 합성 적격 자료를 실제 후보 승격이나 실제 주문 권한으로 사용하지 않는다.

새 부분 청산 복구 회귀는 기존 gate·위험 조회·실행 수명·마감 시계·checkpoint·일지를 사용해 매도 주문 한 개의 1주 부분 체결 뒤 runner를 다시 만들고 남은 1주를 체결한다. 마지막 체결 뒤 재생성/반복 조회에도 주문이 추가되지 않고, 2주 매수/2주 매도의 상세 체결 세 행이 일지에 한 번씩만 들어가며 재투영의 새 행은 0개다. 같은 프로세스의 native 인메모리 SQLite와 격리 브로커를 사용하므로 실제 프로세스/NAS 재시작·실제 broker·장시간 proof로 승계하지 않는다. 현재 배포/소스·모의 binding·후보·명세의 읽기 전용 준비 점검과 명시 승인 범위 안의 실제 모의 체결 확인은 남는다.

### 시간대별 단타 운영 요구 — 2026-10-12

사용자 요구는 일반 거래일의 정규장 단타 진입·청산 체결을 **09:00:00 이상, 15:20:00 미만**에 완료하는 것이다. 15:20:00부터 마감 단일가 구간이므로 그 시각에 청산 신호를 처음 만들면 늦다. 특별 거래일은 검증된 날짜별 시간표를 사용한다. 돌파·눌림의 공통 판단과 연구 체결에 아래 선택 정책을 구현했으며 기존 결과를 소급 변경하지 않는다. 실제 체결 근거가 없으면 임의 가격으로 flat 처리하지 않고 미체결·미청산과 중지/복구 필요 상태를 남긴다.

`session_exit_policy=krx_regular_before_auction/v1`와 정수 `session_exit_buffer_seconds=60~3600`을 함께 명시하면 활성화된다. 필드가 없는 기존 설정은 기존 정책을 유지하며 기본 직렬화에도 필드를 추가하지 않는다. 미등록 정책·불완전한 조합·bool/잘못된 여유는 거절한다. 정책과 여유는 후보 조건·Shadow 설정 ID·연구 요청의 전략 조건에 포함하고 Snapshot의 정책 버전도 구분한다. 시간 정책의 여유는 개발 검증 조건이며 브로커 지연을 측정해 확정한 운영값이 아니다.

진입 판단 시 `현재 판단 시각 + 신호 유효시간 + 최대 보유시간 + 청산 여유 < 연속장 종료`를 요구한다. 예를 들어 보유 10분·신호 60초·여유 60초에서는 15:08:00부터 새 후보 진입을 차단한다. 기존 보유 포지션은 **진입 거래일의** 연속장 끝에서 여유를 뺀 시각(일반 거래일 60초 여유이면 15:19:00)부터 `session_exit_due` 청산 판단을 내린다. 다른 종목 관측으로 보유종목을 청산하지 않고, 청산 신호 뒤에도 실제 체결까지 open 상태를 유지한다. 입력이 없다면 이 봉 기반 판단을 실행할 수 없으며, timer나 브로커 체결 보장은 별도 운영 검증 대상이다.

연구 체결은 raw 늦은 ENTER에도 시간 예산을 다시 검사한다. 실제 다음 봉 시작의 진입에는 보유·청산 여유만 검사하며 신호 유효시간을 두 번 더하지 않는다. 진입·청산 모두 개장 전·주말 체결을 거절한다. 15:20:00 이상 시작하는 다음 봉과 경계 이상 시각으로만 표현할 수 있는 봉 내 위험 청산은 검열·미지원으로 남기고 가격을 만들지 않는다. 이 변경은 프리·애프터 거래 지원, 후보 승격, 실제 모의계좌 운용 시작, NAS 배포 권한을 뜻하지 않는다.

현재 운영 시간과 과거 연구 시간표를 구분한다. NXT 프리장은 **08:00:00 이상, 08:50:00 미만**, 애프터장은 **15:40:00 이상, 20:00:00 미만**이다. KRX 애프터장은 2026-09-14부터 **16:00:00 이상, 20:00:00 미만**이며 KRX 프리장은 준비 중으로 현재 자동매매 가능 시간에 포함하지 않는다. [NXT 공식 시간표](https://www.nextrade.co.kr/), [금융위원회 거래시간 연장 발표](https://www.fsc.go.kr/po010101/87166), [시행 안내](https://www.kbsec.com/go.able?idt=20260904&linkcd=s060901010000&seq=10010298)를 2026-10-12 확인했다. 과거 2024~2025 자료에 신설된 KRX 애프터 시간표를 적용하지 않는다. 프리·정규·애프터의 별도 진입 허용·체결 마감·시장별 종목 적격 및 주문 지원을 검증하기 전에는 정규장 모의 지원을 애프터·프리 자동운용으로 확대하지 않는다.

### 역사 개발 입력의 독립 일별 평가 준비

개발 원본을 다시 추출할 때는 고정 선정 원장의 개발 후보일만 지정하고 마지막 개발 결과일에서 종료한다. NAS의 게시된 고정 run 스냅샷은 `export_historical_reconstruction --intelligence-immutable`로 읽으며 WAL/SHM가 있으면 연결 전에 거절한다. 수집 중인 DB에는 이 옵션을 쓰지 않는다. SQLite 파일 URI의 UNC·예약 문자 인코딩과 지원 봉 주기 60/300초의 날짜 범위 검색은 추출 결과 조건을 유지한다. 새 추출은 새 수집 출처·revision hash로 보존하고, 접근 거부된 기존 동결 입력과 동일한 파일이라고 주장하지 않는다. 실제 자료 대조를 통과하기 전에는 기존 계획의 종목·제외 목록을 새 자료에 맞춰 바꾸지 않는다. [SQLite URI 계약](https://www.sqlite.org/uri.html)을 따른다.

`scripts/prepare_historical_baseline_requests.py --daily-selection <월별 선정 원장> --output <새 결과 폴더>`는 기존 달력 우선 선정의 TRAIN·VALIDATION 결과일을 그대로 고정한 `daily-plan.json`을 생성한다. 검증된 거래일별 개장·마감 시각을 사용하고 마감 시각은 포함한다. 기존 돌파·눌림의 잠정 조건과 개발 비용을 계획에 함께 동결한다. 입력을 지정하지 않으면 `source_required`와 빈 요청 목록을 반환하며 원본 관측·성과·OOS를 읽거나 실행 요청을 만들지 않는다.

같은 명령에 `--development-source <개발 날짜만 포함한 어댑터 입력>`을 추가하면 manifest를 먼저 확인하고, 선언한 모든 개발 날짜·후보 수·기존 제외 코드·결과일의 봉 범위와 각 일별 최소 연속쌍 readiness를 대조한다. OOS 포함·전체 자료·이미 분할된 입력·날짜 누락·다른 세션·별도 테마 입력을 거절한다. 일부 날짜를 건너뛰거나 `--allow-partial`로 gate를 낮추지 않는다. 모든 구간이 준비된 경우에만 기존 `independent_development_validation/v1`의 두 Family 요청과 `daily-readiness.json`을 게시한다. 파일은 원자 생성하며 기존 내용이나 경쟁 작성자의 파일을 교체하지 않는다.

시간 정책 실험은 `--regular-exit-buffer-seconds 60`처럼 명시한 새 결과 폴더에서 생성한다. 동일 선정·입력·비용·다른 전략 조건을 유지하고 변경은 해당 정책과 청산 여유로만 제한한다. 생략한 요청은 기존 정책이다. 이미 동결된 계획의 설정을 교체하거나 결과를 다시 라벨링하지 않는다.

요청 실행은 기존 `kiwoom_monitor.research_process --validate-partitions <요청> --result <결과>` 경계다. 각 날짜의 새 엔진·자금, 미청산 검열, 완료 캐시와 소유권 검사를 그대로 사용한다. 최소 연속쌍 `READY`는 종일 자료 완전성이나 수익성 승인이 아니다. 완료 거래일이 부족하면 기존 `INSUFFICIENT_SAMPLE`·비교 `INCOMPLETE`를 유지하며, 미청산 가격을 만들어 손익을 채우거나 독립 날짜의 계좌 곡선을 합치지 않는다. 이 준비는 후보 승인·final 실행·주문 권한을 부여하지 않는다.

### final 준비 전 개발 비용·입력 점검

`scripts/summarize_historical_baseline_results.py --root <일별 결과 폴더> --inspect-daily-inputs`와 PC 후보 준비 창의 `일별 개발 비용·입력 점검`은 같은 읽기 전용 함수를 사용한다. 계획·두 Family 요청의 전략·비용·분할을 먼저 대조하고, 요청 생성과 공통인 개발 자료 범위 검사로 OOS·전체/분할 입력·추가 날짜를 원본 관측을 읽기 전에 거절한다. 기존 loader의 파일 hash와 실제 요청 dataset/revision 식별자를 확인하며 결과·연구 DB·주문·NAS는 조회하거나 실행하지 않는다. 긴 자료 읽기·투영은 기존 Qt interruption을 확인하며 점검은 이전 후보 게시 검토를 무효화한다.

비용의 bp·출처·근거 종류·유효기간과 모든 개발 구간 포함 여부를 표시한다. 출처 문자열이나 `official_period_rule` 표기만으로 실제 브로커 비용을 확인했다고 판정하지 않는다. 각 후보·결과일의 검증된 개장부터 단일가 시작 직전까지 고유 분봉 수, 첫/마지막 봉, 양끝 미관측 분, 내부 공백, 연속장 밖 관측을 기록한다. 일반일은 `[09:00, 15:20)` 380분이며 검증된 지연 개장은 기존 거래일 시간표를 사용한다. 같은 분의 여러 revision은 한 분으로 센다. 미관측 분은 무거래 또는 공급자 누락 여부 미확인이며 봉을 합성하거나 후보를 자동 제외하지 않는다.

점검 상태 `REVIEW_REQUIRED`와 실제 비용·종일 완전성 미검증을 유지한다. 최소 연속쌍 `READY`·모든 분 관측·비용 유효기간 포함은 final 승인이나 후보 적격 판정이 아니다. 실제 적격 기준 4개, final 기간·완료, 같은 설정 Shadow는 이 점검에서 평가하지 않는다. JSON만 출력하며 CLI·PC 점검 자체는 파일을 갱신하지 않는다.

선택 입력 `--cost-reference <JSON>` / PC `비용 참고 근거`는 `historical_stock_cost_reference/v1`의 개별 주식 범위와 실제 입력의 dataset·revision hash·관측 파일 hash를 고정한다. 시간대가 있는 확인 시각·정확한 bp 문자열·HTTPS 출처와 개발 결과일 전체의 날짜별 매도세를 요구하며 누락·중복·다른 자료는 거절한다. 표준 수수료·당시 세율 참고값의 일치 여부만 표시하고 실제 계좌·매체·할인·슬리피지는 미검증으로 유지한다. 문서 hash는 파싱한 JSON의 정규화된 SHA-256이다. 2026-10-12 확인한 [정부 세율 안내](https://www.korea.kr/news/policyNewsView.do?newsId=148910029)와 [키움 2025년 적용 공지](https://www.kiwoom.com/h/hts/popup/VHtsNoticePopView?flag=content&gubun=1&mtsgb=&pagegb=2&seq=00002361&tp=H4)는 이번 개발의 2024년 4일 18bp·2025년 12일 15bp 참고 근거다. [현재 키움 일반 현금주식 표준 비용](https://www3.kiwoom.com/h/domestic/stock/VStockMainView?dummyVal=0)의 수수료 각 1.5bp·매도세 20bp와 구분한다. 기존 합의 모델·64회 결과는 현재 비용 시나리오로 보존하고 당시 실제 비용으로 다시 라벨링하거나 재계산하지 않는다.

선택 입력 `--gap-provenance <JSON>` / PC `과거 공백 감사`는 기존 `historical_monthly_selected_gap_raw_provenance/v1`의 구간을 현재 미관측 10분 이상 내부 공백에 대조한다. 현재 봉의 실제 `bar_end`를 원응답의 종료시각과 시간대 정규화해 비교하며 임의로 1분을 더하지 않는다. 과거 감사 구간 일치와 현재 원응답 파일 재검증은 별개다. 허용된 CREON 보관 폴더의 `1m.ndjson`만 읽고 과거 SHA-256·조회 중 파일 불변·날짜 전체의 시각과 거래량을 대조한다. 원본 부재·hash/거래량 불일치·중복 충돌은 확인 완료로 승계하지 않는다. 원응답 일치도 무거래·시장 종일 완전성의 증명은 아니며 봉·후보·원장을 변경하지 않는다. 두 선택 옵션은 `--inspect-daily-inputs`에서만 지원한다.

NAS 게시 직전에는 검토한 문서가 그대로인지 확인하고 최신 모의 binding의 계좌·profile·환경·revision을 다시 대조한다. 게시 응답의 package/policy/receipt 식별자·적격 판정과 `orders_started=false`를 확인해야 완료를 표시하며, 실패·응답 불명은 자동 재전송하지 않는다. 게시 완료 신호는 부모 화면의 목록 조회만 요청한다. READY 명세 작성과 자동운용 시작을 호출하지 않는다. 진행 중에는 입력과 닫기를 막고 앱 종료는 작업에 interruption을 요청한 뒤 실제 worker 종료를 기다린다. binding 조회 뒤 interruption이 확인되면 POST를 보내지 않는다.

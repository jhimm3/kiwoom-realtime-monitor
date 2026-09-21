# 모의 전진평가 계약

기준: **2.1.0**, 2026-09-22 문서 정리. 이 문서는 동결 전진평가·피드백·별도 자동 모의운용 연결의 상세 계약이다.
구현·배포·운영 확인 범위는 [현재 상태](CURRENT_STATUS.md), 미완료·보류 작업은 [남은 작업](OPEN_ITEMS.md)을 따른다.
A5 피드백과 O2-M0/Me1~Me3의 runner·제어 API·UI는 구현되어 있다. 코드 연결, NAS 적용, 실제 모의계좌·장시간 검증은 각각 구분한다.

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
- 계좌 단일 owner의 수동/자동 bundle 전환은 flat broker 상태를 확인하고 기존 callback·monitor 작업을 drain한 뒤 lease를 넘긴다.
  자동 모드에서도 조회·명시 취소·대사는 유지하며 수동 신규 주문은 conflict로 거절한다.
- Me3 runner는 중앙 관측 cursor와 O1 상세 체결을 소비하고 supervisor는 저장 RUNNING 의도를 복원한다.
  복원 실패는 신규 주문을 닫는다. 인증 시작·중지·재개·상태 API와 PC 모의 자동운용 창이 이 경계를 사용한다.
- READY 게시 화면은 ELIGIBLE 후보, 같은 설정의 실제 Shadow 근거, 현재 mock binding,
  동결 Forward 기준과 운용 한도를 확인한다. READY 게시와 자동운용 시작은 별도다.
- Shadow monitor 자체에는 주문권한이 없다. 자동운용에서도 같은 Decision의 중복 전송을 막는 기존 O1 원장을 사용한다.

이 연결의 로컬 통합·반복 검증 기록은 실제 브로커 전진 성과나 현재 NAS 배포 상태를 증명하지 않는다.
실제 PostgreSQL·제한 모의계좌·장시간 동시 운용의 확인 범위는 [현재 상태](CURRENT_STATUS.md)와 [남은 작업](OPEN_ITEMS.md)에 남긴다.

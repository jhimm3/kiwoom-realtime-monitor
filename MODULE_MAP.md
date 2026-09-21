# 모듈 지도

> O2-M V1 반복 검사: `scripts/check_mock_automation_v1.py`가 제품 경로를 우회하는 별도 모의 엔진 없이
> 기존 62개 게시·입장·위험·체결·A5·재시작·중지·계좌 회귀를 같은 프로세스에서 기본 30분 반복한다.
> wall/CPU·반복 수·RSS·Python heap peak와 실패 출력을 JSON으로 남기며 제품 DB/NAS 설정은 쓰지 않는다.

> 실시간 순위·신고가·기본정보 성능 경계: `central_server/rest_broker.py`가 30초 순위 경계 예약과
> 중앙 TR 우선순위를, `central_server/autonomous_top20.py`가 신규 추적 종목의 신고가용 일봉 및 당일
> 기본정보 준비를 담당한다. `central_server/market_ingest.py`가 `ka10001` KST 당일 최신성 판정을
> 제공하고 `central_server/app.py`의 저장 응답 경계도 같은 판정을 사용한다. 자동 TOP20의
> `ka00198` 후보는 broker의 unrecorded 경로에서 최신 기준시각·20개 슬롯을 먼저 검사하고, 수락된
> 완성본만 `top20_membership`으로 저장한다. 일반 broker 요청의 응답 저장 계약은 그대로 유지한다.

> 2026-09-21 O2-M0: `application/mock_automation_execution.py`가 영속 control revision과 gate v2,
> ENTER/EXIT 분리, 서버 시각·평가기간·대사 상태를 검사하고 `execution_runtime.py`가 intent claim
> 트랜잭션에 control revision을 전달한다. `mock_automation_recovery.py`는 terminal 주문을 제외하고
> 보유가 있는 같은 run을 manage-only로 복구할 수 있다. `forward_evaluation_repository.py`는
> admission/lease/current recovery/current stop/approved gate/dispatch receipt를 단건 identity로
> 조회하며 `execution_repository.py`는 scope별 active intent만 읽는다. 실제 risk snapshot producer와
> account bundle mode 전환은 O2-Me2, runner/UI는 O2-Me3에서 연결했다. NAS 배포는 V1 전까지 보류한다.

> 2026-09-21 O2-Me3 완료: `central_server/mock_automation_runner.py`가 저장된 NAS
> `top20_membership`·strict KRX 완료 분봉을 cursor로 한 번씩 읽고 게시 후보의 등록 Family를
> 그대로 계산한다. 전략의 실제 open/cooldown 상태는 신호나 주문 접수가 아니라 O1 상세 FILL로만
> 갱신하며, package/spec/run·입력 cursor·체결 cursor·전략 상태·pending intent를 account별 current
> checkpoint에 저장한다. 동일 입력 재처리는 기존 Decision/intent 멱등 경계를 사용한다.
> `central_server/mock_automation_supervisor.py`가 계좌별 mode 전환·runner 수명·재시작 복원·중지/재개를
> 소유하고 `app.py`의 인증 API와 NAS lifespan에 연결한다.
> `application/mock_automation_specification.py`는 후보 package/policy/receipt, forward profile,
> DRAFT→EVALUATED→VALIDATED→SHADOW 증거 chain, 실제 중앙 shadow event와 현재 binding을 저장 전에
> 대조한다. `app.py`의 전용 인증 POST/GET은 검증된 READY 명세만 비공개 repository에 게시·조회하며
> 게시 자체는 runner나 주문을 시작하지 않는다. `infrastructure/central_content_client.py`는 PC에서
> 계좌별 게시 후보 목록과 현재 검증 바인딩을 읽는
> `GET /api/v1/research/mock-automation-candidates/{account_ref}`도 제공한다. 이 읽기 경계는 READY
> 명세 작성 UI가 package hash나 binding revision을 수동 입력하지 않게 하며, 다른 계좌·프로필의
> 후보를 섞지 않는다.
> `presentation/mock_automation_dialog.py`의 READY 작성창은 ELIGIBLE 후보와 같은 monitor ID의 실제
> shadow event만 선택하며, 평가기간·14개 forward 기준·8개 운용 한도를 화면에서 명시한 뒤
> `application/mock_automation_specification.py`의 순수 builder로 profile/stage/spec을 고정해 게시한다.
> 후보·명세 게시, 계좌별 명세·runtime 상태 조회와 시작·중지·재개 요청을 같은 Bearer HTTP 경계로
> 제공한다. `presentation/mock_automation_dialog.py`는 활성 mock profile과 account settings revision,
> READY 명세, 저장 control/runner 상태를 조회해 가능한 시작·중지·재개만 노출한다. 메인 툴바와
> 기본설정에서 열 수 있다. `test_mock_automation_specification.py`는 게시→입장→위험 대사→가짜
> 매수·매도 체결→계좌별 A5 매매일지 투영을 실제 저장소와 runtime 조합으로 검증한다.
> `application/order_lifecycle.py`는 상세 체결이 broker 누적 수량을 이미 설명하면 0수량 aggregate를
> 만들지 않고 broker 누적 상태만 reconciliation event로 갱신한다.

> 2026-09-21 O2-Me1: `application/mock_automation_candidate.py`가 별도 후보 package, 사전 동결
> eligibility policy와 결과 receipt의 정규화·hash·BLOCKED/ELIGIBLE 판정을 소유한다.
> `research_process.prepare_mock_automation_candidate_publication`은 CR3 final 원장/run/report에서만
> 게시 문서를 만들고, `application/research_implementation.py`는 PC와 NAS가 공유하는 scientific hash를
> 계산한다. `central_server/app.py`의 전용 인증 POST와 `forward_evaluation_repository.py`의 비공개
> 컬렉션만 이를 저장한다. 일반 콘텐츠 sync, runtime, O1 transport는 이 경계에 연결하지 않는다.

> 2026-09-21 O2-Me2: `application/mock_automation_risk.py`가 계좌별 O1 상세 체결, 실제 broker 비용,
> broker 복구 잔고를 FIFO로 대조해 불변 risk snapshot과 current revision을 만든다. 자동 bundle에서만
> 기존 모의 REST queue로 비용 근거를 읽으며 KST 날짜 변경 때 과거 보유 비용을 다시 확보한다.
> `MockCredentialOwner.switch_execution_mode`는 flat 확인과 gateway/monitor drain 뒤 기존 lease를 놓고
> 새 자동 run bundle을 만들며, 자동 모드의 수동 신규 주문은 막고 조회·취소·대사는 유지한다. 운영
> 복구/Decision wrapper는 저장된 같은 risk revision만 허용한다. 지속 runner와 UI는 O2-Me3에서 연결했다.

> O2-Md 지속 Decision gate: `application/mock_automation_execution.py`는 저장된 spec/admission/lease와
> 최신 recovery revision을 매 action Decision 직전에 다시 연결한다. 현재 mock binding, lease,
> KRX 정규 연속장, 계좌 snapshot, 데이터 공백, FIFO+broker 비용 출처의 당일 순손익, 자금·손실·
> 장애 한도와 O1 진행 중 주문을 모두 통과한 Decision만 결정적 intent로 만든다. 같은 Decision은
> 기존 O1 intent를 읽어 재전송하지 않는다. `ExecutionRuntime.automation_decision_guard()`와
> `submit_automation_intent()`는 검사와 한 번 제출을 직렬화하고 제출 직후 신규 주문을 다시 닫는다.
> gate/dispatch/긴급 중지 revision은 `forward_evaluation_repository.py`가 account별 비공개 문서로
> 저장한다. 긴급 중지는 기존 주문·포지션을 임의 취소/청산하지 않고 새 recovery 전까지 신규 주문만
> 차단한다. 최종 후보 package를 실제 Decision runner로 복원하는 운영 연결은 아직 없다.

> O2-Mc broker 복구 gate: `application/mock_automation_recovery.py`는 admission/lease/spec 계보와
> runtime lease를 확인하고 기존 `MockAccountRecovery`의 미체결·체결·잔고·주문가능금액 결과를
> 당일 손익·데이터 공백·submission unknown·재접속·잔고 불일치 지표와 합쳐 불변 recovery decision을
> 만든다. 미완성 복구, 알 수 없는 손익/공백, 한도 초과, 기존 주문·포지션·예약자금, 오래된 snapshot은
> `BLOCKED`다. 모든 검사를 통과해도 상태는 `CLEARED_ORDERS_DISABLED`이며 runtime 신규 주문을
> 계속 닫는다. `forward_evaluation_repository.py`가 account별 decision revision을 비공개 저장한다.

> O2-Mb 후보 입장·실행 소유권: `application/mock_automation_admission.py`는 저장된 READY 명세의
> 최신 mock binding·SHADOW revision·CR3 final batch/run/result를 다시 대조하고 spec 내용으로
> 결정한 자동운용 run ID를 만든다. 입장 request를 먼저 불변 저장한 뒤 기존 `ExecutionRuntime`이
> account 단일 lease를 얻는다. runtime은 `new_orders_enabled=False`로 원자 시작하며 lease receipt도
> 주문 비활성 상태만 허용한다. 수동 O1 runtime이나 다른 후보가 계좌 lease를 가지고 있으면 입장
> receipt를 만들지 않는다. 연구 저장소와 candidate에는 주문 권한이 생기지 않는다.

> O2-Ma 자동 모의운용 명세: `domain/execution_activation.py`의
> `MockAutomationOperatingSpec`은 최종 후보/result hash와 final batch/run, 현재 검증된 mock 계좌 binding,
> 동결 forward profile, 동시 전략/포지션·자금·일일 손실·데이터/장애 한도와 후보 교체·중지·복구
> 정책을 내용 주소형으로 고정한다. 미정값, SHADOW 전 단계, profile/scope 불일치와 현재 O1 범위를
> 넘는 다중 전략/포지션·비정규장 session은 `BLOCKED`다. `forward_evaluation_repository.py`는 현재
> 계좌 binding을 재검증한 뒤 비공개 중앙 문서에 불변 저장한다. 이 단계는 transport·runtime을 켜지 않는다.

> A5e3 새 전략 버전·재검증 연결: `application/feedback_strategy_revision.py`는 채택된 A5e2
> proposal을 부모 전략과 분리된 `feedback_strategy_version/v1`으로 고정하고 기존 개발 캠페인의
> 명시 template `ExperimentSpec`을 같은 설정의 baseline/no-trade 재검증 작업으로 바꾼다.
> `forward_evaluation_repository.py`는 proposal당 한 전략 버전, 버전당 한 queue request와
> campaign별 receipt를 기존 중앙 문서 저장소에 불변 저장한다. 중앙 문서와 연구 SQLite는 단일
> 트랜잭션이 아니므로 request를 queue보다 먼저 남기고 strategy/request/experiment/job/receipt ID를
> 결정적으로 만든다. 중단 후 재시도는 기존 job을 재사용한다. final holdout template, 전략 원본
> 덮어쓰기와 주문 활성화는 허용하지 않는다.

> A5e2 개선안 revision: `application/forward_evaluation.py`는 적격 A5e1 review와 등록 Family,
> factor allowlist, 정규화된 기준 전략, 명시 허용값으로 한 필드만 다른
> `journal_feedback_improvement/v1` 후보를 만든다. seed는 후보 순서에만 사용하며 같은
> review·정책·변경은 같은 ID다. 제안은 개선을 확정하거나 전략을 수정하지 않는다.
> `forward_evaluation_repository.py`는 원본 review가 먼저 저장되고 계좌·전략·evidence·평가 방향이
> 일치하는 제안만 `execution_feedback_improvement_proposals`에 불변 저장한다.

> A5e1 기계 복기 revision: `application/forward_evaluation.py`는 저장된 A5d
> FeedbackEvidence와 호출자가 명시한 최소 거래일·거래 수 정책으로
> `journal_feedback_review/v1`을 결정적으로 만든다. 확정 비용 포함 순손익, 승/패/보합 수,
> 승률과 비용 비율을 계산하되 불완전·편향·사후 노출 근거는 개선 제안 입력으로 넘기지 않는다.
> `forward_evaluation_repository.py`는 원본 evidence가 먼저 저장돼 있고 계좌·전략·정책으로
> 다시 계산한 review가 일치할 때만 `execution_feedback_reviews`에 불변 저장한다. 사용자 복기,
> 수동 유형과 전략 원본은 수정하지 않는다.

> A5d FeedbackEvidence: `application/forward_evaluation.py`는 A5c의 선택 체결과 kt00015 비용을
> 회차별로 계산하고 계좌·평가기간·전략·사전 run 선택·PIT 근거·최종결과 노출 상태를 내용 해시에
> 포함한다. 체결/비용/포지션이 모두 확인되지 않으면 확정 순손익을 만들지 않는다. 사전 선택,
> 체결 당시 근거, 개발 자료만 사용한 완결 문서만 기존 `ForwardEvidence`로 변환할 수 있다.
> `forward_evaluation_repository.py`는 `execution_feedback_evidence` 중앙 문서를 불변 저장하며 기존
> 일지 DB와 사용자 복기는 쓰지 않는다.
>
> A5c 체결 대조 read projection: `application/trade_history_query_service.py`의
> `load_reconciled`는 같은 canonical 계좌·KST 거래일·주문번호·종목·매수/매도별로 kt00007
> 요약과 중앙 상세 FILL을 비교한다. 수량과 금액이 모두 같으면 `EXACT`, 상세만 있으면
> `DETAIL_ONLY`, 요약만 있으면 `SUMMARY_ONLY`, 수량 차이는 `PARTIAL`, 같은 수량의 금액 차이와
> broker 체결 ID 충돌은 `CONFLICT`다. 상세가 한 건이라도 있으면 결과 체결은 상세 한 벌만
> 선택해 두 출처를 합산하지 않는다. `journal_trade_repository.py`는 canonical 계좌와 기간으로
> 상세 projection을 읽되 기존 체결·복기·수동 묶음은 수정하지 않는다.
>
> A5b 상세 체결 projection: `application/journal_enrichment.py`는 중앙 page 전체의 연속 cursor와
> 계좌 scope를 검사하고 `FILL`/`BROKER_FILL_AGGREGATE`만 분리한다. 상세 체결 ID는
> 계좌+KST 거래일+broker 주문번호+체결번호로 만들며 run ID는 계보에만 남긴다.
> `journal_trade_repository.py`는 매매일지 DB v9의 projection event와 cursor를 한 `BEGIN IMMEDIATE`
> 트랜잭션에 저장한다. aggregate는 누락 상세수량만 계산하고 실제 상세 체결 합에 더하지 않는다.
> 중앙 원본에서 재생성 가능한 두 v9 표는 기존 journal content sync에 넣지 않는다.

> A5a 계좌별 실행 원장 읽기: `infrastructure/persistence/execution_repository.py`의
> `account_events`는 mock 계좌 scope와 중앙 `accepted_sequence` 커서로 여러 run의 실행 event를
> 불변 조회한다. 중앙 SQLite/PostgreSQL store는 intent와 event를 같은 계좌 조건으로 join하며,
> `/api/v2/mock/accounts/{account_ref}/execution-events`는 현재 credential binding을 다시 확인한 뒤
> bounded page를 반환한다. `RemoteKiwoomRestClient.load_mock_execution_events`는 응답 context와
> 단조 커서를 검증한다. 이 단계는 아직 일지 `trade_fills`를 쓰거나 aggregate를 상세 체결로 바꾸지 않는다.

CR4c 자동 후속 가설 백엔드: `application/research_queue.py`의 campaign 정책은 Family별 허용 파라미터 값,
고정 seed, 회차 생성 상한과 campaign 전체 가설 상한을 저장한다. `application/research_hypotheses.py`는
완료 baseline 보고서에서 이미 분리한 `DevelopmentEvidence`를 콘텐츠 주소형 snapshot으로 고정하고,
부모의 정확한 설정에서 한 필드만 바꾼 자식만 만든다. `research_process.py`의 worker는 완료된 가설 하나를
반복마다 최대 한 번 확장한 뒤 기존 CR4b 예약을 수행한다. final fold·원시 보고서는 생성 입력에 들어가지 않는다.
연구 DB v23 `research_campaign_hypothesis_expansions`는 부모+개발근거+정책 revision별 GENERATED/EXHAUSTED/BLOCKED와
생성 수·이유를 남긴다. 자식 저장 뒤 원장 기록 전 종료돼도 같은 콘텐츠 ID를 다시 확인해 중복 없이 복구한다.
`presentation/research_dialog.py`의 `자동 가설 설정 / 현황`은 일시정지 campaign에서 Family, 한 파라미터,
쉼표로 구분한 허용값, seed, 회차·전체 상한을 policy revision으로 저장한다. 해당 Family 가설이 하나도 없으면
등록된 기준 실험의 정규화 전략과 사용자 설정 근거 snapshot으로 기준/첫 이웃을 멱등 bootstrap한다.
표는 가설 종류·변경·AVAILABLE/ENQUEUED·개발 근거 ID·revision별 확장 상태를 읽기 전용으로 표시한다.

CR4b 가설 실행 연결: 가설은 `research_scope_id=campaign:<campaign_id>`로 소유 범위를 고정한다.
`ResearchCampaignPolicy.auto_hypotheses`는 이제 자동 final과 별도로 허용되며, 명시적으로 켠 campaign만
일시정지 상태에서 같은 scope의 가설 묶음을 등록할 수 있다. 연구 DB v22의
`research_campaign_hypotheses`는 AVAILABLE→ENQUEUED 단방향 바인딩과 job ID를 보존한다.
campaign worker 하나만 매 반복에서 최대 한 가설을 기존 `ExperimentSpec`으로 바꾼다. 실행 spec은
가설의 완전한 전략 설정, Family/Factor, 단일 hypothesis ref를 쓰고 parameter grid·ablation·cost stress를
비운 뒤 기존 baseline/no-trade 두 trial을 실행한다. 마지막 ENQUEUED Family의 다음 등록 Family를 우선한다.
범위 소진·미등록·기준 실험 부재는 `WAITING_HYPOTHESIS` 사유로 남긴다. UI 설정은 CR4c 화면 단계다.

CR4a 자동 가설 기반: `application/research_hypotheses.py`는 호출자가 명시한 등록 Family·Factor·정수 허용값만 받아
정규화한 기준 전략과 한 번에 한 파라미터만 바꾼 후보를 고정 seed 순서로 만든다. seed는 순서만 바꾸고
가설 ID에는 들어가지 않으므로 같은 기준·근거·변경을 다시 생성해도 같은 콘텐츠 ID다.
각 변형은 기준 가설 하나를 부모로 갖고 1~200개 개발 근거 참조를 보존한다. 기존 trial generator의
baseline/no-trade와 실행기는 바꾸지 않았다. `research_repository` v21은 가설 문서와 부모 edge를 불변·원자 저장하며
부모가 없거나 배치 뒤에 나오면 전체를 롤백한다. CR4a에는 자동 campaign 연결·두 Family 순환·실패 반증·UI가 없다.
회귀는 `test_research_hypotheses.py`와 `test_research_hypothesis_repository.py`다.

CR3d3c 개발 피드백 노출: `final_holdout_exposure_request/v1`은 연구 DB, locked batch,
새 request ID, timezone-aware 노출 시각, 1~2000자 사용 근거를 1 MiB JSON에 고정한다.
`research_process --expose-final`은 원장의 window/batch/spec을 다시 대조하고 RUNNING 후보가 없을 때만 기존 v20 원자 API로 EXPOSED_DEVELOPMENT를 기록한다.
최종평가 화면은 검증된 결과와 사용자 근거가 있을 때 UUID child를 실행하며 DB를 직접 열지 않는다.
결과 envelope의 DB/window/batch/request/state를 대조하고 일치할 때만 되돌릴 수 없는 전환을 표시한다.
취소·오류에서는 최종평가 표를 유지한다. 회귀는 test_research_final_exposure_cli.py와 test_research_final_dialog.py다.
CR4b에서 이 원장을 기존 campaign/search 실행에 연결했다. NAS/API/계좌·주문은 바뀌지 않았다.

CR3d3b 최종평가 화면: research_dialog.FinalHoldoutDialog은 independent_final_holdout_request/v1을 선택해
4 MiB parser로 정규화한 뒤 UUID request/result/cancel 파일과 기존 research_process child만 소유한다.
UI는 연구 DB나 frozen source를 열지 않고 batch/window/candidate 순서/구현 hash/종료 코드가 일치한 결과만 표에 반영한다.
COMPLETED/CACHED는 재사용하고 NOT_STARTED만 같은 snapshot으로 이어서 실행한다. FAILED/CANCELLED는 자동 재시도하지 않는다.
선택한 terminal 후보에 사용자 reason과 새 request ID/owner를 넣은 단일 recovery snapshot을 만들어 CR3d2c 원장 경계로 보낸다.
창 닫기는 취소 파일만 쓰고 기다리지 않으며 앱 종료는 자신의 child/임시 파일만 정리한다.
회귀는 test_research_final_dialog.py이며 CR3d3c에서 EXPOSED_DEVELOPMENT 피드백 기록을 연결했다.

CR3d3a 최종평가 별도 프로세스: research_process의 independent_final_holdout_request/v1은 locked batch,
1~200개 fixed single_run candidate, 접근 request ID/시각, owner token, 선택적 candidate recovery 요청을 한 JSON에 고정한다.
loader는 4 MiB/정확한 필드/절대 해석 경로/현재 candidate scientific hash/시간대 접근 시각/recovery 대상을 검사하되 source bytes/DB는 열지 않는다.
--evaluate-final은 기존 child entry point에서 prepare→접근 원장→final 실행/명시 복구를 순서대로 호출하고 원자 result JSON을 쓴다.
result/cancel은 request·DB·frozen dataset·run artifacts와 충돌할 수 없다. cancel은 준비 전/후 기존 callback 경계에서 처리한다.
결과는 기존 independent_final_holdout_result/v1에 status/kind만 추가한다. 자동 retry/노출/새 후보 선택은 없다.
회귀는 test_research_final_cli.py이며 CR3d3b에서 이 불변 snapshot/child를 앱 화면에 연결했다.

CR3d2c 명시 복구: execute_final_holdout_evaluation의 recoveries는 locked candidate hash별 request_id/reason만 받는다.
기존 FAILED/CANCELLED이고 immutable output manifest가 없는 후보만 복구한다. COMPLETED/RUNNING/미실행/노출 창은 거절한다.
research_repository v20은 execution generation과 append-only recovery REQUESTED/CLAIMED 원장을 추가한다.
요청 기록만으로 상태를 바꾸지 않고 claim이 같은 run ID/code/input을 원자적으로 RUNNING으로 되돌리면서 generation을 올린다.
같은 request ID는 멱등이며 이미 CLAIMED인 요청은 재사용할 수 없다. 재복구는 새 request ID/reason이 필요하다.
부분 DB 행은 기존 immutable 검증으로 재사용하지만 manifest가 남아 있으면 불확실한 게시 상태로 보고 수동 검토를 요구한다.
RUNNING orphan 회수와 자동 retry는 후속이다. CLI는 CR3d3a, 명시 복구 UI는 CR3d3b가 이 계약을 호출한다. 회귀는 test_research_final_execution.py와 v19→v20 migration이다.

CR3d2b 최종 전용 실행: research_process.execute_final_holdout_evaluation은 CR3d2a의 PreparedFinalHoldoutEvaluation만 받는다.
현재 구현 hash/정렬된 후보 hash/단일 OOS 정책을 다시 확인하고 후보마다 새 ResourceGuard·PaperExecutionEngine·현금/상태로 실행한다.
run_research의 independent_final_holdout/v1은 final input validator와 repository 소유권을 모두 통과해야 한다.
일반 execute_research 호출, 개발 입력, claim 없는 final scope는 엔진 생성 전에 거절한다.
research_repository v19의 research_final_holdout_executions는 batch+candidate별 원자 claim과 run ID/owner/상태/결과 hash를 고정한다.
claim과 research_runs RUNNING 생성은 한 BEGIN IMMEDIATE다. 완료는 immutable 결과 파일 게시와 research run 완료 뒤 확정한다.
COMPLETED는 DB/report/output manifest를 대조한 뒤 CACHED만 반환한다. RUNNING은 BUSY, FAILED/CANCELLED는 자동 재시도하지 않는 terminal이다.
RUNNING 후보가 있으면 final window를 개발에 노출할 수 없다. terminal 뒤 명시 노출만 허용한다.
결과는 후보별 상태만 반환하며 후보 간 손익 합산·승자 재선택은 없다. CLI는 CR3d3a, UI는 CR3d3b에서 연결했다.

CR3d2a 최종 입력 준비: 기존 research_process.final_candidate_spec_hash가 등록된 고정 전략·실행/비용·세션·구현 hash를 고정한다.
prepare_final_holdout_evaluation은 같은 원본/연구 DB/실행 경로와 단일 OOS 정책, 후보 집합을 확인하고 full frozen bytes를 한 번 검증한다.
research_data_source.prepare_final_holdout_partition은 기존 시간 projection을 재사용하여 final warmup/active/as-of context만 복사한다.
전체 원본 ID/hash와 후보 집합은 outer batch/원장에만 보존하며 projected identity에 섞지 않는다. 일반 runner와 개발 projection은 final 입력을 거절한다.
최종 projection의 평가 정책은 batch canonical UTC 문서에서 복원하여 같은 시각의 KST/UTC 표기가 입력 ID를 바꾸지 않는다.
research_repository.record_final_holdout_access의 선택적 check_development_history는 BEGIN IMMEDIATE 안에서 과거 연구 기간/워밍업을 검사한 뒤 접근을 기록한다.
준비 경로는 이 검사를 항상 사용한다. 실제 입력 captured_range와 평가/warmup의 합집합을 검사해 연속 실행의 fold 사이/밖 자료도 보호한다.
실제 입력 범위가 불명확한 과거 footprint는 차단하며 기존 metadata API 기본값/DB v18은 유지한다.
자료 bytes는 trusted 준비 코드에서 먼저 검증하되 전략 실행/호출자에게 final input 반환은 원장 기록 이후다. final 엔진/claim은 CR3d2b, CLI는 CR3d3a, UI는 CR3d3b로 연결했다.
이는 로컬 연구 원장 범위 검사이며 시스템 밖 열람이나 기존 일반 실행 경로 전체를 자동 감시하지 않는다. 회귀는 test_research_final_preparation.py다.

CR3d1 최종 접근 원장: 기존 research_splits.FinalHoldoutBatchSpec은 하나의 OOS 평가 조건/동결 자료 ID·hash/후보 scientific hash 묶음을 고정한다.
KRX active window ID는 UTC 구간으로 계산하고 자료 revision/이름/세션 profile 변경으로 새 창을 만들지 않는다.
기존 research_repository v18은 최종 창과 append-only 접근/노출 event를 저장한다. BEGIN IMMEDIATE로 최초 batch를 고정하고 겹치는 창/변경 batch/노출 창의 최종 재사용을 막는다.
동일 batch의 새 기술 요청은 이력만 추가하며 동일 request ID는 멱등이다. 노출 상태는 되돌리지 않는다.
읽기 전용 기존 비교는 v17/v18/v19/v20에 호환된다. 테스트는 test_research_final_holdout_ledger.py와 기존 migration/비교 회귀다.
CR3d1 원장 기반과 CR3d2a 준비에 CR3d2b 후보별 소유권 실행, CR3d2c 명시 복구, CR3d3a CLI, CR3d3b 화면, CR3d3c 개발 노출을 연결했다. 새 관리 계층/실제 주문/NAS 변경은 없다.

CR3c2 그룹×시간 순차 실행: research_process.py의 independent_development_validation/v2는
한 stock_hash_partition 정책의 시간순 1~20개 개발 fold와 오름차순 2~20개 bucket을 받는다. 총 step은 200개 이하다.
DevelopmentValidationRequest는 immutable 공통 정책/중복·순서·개수/직렬화 계약을 확인한다. v1 요청/결과는 유지한다.
기존 source 한 번 검증·구간별 독립 엔진·원자 claim·완료 cache·취소/예산 재개를 그대로 재사용한다.
실행은 fold 먼저, bucket 다음 순서다. 모든 step에 fold/bucket/key와 상태를 보존한다.
group_comparisons는 각 bucket의 확인된 ID만 기존 독립 집계로 조회한다. 서로 다른 bucket 손익을 합산하지 않는다.
CR3c3의 기존 DevelopmentValidationDialog가 v1/v2 요청을 별도 프로세스로 실행/취소하고 불변 snapshot으로 명시 재개한다.
GUI는 DB/원본 자료를 열지 않고 250ms마다 bounded 결과 파일만 읽는다. 그룹 요청/전체 matrix 순서·ID·기간·그룹별 counts/범위를 검증한 뒤 두 표를 함께 갱신한다.
그룹별 실행 완료와 식별 결과의 표본 적격을 구별하며, 중앙값/최악 구간 MDD만 그룹별로 표시한다. 새 계층·DB/API는 없다.
research_process._write_result는 Windows polling 읽기 핸들의 rename 충돌(5/32/33)만 25ms/최대 10회 제한 재시도한다.
원자 교체·이전 snapshot/임시 파일 정리와 지속/무관 오류 전파를 유지한다. 대응 회귀는 test_research_result_publication.py다.

CR3c1 고정 종목 분할: research_splits.py의 DevelopmentSymbolPartitionSpec(stock_hash_partition/v1)은
버전/salt/정규 종목코드 SHA256으로 2~20개 bucket 중 하나를 배정한다. 신규 종목/순위/가격으로 기존 배정을 바꾸지 않는다.
DevelopmentPartitionSpec v3는 이 정책을 명시적으로 포함한다. 기존 v2 직렬화/기본 요청은 유지한다.
research_data_source.py는 기존 시간 projection의 전체 as-of 시장 context와 정책 identity를 보존하고 실행 전 종목코드를 검사한다.
run_research.py는 선택 bucket의 bar만 엔진/전략에 보내며 TOP20/peer history를 자르거나 재순위화하지 않는다.
종목코드 정규화 ranking.py를 명시 profile의 implementation hash에 포함한다. legacy profile 없는 고정 hash는 유지한다.
research_evaluation.py는 target 정책을 비교 조건에 넣고, v3 보고서의 종목별 체결 합/허용 종목을 검사한다.
명시 partition 경로를 재사용한다. CR3c2에서 그룹×시간 batch를 연결했다.
최종 종목 holdout 접근/자동 확장/새 자료 요청은 없다.

CR3a2 독립 개발 입력/실행: `research_splits.py`의 DevelopmentPartitionSpec(v2)는 TRAIN/VALIDATION
한 fold와 reset_state_and_cash_per_partition 정책만 선택한다. 기존 v1 split 의미는 바꾸지 않는다.
`research_data_source.py`는 선택 fold/warmup 원본과 직전 순위·테마 seed만 복사해 독립 runtime 입력을 만든다.
전체 source ID/watermark/children/quality/OOS count는 전달하지 않는다. 선택 데이터로 identity/count/hash를 다시 만든다.
`run_research.py`는 일치하는 평가·명시 profile·시간 경계를 검사하고 warmup에서 상태/주문/후보 생성을 막는다.
fold마다 기존 새 engine을 사용하고 내부 날짜는 이어간다. 경계 포지션은 fold 종료 시각에 censor한다.
CR3a3에서 JSON limited_search도 연결했다. research_process가 제출된 원본 ID/hash를 검증한 뒤
선택 입력 ID/hash와 단일 평가로 유효 search spec을 만든다. parser는 partition 정책을 context에 고정한다.
기존 trials/attempts/cards/cache 저장 경로를 재사용하며 제출 요청은 변경하지 않는다.
CR3a4a의 register_campaign_request는 별도 프로세스에서 원본 검증·projection 후 캠페인에 등록한다.
repository v17은 기존 request_json에 유효 spec, source_request_json에 불변 원본 spec을 따로 보존한다.
execute_campaign_cycle은 원본으로 요청을 재구성하고 execute_process_request가 유효 spec/ID를 등록 원장과 대조한다.
discovery도 같은 projection을 사용하며 개발 입력이 같은 자료는 acceptance만 추가하고 기존 job/원본을 변경하지 않는다.
CR3a4b의 research_dialog.py는 기존 AuxiliaryProcessManager로 --register-campaign을 실행한다.
등록 요청을 절대 경로로 고정하며 UUID별 request/result/cancel을 사용한다. paused 선택을 먼저 저장해 commit 뒤 결과 유실도 복원한다.
종료 코드와 결과 ID·원본 근거는 repository.load_campaign_job의 단일 키 조회로 대조한다. 전체 입력/전체 job 목록은 UI에서 읽지 않는다.
등록 중 다른 실행/설정 호출은 거부한다. 닫기는 취소 파일만 보내며 완료 뒤 소유 임시 파일만 정리한다.
CR3b1의 research_evaluation.py는 여러 독립 run의 조건 대조/구간별 불변 결과/손익 분포를 계산한다.
repository.load_independent_development_comparison은 명시 1~200개 run/report를 한 SELECT로 읽으며 DB를 쓰지 않는다.
중복/겹치는 기간/자료 revision은 독립 표본에서 제외하며 실패/표본 부족은 보존한다. 통합 계좌 수익률/MDD는 없다.
CR3b2의 research_process --compare-runs는 작은 명시 요청을 받아 read_only repository로 기존 v17만 조회한다.
IndependentComparisonDialog는 연구 화면의 결과 비교 버튼으로 열며 자기 UUID 파일/낮은 우선순위 프로세스를 소유한다.
UI DB/원본 입력 로드는 없다. 종료 코드/DB/외부·내부 run scope/version 대조 뒤 표를 적용한다. 오류/취소는 이전 표를 보존한다.
닫기는 숨김/비동기 취소, 앱 종료는 own cancel/stop/임시 파일 정리다. 캠페인 상태와 공유하지 않는다.
CR3b3 --validate-partitions는 고정 single_run/원본 ID/hash/시간순 2~20개 개발 fold를 받아 source를 한 번 읽는다.
scripts/run_research.research_run_identity는 실행/캐시 identity를 공유하며 새 scoped ID는 기본 단일 실행 ID와 다르다.
start_run(claim_independent=True)은 기존 v17 run 행을 원자 선점하고 완료 캐시/취소 재개/실패·BUSY 보존을 처리한다.
새 scope의 종료 상태는 batch만 확정한다. 초기 코드 hash를 고정하고 선점 전/runner 진입 시 대조한다.
구간별 상태/미시작·부분 실행과 확인된 ID만의 비교를 반환한다.
CR3b4의 DevelopmentValidationDialog는 여러 구간 순차 검증 버튼으로 열며 별도 실행 수명/UUID 파일/낮은 우선순위 child를 소유한다.
DevelopmentValidationRequest.to_dict는 파싱된 요청의 절대 경로 snapshot을 만들며 GUI에서 입력/DB를 읽지 않는다.
250ms 진행 결과 확인은 모든 구간/기간/역할/run ID/구현 hash/전체 상태와 종료 코드를 대조한다.
같은 요청 이어서 실행은 보관된 snapshot을 재사용한다. 자동 반복/강제 종료 orphan 복구는 후속이며 기존 비교 창은 읽기 전용이다.
자동 final/새 날짜 확장은 아직 후속이다.
품질은 선택 자료로 계산하며 순위 seed/늦은 backfill을 warmup 분봉 근거로 사용하지 않는다. 전체 coverage 보증은 아니다.
연구 DB v17만 추가했다. NAS/API/주문 경로 변경 없음.

CR3a1 후보 선택 근거 격리: `research_evaluation.py`의 불변 `DevelopmentEvidence`는
TRAIN/VALIDATION의 상태·사유·요약 지표·fold 참조만 복사한다. 전체 보고서/OOS/raw 결과는 담지 않는다.
`research_search.py`의 기존 outcome 변환은 이 근거만 읽어 후보 적격·선택 입력을 만든다.
전체 보고서의 상태/사유 또는 OOS 열람 여부가 후보 선택 상태를 바꾸지 않는다. 전체 보고서는 그대로 보존한다.
기존 fold 요약 산술/DB v16/API를 유지한다. 명시 독립 입력/실행은 위 CR3a2를 따른다.

CR2c3c2 완성 자료 등록 복구: `research_process.py`의 기존 source discovery가 완성 폴더를 먼저
검증·등록한 뒤 NAS 준비를 시도한다. 용량 부족/서버 접속 실패가 이미 완성된 자료의 등록을 막지 않는다.
등록 후 활성 job 수를 갱신하고 대기열이 차면 연결 설정 조회/다운로드를 건너뛴다.
NAS 준비 후에는 반환된 완성 경로만 등록하며 전체 폴더를 다시 읽지 않는다. signature 확인은 등록 뒤다.
DB v16의 기존 acceptance/job/준비 원장을 재사용한다. 완료/미등록/다른 범위 완성 폴더도 삭제하지 않는다.
CR3 개발·최종 검증 구간 분리는 위 CR3a1부터 진행한다. NAS/API/주문 변경 없음.

CR2c3c1 미완성 임시 자료 정리: `research_repository.py` v16의 staging_cleanups는 작업별 경로/
marker hash/READY·DELETED·MISSING·PROTECTED·FAILED/재시도를 기록한다. 기존 worker가 NAS 준비 전에 호출한다.
24시간 이상 지난 현재 source root의 미완성 staging만 검사하며 op ID 없는 기존 자료는 보존한다.
`research_storage.py`는 최대 64 entry의 알려진 파일만 검사·개별 unlink/rmdir한다. 재귀 rmtree/새 Manager 없음.
완성 manifest/연구 참조/알 수 없는 파일·링크는 보호한다. caller heartbeat/취소는 DB 쓰기 fence 밖에서만 호출한다.
삭제 직전 DB owner/의도/모든 캠페인 참조를 재확인하고 약 0.25초 checkpoint 예산의 fence에서 처리한다.
실패는 별도 정리 원장 backoff이며 source/연구 실패 횟수와 분리한다. marker를 마지막에 지워 부분 중단을 복구한다.
완성 자료 등록 복구/보호 보관은 위 CR2c3c2를 따른다. NAS/API/주문 변경 없음.

CR2c3b PC 폴더 용량 상한/준비 원장: `research_repository.py` v15가 source의 storage_cap_bytes
기본 0과 storage_operations를 소유한다. 기존 worker lease로 하나의 PC NAS 자료 준비만 허용한다.
stale/120초 만료 작업은 ABANDONED 기록만 남기고 삭제하지 않는다. download/검증 중 DB 쓰기 잠금은 잡지 않는다.
export writer는 자료/테마/일별·bundle manifest/소유 표시의 byte 예산을 저장 전에 차감한다.
완성 rename만 짧은 DB owner/의도 fence 안에서 수행한다. 임시·완성 표시는 operation ID로 원장에 연결한다.
`research_dialog.py` 새 자료 폴더에 용량 상한 GB(1024^3, 0 무제한)를 추가했다.
용량 부족/다른 준비 작업은 WAITING_STORAGE이며 실패 격리로 올리지 않는다. 기존 입력 실행은 계속 가능하다.
다음 CR2c3c는 참조 보호 보관/임시·orphan 정리다. 아직 실제 삭제 없음. NAS/API/주문 변경 없음.

CR2c3a PC 연구 보관 진단: `infrastructure/research_storage.py`가 자동 생성 sidecar 표시와
제한된 읽기 전용 용량 목록을 소유한다. export writer는 새 임시/완성 자료에만 표시를 쓴다.
Repository는 모든 캠페인의 jobs/acceptances 입력 경로를 보호 참조로 조회한다(완료 포함).
기존 worker는 NAS source 처리 뒤 최대 1000 entry 진단을 결과에 싣고 GUI는 파일을 읽지 않는다.
기존 무표시 폴더/링크/손상 자료는 보호한다. 목록은 삭제 권한이 아니며 실제 삭제는 없다.
다음 CR2c3b: cap·보관 원장·외부 참조/lease 재확인 후 정리. DB v14/API/NAS 변경 없음.

CR2c2b NAS 입력 준비: 기존 `scripts/export_research_dataset.py`의 export writer를 streaming/checkpoint로 보완하고
같은 범위의 일별 snapshot probe → 고정 watermark pagination → 임시 검증 → 새 완성 폴더 게시를 연결했다.
bundle은 기존 일별 경계와 reader를 유지한다. `research_data_source.py`는 선택적 initial page/byte cap/checkpoint를 받는다.
`research_process.py`의 기존 worker만 기존 PC DataSourceConfig 경로에서 NAS 인증을 읽는다. 직접 키움 fallback 없음.
`research_repository.py` v14는 source에 NAS opt-in/config 경로/remote signature만 추가한다. 토큰은 복사하지 않는다.
백로그가 꽉 차면 NAS 다운로드를 건너뛰며 source 설정과 signature ack는 기존 worker/의도 경계를 따른다.
`research_dialog.py`의 새 자료 폴더에 NAS 자동 준비 체크를 추가했다. UI는 네트워크를 요청하지 않는다.
회귀는 `test_research_campaign_nas`, campaign dialog/input/bundle/data source 및 기존 연구 회귀다.
다음 CR2c3는 디스크 cap/참조 보호/보관이다. 새 날짜 확장은 CR3다. 서버/API/Manager 변경 없음.

CR2c2a 같은 범위 새 입력: `research_data_source.py`의 scope/evidence fingerprint는 현재 등록 Family의
ranking/top20_membership/KRX minute_bar와 테마 근거를 비교하며 watermark/전송 ordinal은 근거로 세지 않는다.
`research_repository.py` v13의 input source/acceptance 원장은 worker fence·중복 확인·job/budget 등록을 원자 처리한다.
`research_process.py`는 기존 worker에서 명시 상위 폴더의 직접 하위 완성 export/bundle만 60초마다 확인한다.
기존 RSS/CPU guard와 한 source당 최대 30초/1000 자료·10000 directory entry 제한을 적용한다.
`research_dialog.py`의 새 자료 폴더 설정은 작은 DB 설정만 저장하고 실제 자료 읽기는 worker가 맡는다.
회귀는 `test_research_campaign_inputs`, campaign dialog/worker 및 기존 연구 회귀다.
NAS 자동 export 준비는 위 CR2c2b, 날짜 확장은 CR3, 디스크 보관/참조 보호는 후속이다. 새 서버/API/Manager 없음.

CR2c1 작업자 복구: 기존 `research_repository.py`의 연구 v12가 worker 현재 상태와 실행 attempt 이력을 소유한다.
`research_process.py`가 owner/generation/30초 lease를 갱신하고 cycle 실행·결과 commit에 worker fence를 전달한다.
`research_queue.py`의 순수 지연 계산과 `research_dialog.py`의 single-shot 타이머가 저장된 backoff를 따른다.
앱 재시작은 실패 이력을 초기화하지 않는다. 명시 시작/재개만 격리를 해제하며 숨김/일시정지는 실패로 세지 않는다.
새 Manager/서버/API/주문 경로는 없다. 회귀는 `test_research_campaign_worker`, campaign dialog/execution 및 기존 연구 회귀다.
같은 범위 입력 자동 등록은 위 CR2c2a이며 NAS 자료 자동 준비/날짜 확장/디스크 보관은 남는다.

CR2b2 예산 확대: `research_repository.py`의 로컬 연구 v11은 job별 불변 운영 예산 revision과 cycle의 예산 revision을 추가한다.
원래 request_json/science identity는 유지하고 현재 예산을 겹쳐 실행 spec을 구성한다.
일시정지/worker 종료/expected_revision 확인 뒤 예산을 바꾸며 global/campaign backlog 실패는 트랜잭션 rollback한다.
기존 `start_search_job`이 유효한 캠페인에 한해 완료 job 재개와 search lease 취득을 원자 처리한다.
`research_process.py`의 일반 유한 completed cache는 유지하며 캠페인만 증가분을 실행한다.
기존 연구 창의 `실험 예산 / 재시도`가 저장된 실험 선택·예산 편집·명시 retry를 연결한다.
회귀는 `test_research_campaign_budget`, 기존 campaign execution/dialog/queue/repository/process/search다.
worker crash 격리는 위 CR2c1이며 새 자료 자동 선택·등록과 보관은 후속이다. NAS/API/주문 경계 변경은 없다.

CR2b1 실행 연결: `research_process.py`의 기존 유한 실행기를 캠페인 cycle worker가 재사용한다.
DB의 동결 spec/context/input_path로 요청을 재구성하고 실제 데이터·코드 hash를 검사한다.
`research_repository.py`의 결과 commit은 선택적 campaign fence와 기존 search fence를 같은 트랜잭션에서 확인한다.
`presentation/research_dialog.py`는 캠페인 등록/시작/일시정지/중지와 앱 시작 복원을 연결한다.
선택 파일은 PC별 경로 index일 뿐이며 실행 의도/설정의 원본은 연구 DB다(현재 v14). 새 서버/API/Manager는 없다.
회귀는 `test_research_campaign_execution`, `test_research_campaign_dialog`와 기존 연구 회귀다.
운영 예산 확대는 위 CR2b2, worker crash 격리는 CR2c1이며 관련 새 자료 자동 등록/디스크 보관은 후속이다.

CR2a 캠페인 원장: 기존 `application/research_queue.py`의 `ResearchCampaignPolicy`가 retry/backlog 정책을 고정한다.
기존 `infrastructure/persistence/research_repository.py`의 로컬 v10이 설정 revision/desired state/job/cycle을 저장한다.
기존 experiment/job 저장 SQL을 같은 파일의 트랜잭션 helper로 재사용해 캠페인 등록과 job 생성을 원자 처리한다.
cycle claim/sequence/lease generation은 단일 쓰기 경계이며 실제 search job 완료 증거로만 캠페인 완료를 확정한다.
회귀는 `test_research_campaign`, 기존 repository/queue/process/search다. 새 실행 계층/서버/API는 없다.
기존 유한 GUI 자동 재개는 호환 유지하며 별도 캠페인 제어는 위 CR2b1 경로다.

CR1a 다기간 준비: `scripts/export_research_dataset.py`가 명시 KST 날짜별 기존 24시간 export를 생성/재사용한다.
`infrastructure/research_data_source.py`의 별도 FrozenResearchBundle과 bundle reader는 일별 manifest/파일 hash,
입력 계약/시간 순서/중복 revision·테마 충돌/잘린 sidecar를 검증한다. 파일·일별 ordinal은 유지한다.
CR1b 실행 연결: `load_research_input`은 검증한 bundle의 revision을 UTC 가용시각/accepted_sequence/ID 순으로
중복 제거하며 원본 ordinal은 유지한다. `scripts/run_research.py`와 `research_process.py`가 같은 진입점을 쓴다.
bundle은 일별 계약과 일치하는 명시 session_profile을 요구한다. 하루 묶음은 원래 데이터/identity를 그대로 쓴다.
다기간 runner는 현재 ingest까지의 자료만 replay하고 한 PaperExecutionEngine을 끝까지 유지한다.
CR1b 자원: `application/research_resources.py`는 운영 예산과 실제 RSS/CPU batch 경계를 소유한다.
파일 크기 검사는 기존 data_source의 `research_input_encoded_bytes`에서 수행하고 로딩/실행 checkpoint를 공유한다.
`ResearchReplayCursor`는 bundle의 ingest를 한 번씩 변환해 최신 revision을 유지하며 전체 재검증을 줄인다.
앱 연구 프로세스/CLI는 기본 512MiB·CPU 목표 50%를 적용한다. 검색 자원 중단은 기존 INTERRUPTED attempt로 남긴다.
1/5/20일 작은 fixture의 실행과 20종목·390분 규모 입력 preflight를 계측했다. 실제 NAS 전체기간 성능은 V1 검증이다.
새 DB/API/서버 build/Manager는 없다. 회귀는 `test_research_bundle_execution`, `test_research_bundle`, 기존 reader/replay/process/search다.

R7 배포 준비는 `deploy/synology`의 기존 Dockerfile/compose와 `.dockerignore`를 사용한다.
Docker context에서 NAS 전용 백업/실제 env/secret 저장소를 제외하며 build ID 세 곳을 함께 관리한다.
경로 검증/Git 작업본 manifest/원본 코드 백업/hash 확인 뒤 소스를 동기화하고 데이터/인증 폴더는 제외한다.
실제 이미지/health 일치와 운영 검증은 `scripts/check_nas_operational.py`,
`scripts/check_postgres_integration.py` 및 R7 보고서/NAS_DEPLOYMENT_PENDING을 따른다. 새 앱 계층은 없다.

R6c1 PC 실전 입력: `presentation/api_settings_dialog.py`의 NAS 실전 관리 버튼은 공급자별 기존 client 캐시를 쓴다.
`infrastructure/central_credentials_client.py`의 공통 계좌 생성/prepare와 scope 검증 GET/PUT이 real/mock 환경을 구분한다.
`presentation/nas_credentials_dialog.py`는 기존 단건 worker로 실전 키 확인/적용·조회 ON/OFF와
연결 해제된 계좌의 목록 삭제를 실행한다. 삭제는 `credential_runtime.py`와 `database.py`의 기존
profile lifecycle을 archived로 전환해 계좌 신원·binding·매매 이력을 보존한다. 실전 화면은
모의주문 토글을 숨기고 실제 `monitor_status.realtime` 승인 상태를 별도로 표시한다.
기존 mock 이름의 client 메서드는 호환용 진입점으로만 남고 중복 구현하지 않는다. 새 계층/DB/서버 API 없음.
회귀: `test_central_credentials_client`, `test_nas_credentials_dialog`, `test_nas_credentials_ui_integration` 및 기존 설정/계좌 테스트.
R6c2 계획된 재연결: 기존 collector가 유한 30초 deadline·상태 통지·REG 종료·0B 관측 간격을 소유한다.
`central_server/app.py`와 `contracts.py`는 health/capability/WS 상태와 paused TR의 명시 대기 응답을 맡는다.
`remote_client.py`의 대기 오류 분류/공통 유한 시간 검증과 `central_realtime_worker.py`의
generation별 로컬 deadline/계획 오류 유예/정상 장애 복귀가 PC를 연결한다. 장애전환 뒤 직접
실시간 worker는 중앙 재접속 시도와 병행해 계속 수신하며, 현재 요청 종목 전체의 `connection_opened`
상류 승인 뒤에만 종료한다. `central_ready`는 서버 접속과 요청 접수일 뿐 복귀 근거가 아니다.
`main_window.py`의 기존 상태 slot만 변경해 표를 유지하고 수신 재개를 표시한다.
REST failover 자체는 수정하지 않으며 일반 API 대기는 transport 오류에 포함되지 않는 기존 정책을 유지한다.
회귀는 `test_planned_reconnect`와 기존 collector/worker/REST/서버/계좌/역할/PC 설정 테스트다.
새 계층/DB/주문 경로는 없다. R7 실제 배포/공백 관측은 후속이다.

수정 요청을 받으면 먼저 이 표에서 범위를 좁힌 뒤 해당 파일과 연결 테스트만 읽는다. `main_window.py`나 `journal_process.py` 전체를 먼저 읽지 않는다.

## 실행·구성

| 기능 | 실제 담당 파일 | 먼저 볼 테스트 |
| --- | --- | --- |
| 앱 조립, 프로세스 시작, 데이터 모드 선택 | `src/kiwoom_monitor/bootstrap.py` | `test_kiwoom_client_factory.py`, `test_news_process.py` |
| 사용자 데이터 경로 | `infrastructure/app_paths.py` | 관련 저장소 테스트 |
| 로컬/NAS 모드 설정 | `infrastructure/central_server_config.py` | `test_central_server_config.py`, `test_api_settings_dialog.py` |
| 실행 중 API 조회·실시간 경로 교체와 늦은 이전 응답 차단 | `bootstrap.py`의 `build_api_runtime`, `presentation/main_window.py`의 `_restart_for_api_settings` | `test_main_window.py`, `test_planned_reconnect.py` |
| Google Drive 동기화·업데이트 확인/다운로드 worker | 전송과 NAS와 동일한 공통설정 제외 정책은 `infrastructure/persistence/google_drive_sync.py`, `infrastructure/central_settings_sync.py`; 설정 묶음은 `settings_backup.py`, 전체 테마 프로필 직렬화 원본은 `theme_backup.py`; worker 생성·연속 실행·결과 수명은 `presentation/google_drive_worker_controller.py`; 업데이트 확인·다운로드는 `presentation/update_worker_controller.py` | `test_settings_backup.py`, `test_theme_backup.py`, `test_google_drive_sync.py`, `test_google_drive_worker_controller.py`, `test_update_worker_controller.py` |
| 뉴스·매매일지 보조 프로세스 명령 구성·요청 번호·수신 중복 차단·숨김 실행·생존 확인·종료 단계 | `presentation/process_control.py` | `test_process_control.py` |
| 키움 API·NAS 연결 설정 UI/자원 확인 worker | `presentation/api_settings_dialog.py` | `test_api_settings_dialog.py` |
| NAS 모의계좌 및 뉴스/AI 공급자 키 관리 | 기존 HTTPS 경계는 `infrastructure/central_credentials_client.py`; 공급자 고정 옵션·입력/operation 검사와 메모리 요청 수명을 소유한다. 기존 UI는 `presentation/nas_credentials_dialog.py`; global 계좌 UI/I/O 제외·명시 적용·worker 수명을 소유한다. 메뉴와 공급자/NAS별 client 재사용은 `presentation/api_settings_dialog.py` | `test_global_credentials_ui.py`, `test_central_credentials_client.py`, `test_nas_credentials_dialog.py`, `test_nas_credentials_ui_integration.py` |
| 기본 설정 탭·저장·초기화 UI | `presentation/settings_dialog.py` | `test_settings_api_hub.py` |
| 메인 창의 종목명 변경 확인·클릭 라벨·신고가 알림 설정·셀 표시 delegate | `presentation/main_window_components.py` | `test_main_window.py` |
| 메인 표 거래대금·시가총액 단위, 상위 테마 HTML, 행 배경 우선순위, 순위 강조 시간, 소수점 제한과 등락률 색상 | `presentation/main_table_formatting.py` | `test_main_table_formatting.py` |
| 메인 표 반응형 행 높이·열 비율·내용 맞춤 창 크기·저장 위치 해석 | 순수 계산은 `presentation/main_window_layout.py`; 열 표시·순서·폭 저장/복원과 자동 맞춤 상태는 `presentation/main_table_column_controller.py`; Qt 이벤트와 메뉴 호출은 `presentation/main_window.py` | `test_main_window_layout.py`, `test_main_table_column_controller.py`, `test_column_settings_repository.py`, 메인 화면 회귀 테스트 |
| 앱 이름·버전·저작권·투자 유의문 | `presentation/app_metadata.py` | 메인 화면·설정·업데이트 테스트 |
| 로컬 중앙 서버 자식 프로세스 | `infrastructure/central_server_process.py` | `test_central_server_process.py` |

## TR Scheduler와 키움 API

NAS 자동 시장자료 소유권은 `central_server/autonomous_top20.py`가 담당한다. 순위 1~5, 신규 편입 및 실제 매수 체결 종목의 기본정보·NXT·분봉·외국인/기관·역사적 신고가, 실시간 프로그램 스냅샷, 장후 프로그램·시장지수 보완이 이 경계에 있다. `central_server/realtime_collector.py`는 실제 매수 체결 종목을 계좌정보 없이 `account_entry_symbols_daily`에 기록한다. 이 종목은 수집 구독에만 더하고 TOP20 membership·지수에는 넣지 않는다. 앱의 저장 자료 어댑터는 `infrastructure/kiwoom_rest/remote_client.py`, 값 변환은 각 `application/*_service.py`에 유지한다.
매매일지의 오늘+직전 실제 거래일 분봉은 `RemoteKiwoomRestClient.load_stored_recent_minute_bars` → `GET /api/v1/market/recent-minute-bars` → 중앙 `load_minute_bars` 경로를 사용한다. `MinuteChartService.load_two_trading_days`는 중앙 연결이 정상인 동안 빈 결과도 그대로 반환하며 `ka10080`을 요청하지 않는다.

| 계층 | 실제 담당 파일 |
| --- | --- |
| 직접 REST 호출 제한·토큰·재시도 | `infrastructure/kiwoom_rest/client.py`, `settings.py`, `local_config.py` |
| 중앙 단일 큐·우선순위·중복 요청 병합 | `central_server/rest_broker.py` |
| 로컬/원격/장애전환/병행검증 선택 | `infrastructure/kiwoom_rest/client_factory.py`, `remote_client.py`, `failover_client.py`, `validation_client.py` |
| 순위 TR | `application/ranking_service.py`, `infrastructure/kiwoom_rest/ranking_worker.py` |
| 분봉·일봉·기본정보·신고가 TR | `application/minute_chart_service.py`, `daily_high_service.py`, `historical_high_service.py`, `stock_fundamentals_service.py`; 대응 `*_worker.py` |
| 화면 신고가 출처 우선순위·ka10001 보완 시 수정주가 보존 | `application/high_price_policy.py` | `test_high_price_policy.py`, `test_main_window.py` |
| 체결·비용·수급 TR | `application/trade_history_service.py`, `trade_cost_service.py`, `investor_flow_service.py`, `program_trade_service.py` |

순위 우선순위 수정은 `rest_broker.py`, `main_window.py`의 `_refresh_rankings`/후속 작업 예약부, 그리고 `test_central_rest_broker.py`, `test_main_window.py`만 먼저 확인한다.

## 실시간 종목조회순위

- NAS 독립 순위·편입 이력·TOP20 지수·장후 차트 보완 및 키움 실시간 원본 단절 시 분 행 중단: `central_server/autonomous_top20.py`. 순위는 시작 직후 현재 회차를 먼저 수집한 뒤 24시간 30초마다 수집한다. 응답 `dt/tm`이 직전 회차이거나 최신 20행 안에 종목코드·종목명이 빈 자리가 있으면 0.25초 2회→0.5초 2회→이후 0.75초로 제한 재조회하고 미완성 회차는 저장하지 않는다. 앱의 NAS DB 재확인은 `application/ranking_service.py`가 이전 회차와 부분 회차 모두에 같은 간격을 사용하며 키움 TR로 우회하지 않는다. NAS 장애로 PC 키움 API에 전환된 경우에도 같은 간격으로 실제 `ka00198`을 다시 요청하며, 최신 회차를 받지 못하면 직전 회차를 새 결과로 적용하지 않는다. 최신 완성 회차 미수신 시 기존 표를 유지하고 상태를 대기로 구분한다. 순위 조회·저장은 카탈로그·NXT·계좌 편입·기본정보 보완과 분리하며 TOP20 실시간 거래대금 수집과 KRX/NXT 구독은 시장 관측시간에만 유지한다.
- NAS TOP20 KOSPI/KOSDAQ 분류 원본: `infrastructure/krx/stock_catalog.py`; 중앙 보존 컬렉션 `stock_catalog`
- TOP20 지수 재시작 복구 outbox: `central_server/persistent_outbox.py`
- 서버: `central_server/realtime_collector.py`, `realtime_hub.py`, `market_ingest.py` (`ka10016/ka10001/ka10100`의 영속 저장 포함). 중앙 수집기는 단일 키움 WebSocket에서 `0B`, `0w`, `0g`를 타입별 그룹으로 관리한다. 전체 TOP20·조건 코호트 0B를 먼저 보장하고, `0g` 상한가·하한가·기준가는 `stock_price_references` 최신 문서와 실시간 이벤트로 함께 보존한다. 일반 NXT 코호트는 SOR 통합으로 받고, 남는 0B venue 상세와 0w는 TOP20, 실제 매수 편입 종목, 나머지 앱 요청 순으로 배정한다. `_AL`은 SOR 원본으로 보존하고 실제 승인 item 기준으로 누적 baseline과 늦은 source 틱을 관리한다. `market_ingest.py`는 분봉 보완 때 SOR 실시간 거래대금과 KRX/NXT 조회 추정값의 차이를 `minute_trade_value_comparisons`에 기록한다. `realtime_hub.py`가 일반 체결 희망 코드와 우선순위 코드를 분리하며, REG/REMOVE는 0.25초 간격으로 보낸다.
- API: `central_server/app.py`의 `/api/v1/realtime`, `/api/v1/market/snapshots/ranking`, `/api/v1/market/trade-value-comparisons`. 거래대금 비교 요약은 KRX+NXT가 모두 보완된 분만 주 통계에 포함하고 단일 거래소 중간값은 부분 건수로 분리한다.
- 클라이언트: `infrastructure/kiwoom_rest/central_realtime_worker.py`, `realtime_worker.py`, `realtime.py`. 0B 체결과 0g 가격 기준을 각각 기존 Qt 신호로 전달한다. 키움 `REAL data=null`은 모든 실시간 parser와 중앙 시장 관측에서 빈 batch로 처리해 정상 NAS 연결을 페일오버 사유로 만들지 않는다. NAS 장애전환의 직접 WebSocket은 `bootstrap.py`에서 직접 REST fallback과 같은 로컬 client를 사용한다. 중앙 worker 내부에서 만들어진 로컬 QThread 신호는 이벤트 루프 없는 중앙 작업 스레드에 queue하지 않고 즉시 중계한 뒤 최종 GUI 수신 객체의 thread로 전달한다. `realtime_worker.py`는 `REG` 승인 뒤에만 연결·구독 완료 신호를 보낸다.
- 계산: `application/ranking_service.py`, `domain/ranking.py`
- UI/예약: `presentation/main_window.py`의 `MainWindow` 순위 갱신·구독 메서드
- 순위 QThread 생성·신호·현재 worker 수명: `presentation/ranking_worker_controller.py`
- 0B WebSocket QThread 생성·9개 신호 전달·구독 갱신·중지·factory 교체: `presentation/realtime_worker_controller.py`
- 분봉 보완 QThread 생성·신호 전달·강제 보완 문맥과 다음 단계 완료 연결: `presentation/minute_history_worker_controller.py`
- 일봉 기간 신고가 QThread 생성·형식 검증·수신/실패/완료 문맥 전달: `presentation/daily_high_worker_controller.py`
- 종목 기본정보 QThread 생성·형식 검증·수신/실패/완료 문맥 전달: `presentation/fundamentals_worker_controller.py`
- 역사적 신고가 QThread 생성·형식 검증·수신/실패/완료 신호 전달: `presentation/historical_high_worker_controller.py`
- NXT 거래 가능 여부 QThread 생성·형식 검증·수신/실패/완료 신호 전달: `presentation/nxt_eligibility_worker_controller.py`
- 신고가 목록 갱신 QThread 생성·형식 검증·성공/실패/종료 신호 전달: `presentation/new_high_worker_controller.py`
- KRX 상장종목 카탈로그 QThread 생성·형식 검증·자동/수동/후속 실행별 신호 연결: `presentation/krx_stock_catalog_worker_controller.py`
- 다음 조회·저우선순위 양보 시각, 부분 응답 정책, 순위 변경·동일 응답 계산: `application/ranking_schedule.py`
- 밀린 조회 1회 병합, 부분 응답 재시도 횟수, 모달 중 최신 응답 보류, 마지막 순위 상태 소유: `application/ranking_execution.py`
- 0B/0w 구독의 현재 종목·venue·정책 서명 비교, 동일 연결 UPDATE, 장 종료 및 재시작 판단: `application/realtime_subscription.py`
- 시행일별 KRX/NXT 세션·phase·지원 상태와 연구 세션 gate, venue별 0B 구독 대상·세션 경계·보완조회 휴지·NAS 장애전환 중 TOP20 수집 중단 판단: `application/market_session_schedule.py`. `RealtimeSubscriptionTarget`은 시행 후 KRX 애프터에 현재 요청 종목을 사용하고 NXT 적격 목록은 `_NX`에만 적용한다. NAS 연결 수명과 15:30 정규장/20:00 전체일 종료 분리는 `central_server/realtime_collector.py`, `market_events.py`가 담당하며, PC 직접·fallback의 정상 수신 중 경계 refresh는 `infrastructure/kiwoom_rest/realtime_worker.py`가 담당한다. 기존 `krx-regular/v1` reader와 D4 shadow 연결은 `application/research_replay.py`, `central_server/candidate_monitor.py`가 담당한다.
- 장 마감 분봉·일봉 확정 대상·완료 봉 시각·최대 2회/5분 간격 재시도·미확정 판정: `application/market_data_finalization.py`
- 순위 후 분봉·일봉·기본정보·신고가/NXT 보완 대상과 worker 완료 순서 조정: `application/secondary_data_schedule.py`
- DB: 중앙 `central_dataset_snapshots`, 로컬 `stocks`와 화면 메모리 상태
- 테스트: `test_autonomous_top20.py`, `test_ranking_service.py`, `test_ranking_schedule.py`, `test_ranking_execution.py`, `test_ranking_worker_controller.py`, `test_realtime_worker_controller.py`, `test_minute_history_worker_controller.py`, `test_daily_high_worker_controller.py`, `test_fundamentals_worker_controller.py`, `test_historical_high_worker_controller.py`, `test_nxt_eligibility_worker_controller.py`, `test_new_high_worker_controller.py`, `test_krx_stock_catalog_worker_controller.py`, `test_market_session_schedule.py`, `test_realtime_subscription.py`, `test_market_data_finalization.py`, `test_secondary_data_schedule.py`, `test_realtime*.py`, `test_central_realtime_*.py`, `test_main_window.py`

## 거래대금과 TOP20 지수

- 실시간 종목 분봉 계산: `application/minute_trade_value.py`
- 30초 TOP20 코호트 예약·교체, 구간 기준값, 1분 마감과 종료 시 부분 기록: `application/top20_trade_value_collector.py`
- 표시 기간 계산: `application/trade_strength.py`
- 로컬 저장/시장지수/TOP20 통계: `infrastructure/persistence/minute_bar_repository.py` (매매일지 시장지수 보완 결과의 분봉·일봉 원자적 일괄 저장, 15:30 종가 단일가 및 `ka20006` 확정 분모 포함)
- 중앙 실시간 봉: `central_server/minute_bars.py`의 1분/1초 집계, `realtime_collector.py`의 기존 0B 연결과 저장 재시도, `market_ingest.py`, `database.py`
- 중앙 TOP20 통계 집계/API: `central_server/database.py`, `central_server/app.py`의 `/api/v1/market/top20-statistics`; 앱 읽기 어댑터는 `infrastructure/kiwoom_rest/remote_client.py`
- TOP20 차트·전용 창·NAS 비동기 읽기·DB 보완 worker: `presentation/top20_trade_value.py`; 보완 worker 생성·저우선순위 실행·신호 수명은 `presentation/top20_market_repair_worker_controller.py`
- TOP20 데이터 공급·수집 결과 저장·통계 창 호출 등 메인 화면 연결: `presentation/main_window.py`의 관련 `MainWindow` 메서드. NAS 연결은 중앙 원본, PC 직접 연결은 로컬 DB를 선택한다.
- 테스트: `test_minute_trade_value.py`, `test_second_trade_aggregation.py`, `test_second_trade_storage.py`, `test_top20_trade_value_collector.py`, `test_top20_market_repair_worker_controller.py`, `test_minute_bar_repository.py`, `test_trade_strength.py`, `test_central_minute_bars.py`, `test_central_market_ingest.py`, `test_main_window.py`의 화면 모드 독립 수집 회귀

## 과거 시장 재현과 시뮬레이션 데이터

- 보존 기준과 현재 수집 공백: `HISTORICAL_DATA_CONTRACT.md`
- 봉·시장 상태·후보군의 거래소·단위·실제/추정·완결 상태·기준시각/가용시각 공통 계약과 기존 문자열 어댑터: `domain/market_data_contract.py`
- 로컬·중앙 공통 메타데이터 행 변환: `infrastructure/market_data_metadata_codec.py`; 로컬 저장은 `persistence/market_data_metadata_*`, 로컬 분봉·일봉 의미 생성은 `persistence/local_bar_observations.py`, 중앙 저장은 `central_server/central_schema.py`, `central_server/database.py`
- 시각 범위별 가용 관측 수·완결성·고정주기 결측 구간 판정: `application/market_data_coverage.py`; 중앙 조회는 `central_server/database.py`와 `central_server/app.py`의 `/api/v1/market/coverage`
- 중앙 순위·TOP20·시장 상태·조회/실시간 분봉·일봉의 관측 의미 생성과 KST 시각 정규화: `central_server/market_observations.py`; 생산자 연결은 `market_ingest.py`, `autonomous_top20.py`, `realtime_collector.py`
- 시점별 순위 원본: `central_server/market_ingest.py`, `central_server/autonomous_top20.py`; 최신 projection은 `central_dataset_snapshots`, D1 불변 원장은 `central_observation_revisions`. 얇은 revision/hash 계약은 `domain/research_contract.py`, 저장·조회는 `central_server/database.py`, 회귀는 `test_research_observation_history.py`
- D2 고정 추출: `central_server/database.py`의 export manifest/member 트랜잭션과 `app.py`의 `/api/v1/research/observations`; 클라이언트 page 검증·결합은 `infrastructure/research_data_source.py`, 전략 없는 TOP20 순서 재생은 `application/research_replay.py`, 파일 출력은 `scripts/export_research_dataset.py`. 회귀는 `test_research_data_source.py`, `test_research_replay.py`, 서버 API 테스트
- D3a KRX 분봉 재생 입력: `central_server/minute_bars.py`가 delta operation ID와 시간상 마감 후보를 만들고, `central_server/database.py`가 정확히 한 번 누적·전체 봉 revision·처리 ID를 원자 저장한다. `application/research_replay.py`는 가상시각까지 가용한 최신 KRX 마감봉만 읽으며 회귀는 `test_minute_bar_revisions.py`
- D3b 첫 전략 연구: `application/research_factors.py`가 `rolling_high_breakout/v1`과 `rank_persistence/v1`의 결측·입력 revision 계약을, `application/breakout_strategy.py`가 `krx_bar_close_breakout/v1`의 Snapshot·Decision·후보 중복 방지와 flat/candidate/open/cooldown 상태를 소유한다. `infrastructure/persistence/research_repository.py`는 별도 `research.sqlite3` 원장, `scripts/run_research.py`는 고정 export 검증·가상시각 실행·논리 결과 hash를 담당한다. 회귀는 `test_research_factors.py`, `test_breakout_strategy.py`, `test_research_repository.py`
- D3c 내부 모의 실행: `application/research_execution.py`가 `next_tradable_bar_open/v1` 체결, 명시 비용, 현금 예약, 단일 포지션, 손절/목표 동일봉 보수적 순서와 mark/censor를 소유한다. `application/research_evaluation.py`는 T+1초 UNSUPPORTED, 1/3/5/10분 outcome 상태와 비용 포함 성과 적격성을 계산한다. `research_repository.py` v2가 execution/outcome/performance를 저장하고 `scripts/run_research.py`가 같은 가상시계에서 전략과 실행을 조립한다. 회귀는 `test_research_execution.py`, `test_research_evaluation.py`
- O1 키움 모의 주문 기술 경계: `domain/order_contract.py`가 mock/KRX intent·상태·broker snapshot을, `application/order_lifecycle.py`가 공통 세션에 따른 신규 주문 gate·만료·계좌·현금 예약 사전검사와 unknown/부분체결/취소 경합 대조를 소유한다. 신규 주문은 `market_session_schedule.py`의 시행일별 판단으로 KRX 정규장 `09:00~15:20` LIMIT를 허용한다. 새 KRX 애프터의 모의 지원 여부는 인증 수동 주문에 한해 `16:00~20:00` LIMIT를 별도 probe 정책으로 broker까지 보내 실제 응답으로 판정하며, 이를 지원 확정으로 간주하지 않는다. NXT·시장가·15:20~16:00 신규 주문은 거절하고 session/phase/schedule/profile을 기존 execution event에 남긴다. 이 gate는 취소·broker 대조·복구·늦은 체결에 적용하지 않는다. `kiwoom_rest/mock_execution.py`는 모의계좌 인증·속도 제한을 쓰는 단발 주문 transport이며 `client.py`의 `request_once` 외에는 주문 경로가 없다. 실전과 모의투자는 별도 키·`KiwoomRestClient`·broker queue·WebSocket을 사용한다. `kiwoom_rest/mock_account.py`는 모의 account broker의 `ka10075/ka10076/kt00018/kt00001` 연속조회를 복구 snapshot으로 바꾸고 `realtime.py`는 00 상세 체결과 계좌번호를 버린 04 변경 알림을 해석한다. `central_server/mock_account_monitor.py`는 모의계좌 전용 `00/04` WebSocket, 시작 복구, 수동 전송 전 계좌 갱신, 04 후 전체 재조회, 00 broker 주문번호 대조, 임대 갱신을 소유한다. `central_server/execution_runtime.py`는 단일 임대와 run별 수동 `request_id` 멱등성을, `central_server/app.py`는 기본 OFF인 인증 수동 주문·상태·취소 API를 소유하고 응답에 정책 버전을 포함한다. 04 예수금은 주문가능금액으로 사용하지 않고 일반 실시간 허브에도 내보내지 않는다. `persistence/execution_repository.py`와 중앙 스키마 v16이 원장을 보존하며 후보·전략 자동주문은 연결하지 않는다. 회귀는 `test_mock_execution.py`, `test_mock_account.py`, `test_mock_account_monitor.py`, `test_order_lifecycle.py`, `test_execution_repository.py`와 서버 설정·API 테스트다.
- O2a 모의 전진평가 기반과 A5d/A5e 실제 피드백: `domain/execution_activation.py`가 content-addressed mock 전진 프로파일, 명시적 `TBD` 기준과 전략 stage 전이 계약을 소유한다. `application/forward_evaluation.py`는 V1 대조 통계·O1 event·성과 입력을 데이터/시스템/성과 gate로 분리하고 BLOCKED/PENDING/FAILED/PASSED 보고서를 만들며, A5c 체결·실제 비용·선택/PIT/노출 상태를 불변 FeedbackEvidence로 고정한 뒤 명시 표본 정책의 기계 복기와 등록된 한 파라미터 개선안 revision을 파생한다. `application/feedback_strategy_revision.py`는 채택 proposal을 새 전략 버전과 기존 캠페인의 개발 재검증 job으로 연결한다. `persistence/forward_evaluation_repository.py`는 관련 비공개 문서를 불변 ID로 저장한다. 보고서와 피드백은 주문을 보내거나 stage를 자동 변경하지 않으며 O2a는 `approved_for_live` 전환을 거부한다. 회귀는 `test_execution_activation.py`, `test_forward_report.py`, `test_feedback_evidence.py`다.
- D7a 시간순 평가 기반선: `application/research_splits.py`가 TRAIN→VALIDATION→OOS fold, warmup/gap/purge, 연속 상태·현금과 기간말 검열 정책, final holdout 접근 기록을 소유한다. `application/research_evaluation.py`는 비용 출처·유효기간과 데이터 재현성부터 검사한 뒤 fold별 모의 체결 성과·MDD·노출·turnover·거절/검열·NO_TRADE·사건 반응을 분리 집계한다. `research_repository.py` v3가 불변 보고서를 저장하고 `scripts/run_research.py --evaluation-spec`가 실행과 보고를 같은 run 명세로 고정한다. 회귀는 `test_research_splits.py`, `test_research_reports.py`
- D7b 수동 쌍 비교와 연구 화면: `application/research_evaluation.py`가 동일 candidate dedup key로 필터 없음 기준선과 관심순위 variant의 공통/제외/추가 거래, 회피 손실·놓친 이익·사건 반응 차이를 계산한다. `research_repository.py` v4는 완료된 두 run의 비교를 불변 저장한다. `research_process.py`는 명시 JSON 요청을 검증해 한 개의 낮은 우선순위 Windows 보조 프로세스에서 실행·취소하며, `presentation/research_dialog.py`는 설정 요약·진행·fold 결과만 표시한다. 회귀는 `test_research_comparisons.py`, `test_research_process.py`, `test_research_dialog.py`
- F1 시장 특징과 실험 분류: `application/market_research_features.py`가 동일 TOP20 `U(t)/venue/window`의 집중·교체·지속·동일 세션 시각 대비 거래대금·대표 테마 확산·`top20_breadth`·성숙한 3분 돌파 표본을 분모와 함께 계산한다. `application/research_factors.py`의 `market_regime/v1`이 네 유형과 `UNKNOWN`, 후보 유형·이유·품질을 보존한다. `scripts/export_research_dataset.py`는 기존 `/themes/history` 결과를 해시 고정 sidecar로 동결하고, 연구 run 종료 시점의 관측용 시장 분류는 manifest와 연구 화면 상태에 표시된다. 전략별 매매가능성은 `assess_market_for_strategy`로 분리하며 현재 돌파 Family의 필수 진입 조건으로 쓰지 않는다. 회귀는 `test_market_research_features.py`와 연구 데이터/실행/화면 테스트다.
- C1 맥락 가설과 장중 확인: `application/context_candidates.py`가 N2 뉴스 사실, D5 관계 revision, 전 거래일 확정 사실, D4 후보와 기존 Yahoo 지연 5분/일봉을 사실/영향 추론이 분리된 `context-candidates/v1`으로 변환한다. 같은 사건·종목의 복수 출처는 장중 관측 전에만 병합하고 응답 관측별 상태 개정과 근거를 보존한다. `infrastructure/persistence/research_repository.py` v5는 run과 독립된 가설 revision 원장과 as-of 최신 projection을 소유한다. 회귀는 `test_context_candidates.py`, 뉴스 규칙·외부시장·후보·테마 테스트다.
- H2 테마 대장과 진입 근거 비교: `application/theme_leadership.py`가 D5 테마 revision과 H1 1초 가격·거래대금으로 대장 순위, 가격 이탈/재도달, 거래대금 둔화/재가속, 표시 안정화를 계산한다. 데이터 공백은 `UNKNOWN`으로 유지하며 호가 잠김 상태를 추정하지 않는다. `entry-thesis/v1`은 진입 당시 대장과 근거 참조를 고정하고 네 가격 기반 대응 정책을 주문 권한 없는 연구 제안으로 비교한다. `research_factors.py`가 Factor 등록을, `research_repository.py` v6가 대장 revision·진입 근거·정책 판단 불변 저장을 담당한다. 회귀는 `test_theme_leadership.py`, `test_entry_thesis.py`다.
- V1 NAS/직접 자료 대조: `infrastructure/kiwoom_rest/validation_client.py`의 기존 조회·실시간 대조기가 상태, 공유 source ref, venue/단위/집계 범위, 미매칭 만료, queue skip/eviction, 수신 간격과 source clock 차이, bounded p50/p95/p99를 기록한다. `central_realtime_worker.py`는 중앙 envelope를 대조기에만 전달하고 주 화면에는 기존 domain event만 보낸다. `client_factory.py`와 `bootstrap.py`는 사용자 opt-in일 때만 보조 직접 조회/실시간을 만들며 NAS가 계속 주 입력이다. 회귀는 `test_parallel_validation_client.py`, `test_central_realtime_worker.py`, `test_kiwoom_client_factory.py`, `test_failover_kiwoom_client.py`다.
- R1/R2 제한·지속 연구: `application/research_search.py`가 등록된 Family·Factor·soft parameter의 결정론적 후보, 한 번에 한 파라미터 변경, 생성 모드와 실행환경, trial/time/후보 수·동시 실행·CPU duty 예산, 재개 판정, 다중 원지표 후보 카드를 소유한다. `application/research_families.py`는 기존 돌파와 `pullback_reacceleration_strategy.py` 두 Family의 입력·상태·진입/청산·수량 계약 및 허용 파라미터를 등록한다. `research_queue.py`는 동일 ExperimentSpec의 job ID와 결과 변화 알림 판정을 소유한다. `research_process.py`는 기존 D7 실행·보고서 파이프라인으로 기본전략·무거래·민감도·Factor ablation·비용 스트레스를 직렬 실행하고 운영 설정과 분리한다. `infrastructure/persistence/research_repository.py` v9는 불변 ExperimentSpec·종료 trial·후보 카드, 중단과 결과를 분리한 trial attempt, owner/generation으로 fencing된 queued/running/terminal job·상태 event를 저장하며 `presentation/research_dialog.py`가 전체 카드와 job 상태를 표시하고 시간 예산 뒤 남은 실험을 선택적으로 자동 재개한다. 회귀는 `test_research_search.py`, `test_research_extension.py`, `test_research_queue.py`, `test_research_process.py`, `test_research_repository.py`, `test_research_dialog.py`다.
- 종목 분봉·일봉: `infrastructure/persistence/minute_bar_repository.py`, `persistence/journal_bar_repository.py`, `central_server/minute_bars.py`, `central_server/database.py`
- TOP20 합계·시장 분해·구성 종목·30초 코호트: `application/top20_trade_value_collector.py`, `infrastructure/persistence/minute_bar_repository.py`
- 체결 당시 시장·뉴스·수급·호가 근거: `persistence/journal_snapshot_repository.py`, `persistence/journal_snapshot_service.py`, 필드별 관측 의미 `domain/trade_snapshot_observations.py`, 출처 판정 `domain/snapshot_provenance.py`
- 현재 테마 상태: `infrastructure/persistence/theme_repository.py`, `infrastructure/central_content_sync.py`; 중앙 시점 이력 계약은 `domain/research_contract.py`, 저장·조회는 `central_server/database.py`, `/api/v1/themes/history`
- 보존 회귀: `test_market_data_contract.py`, `test_market_data_coverage.py`, `test_central_server_database.py`의 시각별 순위 및 봉/메타데이터 원자적 롤백·범위 조회, `test_central_market_ingest.py`·`test_autonomous_top20.py`·`test_central_realtime_collector.py`의 메타데이터 연결, `test_minute_bar_repository.py`·`test_journal_database.py`의 로컬 봉/체결 문맥 의미·원자성·확정 상태 보존 테스트
- 나스닥·원유 선물 임시 지연 시세: `central_server/external_market_collector.py`, 월물 코드·롤 판단 `central_server/futures_roll.py`, 중앙 `central_external_bars`, 롤 상태 `central_documents.external_market_roll_state`, `test_external_market_collector.py`
- R5d2a 해외시세 운영: 기존 `central_server/app.py`의 operations가 enabled/poll/auto-roll/confirmation 영속 revision·검증·applied_revision을 소유한다. `external_market_collector.py`는 단일 owned 수집 cycle/직렬 owned 갱신·실제 thread/저장 drain과 캐시 수명을 유지한다. `presentation/api_settings_dialog.py`는 기존 worker/CAS와 구 NAS 누락 필드 제외를 소유한다. 회귀는 `test_external_market_runtime.py`, 기존 외부시세/운영/서버/Qt 테스트다. symbol/활성 월물 수동 교체와 조건검색 운영은 후속이며 새 SQL/manager는 없다.
- R5d2b 조건검색 운영: 같은 `app.py` operations가 추적 ON/OFF·정확한 조건명/substring·영속 revision을 소유한다. 기존 `market_events.py`는 정책 revision/기존 활성식/후보/초기 페이지·실시간 신호/해제 ACK·15초 timeout·접수 당시 조건 문맥을 소유한다. `realtime_collector.py`의 같은 단일 수신 loop가 변경을 polling하고 새 WebSocket을 만들지 않는다. `api_settings_dialog.py`는 지원 flag·구 NAS 입력 제외·실제 등록 상태를 표시한다. 기존 코호트/VI/체결 수명과 순위 TR 우선순위는 유지한다. 회귀는 `test_condition_runtime.py`, 기존 시장 이벤트/실시간/운영/Qt/서버 테스트다. 새 manager/SQL 테이블 없음.

과거 시뮬레이션 기능을 수정할 때는 먼저 `HISTORICAL_DATA_CONTRACT.md`에서 원본/파생값, 시점 기준, 누락 의미를 확인한다. 읽기 전용 시뮬레이션 조회 서비스가 생기기 전까지 UI가 저장소 여러 개를 직접 결합하지 않는다.

- S5 세션별 연구 계약: `application/market_session_schedule.py`가 `krx-regular/v1`, `krx-after/v1`, `krx-full-day/v1`의 허용 구간·Factor reset·다음 봉·단일가·horizon 정책을 소유한다. `research_replay.py`가 봉별 session/phase/schedule 근거를 전달하고, `research_execution.py`와 `research_evaluation.py`가 세션 공백·거래일 변경·미지원 단일가 체결을 보존한다. `run_research.py`, `research_process.py`, `research_search.py`, `candidate_monitor.py`, `execution_activation.py`가 profile을 RunSpec·검색·checkpoint·forward ID에 포함한다. profile 없는 기존 요청은 기존 RunSpec/hash를 유지한다.

## 테마

- 파싱/매칭/미리보기: `domain/theme_parser.py`, `domain/theme_text_import.py`, `application/theme_matching.py`, `theme_preview.py`
- DB/프로필: `infrastructure/persistence/theme_repository.py`, `database.py`
- Excel/OCR/백업: `infrastructure/excel/theme_repository.py`, `infrastructure/ocr/paddle_theme_ocr.py`, `persistence/theme_backup.py`; OCR worker 생성·저우선순위 실행·취소·신호 수명은 `presentation/image_theme_ocr_worker_controller.py`
- 중앙 동기화: `infrastructure/central_theme_sync.py`, `central_content_sync.py`. 테마 변경은 변경 시각이 든 디스크 대기 표식을 남기고 실패 시 자동 재시도한다. 시작·재시도에서는 NAS `theme_metadata` 완료 시각과 비교하여 로컬이 최신일 때만 업로드하고 NAS가 최신이면 해당 스냅샷을 적용한다. 완전한 `theme_metadata(default,full)` 수락과 불변 이력 append는 중앙 `database.py`의 한 트랜잭션이며 `test_theme_history.py`가 중복·삭제·활성 전환·지연 도착·rollback을 보호한다.
- 테마 가져오기·미리보기·편집·프로필 관리 UI: `presentation/theme_dialogs.py`
- 테마 색상 표시 보조: `presentation/theme_colors.py`
- TOP20 테마 빈도·거래대금 합계·상위 테마·제외 종목·정렬키 계산: `application/theme_ranking.py`
- 메인 화면 연결·가져오기 worker 조정: `presentation/main_window.py`의 관련 `MainWindow` 메서드
- 테스트: `test_theme_*.py`, `test_paddle_theme_ocr.py`, `test_image_theme_ocr_worker_controller.py`, `test_central_theme_sync.py`

## 뉴스와 AI

- 별도 프로세스: `news_process.py`
- 뉴스 목록·상세 UI: `presentation/stock_news_window.py`
- 네이버/DART/AI·필터·색상·바로가기 설정창: `presentation/news_settings_dialog.py`
- 뉴스 조회·DB 준비·AI 분석 작업 스레드, 선택 기사 NAS 근거 조회와 공통 조회 주기: `presentation/news_workers.py`
- 뉴스 목록 행·AI 판단·NAS 본문/공급계약 근거·상세 HTML 표시 계산: `presentation/news_view_model.py`
- 뉴스 자동분석 후보·수동 시작점·단건/묶음 선택 정책: `application/news_auto_analysis.py`
- 뉴스 worker 종료 정리와 AI 실행 가능 여부·일일 한도·진행 문구: `presentation/news_execution.py`
- 공급자: `infrastructure/naver_news.py`, `dart_disclosures.py`, `article_text.py`, `news_ai.py`. 네이버 원문 URL의 정규화된 `publisher_domain`/`publisher_name`과 언론사명·도메인 제외 판정은 `naver_news.py`가 공통 소유한다.
- 대상 종목 관점 AI 프롬프트·버전 캐시 키와 공급자 오류 상태: `infrastructure/news_ai.py`; 중앙 적용은 `central_server/ai_service.py`, 로컬 적용은 `presentation/news_workers.py`, 이전 계약 결과 제외는 `persistence/news_ai_repository.py`; NAS 자동분석의 새 identity 제한은 `central_server/news_service.py`, 앱 재시작 자동 후보 정책은 `presentation/stock_news_window.py`
- 사건 묶음/기본 판정: `application/news_grouping.py`, `news_analysis.py`; `news_analysis.py`는 종목·시장 가격 반응과 환율·유가·금리·실적 전망 자체의 움직임을 구분한다. 중앙 저장 뉴스는 `central_server/database.py`가 최신 `fulltext`를 함께 읽고 `central_server/news_service.py`가 query-set 기사뿐 아니라 종목별 watchlist 과거 기사도 조회 시 현재 규칙으로 다시 판정한다. N2a 기록 전용 공급계약 판정·근거 span·점수·후보 매칭은 `application/news_rules.py`
- 로컬 DB: `persistence/stock_news_repository.py`, `news_ai_repository.py`, `news_database.py`
- 중앙 서비스/API 클라이언트: `central_server/news_service.py`, `ai_service.py`, `infrastructure/central_news_client.py`, `central_ai_client.py`
- 뉴스 후속 작업 실행과 선택 종목 우선순위: `central_server/news_jobs.py`; 영속 작업 claim 정렬은 `central_server/database.py`
- N1/N2a 기사·작업 계약과 수명: `domain/news_observation.py`가 content hash·처리 버전 기반 job key와 revision 타입을 소유하고, `central_server/news_jobs.py`가 bounded BODY/RULE/AI worker·timeout·재시도·재시작 복구를 소유한다. 공급계약 규칙은 `application/news_rules.py`, 불변 기사/본문/사건/소속/AI 저장과 SQLite/PostgreSQL parity는 `central_server/database.py`, `central_schema.py`가 소유한다.
- N3 query_set 정책·cursor 수명·24k hard budget 사용·정확한 KRX 회사명 target 판정은 `central_server/news_sources.py`가 소유한다. 빈 `stock_catalog`의 제한된 최초 채움과 실패 재시도도 이 수명 안에서 기존 KRX catalog loader를 재사용한다. NAS 처리 제외 언론사의 제목·링크 관측은 유지하고 BODY/RULE 예약을 생략하는 저장 경계는 `news_sources.py`와 `central_server/database.py`가 함께 보호하며, `news_service.py`는 같은 정책으로 자동 AI 후보를 제외한다. `news_service.py`는 앱과 독립된 loop를 시작하고 기존 watchlist를 별도 8k scope로 유지하며, confirmed GLOBAL 저장기사를 기존 종목 owner 결과와 identity 기준 병합한다. 자동 AI 입력은 owner 기사만 유지한다. `naver_news.py`는 기존 `search()`와 page metadata API를 함께 제공한다. source별 판본 재사용, 기간 진단 집계, 완료 본문에 늦게 확인된 target의 RULE 예약 및 종목별 bounded confirmed 조회는 `central_server/database.py`가 소유한다.
- 중앙 보존/동기화: `central_content_client.py`, `central_content_sync.py`; 뉴스 프로세스의 60초 변경 감지는 `news_process.py`의 `_news_content_signature`가 뉴스/AI DB만 대상으로 하며 테마 변경은 `central_theme_sync.py`가 별도로 전송한다. 개인 NAS 모드는 대용량 뉴스·AI 카탈로그 동기화를 제외하고 선택 종목 검색·선택 기사 이력 API를 사용하며, 테마와 계좌별 매매일지 뉴스 연결만 증분 병합한다. 그 밖의 콘텐츠 sync는 성공한 `(collection,owner,key,document)` hash를 원자 manifest에 보존해 변경 문서만 background news process에서 보내고, pull로 받은 hash도 기록해 echo push를 막는다. 기존 `.central_content_seeded` 설치는 성공한 시작 pull 뒤 manifest가 없을 때만 동기화 대상 자료를 한 번 baseline으로 이전한다. 중앙 SQLite/PostgreSQL은 동일 `document_json`의 `updated_at`을 유지해 재다운로드 cursor를 흔들지 않는다. 실시간 순위 요청은 항상 최우선이며 이 별도 프로세스의 증분 동기화가 순위 API 경로를 지연시키지 않는다.
- 테스트: `test_news_*.py`, `test_news_observation_history.py`, `test_news_jobs.py`, `test_naver_news_config.py`, `test_article_text.py`, `test_central_news_*.py`, `test_central_ai_*.py`

- R5d2c 공통 뉴스 운영: `presentation/api_settings_dialog.py`가 기존 query-set ON/OFF·한 줄 검색어/정규화·60~86400초 주기·지원 필드/CAS와 NAS 폼 스크롤을 소유한다. `central_server/news_sources.py`의 동일 collector는 접수 검색어의 owned 처리 완료/다음 검색어 정책 확인, 정상 완료 last_success 기반 새 주기 판정, 실패/부분 페이지 예약과 기사/cursor/예산 보존을 소유한다. `app.py`의 기존 operations/worker/local mirror는 그대로이며 새 endpoint/SQL/manager가 없다. 회귀는 `test_news_query_operations.py`, 기존 뉴스 source/인증/설정/DB/Qt 테스트다.

## 매매일지

- 프로세스·매매일지 본창과 차트 데이터 연결, 일봉 및 시장지수 worker 실행 중 최신 대기 요청 1건 보관·종료 후 연속 실행: `journal_process.py`
- 매매일지→뉴스창 요청번호와 원자적 JSON 명령 기록: `presentation/process_control.py`의 `JsonCommandChannel`; 매매일지는 요청 내용과 결과 문구만 결정
- 체결 확인·기간 체결/비용·분봉/일봉·시장지수 보완 worker: `presentation/journal_workers.py`
- 자동보완 대상 ID·상태·재시도, 파생 분석 revision과 연구 근거 링크 계약: `application/journal_enrichment.py`; 영속 CRUD는 `persistence/journal_trade_repository.py`
- 선택 행의 셀 왼쪽 표시 delegate: `presentation/journal_delegates.py`
- 매매일지 기본 설정·공통 차트 설정 UI와 이동평균선 기본값: `presentation/journal_settings_dialogs.py`
- 본창/따로보기 공통 차트 렌더링·그리기 상태 신호: `presentation/journal_chart_widget.py`; 같은 종목·같은 봉 주기의 그림/봉 수 동기화와 다중 패널 배치: `presentation/detached_chart_window.py`
- `presentation/detached_chart_settings.py`의 예전 전용 색 키는 기존 사용자 설정 호환용으로 남아 있지만, 실제 차트 배경·선 설정은 본창의 공통 차트 설정을 사용한다.
- 분봉·일봉 렌더링, 확대·가로 스크롤·마우스 드래그 이동, 체결 꼬리표, 이동평균선, 차트 그리기 위젯: `presentation/journal_chart_widget.py`
  - 최대 6개 차트 패널·배치·비교 종목/지수·따로보기 이미지 저장: `presentation/detached_chart_window.py`
  - 따로보기 Qt 객체 종료 수명 회귀: `tests/unit/test_detached_chart_window.py`의 `tearDown`
  - 체결 묶음: `application/trade_history_service.py`, `trade_journal_summary.py`
- 기간 체결·비용 조회, 과거 진입 연결용 회차 구성, kt00007 요약과 중앙 상세 체결의 계좌/주문 단위 대조 read projection, 종목·손익·복기 필터, 묶음/개별 체결 선택 시 차트 종목·초점·날짜 범위 결정, 분봉 보완 기준일·상태·체결시각 범위 및 현재 선택 영향 판정: `application/trade_history_query_service.py`
- 수동 매매 묶음 합치기·선택 체결 분리·자동분류 복원의 검증, 수동 ID 발급과 저장 명령: `application/trade_group_edit_service.py`
- 비용: `application/trade_cost_service.py`
- 자동 복기/통계: `trade_review_analysis.py`, `trade_journal_statistics.py`
- 기본 유형/전략팩: `trade_setup_classification.py`가 원 체결 계좌 scope·venue·event time과 `market_session_schedule.py`의 시행일별 정적 KRX 구간으로 자동 유형 근거를 만들고 NXT 동적 phase/venue 결측은 UNKNOWN으로 유지한다. 전략팩 평가는 `generic_strategy_evaluator.py`, `strategy_pack.py`, `strategy_pack_extraction.py`, `personal_trade_rules.py`가 담당한다.
- 전략팩 등록·강의 추가·규칙 검토·승인·버전 복원 UI: `presentation/strategy_pack_dialogs.py`
- 차트 계산: `trade_chart.py`(봉 집계·일봉 표시 대상·과거 250개 캐시 재사용), `minute_chart_service.py`, `market_index_chart_service.py`, `journal_chart_layout.py`(가격축·시행일 이후 15:20/15:30/15:40/16:00/20:00 시간눈금, KRX 정규장 종가 15:30·전체일 최종가 20:00·지수 제공 범위 종가, 체결 문구·레이블 배치)
- 로컬 DB 저장/조회: `persistence/journal_database.py`, `entry_snapshot_writer.py`, `journal_backup.py`
- 매매일지 테이블 생성·호환성 복구: `persistence/journal_schema.py`
- 체결 시점 순위·대금·테마·뉴스·수급·호가·시장 스냅샷과 장후 보완: `persistence/journal_snapshot_repository.py`, `entry_snapshot_writer.py`
- 매매 회차 스냅샷 조회·누락 뉴스 검색 범위·장후 뉴스 연결 조립: `persistence/journal_snapshot_service.py`
- 스냅샷 필드별 실시간 관측/장후 보완/누락 판정: `domain/snapshot_provenance.py`
- 매매 회차별 스냅샷 연결·매수 진입 자료 선택·판정 불가 항목 정리: `application/trade_snapshot_context.py`
- 회차별 자동분석 본문·진입 스냅샷·시장 상태 표시 문구: `presentation/trade_review_formatting.py`
- 기간 손익·비용 요약, 매매 묶음 표 15개 열·유형 덮어쓰기 표시, 매매유형 요약·회차 편집 행·연결 상태·전체 분석 문서 화면 모델: `presentation/trade_review_view_model.py`
- 기본 강의/사용자 전략팩별 복기 참고자료명·원칙 수 계산: `application/strategy_review_context.py`
- 기본분석·사용자 전략팩 후보 평가·표시방식 선택·회차별 수동 유형 적용: `application/trade_strategy_coordinator.py`
- 자동분석 전 일봉 준비·자동 판정 저장·과거 단일 수동 유형 이전·전략 초안 수 집계 조정: `application/trade_analysis_preparation_service.py`
- 회차별 분석 입력 구성·수동/자동 근거 분기·스냅샷 판정 범위 보정·분석기 실행: `application/trade_episode_analysis_service.py`
- 개인 원칙·구조화 원칙·전략팩·강의 추출 초안·버전 저장: `persistence/journal_strategy_settings_repository.py`
- 매매유형·체결·수동 묶음·복기·실제 비용 저장과 체결/비용 동기화 결과의 원자적 일괄 반영: `persistence/journal_trade_repository.py`
- A2 계좌별 매매일지: `domain/order_contract.py`의 `LEGACY_ACCOUNT_SCOPE`와 체결·비용 계약이 origin/canonical scope를 소유한다. `trade_journal_summary.py`는 canonical scope별 FIFO와 `fill:v2` key를 계산하고 `trade_group_edit_service.py`는 계좌 간 수동 병합을 거절한다. `persistence/journal_schema.py` v5는 체결·비용, v6는 스냅샷·복기·유형·자동보완·분석 revision·연구 링크를 기존 참조 그대로 legacy 이전한다. `journal_snapshot_repository.py`는 신규 `snapshot:v2` 키와 계좌별 저장·조회를, `journal_trade_repository.py`는 파생 자료의 origin/canonical scope를 담당한다. 실제 조회 context 연결과 계좌 선택·v2 중앙 동기화는 A3/A4 대상이다.
- 분봉·일봉·시장지수 봉, 분봉/관측 메타데이터/백필 상태의 원자적 결과 저장과 메인 DB 읽기 전용 가져오기: `persistence/journal_bar_repository.py`
- 중앙 동기화: 병합·업로드는 `infrastructure/central_journal_sync.py`의 `CentralJournalSyncService`; 중복 실행 차단·백그라운드 스레드·오류 격리는 같은 모듈의 `CentralJournalSyncRunner`
- 중앙 동기화 공통 배치·문서 envelope·로컬 테이블 확인: `infrastructure/central_sync_utils.py`
- 테스트: `test_journal_*.py`, `test_trade_*.py`, `test_strategy_pack*.py`, `test_strategy_review_context.py`, `test_trade_strategy_coordinator.py`, `test_trade_episode_analysis_service.py`, `test_trade_group_edit_service.py`, `test_trade_review_view_model.py`, `test_trade_chart.py`, `test_detached_chart_window.py`, `test_journal_detached_flow.py`, `test_snapshot_provenance.py`, `test_journal_snapshot_service.py`, `test_trade_snapshot_context.py`, `test_trade_review_formatting.py`, `test_journal_workers.py`

매매일지의 분석식만 고칠 때는 `journal_process.py`를 먼저 건드리지 않고 `application/trade_*`와 해당 단위 테스트를 수정한다. 기간 요약·비용/순손익/유형 표시는 `trade_review_view_model.py`, 일봉 준비·판정 저장·과거 유형 이전 순서는 `trade_analysis_preparation_service.py`, 실제 저장소 조회와 위젯·선택 상태·화면 배치·그리기·이미지 저장은 `journal_process.py`에서 다룬다.

## DB와 설정

  - 메인 스키마/초기값: `persistence/database.py`
  - 로컬 SQLite 공통 마이그레이션 실행기: `persistence/schema_migrations.py`
  - 로컬 SQLite 읽기 연결·쓰기 트랜잭션/종료 경계: `persistence/sqlite_connections.py`
  - 로컬 시장 관측 메타데이터 스키마·저장: `persistence/market_data_metadata_schema.py`, `persistence/market_data_metadata_repository.py`; 의미 계약은 `domain/market_data_contract.py`
  - 마이그레이션 회귀(기존 데이터 보존·멱등성·실패 롤백·신버전 거부): `tests/unit/test_schema_migrations.py`
  - 관측 시각·출처 round-trip/보수적 레거시 판정: `tests/unit/test_market_data_metadata_repository.py`
  - 세부 저장소: `persistence/*_repository.py`
  - 장중 실시간 캐시 writer: `persistence/market_cache_writer.py`; `presentation/main_window.py`가 1초마다 분봉·시장지수·현재가·당일고가의 최신 대기분과 중앙/키움에서 받은 분봉 이력 batch를 넘기고 writer가 GUI 밖에서 같은 DB 쓰기를 직렬화한다. NAS 분봉 DB 우선 읽기는 `application/minute_chart_service.py`와 `infrastructure/kiwoom_rest/remote_client.py`, 완료 일봉·기본정보·NXT 중앙 재사용은 `central_server/app.py`가 담당한다. 종료 drain과 실패분 재병합은 `test_market_cache_writer.py`, `test_main_window.py`가 보호한다.
  - 뉴스 스키마/마이그레이션 기준선: `persistence/news_schema.py`; 기존 메인 DB 이전은 `news_database.py`, 저장은 `stock_news_repository.py`, `news_ai_repository.py`
- 매매일지 스키마: `persistence/journal_schema.py`
  - 중앙 SQLite/PostgreSQL: `central_server/database.py`
  - 중앙 SQLite/PostgreSQL 공통 마이그레이션 원장·실행기: `central_server/schema_migrations.py`
  - 중앙 SQLite/PostgreSQL 공통 봉 열 계약·행 변환·조회 범위 보정과 문서/스냅샷 JSON 행 codec·문서 조회 조건: `central_server/database_codec.py`
  - 중앙/로컬 공통 관측 메타데이터 행 codec: `infrastructure/market_data_metadata_codec.py`
- 중앙 SQLite/PostgreSQL 테이블·인덱스 방언별 실행 명세: `central_server/central_schema.py`
- NAS 암호화 파일·프로세스 소유권·초기 env 이관·revision 복구 fence·기동 설정 합성: `central_server/credential_store.py`. DB에는 비밀 없는 프로필·활성화 원장만 저장한다. 계좌 신원 준비는 `application/account_identity.py`, binding+활성화 최종 커밋은 `central_server/database.py`의 한 트랜잭션이다.
- R6b1 실전계좌 활성화 저장: 기존 `database.py`의 `_claim_account_settings`가 실전/모의 verified scope의 단일 active_profile_id·최초 조회 ON/모의주문 OFF·키 갱신 설정 보존·disable/완료 replay를 binding/activation과 같은 트랜잭션에서 처리한다. 중복 claim 실패는 profile/binding/원장까지 rollback한다. `test_account_runtime_settings.py`가 다중 연결 경쟁/방언/기존 receipt를 검증하고 `check_postgres_integration.py`에 실환경 검사와 생성 행 정리를 추가했다. 새 SQL/manager/실전 hook 없음; R6b2 owner/API는 후속이다.
- REST 키 교체 공통 장벽: `central_server/rest_broker.py`가 새 접수 잠금·기존 HTTP/DB 작업 drain·교체/재개 task 수명을 소유하고, `infrastructure/kiwoom_rest/client.py`가 동일 요청 잠금/한도에서 후보 OAuth·계좌 확인·토큰 refresh 차단·메모리 인증 교체를 담당한다.
- 런타임 인증 API와 operation 수명: `central_server/credential_runtime.py`가 프로필 생성·준비/적용/상태/취소, TTL·상한·멱등성, vault→DB→runtime commit 조정 및 비밀 없는 HTTPS HTTP 경계를 소유한다. R3e mock/R5a NAVER/R5b DART/R5c AI owner를 연결했고 real owner는 후속이다. 후보 drain/commit 시작/release와 profile별 지원 여부, 선택 runtime_validation을 hooks로 확인한다. 회귀는 `test_credential_runtime.py`, `test_credential_store.py`, `test_credential_barrier.py`다.
- R5c AI 인증: `central_server/ai_credentials.py`는 3개 고정 global 프로필·유료 호출 없는 prepare/UNVERIFIED·후보 수명·commit fence·활성 revision을 소유한다. `ai_service.py`는 본문 준비 전 공급자/model/key/revision snapshot, 접수 task/공유 실행 task의 실제 완료 drain, 공급자별 pause·실제 분석의 runtime_validation을 소유한다. 실행 공유 키는 revision/body hash까지 구분하지만 저장 성공 캐시·품질 버전·사용량/일일 상한은 유지한다. `app.py`가 keyless service/뉴스 작업기에 owner를 연결하고 종료에도 실제 분석 저장을 기다린다. 회귀는 `test_ai_credential_owner.py`, 기존 AI/뉴스/인증/서버 테스트다. 새 SQL 테이블/범용 provider 계층은 없으며 UI/운영 확대와 R7 실환경은 미완료다.
- R5b DART 인증: 기존 `central_server/news_credentials.py`의 DART owner가 고정 기본 프로필·cache Path·후보 수명·commit fence·활성 revision을 소유한다. `infrastructure/dart_disclosures.py`의 validate_credentials는 회사코드 캐시를 건드리지 않는 공시 검색 probe와 비밀 없는 인증/재시도 가능 오류를 소유한다. `news_service.py`는 접수 시 두 client/DART enabled snapshot과 독립 pause·종목 task 실제 drain을 소유한다. NAVER query-set/기사/작업/사용량과 운영 토글을 유지한다. `app.py`가 owner를 연결하며 회귀는 `test_dart_credential_owner.py`, R5a/뉴스/인증/서버 테스트다. AI 연결은 위 R5c를 따르며 UI/운영 확대와 R7 실환경은 미완료다.
- R5a 네이버 인증: `central_server/news_credentials.py`가 고정 기본 프로필 등록·검증 후보·commit 이후 이전 키 차단·활성 revision을 소유한다. `news_service.py`는 종목 수집 task와 cache-only pause, `news_sources.py`는 공통 검색 모든 페이지/실패 기록의 owned task·drain을 소유한다. 새 client는 두 경로에 함께 적용하고 기존 기사/cursor/작업/예산을 보존한다. 검증 호출도 영속 watchlist/shared 예산에 합산한다. `app.py`는 keyless vault 설치에도 service/owner를 연결한다. 회귀는 `test_naver_credential_owner.py`, `test_central_news_service.py`, `test_news_source_collection.py`, `test_news_jobs.py`다. DART/AI 연결은 위 R5b/R5c를 따르며 UI/운영 확대 및 R7 NAS 실환경은 미완료다.
- R3a 모의 명령 장벽: `central_server/execution_runtime.py`의 gateway가 HTTP 대기자와 독립된 명령 task·접수 gate·실제 drain을 소유한다. ExecutionRuntime은 동기 작업/stop 직렬화·독립 heartbeat·신규 주문 OFF를 담당한다. 임대 조건부 해제는 `persistence/execution_repository.py`와 중앙 SQLite/PostgreSQL `database.py`다. 계좌 bundle 연결은 후속 R3이며 회귀는 `test_mock_runtime_barrier.py`, `test_order_lifecycle.py`, `test_execution_repository.py`다.
- R3b 모니터/WS 종료·DB 소유권: `central_server/mock_account_monitor.py`가 시작·복구·직접 refresh·heartbeat·close task와 queue drain을 소유하고, 모의 WS는 token thread/socket 실제 종료와 닫는 중 이벤트 차단을 담당한다. `ExecutionRuntime.start`가 repository owner context를 불변 연결하고 중앙 DB 세 쓰기 경로가 lease/scope 검사와 쓰기를 한 트랜잭션으로 수행한다. 회귀는 `test_mock_account_drain.py`이며 실제 복수 계좌 bundle은 다음 부분이다.
- R3c 계좌 bundle: `central_server/mock_runtime.py`의 `MockAccountBundle`이 계좌/run/profile 고정 client·broker·repository·runtime·monitor·WS·gateway와 owned start/close를 소유한다. `app.py`의 기존 모의 조립도 이 bundle을 사용한다. 연결별 신원은 bundle에 고정하고 주문 응답은 요청 시작 gateway를 끝까지 사용한다. 회귀는 `test_mock_account_bundle.py`, `test_central_server_app.py`이며 실제 복수 계좌 owner·인증 hooks·scoped API는 연결 전이다.
- R3d 계좌 운영 설정: `central_server/database.py`의 공통 `_load/_save_account_settings`와 두 DB store가 verified scope·profile/binding·설정 CAS를 검증한다. 모의 활성화의 기본 설정/중복 profile 충돌은 binding/activation과 같은 트랜잭션이다. `app.py`는 인증 GET만 제공하며 실제 설정 적용 owner/PUT은 후속이다. 회귀는 `test_account_runtime_settings.py`, `test_credential_store.py`, `test_credential_runtime.py`이며 PostgreSQL 실검증은 `scripts/check_postgres_integration.py`다.
- R3e 실제 모의 owner: `central_server/mock_runtime.py`의 `MockCredentialOwner`가 profile bundle/context/revision·계좌 lock·공통 동시 실행 2개·bootstrap·후보 drain/publish/recovery를 소유한다. 신규 검증 계좌의 client는 `client.py.fork_verified_candidate`에서 probe 호출 이력과 별도 잠금을 받는다. ka00001 payload의 신원 변환은 `account_identity.py.identity_from_payload`다. monitor 초기 read와 WS async token도 공통 limiter를 사용한다. `app.py`는 실제 지원 hook·applied revision·기본 계좌 주문 gate를 연결한다. 회귀는 `test_mock_credential_owner.py`이며 키 비활성화/설정 PUT/scoped API는 후속이다.
- R3f 모의 계좌 제어: `mock_runtime.py`의 기존 owner가 disable 후보·tombstone 실행과 owned 설정 적용 task를 소유한다. `credential_runtime.py`는 기존 HTTPS/제한 JSON 경계에서 계좌 PUT을 제공한다. `credential_store.py`와 `database.py`는 disabled activation metadata·기존 binding 참조·OFF 설정의 원자 저장을 처리하며 `client.py.disable_credentials`는 drain 후 활성 키/토큰을 비운다. 회귀는 `test_mock_credential_owner.py`, `test_account_runtime_settings.py`이고 scoped API/UI는 후속이다.
- R3g 선택 계좌 API: `app.py`가 scope/profile/binding 버전과 계좌/run intent 귀속을 검증하고 기존 bundle 또는 검증된 main manager를 선택한다. `account_query.py`는 bundle별 cursor·owned query·close drain·늦은 binding 검사를 소유하며 `mock_runtime.py`의 기존 close에 연결한다. `execution_runtime.py`는 scoped ID 정책을 선택적으로 적용해 legacy ID를 보존한다. 회귀는 `test_scoped_account_api.py`, `test_central_account_query.py`, `test_order_lifecycle.py`다. R4 UI/context 전달과 R7 실환경 검증은 후속이다.
- 공통 설정과 메인 표 표시·순서 중앙 병합: `central_settings_sync.py`, `persistence/settings_repository.py`, `persistence/column_settings_repository.py`
- 뉴스·AI·매매일지 뉴스 연결·전체 테마 중앙 병합: `central_content_sync.py`, 즉시 테마 교체와 영속 재시도 dispatcher는 `central_theme_sync.py`
- NAS 뉴스·AI·Shadow 운영 설정의 GET/부분 PUT 단일 경계와 로컬 뉴스 설정 미러: `infrastructure/central_operational_settings.py`; NAS 연결 설정 UI, 뉴스 설정 UI, Shadow 후보 창이 이 경계를 함께 사용한다.
- 설정창의 단건 I/O와 창 해제 후 작업 완료 수명: `presentation/settings_request_worker.py`.
- PC 직접 연결 저장량 진단: `infrastructure/local_storage_diagnostics.py`가 라이브 SQLite를 열지 않고 데이터 폴더의 주식·뉴스·매매일지·연구·로그·기타 실제 파일 용량을 읽기 전용으로 집계한다. `presentation/api_settings_dialog.py`의 `이 PC 저장량`에서 표시하며, 자동 정리는 기존 분봉 30일·일봉 종목별 250개 정책만 설명하고 새 삭제 권한은 만들지 않는다.
- R4a NAS 모의계좌 입력: `presentation/api_settings_dialog.py`에서 `nas_credentials_dialog.py`를 연다. `infrastructure/central_credentials_client.py`는 HTTPS/redirect 차단·동일 요청 ID·계좌/프로필/버전 응답 검증을 소유한다. 같은 창의 계좌 이름 변경은 credential revision을 확인한 뒤 `central_credential_profiles.label`만 수정하며 계좌 신원·binding·인증키·운영 설정·매매 이력에는 관여하지 않는다. UI는 기존 `SettingsRequestWorker`로 I/O를 실행하고 PC 키 설정/미러에는 저장하지 않는다. 회귀는 `test_central_credentials_client.py`, `test_nas_credentials_dialog.py`, `test_nas_credentials_ui_integration.py`다. 매매일지의 명시 계좌 선택/worker context는 아래 R4b가 연결한다.
- R4b 선택 계좌: `central_server/app.py`의 v3 목록은 기존 mock owner의 admitted binding과 main binding만 읽고 활성 자격 프로필의 사용자 이름을 표시 전용 `display_label`로 함께 제공한다. `journal_process.py`는 background 목록/선택과 `실전/모의 · 사용자 이름` 표시를, `journal_workers.py`는 import 전체 context와 빈 결과를 소유한다. `kiwoom_rest/remote_client.py`는 고정 선택 context의 v3 조회·v2 mock 명령/미확인 submit ID를 소유하고 `account_query.py`의 직접 adapter는 자기 검증 scope만 허용한다. 표시 이름은 scope/profile/binding 동일성에 참여하지 않는다. `failover_client.py`/`validation_client.py`는 명시 NAS 계좌를 primary에만 연결한다. 회귀는 `test_selected_account_client.py`, `test_journal_selected_account.py`, `test_selected_account_api_integration.py`다. 신규 자동주문 UI는 별도 후속이다.
  NAS/뉴스 설정 조회·저장 및 API 연결 테스트는 UI에서 값을 확보한 뒤 이 worker에서 실행하며,
  QObject 슬롯으로 결과를 전달한다. 운영 설정은 부분 변경과 revision 충돌 계약을 사용한다.
- 메인 순위표 열 표시·순서 편집 UI: `presentation/column_manager_dialog.py`
- Google Drive: `persistence/google_drive_sync.py`, `settings_backup.py`, `theme_backup.py`, `news_ai_backup.py`, `journal_backup.py`

- A1 계좌 신원: `domain/order_contract.py`의 `AccountScope`·`AccountBinding`·`AccountScopeAlias`가 broker/real·mock/지속 UUID, 검증 revision, origin→canonical 계약을 소유한다. `infrastructure/kiwoom_rest/account_identity.py`는 해당 환경 broker로 `ka00001`을 호출해 원문을 메모리에서만 HMAC 지문으로 바꾸고, `local_account_binding.py`는 Windows 직접 프로필의 최신 binding을 별도 DPAPI 파일에 보존한다. `application/account_identity.py`가 registry/binding/mirror/alias 저장을 조정한다. 중앙 DB v17 registry와 v18 alias, `scripts/register_account_identity.py`가 최초 등록·자격 재검증을 담당한다.
- A3 계좌 조회: `central_server/account_query.py`가 `kt00007/kt00015`의 검증 binding·본문·cursor·페이지 순서를 가진 짧은 NAS 세션을 소유한다. `kiwoom_rest/account_query.py`는 완료 batch/context와 legacy·검증 직접 adapter를, `remote_client.py`는 v2 page 검증을, `failover_client.py`는 미완료 NAS batch 폐기와 같은 계좌 직접 재시작을 담당한다. `trade_history_service.py`와 `trade_cost_service.py`는 응답 context의 scope로만 행을 만들고 `journal_workers.py`가 체결·비용 scope 불일치를 저장 전에 차단한다. 일반 시세 `QueryClient` 반환형과 페이지별 failover는 유지한다.
- A4a/A4b 계좌 경계: 뉴스 DB v3는 계좌별 연결 tombstone과 원본 provenance를, 일지 DB v8은 legacy source owner/content hash와 충돌 상태를 소유한다. `central_content_sync.py`는 v1 owner/key와 origin 기반 v2 hash key를 분리하고 `central_journal_sync.py`는 v1/v2 기존행·삭제 scope를 검사한다. UI relay와 수동 묶음 scope 및 구 NAS 호환은 A4b 0a~0c에서 보완됐다. 직접 REST 현재 자격 재검증·직접 WS는 후속 단계다.

## NAS API

- R6b2 실전 인증/과거 조회: `central_server/real_runtime.py`의 owner가 profile별 검증 context/broker/query·계좌 lock·후보/commit fence·keyless bootstrap·실패 복구를 소유한다. 시세 담당은 기존 nas-real-default client/broker/collector를 유지하고 비담당 profile의 과거 조회는 독립 read-only broker/공통 동시 실행 2개를 쓴다. `client.py.prepare_drained_credentials`와 `rest_broker.py`의 큐 작업은 일반 조회 차단을 유지하며 후보만 같은 물리 잠금/한도로 검증한다. `app.py`가 기존 HTTPS credential hook/v3 선택 계좌/v2 기본 대상/수신 scope를 연결한다. 회귀는 `test_real_credential_owner.py`와 인접 인증/REST/모의/계좌/실시간/서버 테스트다. 계좌 실시간 수집/운영 PUT과 실제 시세 역할 전환은 R6b3 후속, PC 입력/계획된 재연결은 R6c다. 새 주문/SQL/범용 manager 없음.
- R6b3a 역할 저장: database.py의 공통 SQLite/PostgreSQL helper가 역할 CAS·binding/계좌 자격 검증·담당 disable/연결 해제 보호를 담당한다. real_runtime.py는 vault 적용 전에 담당 disable을 거절하고 app.py는 인증된 GET만 제공한다. test_market_profile_settings.py가 저장/경쟁/보호/HTTP와 PostgreSQL 검사 helper의 rollback을 검증한다. check_postgres_integration.py의 임시 역할 검사는 운영 역할을 commit하지 않는다. 새 manager나 저장 계층 없이 기존 DB/owner/API 경계를 유지한다.
- R6b3b1 전환 독점 구간: 기존 real_runtime.py owner의 market_role_change async context manager가 역할 작업과 credential 후보의 상호 배제/취소 해제/종료 대기를 소유한다. validate_market_role은 역할·binding·활성 profile·적용된 vault revision·동일 context snapshot을 재검증한다. credential prepare는 disable 정책 읽기 전에도 먼저 예약한다. test_market_role_barrier.py는 실전 owner의 가짜 API/임시 vault fixture를 재사용한다. 독점 구간 자체는 전환하지 않으며 새 manager·DB·WS는 없다.
- R6b3b2 실제 전환: real_runtime.py의 소유 task가 독점 구간에서 drain/검증/CAS/전환/실패 fencing·재기동을 담당한다. rest_broker.py.swap_drained_clients는 기존 큐를 유지하며 물리 client/잠금/한도를 교환하고 broker cache generation을 증가시킨다. account_query.py.rebind_drained_broker는 drain된 manager의 binding/cursor를 보존하며 routing/조회 limiter를 갱신한다. app.py가 인증된 PUT 및 동적 token/시각/실시간 계좌 신원을 연결한다. test_market_role_change.py와 기존 HTTPS 실전 owner 테스트가 전환/복구/취소/재시작/기본계좌를 검증한다. 새 추상 계층·스키마·시장 WS는 없다.
- R6b3c1 실전 복구 조회: 기존 mock_account.py의 private reader base가 연속조회/파서를 공유하고 KiwoomRealAccountReader/KiwoomMockAccountReader가 환경·조회 거래소를 분리한다. AccountRecovery의 기존 MockAccountRecovery import는 alias로 유지한다. real_runtime.py의 context별 단일 read task/paused 상태는 조회 취소·키/역할 변경·종료 때 실제 전체 cycle을 drain한다. rest_broker.py의 계좌 복구 endpoint는 내부 broker에만 사용하고 app.py는 일반 v1 무신원 조회를 차단한다. test_real_account_reader.py/test_real_account_reads.py와 기존 mock/role/owner 회귀가 검증한다.
- R6b3c2a 실전 자동 REST/운영 설정: 기존 real_runtime.py owner/context가 30초 수집 task·OFF/ON·실제 저장 drain·실패 정책·역할/키 재시작을 소유한다. database.py 공통 helper/두 store는 내부 real_account_recovery의 verified binding/정책 revision 검증과 append를 같은 트랜잭션에서 처리한다. credential_runtime.py가 HTTPS 계좌 PUT을 환경별 owner로 분배하고 app.py GET은 rest_poll 상태/성공 관측을 제공한다. test_real_account_monitor.py가 저장 분리/취소/키/역할/실패/HTTPS를 검증한다. check_postgres_integration.py는 임시 계좌의 저장 helper를 rollback으로 검사한다. 새 manager/SQL schema/mock 원장 변경은 없다.
- R6b3c2b 계좌 WS: realtime_collector.py의 parsed account callback과 publish_account_event가 담당 분배·기존 hub/daily 매수 편입을 맡는다. mock_account_monitor.py의 private socket base/구체 real·mock collector는 별도 URL·시간·신원/승인 정책을 유지한다. real_runtime.py owner/context가 비담당 socket·0.5초 wake/30초 backup·별도 event writer·pending/overflow·실제 drain·generation을 소유한다. database.py는 공유 계좌 저장 fence/real_account_event append를 맡고 app.py가 publisher/handler를 연결한다. test_real_account_realtime.py와 기존 실시간/키/역할/모의 회귀가 검증한다. check_postgres_integration.py는 REST/event 멱등/fence/rollback을 검사한다. PC 표시/실환경 동시 토큰은 R6c/R7 후속이다.

- R6a 실전 재연결 기반: 기존 `central_server/realtime_collector.py`가 동일 aggregator/허브를 유지하는 pause/drain/resume·connection generation, token thread/socket 종료·직렬 owned DB 저장/장 마감 작업 완료와 최초 승인 REG의 누적 source baseline/연속 관측시각을 소유한다. `central_server/account_query.py`는 신규 조회 pause/실제 접수 작업 drain/적용 또는 취소 시 선택적인 cursor 폐기를 소유한다. 기존 `rest_broker.py`의 동일 client/priority/generation 교체 경계는 재사용 대상이며 새 실전 owner/API/UI에는 아직 연결하지 않았다. 회귀는 `test_real_reconnect_barrier.py`와 기존 실시간/계좌/REST/모의/초·분봉/서버 테스트다. 실제 키 적용/계획된 재연결 PC 표시는 R6b/R6c 후속이고 새 endpoint/SQL/manager 없음.

- 라우트와 입력 검증: `central_server/app.py`
- VI·저장 조건식 이름 선택·15% cohort 관측 세션 수명·상한가 사실: `central_server/market_events.py`. 기존 `CentralRealtimeCollector` 단일 WS와 `CentralRestBroker`, `RealtimeHub` union을 사용하며 별도 Kiwoom 연결이나 주문 경로를 소유하지 않는다.
- 공개 버전/기능 문서: `central_server/contracts.py`
- 환경설정: `central_server/config.py`
- DB: `central_server/database.py`
- 상태 검사: `central_server/deployment_check.py`, `resource_usage.py`
- 배포: `deploy/synology/`. Compose의 고정 subnet/gateway는 `KIWOOM_DOCKER_SUBNET`·`KIWOOM_DOCKER_GATEWAY`가 소유하고, HTTPS 인증 쓰기 신뢰 프록시는 같은 gateway 값을 사용한다. 중앙 서버 로그 경계는 `central_server/server_logging.py`이며 access 상세와 `rest_broker.py`의 실제 키움 전송 감사 로그는 `server-data/logs`의 일별 회전 파일에만, 일반 서버 상태는 파일과 컨테이너 콘솔에 함께 기록한다.
- 실제 PostgreSQL 경계 검사: `scripts/check_postgres_integration.py` (NAS 서버 컨테이너 안에서 실행)
- 인증 API·WebSocket·자원·스냅샷 운영 검사: `scripts/check_nas_operational.py` (개발 PC/다른 PC 공용)
- 클라이언트 계약: `infrastructure/central_*_client.py`, `kiwoom_rest/remote_client.py`
  - 테스트: 모든 `test_central_*.py`(스키마 버전은 `test_central_schema_migrations.py`), `test_remote_kiwoom_rest_client.py`

## 큰 UI 파일에서 먼저 찾을 클래스

- `main_window.py`: `MainWindow`
- `journal_process.py`: `JournalWindow`
- `stock_news_window.py`: `NewsCellMarkerDelegate`, `StockNewsWindow`
- `news_settings_dialog.py`: `NaverNewsSettingsDialog`
- `news_workers.py`: `NewsSearchWorker`, `NewsPrepareWorker`, `NewsEvidenceWorker`, `AINewsWorker`
- `news_view_model.py`: `NewsDisplayRow`, `StoredNewsEvidence`와 뉴스 표시용 순수 함수
- `news_auto_analysis.py`: 자동/수동 AI 분석 후보 선택 순수 함수
- `news_execution.py`: worker 수명 정리와 AI 실행 경계 정책

## D4 후보 감지·알림

- `central_server/candidate_monitor.py`: 중앙 관측 증분 cursor, D3 판단 호출, bounded checkpoint, stale 품질과 주문 없는 후보 원장을 소유한다.
- `central_server/database.py`, `central_schema.py`: 중앙 v15 shadow state/decision/candidate 저장과 후보 page cursor를 소유한다.
- `central_server/app.py`: 후보 생성 수명, 런타임 설정 교체와 읽기 전용 `/api/v1/research/candidates`를 조립한다.
- `infrastructure/central_content_client.py`: 후보 page 인증 HTTP 호출만 담당한다.
- `presentation/candidate_monitor_dialog.py`: PC 로컬 최초 무음 동기화, event ID 중복 방지, 창·beep 알림과 NAS 재시작 없는 후보 감지/주요 조건 변경을 담당한다.
- `presentation/main_window.py`: 후보 창 열기와 종료 시 polling worker 정지만 담당한다.

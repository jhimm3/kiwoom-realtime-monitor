# 현재 구현 아키텍처

2026-09-16 시장 조회 우선순위 보완: NAS의 단일 시장 REST broker는 매 30초 순위 경계 직전 5초 동안
일봉·분봉·기본정보 같은 새 저우선순위 호출 시작을 미룬다. 실행 중 HTTP는 선점할 수 없으므로 경계 전에
빈 구간을 확보하고, 경계 뒤 들어온 `ka00198`이 큐에서 먼저 실행되게 한다. TOP20 및 계좌 추적 종목의
편입 준비 과정은 신고가 계산용 일봉이 중앙 DB에 없을 때 KRX와 NXT 대상 `ka10081`을 한 번 채우고,
`ka10001`은 KST 당일 `observed_at` 문서만 재사용한다. 앱의 자동 화면 갱신은 이 NAS 저장 자료를 읽는다.

2026-09-16 O2-M 재검토 정정: Ma~Md의 기본 구현은 있으나 자동 모의운영 runner는 연결되지 않았다.
Md는 현재 손익 scalar/출처 문자열을 검사하며 실제 FIFO/비용 근거 producer는 없다.
중지 경합·체결 전 잔고 재사용·terminal 주문 차단을 가짜 transport로 재현했다.
아래 기존 Md 설명을 안전 경계 완료로 해석하지 않는다. 현재 다음 단계는
[설계 결정](reports/O2M_DESIGN_REVIEW_DECISIONS_20260916.md)의 O2-M0 안전 보완이다.
이번 재검토에서는 제품 코드·NAS·주문 상태를 변경하지 않았다.

CR4c 백엔드는 완료된 자동 가설의 baseline run에서 TRAIN/VALIDATION만 복사한 `DevelopmentEvidence`를
콘텐츠 주소형 snapshot으로 만든다. campaign 정책에 명시된 등록 파라미터 값 안에서 부모 설정의 한 필드만
바꾼 후속 가설을 만들며, 같은 Family+전체 설정은 campaign 안에서 다시 등록하지 않는다. 고정 seed는 순서만
정하고 ID는 부모·개발근거·변경 내용으로 정해진다. 회차 생성 수와 campaign 전체 가설 수에 별도 상한이 있다.
연구 DB v23은 부모+근거+정책 revision별 생성/소진/차단 결과를 저장한다. 일시정지 후 정책을 명시적으로
개정하면 완료 부모를 새 revision에서 다시 확장할 수 있다. 저장·등록 뒤 원장 기록 전에 종료돼도 같은 자식 ID를
재인식해 중복하지 않는다. 보고서의 FINAL/OOS 값과 원시 결과는 이 경로에서 읽지 않는다. 자동 최종평가는 계속
금지한다. 전략 연구 창의 `자동 가설 설정 / 현황`은 일시정지 상태에서 Family별 한 파라미터 허용값,
seed, 회차 생성 상한과 전체 상한을 새 policy revision으로 저장한다. 최초 Family는 기존 기준 실험의 전체
정규화 설정에서 기준/첫 이웃 가설을 만들며, 다시 열면 가설 변경·큐 상태·개발 근거와 revision별 확장 결과를 표시한다.

CR4b는 저장된 READY 가설을 기존 지속 campaign과 제한 탐색 실행기에 연결한다.
가설은 `campaign:<id>` scope가 일치해야 하며, 자동 가설 정책을 켠 campaign에 일시정지 상태에서만 등록한다.
연구 DB v22의 campaign 가설 큐는 AVAILABLE→ENQUEUED와 대응 job을 같은 트랜잭션으로 고정한다.
활성 worker 소유권이 없으면 자동 예약할 수 없고, 같은 가설은 완료·재시작·재시도 뒤에도 다시 독립 job이 되지 않는다.
worker는 반복마다 최대 하나를 예약하며 두 등록 Family가 모두 남아 있으면 직전 Family 다음 Family를 우선한다.
각 job은 가설에 저장된 정확한 전략 설정으로 기존 baseline/no-trade trial을 사용한다. grid·ablation·cost stress는 섞지 않는다.
가설 없음·소진·기준 실험 없음은 `WAITING_HYPOTHESIS`와 구체적 사유로 표시한다.
개발 결과 후속 생성과 설정·현황 화면은 CR4c에서 연결됐다. NAS/API/계좌·주문은 바뀌지 않았다.

CR4a는 자동 연구의 첫 단계로 등록 전략의 단일 파라미터 가설과 계보만 추가한다.
`research_hypotheses.py`는 명시 Family·Factor·허용 정수값, 정규화된 기준 설정, 개발 근거 참조와 seed를 받는다.
기준 가설과 한 필드만 다른 후보만 만들며 모든 후보는 기존 전략 config 검증을 다시 통과한다.
콘텐츠 ID는 seed·생성 순서와 무관하므로 같은 조건의 재생성은 독립 가설이나 새 증거로 늘어나지 않는다.
연구 DB v21은 불변 가설 문서와 부모 edge를 append하고, 부모 선행·배치 원자성을 보장한다.
기존 제한 탐색의 baseline/no-trade·trial 실행, campaign 예약, final 원장은 유지한다.
자동 연속 실행·두 Family 순환·실패 반증·미등록 계산식 draft·사용자 화면은 아직 연결하지 않았다.
NAS/API/계좌·주문은 바뀌지 않았다.

CR3d3c는 최종 결과를 후속 전략 개발에 사용했다는 사실을 기존 final 원장에 기록한다.
화면은 검증된 최종 결과와 1~2000자 사용 근거가 있을 때만 `final_holdout_exposure_request/v1`을 만든다.
기존 child가 연구 DB의 window/batch/spec과 RUNNING 후보 부재를 확인한 뒤 `EXPOSED_DEVELOPMENT`를 원자 기록한다. UI는 DB를 열지 않는다.
이 전환은 되돌릴 수 없고, 같은 창은 다시 미사용 final로 평가할 수 없다. 다음 final은 새 미사용 기간이 필요하다.
취소·오류·결과 범위 불일치에서는 이전 후보 표를 유지한다. DB v20과 NAS/API/계좌·주문은 바뀌지 않았다.

CR3d3b는 전략 연구 창에 별도 `FinalHoldoutDialog`을 연결한다.
화면은 선택한 final request를 정규화해 UUID 작업 파일로 복사하고 기존 `research_process --evaluate-final` child를 낮은 우선순위로 실행한다.
화면 프로세스는 DB/source를 열지 않는다. 자식이 준비·접근 원장·실행/복구를 담당한다.
결과의 native exit/status, batch/window, 정렬 candidate hash, 구현 hash, recovery ID, batch 완료 계산을 검증한 뒤에만 기존 표를 교체한다.
취소·오류·위조 결과에서는 이전 표를 유지한다. terminal FAILED/CANCELLED는 선택·사용자 reason·새 request ID/owner가 있을 때만 명시 recovery snapshot으로 보낸다.
완료 결과의 개발 노출은 CR3d3c에서 연결했다. 자동 final 선택/실행과 새 가설 생성은 아직 활성화하지 않았다. DB v20·NAS/API·계좌·주문은 바뀌지 않았다.

CR3d3a는 최종평가를 기존 research_process 별도 프로세스 경계에 연결한다.
independent_final_holdout_request/v1 JSON은 batch, fixed candidate 문서, 접근 nonce/시각, 실행 owner와 선택적 recovery를 완전한 snapshot으로 보존한다.
parser는 4 MiB와 정확한 외곽/후보 필드, 현재 구현 기준 후보 hash, timezone-aware 접근, recovery 대상 hash를 검사한다.
이 단계의 parser는 source bytes나 연구 DB를 읽거나 만들지 않는다. 실행 함수가 CR3d2a 준비와 CR3d2b/2c 실행을 순서대로 호출한다.
--evaluate-final은 기존 --request/--campaign/--validate-partitions/--compare-runs와 상호 배타적이며 결과/취소 파일이 원본·DB·run artifact를 덮지 못한다.
종료 결과는 기존 final result에 status와 kind만 더한다. 후보 실패는 과학적 terminal 결과이고 프로세스 파싱/준비 실패와 구별한다.
UI는 CR3d3b, EXPOSED_DEVELOPMENT 기록은 CR3d3c에서 연결했다. 실행 중 후보별 진행 파일은 후속이다. DB v20과 NAS/API/계좌·주문은 바뀌지 않는다.

CR3d2c는 final terminal 실행의 자동 재시도를 계속 금지하면서 명시 recovery만 연결한다.
호출자는 candidate hash별 request_id/reason을 제출하고, 현재 코드/입력/정책/경로 재검증 뒤 기존 상태가 FAILED/CANCELLED인지 확인한다.
immutable output manifest가 있으면 게시 완료 여부가 불확실하므로 복구하지 않는다. DB의 부분 immutable 행은 deterministic 재실행에서 내용 일치로만 재사용한다.
연구 v20은 final execution generation과 REQUESTED/CLAIMED recovery 이력을 저장한다. request 기록과 실행 claim은 분리되고 claim만 같은 run을 RUNNING으로 되돌린다.
같은 request는 멱등이지만 CLAIMED 요청을 다시 실행권으로 쓰지 않는다. 매 추가 복구는 새 감사 요청이 필요하다.
COMPLETED/RUNNING/미실행/EXPOSED_DEVELOPMENT 창은 복구할 수 없다. crash로 남은 RUNNING orphan 회수는 아직 제공하지 않는다.
read-only 비교는 v17/v18/v19/v20을 마이그레이션 없이 읽는다. CLI는 CR3d3a, 실행/명시 복구 UI는 CR3d3b, 개발 피드백 노출은 CR3d3c에서 연결했다.

CR3d1: final_holdout_batch/v1은 strict KRX의 단일 OOS 정책, 자료 ID/revision hash, session profile, 정렬된 1~200개 후보 scientific hash를 고정한다.
window identity는 active start/end를 UTC microsecond로 정규화한 값이며 batch identity는 전체 정규 spec hash다. 자료/version/profile 변경이나 겹치는 기간은 새로운 미사용 창으로 인정하지 않는다.
연구 v18의 창(FINAL_RESERVED/EXPOSED_DEVELOPMENT)과 event 원장은 별도 실행·보고서 테이블을 수정하지 않고 추가한다.
최초 접근에서 batch를 원자 고정한다. 같은 요청은 멱등이며 동일 batch의 기술적 새 접근만 허용한다. 노출은 이유/시각을 append하고 영구 유지한다.
read-only 비교는 기존 v17/v18/v19와 현재 v20을 마이그레이션 없이 읽는다. 접근 ledger는 v18, 실행 소유권은 v19, 명시 복구는 v20이 필요하다.
CR3d2a는 기존 research_process 안에서 후보 full scientific hash와 full frozen source를 검증하고, final 전용 시간 입력을 준비한 뒤 과거 연구 footprint 검사/접근 원장을 거쳐 반환한다.
후보 hash에는 전략·실행/명시 비용·세션·실제 구현 hash가 들어가고 날짜/경로/자원 설정은 빠진다. full source binding은 batch에 있고 projected 입력에는 미선택 원본의 ID/통계를 넣지 않는다.
기존 projection 공유 함수는 개발 출력 계약을 유지한다. final runtime tag/descriptor가 있으면 일반 runner 시작과 개발 재활용을 차단한다.
최종 평가 descriptor는 canonical UTC 정책으로 복원하여 동일 batch의 timezone 표기 차이로 입력 ID가 달라지지 않는다.
과거 실제 입력 captured_range와 evaluation_spec의 각 구간/warmup 합집합을 같은 BEGIN IMMEDIATE에서 확인한다.
예전 연속 runner는 평가 구간 사이/밖에서도 입력을 처리하므로 평가 정책만으로 사용 범위를 좁히지 않는다. 실제 범위가 없거나 malformed이면 차단하며 결과/성과는 읽지 않는다.
검증을 위한 raw bytes 읽기는 접근 event 이전 trusted 코드 안에서 수행한다. final 입력 외부 반환/전략 실행의 선행 접근 기록이며 전체 bytes 읽기 전 방화벽은 아니다.
CR3d2b의 independent_final_holdout/v1은 v19 후보별 소유권 claim을 확인한 경우에만 final input을 실행한다.
후보마다 새 엔진/현금/상태를 사용한다. claim과 research run 생성은 원자적이고, 결과 manifest 게시 후 run과 final execution을 완료한다.
완료 캐시는 DB run/report와 immutable output을 대조한다. 실행 중은 BUSY이며 실패·취소는 terminal로 고정하고 자동 재시도하지 않는다.
고정 후보를 모두 독립 실행하되 비교·합산·승자 재선택을 만들지 않는다. RUNNING 중 개발 노출도 막는다.
CLI는 CR3d3a에서, UI는 CR3d3b에서 연결했다. 기존 v1 실행을 새 원장으로 자동 감시하거나 전체 시스템의 미사용 기간을 증명하지 않는다.

CR3c2: independent_development_validation/v2가 한 hash 정책의 선택 buckets×시간순 개발 folds를 순차 실행한다.
2~20 bucket/1~20 fold/총 200 step 제한이며 가격·성과에 따라 정책을 바꾸지 않는다. request는 고정 single_run이다.
기존 source 한 번 로드/독립 v3 projection/새 engine/claim·cache/취소·자원·시간 예산 처리와 scoped run identity를 재사용한다.
fold 먼저/bucket 다음으로 실행하며 모든 step_key/fold/symbol_bucket·상태와 전체 batch_status를 유지한다.
각 group_comparisons는 동일 bucket의 확인된 run ID만 집계한다. 전체 comparison은 None이며 서로 다른 bucket PnL/MDD를 합산하지 않는다.
요청/결과 v1과 DB v17은 유지한다. 출력 v2는 version/symbol_partition/총 requested_step_count와 그룹별 요청·완료·미시작 개수를 추가한다.
CR3c3에서 기존 순차 검증 창에 v2 실행/취소/250ms 진행 표시·같은 snapshot 재개를 연결했다. 기존 시간 v1 화면도 유지한다.
전체 fold×bucket 표와 그룹별 요청/완료/미시작·적격/양수·구간 손익 중앙값/최악 구간 MDD를 표시한다.
그룹 comparison의 COMPLETE는 식별된 실행의 적격이며 전체 요청 완료가 아니다. 계약/종료 불일치 시 이전 두 표를 보존한다.
GUI의 DB/원본 입력 읽기나 새 프로세스 관리 계층 없이 기존 child/file 경계를 재사용한다.
Windows에서 GUI 읽기와 결과 rename 충돌이 단계 FAILED/프로세스 오류가 되는 것을 실제 child 반복으로 재현했다.
기존 결과 발행은 같은 임시 파일의 원자 rename을 해당 Windows PermissionError에만 최대 10회/225ms 제한 재시도하며 지속 오류는 전파한다.

CR3c1: stock_hash_partition/v1의 버전/salt/bucket_count/bucket을 development_partition/v3에 고정한다.
SHA256(version,salt,정규 종목코드) mod bucket_count이며 등록 순서/순위/주가/새 종목으로 기존 배정을 바꾸지 않는다.
기존 시간 projection은 전체 as-of TOP20/peer bars/테마를 보존한다. 선택 bucket은 거래 대상만 제한하며 시장 context를 재구성하지 않는다.
runner는 admission을 엔진 bar/전략 평가 전에 확인한다. 각 명시 실행은 새 현금/빈 상태이며 warmup/경계 censor 계약은 유지한다.
정규화 ranking.py도 명시 profile 실행 hash에 포함한다. 배정 동작 변경을 cache identity가 반영하며 legacy 고정 hash는 유지한다.
완료 보고서의 종목별 closed trade count는 전체 closed count와 맞아야 하며 다른 bucket 체결은 INVALID다.
bucket 정책은 기존 집계의 조건에 포함한다. 서로 다른 거래 대상 bucket을 기존 동일 조건 통계로 섞지 않는다.
v2 partition 요청/직렬화와 DB v17은 유지한다. CR3c2 grouped batch는 v3 partition을 선택하며 CR3c3 화면에서도 실행한다.
shared 시장 context를 사용하는 개발 검증이며 미사용 최종 종목 holdout 격리 완료가 아니다.
기존 전체 context 품질 판정은 개별 허용 종목의 전체 warmup/분봉 coverage 보장이 아니다. 그룹별 결측/편중 보고는 후속이다.

CR3a2 명시 독립 개발 실행: DevelopmentPartitionSpec(development_partition/v2)는 기존 v1 평가에서
TRAIN/VALIDATION 하나를 선택한다. 원래 v1 연속 실행 의미를 덮어쓰지 않는다.
검증된 export/bundle을 읽은 뒤 RAM에서 선택 fold/warmup만 복사한다. 엔진에는 원래 전체 dataset을 전달하지 않는다.
직전 TOP20/테마는 원래 시각/ID를 보존해 초기 상태로 사용하며 미래/시각 불명 테마는 제외한다.
선택 데이터·평가/명시 profile로 identity/hash/count를 다시 만들고 전체 source quality/children/ID/watermark는 제외한다.
기존 새 PaperExecutionEngine을 fold마다 사용한다. warmup은 replay history만 갱신하며 주문/전략 상태/후보는 만들지 않는다.
같은 fold 내부 날짜는 연속 유지하고 경계 open position은 fold 종료 시각에 censor한다. 독립 손익을 한 계좌로 합치지 않는다.
JSON single_run/rank_comparison/limited_search 및 CLI --development-partition을 연결했다.
CR3a3 유한 탐색은 원본 ID/hash 확인 후 선택 입력과 단일 평가의 유효 spec으로 저장한다.
원본 전체 평가/ID는 scientific cache에 넣지 않으며 제출 요청의 원본 계약은 그대로 유지한다.
기본/no-trade/ablation/cost-stress는 같은 선택 자료를 쓴다. 자동 final은 아직 금지다.
CR3a4a는 별도 프로세스 등록과 기존 campaign/source를 연결했다. 연구 DB v17 source_request_json은
불변 원본 spec을 보존하고 기존 request_json/예산 revision은 유효 실행 spec을 유지한다.
재시작은 원본을 검증·분리한 spec이 등록된 유효 spec과 일치할 때만 실행한다.
구간 밖 차이만 있는 새 자료는 기존 job의 acceptance만 추가한다. 원본 경로/spec을 덮어쓰지 않는다.
품질은 선택 자료로 계산하며 warmup 시작 봉이 active 시작 전에 실제로 수신됐는지 확인한다. 연속 coverage 보증은 아니다.
CR3a4b는 독립 구간의 화면 등록을 기존 별도 프로세스에 연결했다. UI는 작은 요청/선택 메타데이터만 처리한다.
UUID별 요청 snapshot/결과/취소 파일을 사용하고 parsed request의 절대 경로와 정책을 고정한다.
새 campaign 선택은 paused 상태로 먼저 보존한다. 등록은 trial/주문을 실행하지 않으며 성공 뒤에도 paused다.
종료 코드 0과 결과 ID·원본 evidence를 해당 DB job 한 행으로 대조한다. 오류 문구는 상태 refresh 뒤에 유지한다.
등록 중 다른 실행/설정을 거부한다. 창 숨김은 비동기 취소 요청이며 commit 전에 못 막은 완료 job은 보존한다.
CR3b1은 기존 평가 모듈의 순수 계산과 repository 단일 SELECT로 명시 독립 run/report를 비교한다.
조건 key가 다른 결과는 혼합하지 않으며 중복/겹침/자료 revision과 실패/누락/표본 부족을 보존한다.
구간 손익 분포/최악 구간 MDD만 제공한다. DB v17/기존 search 선택 계약은 유지한다.
CR3b2 --compare-runs는 기존 v17을 read_only/timeout 1초로 조회하며 loader/runner/마이그레이션을 호출하지 않는다.
연구 화면의 독립 구간 결과 비교 viewer는 별도 조회 프로세스/UUID 파일을 사용하고 캠페인과 상태를 공유하지 않는다.
UI DB/원본 입력 로드 없이 결과 scope/version/종료 코드 대조 뒤 KST/구간 손익·MDD/표본/제외 근거를 표시한다.
닫기/취소/오류는 이전 표 보존, 앱 종료는 own child 정리다.
CR3b3 --validate-partitions는 고정 전략과 원본 ID/hash를 확인해 개발 fold 2~20개를 순차 실행한다.
source는 한 번 읽고 구간마다 선택 입력/초기 상태를 사용한다. scoped scientific ID와 v17 원자 선점을 재사용한다.
캐시/취소 재개/실패·BUSY/미시작 상태를 보존하고 batch만 scoped run의 종료 상태를 확정한다.
implementation hash 고정·대조와 RUNNING/최종 진행 저장을 연결했다. 비교는 확인된 ID 범위만이며 전체 batch와 구분한다.
기본 단일 실행 ID/예외 처리와 DB schema는 유지한다.
CR3b4는 연구 창의 여러 구간 순차 검증 버튼으로 별도 DevelopmentValidationDialog를 연다.
새 정책/Manager 없이 기존 실행기와 AuxiliaryProcessManager를 재사용한다. 실행 수명은 캠페인/읽기 전용 비교와 독립이다.
파싱된 절대 경로 요청 snapshot으로 실행/명시 재개하며 250ms마다 제한된 atomic 진행 파일만 읽는다.
외부/내부 구간 scope·기간·역할·ID·구현 hash·전체 상태 및 native 종료를 확인하고 모든 미시작/실패 상태를 표시한다.
닫기는 숨김/비동기 취소, 앱 종료는 own child stop/임시 파일 정리다. 완료 scientific DB 결과는 유지한다.
자동 시간 slice 반복과 orphan 복구/영속 batch 운영 원장은 아직 없다. 각 child의 자원 한도는 PC 전체 합산 한도가 아니다.
자동 final/새 날짜 확장은 후속이다.
기존 v1 요청은 source_request_json 기본 빈값으로 같은 spec을 재구성한다. NAS 변경은 없다.
이번 단계 NAS 재빌드는 필요 없다.

CR3a1 개발 후보 선택 근거: 기존 report → search 경계에서 `DevelopmentEvidence`를 만든다.
TRAIN/VALIDATION의 fold 상태/사유/요약 지표만 불변 값으로 복사하며 전체 report 상태/사유와 OOS 결과는 제외한다.
개발 fold에 실패가 있으면 전체 보고서가 ELIGIBLE이어도 후보는 부적격이다. 미등록 개발 상태도 적격으로 통과시키지 않는다.
후보 TrialOutcome.report_status는 개발 fold의 집계 상태다. 원래 전체 report/최종 보고서는 수정하지 않는다.
기존 v1 fold 요약 산술과 보고·DB v16은 유지한다. 명시 독립 실행은 위 CR3a2를 따르며 최종 접근 원장은 미구현이다.

CR2c3c2 PC 완성 자료 복구: 자동 source는 저장된 완성 폴더를 NAS 준비보다 먼저 검증·등록한다.
완성 게시 후 등록 전에 종료됐더라도, 용량 상한/접속 실패 때문에 기존 자료 등록이 막히지 않는다.
동결 scope/manifest와 worker lease/실행 의도 fence를 유지한다. 등록으로 대기열이 차면 새 준비는 대기한다.
등록 후 NAS acceptances를 다시 조회하며, 새 준비 경로만 추가 검증한다. signature는 등록 성공 뒤 기록한다.
완성 폴더는 완료/미등록/무관 자료라도 자동 삭제하지 않는다. 참조는 기존 연구 v16 원장을 재사용한다.
파일 이동/압축 archive나 NAS 원시자료 삭제는 추가하지 않았다. NAS 재빌드가 필요 없는 PC 변경이다.

CR2c3c1 PC 임시 정리: 연구 v16은 작업별 staging_cleanups만 추가한다. NAS 자동 source worker가
현재 root와 준비 원장의 경로/op ID/marker hash를 대조하고 24시간 이상 지난 미완성 staging만 정리한다.
완성 manifest가 하나라도 있으면 보존하므로 유효한 외부 일지·연구 근거도 삭제하지 않는다.
일지 DB의 모든 참조를 스캔하는 보관 기능은 아직 아니다. 완성 폴더/DB/원시 시장자료는 삭제하지 않는다.
삭제 직전 current worker/의도/현재 root/모든 캠페인 jobs·acceptances 및 준비 입력 참조를
SQLite BEGIN IMMEDIATE fence에서 재확인한다. manifest·표시·알려진 파일도 다시 검사한다.
최대 64 entry, 20 작업/pass, 약 0.25초의 cooperative checkpoint 예산으로 개별 파일을 지운다.
heartbeat/취소 callback은 DB write fence 밖에서만 실행한다. OS 파일 호출 중 hard timeout을 보장하지는 않는다.
marker 마지막 삭제/영속 READY intent로 부분 정리를 재개한다. 없어진 경로는 MISSING으로 기록해 실제 삭제로 추정하지 않는다.
잠김/IO 오류는 별도 원장에 60초부터 최대 1시간 backoff를 적용한다. 기존 연구/수집 실패 횟수는 바꾸지 않는다.
완성 자료 등록 복구/보호 보관은 위 CR2c3c2를 따른다. NAS 재빌드는 필요 없다.

CR2c3b PC 연구 폴더 예산: 연구 DB v15는 source의 storage_cap_bytes(기본 0 무제한)와
준비 원장(operation/source/campaign/root/cap/owner/generation/state/시각/staging_path/input_path)을 추가한다.
worker는 signature 변경 뒤 준비를 예약한다. 기존 lease로 중복 파일 준비를 직렬화하며 stale 원장은
ABANDONED로 바꾸기만 한다. 예약은 worker lease와 별도로 시작 후 120초에 만료되어 종료 기록 유실 때도
회수/과거 게시 차단이 가능하다. 임시·완성 sidecar의 operation_id로 강제 종료 전후 자료를 대조할 수 있다.
상한이 있으면 상위 폴더의 기존 파일도 합산하고 새 저장마다 자료/manifest/표시 byte를 차감한다.
완성 게시 전 용량을 다시 확인하고 짧은 DB 트랜잭션의 owner/의도 검증 아래 rename한다.
기존 NAS 관측 GET/worker/resource guard를 유지하고 GUI는 작은 설정만 저장한다.
cap/다른 준비 작업 때문에 대기하면 WAITING_STORAGE로 60초 후 확인하며 실패 상한에 포함하지 않는다.
이는 폴더 파일 길이 기준 앱 예산이다. 외부 프로그램의 동시 쓰기를 막는 파일시스템 quota가 아니다.
실제 정리/보관·외부 일지 참조 보호는 CR2c3c다. 기존 사용자 파일과 NAS 원시 자료를 삭제하지 않는다.

CR2c3a PC 보관 준비: 새 NAS 연구 export의 임시/완성 폴더에 별도 ownership sidecar를 기록한다.
기존 manifest와 evidence hash는 유지한다. 읽기 전용 목록은 파일 내용 전체를 읽지 않으며
완료 포함 모든 캠페인 jobs/acceptances 경로를 보호한다. 링크·무표시·손상 자료는 보호하고
상한/IO 오류 때 complete=false와 부분 용량만 반환한다(총 사용량 확정 아님).
NAS source worker 결과의 storage_inventory는 최대 1000 filesystem entry 진단이다.
목록은 삭제 허가가 아니며 DB 참조 snapshot 이후 변경과 외부 일지 참조까지 보장하지 않는다.
실제 삭제/cap/보관 원장은 다음 CR2c3b다. DB v14와 NAS 수집/주문 경계는 그대로다.

CR2c2b는 source별 opt-in으로 같은 범위 NAS 입력을 자동 준비한다. 기존 앱 NAS 설정 파일의 위치만 v14에 저장한다.
worker가 scan 시 현재 설정을 읽고 기존 CentralContentClient의 DB export API만 호출한다. 토큰은 연구 DB/결과에 넣지 않는다.
NAS 관측 1행 probe와 테마 이력 signature가 같으면 전체 download를 생략한다.
다른 snapshot은 probe의 fixed watermark를 이어 읽고 기존 writer로 임시 디렉터리에 streaming 저장한다.
reader/scope/fingerprint 검증 뒤 새 폴더로 rename하고 기존 자동 등록기로 연결한다. 같은 근거는 게시하지 않는다.
signature ack는 폴더 확인/등록 뒤 worker/RUNNING fence를 확인한다. backlog 실패 때 ack하지 않아 재시도 자료를 놓치지 않는다.
일별 bundle은 기존 일별 captured range/ordinal/세션 계약을 재구성하며 새 거래일을 임의 추가하지 않는다.
known backlog가 꽉 차면 API 요청을 생략한다. 실패는 기존 source backoff이며 직접 키움 우회는 없다.
요청 timeout 10초, 기존 source 시간/RSS/CPU guard, 전체 준비 입력 encoded byte 상한(memory MiB/16)을 적용한다.
날짜 확장/디스크 보호 보관/실제 NAS 장시간 부하는 후속이다. NAS 서버/API는 바꾸지 않았다.

CR2c2a는 연구 DB v13의 source/acceptance 원장에 같은 범위 새 export 등록을 연결했다.
사용자가 기준 실험/상위 폴더/ON·OFF를 일시정지 후 저장한다. 폴더 변경·재시도에도 기존 acceptance는 보존한다.
worker가 기준 입력 identity/code hash를 확인해 scope를 동결하고 60초마다 직접 하위 완성 입력을 확인한다.
기간/종목 범위/kinds/session/universe/order contract가 다르면 등록하지 않는다. 날짜를 자동 이동하지 않는다.
현재 두 Family와 시장 보고가 소비하는 ranking/membership/KRX 분봉/테마의 fingerprint만 비교한다.
watermark/id/ordinal만 다르거나 무관 kind/비KRX 봉만 추가된 입력에는 새 job을 만들지 않는다.
source 초기화·job/예산/acceptance 저장은 활성 worker/RUNNING 의도를 확인한다.
기존 source의 파일을 덮는 대신 새 완성 폴더로 발행해야 한다. 승인된 경로는 manifest hash로 재읽기를 생략한다.
source 오류 backoff/격리는 worker 실패와 별도이고 기존 연구는 계속된다. 백로그 포화는 정상 대기다.
기존 CPU/RSS 제한과 30초 source scan 한도를 재사용한다. NAS 자동 export 준비는 CR2c2b이며 새 날짜 정책·보관은 후속이다.

CR2c1은 PC 연구 DB v12에 campaign worker 현재 상태와 세대별 실행 이력을 추가했다.
DB 원자 claim으로 중복 실행을 막고 worker는 30초 lease를 약 1초마다 갱신한다.
trial commit은 기존 campaign/search 소유권에 worker 소유권도 확인한다.
예상하지 못한 종료·시작 실패·lease 만료는 같은 attempt에 한 번만 기록한다.
기존 campaign policy의 실패 상한/백오프를 worker별 독립 횟수에 적용한다(기본 30/60초 대기, 연속 3회 격리).
60초 생존 갱신 후 연속 실패 횟수를 초기화한다. 사용자 일시정지/숨김/앱 종료는 실패에 포함하지 않는다.
앱 재시작은 저장된 대기/격리를 유지하며 명시 시작/재개만 worker 재시도 이력을 초기화한다.
관련 새 자료 자동 등록과 디스크 보관은 아직 후속이며 실제 24시간 운용을 검증한 상태는 아니다.

CR2a는 기존 PC 연구 저장소 v10에 캠페인 정책 revision/사용자 실행 의도/job/cycle 원장을 추가했다.
등록 시 기존 유한 ExperimentSpec과 input_path를 동결하고 기존 search job identity를 재사용한다.
단일 트랜잭션으로 cycle/sequence/owner/generation을 예약하며 만료 소유자는 갱신·완료할 수 없다.
재시도→미실행 가설→관련 새 자료 순으로 준비된 작업을 예약하고 실패 backoff/자원 차단을 분리한다.
등록된 job 집합의 완료와 실제 search job 완료를 함께 확인하며 응답 유실도 기존 결과를 재사용한다.
기존 v9 데이터는 그대로 보존한다. 자동 최종평가/새 가설 생성은 OFF다.

CR2b1은 DB 동결 spec/context를 기존 유한 실행기로 실행하는 별도 캠페인 worker를 연결했다.
campaign heartbeat와 기존 search lease를 유지하고 trial 결과 commit 시 두 소유권과 RUNNING 의도를
단일 트랜잭션에서 확인한다. GUI의 등록/시작·재개/일시정지/중지는 기존 연구 창에 있다.
PC state_dir의 선택 index에는 DB/결과 경로와 campaign_id만 저장한다. 메인 창이 연구 dialog를 생성할 때
RUNNING 의도를 자동 복원하며 원래 JSON 파일은 재개에 필요 없다. 숨김/앱 종료는 의도를 유지하고 worker만 종료한다.
실험 횟수 한도는 실제 등록 조합 소진과 구별한다. 미실행 조합이 남으면 NEEDS_ATTENTION/예산 확대 필요다.
기존 유한 완료 캐시는 유지한다. worker crash 격리는 CR2c1이며 자동 새 자료 선택/보관은 후속이다.

CR2b2는 PC 연구 DB v11의 job별 운영 예산 revision과 cycle 예산 revision을 추가했다.
원래 동결 request_json/experiment identity/trial 결과/보고서를 유지하고 현재 운영 예산만 실행 요청에 겹친다.
일시정지 후 활성 worker 종료와 expected budget revision을 확인한다. 누적 trial 한도는 줄이지 않으며
전략/자료/평가/생성 조합 한도 변경은 운영 예산 편집으로 허용하지 않는다.
기존 start_search_job의 한 트랜잭션에서 캠페인 소유권·예산을 검증한 뒤 완료 job을 열고 search lease를 취득한다.
일반 유한 요청은 기존 completed cache를 사용한다. GUI는 실험 선택/횟수·회차 시간·메모리·CPU 예산과
자원 차단/실패의 명시 retry를 연결했다. 새 서버/Manager/주문 연동은 없다.

CR1a는 PC 명령에서 여러 명시 KST 날짜의 NAS 일별 export를 준비하고 검증된 bundle index만 추가한다.
기존 24시간 중앙 API와 일별 불변 파일/ordinal/ID를 재사용하고 테마 잘림·변조·입력 충돌은 거부한다.
FrozenResearchBundle은 기존 FrozenResearchDataset을 대체하지 않는다. CR1b의 `load_research_input`이
일치하는 명시 session_profile을 확인하고 기존 runner용 runtime 입력으로 연결한다. 일별 ordinal/파일은
유지하고 UTC available_at/accepted_sequence/revision_id로 정렬·중복 제거한다. 같은 시각의 후속 ingest와
미래 자료는 해당 순서 전까지 보이지 않는다. 한 실행 엔진으로 현금/보유를 이어가며 날짜마다 청산하지 않는다.
하루 묶음은 원래 일별 identity/결과를 유지한다. CR1b 자원 정책은 기본 512MiB와 CPU 목표 50%를
CLI/앱 별도 연구 프로세스에 적용한다. 입력 바이트×8의 보수적 preflight와 로딩/실행 중 실제 RSS를 확인하고
약 50ms batch마다 CPU 시간과 이미 지난 IO 대기를 고려해 짧게 양보한다. OS 강제 quota는 아니다.
bundle 증분 cursor는 최신 유효/무효 정정 선택을 전체 replay와 동일하게 유지한다. 자원 중단은 검색 실패
결과가 아니라 기존 INTERRUPTED attempt이며 재시도 가능하다. GUI 자동 재개는 자원 차단을 무한 재시도하지 않는다.
1/5/20일 작은 실행 fixture와 20종목·390분 입력 크기를 계측했다. 실제 NAS 전체기간 처리량은 V1에서 확인한다.
지속 campaign과 등록 범위 안의 자동 가설은 CR2~CR4에서 구현됐다. 실제 NAS 전체 규모 24시간 운전과
CPU/RSS·디스크 증가량 계측은 V1에서 확인한다.

R6b3c2b는 시세 담당의 기존 시장 WS 00/04를 실전 계좌 owner에 전달하고 비담당 계좌 WS를 연결했다.
기존 mock 계좌 WS의 socket 수명은 private base로 공유하며 구체 real/mock URL·관측시간·신원 정책은 분리한다.
real 전용 연결은 명시 LOGIN/REG 성공 뒤 REAL을 전달한다. OFF/키/역할 변경은 전용 socket/token 작업도 drain한다.
계좌 owner의 별도 writer가 scope/binding/설정 revision 검증 후 real_account_event 내부 문서를 append한다.
이벤트 재조회 신호는 0.5초 debounce하고 30초 REST backup을 유지한다. 시장 callback은 IO를 기다리지 않는다.
비담당 이벤트도 기존 hub/daily 매수 종목 수집 범위에 합치며 담당 이벤트를 중복 발행하지 않는다.
시장 재개 전에 새 monitor generation/계좌 수집기를 준비하여 첫 이벤트를 받는다.
monitor_status.realtime에 실제 ready·마지막 저장·안전한 오류·queue overflow 수를 공개한다.
DB 실패 pending은 재시도하고 살아 있는 전환 commit을 막는다. 강제 종료를 견디는 영속 outbox는 아니다.
socket 종료 실패 context는 새 연결/변경 commit을 막고 수집 paused/applied null로 유지한다.
R6c1은 기존 PC NAS 설정에 실전 계좌 프로필 생성/키 확인·적용·비활성화와 조회 ON/OFF를 연결했다.
실전/모의 HTTPS client와 진행 요청은 별도이며 실전 화면에서 모의주문 허용은 제공하지 않는다.
계좌 설정 revision 적용과 monitor_status.realtime의 실제 REG 승인은 따로 표시한다.
R6c2는 담당 collector의 계획된 재연결을 최대 30초로 제한하고 health/capability/WS에 상태를 배포한다.
PC worker는 generation별로 늘어나지 않는 로컬 deadline을 유지한다. 계획된 상류 오류만 유예하며
실제 transport 오류/대기 종료는 기존 장애 정책에 복귀한다. 휴장/구독 대기는 별도로 표시한다.
paused TR의 대기는 일반 API 오류로 구분해 REST 로컬 전환을 유발하지 않는다. 기존 NAS DB 우선은 유지한다.
메인 상태 slot은 표를 비우지 않고 변경/수신 재개를 표시하며 교체 전후 허용 0B 관측 간격을 알린다.
관측 간격은 최신 상태와 PC/NAS 로그에 남으며 종목별 누락량이나 별도 영속 DB 이력은 아니다.
동시 토큰/실시간 운용·실제 교체 간격은 실환경 미검증이다. 누적 배포/운영 확인 R7이 후속이다.
R7 배포 준비에서 Docker context의 백업/비밀 제외를 보완하고 build 세 곳을 갱신했다.
현재 누적 build는 `2026.09.16-runtime-credentials-r7-deploy-v1`이다. NAS 원본 백업/소스 동기화와
전체 763개 hash 확인을 완료했다. 상세는 R7 배포 보고서/대기 문서를 따른다.
실행 이미지의 R7 build/health 일치·DB 읽기·앱 WS ready·30초 순위 회차 진행을 확인했다.
HTTPS 8443 프록시의 TLS/DB/WSS ready와 실제 peer 172.23.0.1 신뢰 검증을 완료했다.
앱 저장 NAS 주소도 HTTPS로 전환했고 접속 토큰/다른 설정은 유지한다. 실행 중인 앱은 재시작이 필요하다.
장중 REG/다계좌·교체 관측 간격·PostgreSQL 통합/실제 Linux 권한 검증은 남아 있다.

R6b3c2a는 기존 RealCredentialOwner/context에 계좌별 30초 REST 자동 수집과 ON/OFF를 연결했다.
첫 자동 계좌 조회도 30초 뒤 실행하여 부팅의 첫 순위 요청을 앞에 둔다. 모든 요청은 기존 broker를 사용한다.
동일 계좌 수동 조회가 진행 중이면 자동 회차를 건너뛰며 두 번째 요청을 추가하지 않는다.
실전 정규화 결과는 real_account_recovery 내부 문서로 저장하고 mock 실행 원장에는 쓰지 않는다.
저장 트랜잭션은 verified scope/현재 binding/profile/monitor 설정 revision을 재검증한다.
키/역할 변경·OFF·종료가 실제 조회와 background 저장을 drain한다. 계좌 OFF는 시장 collector를 재연결하지 않는다.
실전 applied_revision은 REST 정책 적용이며 성공 관측/WS 승인과 다르다. monitor_status.mode=rest_poll로 명시한다.
계좌 WS 분배/비담당 WS는 R6b3c2b, PC 표시 R6c·누적 배포/실환경 R7은 후속이다.
당시 누적 build는 `2026.09.16-runtime-credentials-r6-real-account-monitor-v1`이다.

R6b3c1은 실전 read-only 계좌 복구 조회와 context별 단일 소유 task를 구현했다.
mock_account.py의 기존 파서/연속조회는 private base로 공유하고 real/mock reader의 환경 검사를 유지한다.
실전 주문은 통합 조회, 잔고는 두 venue 결과를 중복 합산하지 않으며 충돌/미완성은 부분 결과를 반환하지 않는다.
시세 담당은 기존 중앙 broker, 비담당은 기존 계좌 broker/동시 실행 2개 limiter를 사용한다.
키/역할 전환과 종료가 전체 조회 cycle을 drain하며 호출자 취소가 실제 조회를 버리지 않는다.
일반 v1 query는 새 복구 계좌 API 네 개를 차단한다. 당시 자동 monitor/계좌 저장/WS/운영 PUT은 미연결이었다.
당시 누적 build는 `2026.09.16-runtime-credentials-r6-real-account-reads-v1`이다.

R6b3b2는 역할 독점 구간 안에서 물리 drain·snapshot/DB CAS·검증 client 교환·재연결을 실행한다.
기존 중앙 broker/단일 collector/집계/hub/큐를 유지하고 두 계좌 client의 토큰·잠금·호출 이력을 복제하지 않는다.
각 계좌 query manager는 drain 후 broker만 바꾸므로 기존 binding/cursor와 legacy 기본계좌/v2 대상을 보존한다.
실시간 token/시각/계좌 신원 resolver는 현재 시세 담당을 사용한다. 저장된 담당은 재시작에도 복원한다.
인증된 역할 PUT을 제공하며 적용 revision은 routing/재연결 시작 기준이고 REG 준비는 별도다.
저장 전 실패는 이전 routing을 복구한다. 저장 후/결과 불명확/복구 실패는 조회·시장 연결을 차단하며,
기존 역할로 임의 복귀하지 않고 저장 상태/키 확인 후 서버 재시작으로 복구한다.
R6b3b2 당시 build는 `2026.09.15-runtime-credentials-r6-market-role-switch-v1`이었다.

R6b3b1은 기존 실전 owner에 역할 전환의 독점 구간과 직전 snapshot 재검증을 구현했다.
credential 후보와 역할 작업은 서로 시작을 막고, 역할 작업 취소/예외는 구간을 해제한다.
종료가 구간 도중 context를 닫지 않도록 기다리며 종료 시작 뒤 새 전환은 받지 않는다.
문서 CAS·실제 REST/WS 전환·공개 PUT 호출자는 아직 연결하지 않았다.
R6b3b1 당시 build는 `2026.09.15-runtime-credentials-r6-market-role-barrier-v1`이었다.

R6b3a는 시세 담당과 기존 기본계좌를 별개 역할로 저장하는 기반을 구현했다.
기존 DB 문서/트랜잭션에서 역할 revision CAS·현재 binding revision·활성 계좌를 검증하며,
담당 profile의 disable/연결 해제를 보호한다. 인증된 GET만 제공하고 적용 revision은 null이다.
실행 중 REST/단일 시장 WS를 다른 담당으로 전환하는 처리와 공개 PUT은 아직 연결하지 않았다.
R6b3a 당시 build는 `2026.09.15-runtime-credentials-r6-market-profile-settings-v1`이었다.

R6b2는 `real_runtime.py`의 실전 인증 owner를 기존 HTTPS prepare/apply API와 v3 선택 계좌 조회에 연결한다.
vault/HMAC/real 주 실행 환경을 갖춘 설치에서 기존 nas-real-default client/broker/collector를 유지하고,
추가 실전계좌의 kt00007/kt00015 과거 조회는 별도 read-only broker/query로 분리한다.
추가 계좌 등록/갱신/조회는 시장 WS를 재연결하지 않는다. 후보 준비·계좌 lock·commit fence·keyless 기동과
명시 복구를 owner가 소유하며, 복구 후보도 같은 broker queue/client 잠금/호출 간격에서 검증한다.
일반 조회는 paused 상태를 유지해 commit 후 실패에서 기존 키를 다시 사용하지 않는다.
시세 담당 disable은 MARKET_PROFILE_REQUIRED로 거절한다. 완료 ACTIVE는 REST/binding 적용이며 REG 수신 준비는 별도다.
실전 계좌 실시간 수집/운영 PUT·실제 시세 역할 전환은 R6b3 후속, PC 입력/계획된 재연결 표시는 R6c, 배포/실환경은 R7이다.
R6b2 당시 build는 `2026.09.15-runtime-credentials-r6-real-owner-v1`이었다.

R6b1은 기존 DB 활성화의 계좌 설정 claim을 실전에도 적용한다. verified scope별 단일
active_profile_id를 binding/적용 원장과 함께 확정하고 중복 연결 실패는 전체 rollback한다.
키 갱신은 설정을 보존하고 disable은 과거 binding을 유지한다. 완료 replay는 이후 설정/새 profile을 덮지 않는다.
새 SQL/endpoint/계층은 없으며 실제 실전 owner/API는 R6b2, PC 입력/계획된 재연결 표시는 R6c 후속이다.
최초 조회 ON 저장은 실제 runtime 시작을 뜻하지 않는다. R6b1 당시 build는
`2026.09.15-runtime-credentials-r6-real-account-claims-v1`이며 현재 진행 상태는 위 R6b2를 따른다.

R6a는 실전 인증 교체에 앞서 기존 collector/계좌 조회에 중지·완료 대기·재개 경계를 추가한다.
collector는 동일 허브/초·분봉 누적기·실패 대기 자료를 유지하고 이전 연결 generation의 늦은 프레임을
적용하지 않는다. pause는 실제 socket 종료·token thread와 접수 장 마감 처리를 기다린다.
DB 저장은 단일 lock에서 owned task로 직렬 처리하며 대기자 취소로 물리 저장/최종 버퍼 처리가 취소되지 않는다.
새 연결의 첫 전체 REG 승인에는 같은 source도 누적 기준점과 연속 관측 시작을 재설정한다.
수신 공백을 완료 봉으로 숨기거나 공백 중 누적 거래량을 한 틱에 합산하지 않는다.
계좌 조회는 신규 요청을 BUSY로 제한하고 접수 작업을 drain한 뒤 적용은 이전 cursor를 폐기,
commit 전 취소는 기존 cursor를 보존해 재개할 수 있다. 종료 뒤 재개는 거절한다.
실제 키 적용·활성 account binding·PC planned reconnect는 R6b/R6c에서 연결한다.
현재 실전 credential hook/capability는 추가하지 않았고 공개 endpoint/SQL 계약도 변경하지 않았다.
R6a 당시 build는 `2026.09.15-runtime-credentials-r6-reconnect-barriers-v1`; 현재 build는 위 R6b1을 따른다.

R5d2c는 PC NAS 설정에 기존 공통 뉴스 query-set ON/OFF·검색어·주기를 연결한다.
검색어는 한 줄 단위로 빈 줄/중복/앞뒤 공백만 정리하고 구문 내부 공백을 유지한다.
기존 worker/부분 PUT/revision CAS와 지원 필드 확인을 재사용하며 PC 직접 뉴스 키/설정에는 검색어를 복사하지 않는다.
NAS 폼은 스크롤하고 Save/Cancel은 폼 밖에 유지해 작은 화면에서도 저장할 수 있다.
`news_sources.py`의 같은 collector는 이미 접수한 검색어의 페이지/저장을 완료하지만 정책 변경 뒤
다음 검색어는 낡은 정책으로 접수하지 않는다. 정상 완료 cursor는 last_success + 현재 주기로
다음 조회를 판정해 주기 단축/연장을 적용한다. 오류 backoff·미완료 페이지·명시 예약 0은 유지한다.
기존 기사/source cursor/사용량·일일 상한은 보존하고 재추가한 검색어는 기존 위치를 재사용한다.
새 API/SQL/범용 계층 없이 기존 화면 → operations → 동일 수집기 경계를 유지한다.
R5 로컬 구현 완료이며 다음은 R6 실전 인증 교체, NAS 누적 배포/실환경은 R7이다.

R5d2b는 기존 NAS operations에 조건검색 추적 ON/OFF·정확한 이름·substring 선택을 연결한다.
`market_events.py`의 동일 서비스가 정책 revision과 기존 활성 조건/등록 후보/해제 ACK를 소유하고,
기존 `realtime_collector.py` 단일 WebSocket 수신 loop에서 변경을 반영한다. HTTP PUT은 상류 응답을 기다리지 않는다.
새 조건 전체 초기 페이지를 검증한 뒤 전환하며 실패한 후보는 기존 활성식/코호트를 덮어쓰지 않는다.
초기 결과 뒤 보류 실시간 신호를 적용하고 queue는 접수 당시 seq/name을 보존한다.
OFF도 이미 수집한 종목과 당일/익일 보존·VI/체결 서비스를 유지한다. 15초 timeout과 해제 ACK 실패는
실제 등록 상태와 operations GET의 복구 필요로 공개하고 같은 영속 revision을 명시 재저장할 수 있다.
PC는 지원 flag와 필드가 있는 NAS에만 조건 입력을 보내며 저장 전달 완료와 실제 등록 대기를 구분한다.
새 연결/범용 owner/SQL 테이블 없이 기존 API → 기존 서비스 → 기존 수신 loop 경계를 유지한다.
로컬 구현만 반영했으며 NAS 누적 배포/실환경 검증은 R7에서 진행한다.

R5d2a는 기존 operations의 영속 revision/apply 상태에 해외 지연 시세 수집 ON/OFF·주기·
자동 월물 전환·확인 횟수를 연결한다. 초기 symbol이 있으면 OFF 설치에도 같은 수집기를 준비하되
background를 시작하지 않는다. 저장 OFF는 다음 기동의 ENV ON보다 우선한다.
`external_market_collector.py`가 단일 수집 cycle과 설정 갱신 task를 소유한다. loop/HTTP waiter
취소는 실제 fetch thread/봉/roll/status 저장의 완료가 아니다. 갱신은 기존 cycle을 drain한 뒤
직렬 적용하고 shutdown은 pending 갱신/실제 cycle을 기다린다. 캐시/봉/roll 이력을 초기화하지 않는다.
PC 설정은 기존 worker와 변경 필드만 보내는 CAS를 사용하며 구 NAS에 없는 추가 필드는 비활성화한다.
실행 적용 실패는 저장 완료와 복구 필요를 구분해 명시 재저장한다. 새 owner/manager/SQL은 없다.
이번 단계의 상품 symbol/활성 월물 수동 교체는 미포함이며 기존 자동 roll 정책을 유지한다.
R5d2a 로컬 완료, 다음은 R5d2b 조건검색/기타 운영 확대. NAS build는
`2026.09.15-runtime-credentials-r5-external-operations-v1`, 동기화/배포 전이다. R6/R7은 후속이다.

R5d1은 기존 `api_settings_dialog.py`의 NAS 메뉴에 5개 뉴스/AI 공급자 키 관리를 추가했다.
`CentralCredentialsClient`는 생성 시 provider를 고정하고 global 입력 계약/operation target=null을
검사한다. 기존 mock 프로필 추가/조회/주문 계약은 유지한다. 공급자·NAS별 client의 메모리 pending
ID를 재사용하며 timeout 후 같은 요청 확인만 허용한다. PC 설정/DB/미러에는 키를 저장하지 않는다.
`NasCredentialsDialog`는 기존 worker/operation 수명을 사용하며 global 화면에서 계좌 UI/I/O를
제외한다. 서버 고정 기본 프로필만 표시하고 AI ACTIVE를 인증 성공으로 표시하지 않는다.
새 wrapper/관리 계층/서버 경로/SQL 변경은 없다. R5d1 로컬 완료, 다음은 R5d2 운영 확대다.
R5d1 이후 진행 상태와 최신 NAS build는 위 R5d2a 설명을 따른다.

R5c의 `central_server/ai_credentials.py`는 기존 runtime hooks를 사용해 OpenAI/Gemini/Claude의
고정 기본 프로필을 연결한다. vault 설치에는 키가 없어도 같은 CentralAIService와 뉴스 작업기를 조립한다.
prepare는 유료 호출 없이 UNVERIFIED 후보를 만들고 ACTIVE는 키 적용 완료를 뜻한다.
첫 실제 분석의 성공/인증/접근/일시 오류는 현재 revision의 메모리 runtime_validation으로 공개한다.
vault validation은 당시 검증 기록으로 보존하고 캐시 읽기는 새 키의 인증 성공으로 처리하지 않는다.
`ai_service.py`가 본문 준비 전 공급자/model/key/revision을 고정한 접수 task를 소유한다.
공유 실행 task는 revision/body hash까지 구분하며 caller 취소 뒤에도 실제 분석·저장을 끝낸다.
공급자별 pause는 그 공급자의 접수/대기/실행 task를 drain하고 다른 공급자 gate는 유지한다.
서버 종료도 실제 thread/사용량 저장을 기다린다. 저장 성공 캐시·품질 버전·일일 상한은 그대로이며
JSON 결과/사용량에 실행 credential_revision만 추가한다. 키 교체가 과거 AI 작업을 재예약하지 않는다.
commit 이후 실패는 해당 공급자 키를 비우고 복구 필요 상태로 두며 이전 ENV 키를 사용하지 않는다.
R5c 이후 진행 상태는 위 R5d1 설명을 따른다.

R5b는 같은 `central_server/news_credentials.py`에 DART owner를 연결한다. 고정 `nas-dart-default`를
keyless vault 설치에도 등록한다. 공시 검색 1건의 검증은 회사코드 캐시를 읽거나 쓰지 않으며,
신규 client도 기존 cache Path와 정상 조회의 30일 갱신 정책을 사용한다.
종목별 수집은 접수 시 네이버/DART client와 DART 사용 여부를 고정한다. DART 교체는 기존
종목 task와 최종 저장을 drain하지만 NAVER query-set는 멈추지 않는다. provider별 pause를
분리하고 두 flag가 모두 풀릴 때만 새 종목 수집을 시작해 동시에 교체해도 서로의 gate를 열지 않는다.
키 비활성화/교체는 운영 `dart_enabled`를 변경하지 않는다. commit 이후 실패는 DART만 None/
복구 필요 상태로 두며 이전 키를 복원하지 않는다. 기사·cursor·작업·NAVER 사용량을 보존한다.
R5b 이후 진행 상태는 위 R5c 설명을 따른다.

R5a의 `central_server/news_credentials.py`는 네이버 candidate 검증·commit fence·활성 revision을
소유한다. 고정 `nas-naver-default`를 키 없는 설치에도 등록하고 기존 CredentialRuntime hooks로
연결한다. NewsService와 QuerySetNewsCollector는 같은 client를 작업 시작 시 고정한다.
종목별 수집 task와 공통 검색의 페이지/실패 기록 task는 HTTP/loop waiter 취소로 종료되지 않으며,
키 교체/서버 종료는 실제 thread 작업과 DB 기록이 끝날 때까지 drain한다. 새 작업은 pause 동안
저장 기사만 읽고, deadline 초과는 실제 종료 후 commit 없이 이전 연결을 재개한다.
종목 task의 완료 정리는 요청자 finally에만 의존하지 않으며 완료 callback이 같은 owned task를 제거한다.
취소 뒤 늦게 끝난 수집이 다음 요청에 이전 완료 객체로 재사용되는 것을 막는다.
commit 시작 이후 실패는 NAVER client를 None으로 두고 복구 필요 상태를 유지한다. DART·캐시·
BODY/RULE/AI의 독립 경계는 유지한다. 검증 요청도 기존 NAVER 일일 예산에 포함하며 기사·cursor·
작업·사용량·분류 규칙을 초기화하지 않는다. 새 테이블/범용 작업 관리 계층은 없다.
R5a 이후 진행 상태는 위 R5c 설명을 따른다. 공급자 입력 UI/운영 확대·R6/R7은 후속이며 NAS 배포 전이다.

R4b는 인증된 v3 계좌 목록에서 실제 조회 가능한 mock bundle/main binding을 공개한다.
새 raw 계좌번호/키/토큰 필드는 없다. `journal_process.py`가 기존 SettingsRequestWorker로
목록을 읽어 저장 일지 scope와 합친다. HistoryWorker는 선택 scope의 현재 NAS binding을
조회 시작 시 한 번 확인한 뒤 고정 client를 만들고 모든 날짜/페이지/비용의 context를 검사한다.
빈 결과도 완료 context를 전달하며 선택이 바뀐 늦은 결과는 저장하지 않는다.
RemoteKiwoomRestClient는 optional 고정 context의 v3 조회/v2 mock submit·GET·cancel을 소유한다.
미확인 submit은 같은 ID/내용의 확인만 허용하는 메모리 상태를 유지한다. 새로운 계층/DB migration은 없다.
선택 NAS 계좌 경로는 failover/병행 client의 primary만 사용하고 단일 PC 프로필로 조회하지 않는다.
기존 context 없는 기본 v2 조회와 시장자료 fallback는 유지한다. R0~R4 로컬 완료이며 R5 진행 상태는 위를 따른다.

R4a는 기존 API 설정의 NAS 메뉴에 모의계좌 관리 화면을 연결한다. 중앙 인증 HTTPS client가
키 전달·redirect 거절·프로필/버전/익명 계좌 확인·안전한 요청 ID를 소유하고 화면은 기존
SettingsRequestWorker로 실제 I/O를 수행한다. 창을 닫아도 worker를 강제 파괴하지 않으며
같은 NAS 대상의 재열기는 메모리 요청 상태를 공유한다. 입력 키는 즉시 비우고 PC 설정/DB/
미러/백업에 쓰지 않는다. 기존 계층 호출은 설정→관리 화면→HTTPS client→기존 서버 owner이며
새 manager/저장소/DB migration은 없다. R4b가 매매일지 선택 계좌와 PC 조회/주문 context를 연결했다.

R3g는 명시 scope/profile/binding revision의 v3 계좌 조회와 v2 선택 모의 주문을 연결한다.
app.py의 대상 검증 후 mock bundle 또는 기존 검증 main real manager를 선택한다.
다른 real 계좌 runtime은 R6 전까지 미지원이다. AccountQuerySessionManager는 bundle별 cursor·
owned query·close drain을 소유한다. 응답 전 binding 재검사와 공통 mock read limiter를 적용한다.
기존 bundle close가 gateway 접수를 먼저 닫고 페이지 작업까지 기다린 뒤 WS/monitor/lease를 종료한다.
scoped 주문 ID는 계좌/run을 함께 사용하며 legacy v1 ID는 바꾸지 않는다. intent GET은
주문 OFF라도 scope/current run을 확인한 뒤 기존 repository를 읽는다. 새 실행 저장소/manager 계층은 없다.
R3 서버 기능과 R4 입력/선택 계좌 context 전달은 로컬 완료, R7 NAS 실환경 검증은 미완료다.

R3f는 같은 mock owner에 비활성화와 계좌 설정 PUT을 연결한다. 키 비활성화는 vault의
빈 credentials/disabled tombstone과 DB activation receipt를 기록하고 기존 binding을 재검증 이력처럼
증가시키지 않는다. 계좌/run·미확정 주문은 보존한다. active client의 키/토큰은 drain 이후 비우며
재시작 시 disabled profile은 bootstrap하지 않는다. 파일의 activation metadata에 disabled를 명시하고
기존 DB 원장을 그대로 사용한다(중앙 v19 유지).
설정 변경은 계좌 lock과 READY/boot 제외를 공유하고 실제 gateway/WS/monitor 종료 뒤 CAS 저장한다.
새 bundle은 같은 client/broker·binding·run을 재사용한다. 저장 전 실패는 DB가 이전 값임을 확인한 뒤
기존 연결을 복원하고, 저장 후 실패는 접수를 닫아 GET applied_revision=null로 남긴다.
동일 설정 PUT으로 실행 연결 복구가 가능하다. HTTP 대기자 취소와 별개로 owner가 적용 task를 소유하며
종료 때 실제 작업을 기다린다. scoped query/order와 입력 UI는 다음 단계다. NAS 동기화/배포 전이다.

R3e는 `MockCredentialOwner`를 실제 인증 API에 연결했다. vault/HMAC registry를 사용하는 모의
계좌만 지원하며 news/real owner는 미연결이다. owner가 profile별 bundle·revision·계좌 lock과
비동기 bootstrap을 소유한다. bootstrap은 최대 2개이고 대기 중인 과거 키를 새 사용자 적용으로 되돌리지 않는다.
동일 계좌는 client/broker의 한도·호출 이력·run을 재사용한다. 미확인 키 검증은 한 probe에서
직렬화하고 신규 검증 계좌는 이력을 보존한 별도 잠금의 client로 옮긴다. 준비 후보는 TTL/취소 후
계좌 lock과 비밀 참조를 해제한다. monitor 조회·prepare·WS token은 같은 동시 실행 2개 limiter를 쓴다.
교체는 기본 주문 API에서 bundle을 먼저 내린 뒤 gateway/WS/monitor를 drain하고 client를 pause한다.
설정 revision을 재검증한 뒤 vault/DB commit, token 활성화, 고정 binding/identity의 새 bundle 조립을 한다.
monitor ON은 lease/초기 read 확인 후 ACTIVE와 applied settings revision을 공개하고 OFF는 수신을 시작하지 않는다.
commit 전 실패는 이전 연결을 같은 계좌/run으로 재조립하고, commit 이후 실패는 닫힌 복구 필요 상태다.
다음 키 입력 전에 이전 암호화 activation의 DB receipt를 완성해 복구 기록을 잃지 않는다.
legacy 기본 주문은 nas-mock-default의 admitted bundle만 사용하고 UI 계좌 선택에 따라 바뀌지 않는다.
시세 담당 main mock은 nas-main-mock-default로 분리해 계좌 bootstrap/변경 대상에서 제외한다.
키 비활성화·설정 PUT·scoped query/order·입력 UI·NAS 배포는 아직 후속이다.

R3d의 계좌 운영 설정은 기존 `central_documents.server_account_settings`에 scope별로 저장한다.
SQLite BEGIN IMMEDIATE와 PostgreSQL credential-activation advisory lock 아래에서 설정 revision CAS,
검증 registry·최신 binding·활성 provider/profile을 확인한다. 모의 활성화의 binding/원장/초기 설정은
같은 트랜잭션이며 같은 계좌의 다른 활성 profile은 롤백한다. 같은 profile 갱신은 토글을 보존하고
완료 replay는 이후 설정을 되돌리지 않는다. 인증 GET은 저장 설정과 applied_revision=null을 반환한다.
R3e owner가 실제 확인한 settings revision을 공개하며 설정 PUT은 미연결이다. DB 토글만으로 runtime 적용을 주장하지 않는다.

R3c의 `central_server/mock_runtime.py`는 기존 모의 연결을 계좌/run/profile 고정 bundle로 조립한다.
bundle은 client/broker와 실행·수신 수명을 소유하며 시작/종료 대기자의 취소와 실제 작업을 분리한다.
종료는 gateway → WS → monitor/lease → broker 순서다. WS 신원은 bundle에 고정하고 닫힌 연결은
scope를 반환하지 않는다. 계좌 불일치는 binding 확정 전에 거절하며 모의 인증/시작 실패는 NAS 전체를
닫지 않는다. 주문 응답은 시작 당시 gateway로 기록을 읽는다. 복수 계좌 활성화 owner·키 교체 hooks와
계좌별 query/order API는 미연결이다. 기존 단일 모의 API의 기본 계좌 의미를 바꾸지 않았다.

이 문서는 미래 계획이 아니라 저장소에 **현재 구현된 실행 구조**만 기록한다. 상세 기능 요구는 기존 기술명세서, NAS의 목표 구조는 `docs/개인_시놀로지_중앙서버_설계.md`를 참고하되 서로 충돌하면 코드와 이 문서를 우선 확인한다.

## 실행 단위

NAS 운영 설정은 revision으로 저장 충돌을 검출하고 영속 revision과 적용 revision을 구분한다.
NAS/뉴스 설정창의 HTTP 조회·저장·연결 테스트는 단건 설정 worker에서 실행한다.
화면을 닫아도 이미 시작한 요청은 완료하며 저장 결과가 불확실하면 다시 조회한다.
NAS `KIWOOM_SERVER_SECRET_DIR`가 지정되면 `CredentialStore`가 환경 초기값과 전용 암호화
파일을 기동 시 합성한다. 초기 이관 표식·revision fence는 중앙 문서, 프로필·활성화는 중앙 v19
원장에 저장하고 키는 DB에 넣지 않는다. 파일 손상·분실은 해당 공급자의 복구 필요 상태이며
env로 돌아가지 않는다. 활성화 재처리는 binding과 함께 한 트랜잭션으로 멱등 처리한다.
R2 공통 준비·적용 API와 R3e 복수 모의 runtime 연결은 구현됐다. news/real owner·입력 UI는 R5/R6/R4다.
R2a 공통 REST 장벽은 구현됐다. 후보 OAuth/계좌 확인은 별도 broker 관리 작업이며 순위보다
낮은 우선순위다. 후보 전송도 원래 client 잠금·최근 호출 시각·감속 상태를 사용한다.
교체/재개/종료 대기자 취소는 실제 작업을 취소하지 않는다. 새 접수를 막고 HTTP·저장·
토큰 refresh의 실제 종료를 기다린다. 실제 메모리 교체 후에만 캐시 세대를 변경한다.
`CredentialRuntime`은 프로필당 한 개·전체 32개 준비 작업, 5분 TTL과 서버 소유 task를 관리한다.
vault 파일 commit 뒤 binding/활성화를 한 DB 트랜잭션으로 기록하고 실제 runtime revision이
확인돼야 ACTIVE를 표시한다. commit 이후 실패는 RECOVERY_REQUIRED이며 이전 키로 자동 복귀하지 않는다.
draft 생성 request는 비밀 없는 중앙 문서로, 적용 완료 request는 활성화 원장으로 멱등 재시도한다.
키 입력 HTTP는 bounded 수동 JSON 검증으로 raw input을 오류에 포함하지 않는다. 쓰기는 HTTPS 또는
명시된 IP의 trusted proxy가 보낸 단일 HTTPS 헤더에만 허용하며 Uvicorn의 암묵적 proxy 신뢰는 끈다.
실제 공급자 owner는 아직 등록 전이다. capability는 공통 API 존재, providers[].supported는
해당 공급자의 실행 중 변경 가능 여부다. 미연결 공급자는 prepare 503으로 저장·적용하지 않는다.
주문/WS/계좌 세대 연결은 R3/R6, 뉴스·AI owner는 R5, 입력 UI·쓰기 client는 R4에 남아 있다.

R3a의 `ManualMockOrderGateway`는 명령 task를 소유하고 HTTP 취소 후에도 실제 계좌 refresh·
주문/취소·DB 저장의 완료를 추적한다. credential 장벽은 새 명령을 막고 이 task들을 drain한다.
서버 정상 종료도 gateway를 먼저 drain한다. `ExecutionRuntime`의 동기 주문/취소/reconcile·stop은
같은 operation lock을 쓰고 heartbeat는 별도 state lock으로 느린 전송 중 임대 갱신을 유지한다.
신규 주문 OFF는 reconcile/취소를 막지 않는다. stop은 현재 account/run/owner lease만 조건부 해제하고
종료된 인스턴스가 start/heartbeat로 다시 살아나지 않게 한다.
R3b의 모니터는 시작·조회/복구 작업을 소유 task로 shield한다. close는 새 접수를 막고 queue의
종료 표식까지 실행하며 직접 refresh도 drain한 뒤 heartbeat를 닫고 runtime.stop으로 lease를 해제한다.
heartbeat는 별도 task에서 실행해 느린 복구 중에도 유지한다. 모의 WS close는 별도 task로 소유하며
토큰 thread와 실제 socket context 종료를 기다리고 닫는 중의 이벤트 전달을 막는다.
ExecutionRuntime은 lease 획득 뒤 해당 repository에 불변 account/run/owner context를 연결한다.
중앙 intent/event/account snapshot 쓰기는 같은 DB 트랜잭션에서 임대·scope를 확인한다.
SQLite BEGIN IMMEDIATE, PostgreSQL lease row FOR UPDATE가 검사와 쓰기 사이 owner 교체를 막는다.
늦은 상류 성공 응답의 owner가 바뀌었으면 원장의 SUBMISSION_UNKNOWN을 성공으로 덮지 않는다.
비운영 import/오프라인 원장의 비소유 쓰기 계약은 유지한다. 실제 키 교체·복수 계좌 bundle·
연결별 identity/binding snapshot과 scoped API는 아직 R3 후속 부분이다.

```text
Windows 메인 프로세스 (bootstrap.py / MainWindow)
├─ 순위·실시간 체결·분봉/일봉/신고가 작업
├─ monitor.sqlite3
├─ 뉴스 프로세스 ─ news.sqlite3
└─ 매매일지 프로세스 ─ journal.sqlite3

데이터 연결 모드
├─ local: Windows 앱 → Kiwoom REST / WebSocket
├─ local_server: Windows 앱 → 같은 PC 중앙 서버 → Kiwoom (기존 설정 호환용 내부 모드)
└─ personal_server: Windows 앱 → NAS 중앙 서버 → Kiwoom (UI 문구: NAS로 연결)
                         └─ PostgreSQL(권장) 또는 SQLite(개발/로컬 서버)
```

메인, 뉴스, 매매일지는 별도 프로세스다. 메인은 명령 파일로 뉴스·매매일지 창을 열거나 종목을 전달한다. 뉴스와 매매일지의 무거운 조회·분석·차트 작업은 메인 Qt 이벤트 루프와 분리된다.

## 실제 데이터 흐름

### 직접 연결

```text
Kiwoom REST ─→ KiwoomRestClient ─→ application service ─→ MainWindow/worker
Kiwoom 0B ──→ RealtimeTradeWorker ─→ MinuteTradeValueAggregator ─→ 화면 + monitor DB
```

`KiwoomRestClient`가 한 프로세스 안의 REST 요청 간격과 재시도를 담당한다. 메인 화면은 순위 시각 직전에 후속 작업을 시작하지 않는 방식으로 `ka00198`을 우선 보호한다.

### NAS/중앙 연결

```text
Kiwoom REST
  → CentralRestBroker (단일 우선순위 큐, 중복 병합, 짧은 캐시)
  → MarketDataIngestor
  → 중앙 DB
  → /api/v1/*
  → RemoteKiwoomRestClient
  → 기존 application service / UI

Kiwoom WebSocket
  → CentralRealtimeCollector
  → RealtimeHub
  → /api/v1/realtime
  → CentralRealtimeWorker
  → 기존 Qt 신호 / UI
```

NAS에서는 `AutonomousTop20Service`가 24시간 30초마다 순위를 직접 조회한다. 키움 응답 `dt/tm`이 목표 회차보다 오래되면 0.25초 2회, 0.5초 2회, 이후 0.75초 간격으로 제한 재조회하고, 데스크톱도 NAS 저장 순위가 직전 회차이면 같은 간격으로 중앙 DB만 다시 확인한다. 내부 실시간 구독자와 TOP20 거래대금 집계는 시장 관측시간에만 현재/다음 TOP20의 `0B`를 유지한다. 기존 0B 스트림을 종목·KRX/NXT·거래초별 OHLC, 거래량, 거래대금, 체결 건수로 집계하고 1분봉·TOP20 구성/지수·시장 상태와 함께 중앙 DB에 저장한다. 순위와 TOP20 편입은 기존 최신 projection과 함께 D1 불변 revision에 같은 트랜잭션으로 기록되며 캐시 재처리는 합치고 정정 순서는 보존한다. D3a부터 실시간 1분 delta도 stable operation ID로 정확히 한 번 누적하고, 누적 뒤 전체 봉의 형성 revision과 실제 타이머 처리시각의 마감 revision을 남긴다. 구독을 분 중간에 시작했거나 연결이 끊긴 봉은 partial로 남고, 체결이 없던 분은 합성하지 않는다. 새 키움 연결이나 추가 종목 구독은 만들지 않는다. 20:05 또는 다음 거래일 07:40 이전에는 당일 편입 전 종목의 분봉과 최근 250일 일봉을 저우선순위로 보완한다. NAS와 키움 사이의 WebSocket 원본이 끊기면 해당 분의 TOP20 지수는 만들지 않고, 재연결 뒤 저장을 재개한다. 이 누락 시각은 데스크톱 차트에서 `수집 중단` 세로 경계로 표시된다.

같은 중앙 WebSocket은 시장 전체 `1h` VI와 키움 저장 조건검색도 처리한다. 조건식은 매 연결마다 이름으로 seq를 다시 찾고, 정확한 설정 이름 또는 `15%` 부분문자열이 정확히 하나인 경우에만 KRX 실시간 조건검색을 시작한다. 편입 종목은 D 신호나 15% 아래 하락으로 즉시 제거하지 않고 편입 세션과 그 다음 실제 관측 KRX 세션 종료까지 추적한다. 독립 hub subscriber를 쓰되 TOP20과 겹친 종목은 upstream 0B가 한 번만 구독된다. 편입 후 NXT 가능 여부가 확인되면 기존 KRX/NXT 범위와 장후 봉 보완에 합류한다. 추적 종목의 `0g` 상한가·하한가·기준가 묶음을 중앙 최신 문서와 앱 실시간 이벤트로 보존한다. `upl_pric`과 실제 0B 현재가/당일고가로 상한가 사실을 기록하되, 0B 등락률 기준과 가격제한가가 모순되는 전환 구간에는 새 사실과 화면 강조를 만들지 않는다. 주문은 만들지 않는다.

NAS를 통과한 `ka10016` 신고가 목록, `ka10001` 기본정보·시가총액, `ka10100` NXT 가능 여부는 응답 캐시에만 머물지 않는다. 신고가는 조회 시점별 스냅샷으로, 기본정보와 NXT 가능 여부는 시점별 스냅샷과 종목별 최신 문서로 중앙 DB에 함께 보존한다.

`YahooDelayedMarketCollector`는 키움 수집 경로와 독립적으로 나스닥·WTI 선물의 전월물과 차월물 5분봉을 5분 간격, 일봉을 하루 한 번 가져온다. 차월물의 최신 세션 거래량 우위를 연속 2회 확인하면 대표 월물을 앞으로 교체하고, 실제 계약별 원본 봉은 모두 보존한다. 시장 방향 등락률은 월물 간 가격을 연결하지 않고 선택된 계약 자체의 전일 종가를 기준으로 계산한다. 실패는 상태 문서에만 남기며 중앙 REST 큐나 실시간 순위를 중단하지 않는다. 이 공급원은 임시 지연 시세다.

테마 변경은 로컬 DB 저장 뒤 0.4초 동안 연속 변경을 합쳐 NAS 전체 테마 스냅샷으로 교체한다. 전송 실패 시 로컬 변경 시각이 담긴 디스크 대기 표식을 유지하고 2초·10초·30초·60초 간격으로 재시도한다. 앱 시작과 재시도 때는 로컬 대기 변경 시각과 NAS `theme_metadata` 완료 시각을 비교한다. 로컬이 최신이면 업로드하고 NAS가 최신이면 NAS 스냅샷을 로컬에 적용한다. 중앙 서버는 마지막 `theme_metadata(default,full)` 전체 문서가 수락되는 같은 트랜잭션에서 내용 hash가 달라진 revision만 `central_theme_snapshots`에 append한다. 현재 projection과 대기 재시도 흐름은 유지하며, 과거 조회는 서버 가용시각을 기준으로 늦은 다른 PC 전송을 소급 적용하지 않는다.

NAS 모드에서 NAS 연결 설정과 뉴스 창 설정이 공통으로 다루는 AI 공급자·모델·일일 한도와 DART 사용 여부는 `/api/v1/settings/operations`를 단일 원본으로 사용한다. 어느 화면에서 저장해도 NAS와 로컬 암호화 뉴스 설정에 함께 반영한다. 뉴스 필터·표시 열·색·바로가기·자동분석 방식은 뉴스 화면의 로컬 설정이며, API 비밀키는 기존처럼 각 저장 경계에서 반환하지 않는다.

중앙 REST 브로커의 숫자가 낮은 요청이 먼저 실행된다. 순위 `ka00198`은 10, 역사 분봉·일봉·월봉·연봉은 70~95다. 이미 실행 중인 요청은 중단하지 않지만 대기 중인 백필보다 새 순위 요청이 앞선다. 완료 coverage가 있는 차트 요청은 중앙 DB 자료를 먼저 돌려주고, 없을 때만 키움 TR 큐로 보낸다.

NAS 모드에서도 로컬 DB는 화면 캐시와 오프라인 변경 보존에 사용된다. 콘텐츠·설정·매매일지는 로컬 우선으로 쓰고 주기적으로 중앙 문서 저장소와 병합한다. 따라서 현재 구현은 “PostgreSQL만이 유일한 원본”으로 완전히 전환된 구조가 아니다. 연구용 읽기는 중앙 revision을 한 DB snapshot에서 ID 집합으로 고정한 뒤 stable cursor로 추출한다. D3a reader는 그 고정 입력에서 가상시각까지 실제로 가용했던 최신 KRX strict 마감봉만 선택한다. D3b runner는 검증된 export만 받아 완료봉 돌파와 TOP 순위 체류 Factor를 계산하고, 매 판단의 입력 revision·Snapshot·Decision·후보 상태를 별도 로컬 `research.sqlite3`에 기록한다. D3c는 그 판단을 실계좌와 분리된 mock 계좌에만 전달한다. 판단시각 이후 시작하는 첫 완료봉 시가 체결, 명시 비용, 현금 예약, 단일 포지션과 결과 label을 같은 run에 기록하되 기록 끝의 미체결·보유 포지션을 임의 가격으로 청산하지 않는다. D7 보고는 같은 run을 임의 종목 분할 없이 시간순 fold로 평가하며 warmup/gap/purge와 연속 상태·현금 정책을 명시한다. 비용 출처·유효기간, 고정 입력 hash와 최소 거래·활동일 기준을 먼저 검사하고, 아직 열지 않은 final OOS는 수치 없이 SEALED로 보존한다. 첫 쌍 비교는 필터 없음과 관심순위 지속 Factor만 바꾼 두 run을 candidate key로 대조하며, 연구는 메인 Qt 이벤트 루프 밖의 단일 낮은 우선순위 프로세스에서 실행한다. F1 export는 기존 중앙 테마 이력을 별도 hash sidecar로 함께 동결한다. 종료 시점의 `market_regime/v1`은 TOP20 집중·교체·지속·대표 테마 확산·성숙한 3분 결과를 분모와 품질과 함께 계산해 네 유형 또는 `UNKNOWN`으로 표시하지만, 검증 전 임계값을 기존 돌파 전략의 필수 진입 조건으로 사용하지 않는다. C1은 N2 뉴스 사실, D5 관계 revision, 전 거래일 확정 사실, D4 장중 발견과 기존 지연 해외시장 봉을 종목·사건별 맥락 가설로 정규화한다. 사실과 영향 추론은 서로 다른 필드이며 장중 수급·대장 반응은 관측별 새 revision으로 누적된다. 무반응이나 반대 반응이 생겨도 원래 기사·전일 사실을 틀린 사실로 고쳐 쓰지 않는다. 전 거래일은 세션 달력의 명시된 predecessor만 사용하고, Yahoo 5분/일봉에는 지연 자료·월물·기준을 남긴다. 이 가설은 관찰·연구 입력으로만 쓰며 자동 진입 권한이 없다. 순위 Factor를 끄거나 선택/필수로 정한 정책과 모든 임계값은 run 명세에 남으며, 앱의 일반 콘텐츠 동기화에 연구 dataset이나 실행 상태를 섞지 않는다. 등록된 두 번째 Family `krx_pullback_reacceleration/v1`은 같은 strict KRX 분봉·TOP20과 단일 포지션 모의 체결 경계를 사용한다. 제한 탐색 v2의 실행 의미는 historical_simulation으로 고정하고, 작업 상태와 owner token·generation·heartbeat 임대 및 trial attempt는 로컬 연구 DB v9에만 저장한다. 완료된 같은 dataset/spec 작업은 다시 실행하지 않으며 새 결과가 운영 전략을 자동 변경하지 않는다.

O1 broker mock 경계는 연구 paper 체결과 별개다. mock/KRX intent만 받아 전송 직전 공통 session·계좌·만료·예약을 검사하고, 주문은 모의계좌 전용 인증·1초 호출 간격 안에서 한 번만 전송한다. 현재 검증된 신규 주문은 KRX 정규장 연속매매 `09:00~15:20`의 수동 LIMIT뿐이다. 장후종가·KRX 애프터·NXT·동시호가는 명시적 `UNSUPPORTED` event로 남긴다. 응답 유실은 unknown으로 중앙 DB v16에 남겨 재전송하지 않고 broker snapshot으로 대조한다. 정규장 종료시각은 broker 주문을 종료하거나 예약자금을 해제하는 근거가 아니며, 취소·계좌 대조·복구·늦은 체결은 session gate 밖에서 계속 처리한다. 격리된 account broker는 `ka10075/ka10076/kt00018/kt00001`만 읽으며, 주문가능금액·보유수량·미체결 예약과 주문별 누적 체결량을 만든다. 00 실시간의 단위체결 ID와 REST 복구의 누적 체결량은 별도로 보존해 늦게 도착한 상세 체결을 이중 합산하지 않는다. failover·병행검증 조회기는 주문 ID를 거부한다. account reader는 명시 설정 시 서버에 연결되고, 별도 기본 OFF 플래그를 켜면 인증된 수동 KRX 지정가 주문·상태·취소 API만 열린다. 같은 run의 `request_id`를 멱등키로 사용하며 후보·전략은 이 API를 자동 호출하지 않는다.

O2a 전진평가는 주문 실행과 별도인 증거 경계다. 전략·Family/Factor·정책·주 데이터 경로·mock 계좌·기간·통과 기준을 평가 시작 전에 하나의 content-addressed profile로 동결한다. V1 비교 가능 분모/누락/지연과 O1 실행 event, mock broker 기준 순손익·추가 비용·MDD·노출을 데이터/시스템/성과로 나눠 평가한다. broker 순손익에 이미 포함된 broker 비용은 다시 빼지 않고 별도 누락 비용만 차감한다. 기준 미정은 BLOCKED, 기간·최소 표본 미성숙은 PENDING, 최대 장애 한도 초과는 즉시 FAILED로 남긴다. PASSED도 승격 가능 근거일 뿐 주문이나 stage를 자동 변경하지 않으며 live 승인은 별도 O2b 경계다. 프로파일·보고서·stage revision은 기존 중앙 문서 저장소의 내부 컬렉션에 content ID로 저장하므로 중앙 schema v16을 올리지 않는다.

O2-Ma는 자동 모의운용 전에 최종 후보 package/result hash와 final batch/run, 현재 검증된 mock binding, forward profile과 모든 운용 한도를 `mock_automation_operating_spec/v1`으로 동결한다. 한도가 비었거나 전략이 SHADOW가 아니거나 profile·계좌·데이터 경로가 다르면 `BLOCKED`다. 현 O1이 안전하게 소유할 수 있는 단일 전략·단일 포지션·KRX 정규장만 `READY`로 평가한다. 저장된 `READY`는 후속 실행 승인 입력일 뿐 runtime claim, 수동 transport 플래그와 주문을 변경하지 않는다.

O2-Mb는 입장 시점의 최신 mock binding과 SHADOW revision, CR3 final execution/run의 COMPLETED 상태와 result hash를 다시 검사한다. spec당 admission을 먼저 저장하고 spec에서 결정한 자동 run이 기존 O1의 account 단일 lease를 얻는다. `ExecutionRuntime.start(..., new_orders_enabled=False)`가 lease claim과 주문 차단을 같은 잠금 구간에서 적용하며 성공 receipt도 이 상태만 기록한다. 다른 수동/자동 run과 충돌하면 admission request는 재시도 근거로 남고 lease receipt와 주문은 없다.

O2-Mc는 lease owner를 heartbeat로 재확인하고 신규 주문을 다시 닫은 뒤 broker 전체 복구 결과를 판정한다. 현재 정책은 후보 교체 시 flat을 요구하므로 broker open order, 보유 포지션, 매수 예약금이 하나라도 있으면 차단한다. 검증된 당일 손익과 데이터 공백 값이 없거나 명세의 손실·공백·submission unknown·재접속·잔고 불일치 한도를 넘는 경우, account/order snapshot이 오래되거나 시계가 앞선 경우도 차단한다. 판정은 recovery fingerprint와 함께 append-only revision으로 남고 통과해도 주문은 닫힌 상태다.

O2-Md는 최신 recovery 통과를 영구 허가로 사용하지 않는다. 각 action Decision마다 현재 binding을 다시 검증하고 account lease, KRX 정규 연속장, Decision·broker snapshot freshness, 계좌별 FIFO+broker 비용 출처의 당일 순손익, 데이터 경로/공백, 자금·손실·장애 한도와 기존 O1 비종결 intent를 검사한다. 승인 gate를 먼저 불변 저장한 뒤 실행 잠금 안에서 결정적 LIMIT intent를 O1에 한 번 전달하고 신규 주문을 즉시 다시 닫는다. 같은 Decision 재호출은 기존 intent만 읽는다. 긴급 중지는 신규 주문을 닫고 더 늦은 recovery 전까지 차단하며 기존 주문/포지션을 자동 취소·청산하지 않는다. 현재 NAS shadow `CandidateMonitor`는 동결 final candidate package의 실행 주체가 아니므로 자동 주문에 연결하지 않았다.

테마의 현재 원본은 로컬 `theme_profiles/profile_themes/profile_stock_themes`다. 별도 테마 DB 백업, NAS 테마 스냅샷, Google Drive 테마 파일은 모두 이 프로필 구조에서 전체 프로필을 생성한다. 활성 프로필과 프로필 초기화 상태는 공통 설정으로 보존하고, 테마 백업에는 테마 가져오기 규칙도 함께 넣는다. 창 위치·크기와 로컬 파일 선택 경로는 계속 PC별로 유지한다.

백업 경계는 서로 같은 파일 하나가 아니다. 일반 설정 백업은 공통 앱 설정, 표의 표시·순서, 뉴스 표시/AI 운용 설정을 담고 테마 DB와 뉴스 AI 분석 행은 담지 않는다. 별도 테마 DB 백업은 전체 테마 프로필을 담는다. Google Drive는 `settings`/`themes`/`both` 중 어느 대상을 골라도 AI 분석 결과 파일을 함께 보존하며, 선택값은 공통 설정 파일과 전체 테마 파일 중 무엇을 추가로 동기화할지만 결정한다. NAS는 공통 앱 설정, 메인 표의 표시·순서(폭 제외), 뉴스 기사/AI 결과/매매일지 뉴스 연결, 전체 테마 문서와 시장·매매일지 자료를 중앙 컬렉션별로 보존한다. API 키·접속 토큰, 창 위치·크기, 열 너비, 로컬 파일 경로, Google 동기화 실행 상태 같은 PC 전용 값은 의도적으로 동일화하지 않는다. 새 DB 테이블은 보안상 자동 외부 전송하지 않으며 각 동기화 계약에 명시적으로 추가한다.

매매일지·뉴스·TOP20 보조창이 Windows `QSettings`에 저장하는 창별 UI 상태는 현재 파일 설정 백업/Google 설정 문서의 범위 밖이다. 이 중 창 geometry·splitter는 PC 전용이지만 차트 색·이동평균선 같은 기능 설정까지 공통화하려면 별도 명시 계약과 이전이 필요하다.

S5 연구 요청은 공통 시간 정책의 `krx-regular/v1`, `krx-after/v1`, `krx-full-day/v1`을 RunSpec·검색·forward evidence ID에 고정한다. full-day는 09:00~15:30과 16:00~20:00의 합집합이며 15:30~16:00 고정가 구간을 제외한다. Factor와 pending은 두 구간 사이에서 이어지지 않고, 다음 1분봉 전 공백·거래일 변경·단일가/VI 호가 근거 없는 봉은 체결 또는 horizon 완료로 만들지 않는다. profile 없는 기존 요청은 S5 전 RunSpec과 구현 hash로 계속 식별한다.

### 뉴스와 AI

```text
Naver/DART → news process 또는 CentralNewsService → 기사 저장
기사 → 48시간 사건 묶음 → AI provider(OpenAI/Gemini/Claude) → 분석/사용량 저장
```

직접 연결에서는 뉴스 프로세스가 로컬 공급자를 사용한다. 중앙 연결에서는 중앙 뉴스·AI API를 우선하고 받은 결과를 `news.sqlite3`에 반영한다. 중앙 서버는 기사 제목 projection을 먼저 저장하면서 불변 기사 revision과 BODY job을 같은 트랜잭션에 기록한다. 단일 bounded worker가 본문 추출, 기록 전용 공급계약 RULE, 선택적 AI를 처리하므로 느린 공급자가 다음 제목 수집을 막지 않는다. 본문·규칙 사건·기사 소속·AI는 입력 revision·처리 버전·가용시각을 가진 새 revision으로 누적되고 `news_article/news_ai` 최신 projection은 기존 UI 호환을 위해 유지된다. 공급계약 규칙은 사실 근거와 해석을 함께 설명 가능한 JSON으로 저장하지만 현재 화면 후보, 자동 AI 필터, 수동 분석, 주문에는 연결하지 않는다. AI 프롬프트와 캐시 키는 선택한 종목을 분석 기준으로 고정한다. 기사가 그 종목을 단순 나열했을 뿐 직접 영향 근거가 없으면 종목 분석으로 억지 해석하지 않고 `판단 자료 부족`으로 처리한다.

중앙 모드의 종목 뉴스 목록은 최신 `fulltext` 원문이 있으면 제목·요약과 원문 도입부를 함께 사용해 관련성을 다시 계산한다. 이미 발생한 가격 움직임을 중계하고 뒤쪽에 과거 계약·일반 기대를 붙인 기사는 낮은 관련성으로 분리하되, 제목이나 원문 도입부에서 당일 확인 사건을 제시한 기사는 보존한다. 사용자가 대표 기사를 선택할 때는 별도 worker로 NAS의 기사·본문·공급계약 이력을 읽는다. 현재 목록의 identity와 제목·요약이 일치하는 기사 revision, 그 기사에 속한 본문 revision, 선택 종목에 속한 사건 revision만 결합한다. 종목별 본문이 아직 없으면 같은 identity와 제목·요약인 GLOBAL 기사 본문을 사용할 수 있지만 다른 종목 사건은 붙이지 않는다. 공급계약 v2는 계약 표현과 같은 문장의 금액만 선택하고, 수주잔고와 시세 기사 뒤쪽의 과거 계약을 새 사건으로 만들지 않는다. 이 영역의 규칙 점수와 추가 확인 표시는 기존 AI 판단과 별도 근거이며 AI나 주문을 새로 실행하지 않는다.

### 매매일지

본창과 차트 따로보기는 같은 종목·같은 봉 주기에 한해 화면 봉 수와 선·텍스트 그리기 상태를 공유한다. 비교 종목과 KOSPI/KOSDAQ 패널은 별도 상태다. 차트는 가로 스크롤과 그리기 끄기 상태의 마우스 드래그로 과거·최신 구간을 이동한다. 매매일지 일봉은 현재 조회 가능한 최신 거래일까지 읽고 매매 체결일에 최초 초점을 둔다. 복기 이미지 저장은 별도 봉 수/일봉 포함 옵션을 사용하지 않고 본창에서 현재 보이는 분봉 또는 분봉+일봉 구성을 그대로 렌더링하며, 따로보기의 현재 구성 저장은 표시 중인 패널을 PNG로 렌더링한다.

자동보완은 매매일지 프로세스의 단일 저우선 worker가 체결·비용·분봉·일봉·시장지수·사후 뉴스·수급·파생 분석을 대상/입력 지문/정책 버전별 영속 작업으로 관리한다. 프로세스 재시작 시 남은 `running`을 재검사하고, 저장 성공 뒤에만 완료한다. 조회 기간의 회차는 화면 행 선택과 무관하게 최대 20건씩 보완한다. 사용자 복기·수동 묶음·회차 override는 쓰지 않고 기계 판정은 별도 revision에 남긴다. 실제 체결과 연구 근거의 연결은 별도 불변 링크이며, N1 revision과 실제 `available_at`이 모두 없는 레거시 뉴스는 당시 근거가 아니라 가용성 미확인으로 취급한다. 첫 버전의 실행 소유자는 매매일지 프로세스이므로 그 프로세스가 닫힌 동안의 상시 실행은 보장하지 않는다.

```text
Kiwoom kt00007 체결 + kt00015 정산비용
  → TradeHistoryService / TradeCostService
  → JournalRepository
  → 묶음·회차 계산 / 유형 분류 / 전략팩 / 복기
  → JournalWindow와 차트
```

분봉·일봉은 먼저 로컬 `monitor.sqlite3`와 `journal.sqlite3` 캐시를 사용하고, 누락 시 현재 데이터 연결 방식의 조회 클라이언트로 보완한다. 중앙 순위 응답은 관측 시각별 원본 스냅샷으로 보존하고, 로컬 TOP20 거래대금은 분 합계와 그 분에 적용된 30초별 구성 종목을 함께 저장한다. 테마는 D5 이력 기록을 시작한 뒤 중앙 서버가 수락한 전체 프로필 문서에 한해 당시 가용 상태를 재현하며, 최초 기록 전은 unknown이다. 상세한 보존 범위와 공백은 `HISTORICAL_DATA_CONTRACT.md`에 고정한다. 진입 스냅샷은 순위·거래대금·테마·신고가 거리·뉴스·수급·호가·시장 상태를 JSON 묶음으로 저장한다. persistence 서비스가 매매 회차 주변 스냅샷을 읽고 누락 뉴스만 장후 연결한다. application 계층은 전략 후보와 수동 유형을 확정한 뒤 회차별 분석 입력을 구성하고, 체결 당시 관측값과 장후 보완값을 구분해 판정 가능 범위를 계산한다. presentation 계층은 완성된 분석 결과를 문구와 위젯에 반영한다.

## 저장 경계

- `monitor.sqlite3`: 메인 설정, 종목, 테마, 순위 화면용 분봉·일봉·신고가 근거, 시장지수, TOP20 지수.
- `news.sqlite3`: 기사, 수집 시각, AI 분석, AI 요청 사용량, 매매일지 뉴스 연결.
- `journal.sqlite3`: 체결, 비용, 복기, 전략팩/개인원칙, 차트 캐시, 진입 스냅샷.
- `research.sqlite3`: 고정 입력을 사용한 연구 run, Feature Snapshot, 전략 Decision, 후보 사건, 모의 주문·체결·mark, outcome, 시간순 fold 보고서, 조건 하나만 바꾼 쌍 비교, 제한 탐색 experiment/trial/후보 카드, fenced job·trial attempt와 상태 event, C1 맥락 가설 revision, H2 테마 대장 revision·고정 진입 근거·정책 비교. 실제 체결·비용·매매일지 행과 분리한다.
- 중앙 DB: REST 캐시, 최신 실시간 값, 중앙 1초 체결 집계·분봉·일봉, 순위/시장/수급 스냅샷, 순위·TOP20 편입 불변 revision, VI·15% cohort·상한가 불변 사실, 불변 테마 snapshot 이력, 불변 뉴스 기사·본문·공급계약 사건/소속·AI revision과 영속 작업, query_set source cursor/run/observation/target/budget, O1 mock 주문·계좌 복구 원장, 범용 콘텐츠 문서.
- PC 전용 파일: API/NAS 접속 정보, 창 위치, 로그, Google OAuth 파일, 비교 검증 로그.

중앙 스키마 v17의 계좌 신원 registry는 `ka00001` 원문을 저장하지 않고 broker·real/mock 환경과 보호 키 HMAC 지문을 지속 UUID에 연결한다. credential profile 검증은 binding revision으로 남는다. v18 alias는 오프라인에서 먼저 생성한 origin UUID를 같은 환경의 실제 binding revision으로 검증된 중앙 UUID에 한 번만 연결하며, 원본 UUID를 바꾸거나 환경 교차·연쇄 연결을 허용하지 않는다. Windows 직접 프로필은 선택적으로 최신 binding을 별도 DPAPI 파일에 mirror할 수 있다. 등록 명령으로 UUID를 발급한 뒤 기능 플래그를 켜면 mock 계좌 모니터 시작 전에 현재 인증 계좌와 `MOCK_ACCOUNT_REF`를 대조하며 불일치는 `ACCOUNT_CONTEXT_MISMATCH`로 차단한다. 보호 키는 DB 밖 환경 설정이며 DB와 별도로 복구 가능하게 보관해야 한다.

매매일지 DB v5는 체결/비용에, v6는 스냅샷·복기·묶음·유형·보완·분석·연구 링크에 origin/canonical scope를 저장한다. 기존 ID와 사용자 JSON은 `legacy-unassigned`로 보존한다. A3의 NAS `kt00007/kt00015` 조회 세션과 A4a의 중앙 00/04→scope envelope→entry snapshot, 일지 계좌 선택/후착 검사까지 구현돼 있다. 직접 REST는 저장 binding을 사용하지만 현재 자격 재확인이 없고 직접 WS는 아직 검증 scope를 만들지 않는다. DPAPI mirror의 운영 발급·로컬 verifier도 미완료다.

뉴스 DB v2는 계좌별 `journal_news_links`, 일지 DB v7은 `journal_legacy_imports`를 추가했다. 2026-09-13 실제 경로 감사에서 메인 뉴스 명령의 scope 유실, 수동 묶음의 legacy 저장, 일지 sync 첫 API 404, 구 NAS capability 오판, 삭제 부활과 v1 문서의 verified 행 덮어쓰기를 재현했다. 따라서 end-to-end 계좌 분리 완료 상태가 아니다. [A4b 재검토 결정](reports/A4B_DIRECT_WEBSOCKET_SCOPE_REVIEW.md)의 0a~0c로 보완한 뒤 HTTPS 기존 계좌 대조·로컬 재검증을 연결한다. 이 후속 API와 보호파일 v2는 설계이며 아직 현재 기능이 아니다.

## 장기 연구 제어

A5a에서 중앙 모의 실행 원장을 계좌별 증분으로 읽는 경계를 추가했다. 서버는 현재 검증된 mock
credential binding과 경로의 account ref가 일치할 때만 여러 execution run의 intent/event를
`accepted_sequence` 순서로 반환한다. PC 클라이언트는 응답 계좌 문맥과 커서 단조성을 확인한다.
이 읽기는 원장을 변경하거나 Kiwoom TR을 만들지 않는다. A5b는 이 page를 매매일지 DB v9에
원자 projection한다. 상세 FILL과 누적수량 event를 분리하고, 실제 상세 체결은 계좌·KST 거래일·
broker 주문/체결 ID로 멱등화한다. 후착 상세 체결은 aggregate 누락량을 줄일 뿐 수량을 이중 합산하지
않는다. A5c의 일지 read projection은 같은 canonical 계좌·거래일·주문·종목·매수/매도로 kt00007
요약과 상세 FILL의 수량·금액을 비교한다. 상세가 있으면 상세만 선택하고 요약을 더하지 않으며,
요약 전용·부분·충돌은 미확인 상태로 남긴다. run 변경과 canonical alias 뒤의 같은 broker 체결도
한 번만 센다. A5d는 이 결과와 kt00015 실제 비용을 회차별로 연결해 계좌·기간·전략·run/decision,
사전 선택 여부, PIT 근거, 최종결과 노출 상태를 `journal_feedback_evidence/v1`에 동결한다. 체결·비용·
포지션이 모두 확인된 경우에만 broker 비용을 한 번 차감한 순손익을 기록한다. 사전 선택·체결 당시
근거·개발 자료 조건까지 충족한 문서만 기존 forward 평가 입력으로 바꿀 수 있다. 피드백은 기존
`central_documents`의 비공개 `execution_feedback_evidence` 컬렉션에 내용 주소형으로 저장한다.
A5e1은 저장된 evidence와 명시 최소 거래일·거래 수 정책만 사용해
`journal_feedback_review/v1`을 결정적으로 파생한다. 확정 순손익·승패·비용 비율을 보존하되
원본이 부적격하거나 표본이 부족하면 개선 제안 입력을 차단한다. 저장 시 원본 evidence로 다시
계산한 결과와 일치해야 하며 비공개 `execution_feedback_reviews`에 불변 저장한다. 기존
`trade_fills`와 사용자 복기·수동 유형·전략 원본은 변경하지 않았다. A5e2는 적격 review를
등록 Family와 명시 허용값에 연결해 기준 전략과 한 파라미터만 다른 검토 대기 제안을 만든다.
seed는 순서만 바꾸고 후보 ID는 바꾸지 않는다. 제안은 비공개
`execution_feedback_improvement_proposals`에 저장하며 아직 전략 버전·연구 queue·주문을 변경하지 않는다.
A5e3는 명시 채택된 proposal을 부모 전략과 분리된 `feedback_strategy_version/v1`으로 만들고 기존
개발 캠페인의 명시 template에서 exact 재검증 `ExperimentSpec`을 파생한다. 새 버전은 원본 전략을
덮지 않으며 final holdout template을 사용할 수 없다. 중앙 문서와 연구 SQLite가 분리돼 있으므로
queue 전에 불변 request를 남기고 내용 기반 version/request/experiment/job/receipt ID로 중단을 멱등
복구한다. 같은 proposal은 전략 버전 하나만 만들고 한 전략 버전은 재검증 request 하나와 연구 job
하나만 남는다. 이 경로는 주문을 활성화하지 않는다.

제한 탐색 v2는 기준 전략 전체·체결/비용·실제 평가 구간·관련 구현 hash를 과학적 실험 ID에 포함한다. CPU·시간·메모리 같은 운영 예산은 ID에서 분리한다. 후보 점수는 TRAIN/VALIDATION만 사용해 열린 OOS도 선택 점수에 섞지 않으며, 후보 조합 수를 Cartesian product 생성 전에 검사한다. 기존 v1 결과는 그대로 보존하고 v2 결과로 인증하지 않는다. `one_parameter_at_a_time`, trial 뒤 휴식 비율인 `cpu_duty_percent`, GUI 자동 재개는 유지한다.

CR0에서 식별·OOS 선택·후보 사전 제한과 중단 attempt 재시도, 임대 fencing, 취소 가능한 GUI 예약을 수정했다. 시간 slice는 진행 중 trial을 완료하고 다음 시작만 막는다. CPU duty는 아직 trial 뒤 목표 휴식이며 RSS는 실측하지 않으므로 24시간 자동 연구 완료로 보지 않는다. D7 SEALED도 아직 전체 run 입력/원장의 최종 구간까지 격리하지 않는다. 계좌 기능의 구현/보완 범위는 위 계좌 경계 설명을 따른다.

지속 캠페인·개발/최종 검증·계좌 v2는 [설계 결정](reports/CONTINUOUS_RESEARCH_ACCOUNT_SCOPE_REVIEW.md)과 [Sol 구현 계획](reports/CONTINUOUS_RESEARCH_ACCOUNT_IMPLEMENTATION_PLAN.md)으로 확정했다. CR0의 실제 CPU/RSS 제어와 A3 이후 단계는 구현 전이다.

## 장애 경계

- 중앙 DB 저장 실패가 성공한 화면 조회를 실패로 바꾸지 않는다.
- NAS 장애 전환은 사용자가 켠 경우에만 로컬 키움 REST/WebSocket으로 전환한다.
- 중앙과 로컬 실시간을 일반 운용에서 동시에 화면에 섞지 않는다.
- 뉴스·매매일지 프로세스 오류가 메인 실시간 순위 처리를 멈추지 않아야 한다.
- mock 계좌 모니터는 기본 OFF다. 별도 모의 App Key/App Secret·익명 account ref·run ID를 모두 명시한 경우에만 초당 1회용 REST client·계좌 broker·`00/04` WebSocket과 임대를 시작한다. 실전 조회의 초당 5회 client·broker·시세 WebSocket과 잠금·토큰·호출 시각을 공유하지 않는다. 00 상세 체결과 04 변경 신호는 기존 O1 원장에 대조하고, 04 원문은 일반 실시간 허브에 공개하지 않는다. 주문 transport도 기본 OFF이며 별도 플래그를 켰을 때 같은 모의 client와 limiter에만 연결된다.
- NAVER 자격증명이 있으면 중앙 뉴스 서비스는 데스크톱 창·관심종목과 독립된 설정 query_set loop도 시작한다. 이는 9개 기본 검색어의 검색 범위이며 시장 전수 피드가 아니다. Synology Compose는 `.env`의 `NEWS_QUERY_SET_ENABLED`, `NEWS_QUERY_SET`, `NEWS_QUERY_SET_REFRESH_SECONDS`와 hard/watchlist/query_set 예산값을 서버에 전달한다. `NEWS_QUERY_SET_ENABLED=false`는 이 loop만 끄고 기존 watchlist를 유지한다.
- query_set 기사에는 원문 URL에서 정규화한 `publisher_domain`과 `publisher_name`을 기록한다. 화면 제공처 제외는 로컬 목록 숨김이고 NAS 처리 제외는 별도 운영 설정이다. NAS 처리 제외 기사는 제목·링크 관측을 남기되 새 BODY/RULE/자동 AI 작업을 만들지 않으며 기존 작업은 소급 삭제하지 않는다. 같은 source+기사 identity의 직전 내용 hash가 같으면 기존 GLOBAL article revision을 재사용한다. 여러 검색어의 요약 변형이 번갈아 보여도 같은 source의 동일 판본은 BODY를 다시 만들지 않으며, 같은 source 자체의 A→B→A 변화는 관측과 판본을 모두 보존한다. 빈 KRX catalog는 기존 loader로 한 번 채우고 실패 시 제한된 간격으로 재시도한다. 본문 완료 뒤 정확한 종목 target이 확인돼도 기존 본문을 입력으로 RULE 작업을 멱등 예약한다.
- 종목 뉴스 검색은 기존 종목 owner 기사와 정확한 종목 코드로 confirmed 연결된 GLOBAL N3 저장기사를 합쳐 최대 1000건을 안정적인 최신순으로 반환한다. identity가 같으면 기존 owner 기사를 우선하고 unresolved·ambiguous·다른 종목은 제외한다. 이 DB 읽기는 외부 뉴스 호출이나 BODY/RULE/AI 작업을 만들지 않으며, 주기 자동 AI 후보에는 기존 owner 기사만 전달한다.
- D4 `CandidateMonitor`는 NAS에 저장된 TOP20과 strict KRX 완료봉 revision을 sequence 순서로 D3 Factor/Family에 넣는다. 전략 JSON과 TOP20 최신성 한도를 환경변수로 명시한 경우만 생성하며 기본은 OFF다. 중앙 v15 원장에는 판단·후보·재시작 checkpoint만 남고 주문·체결·실제 일지는 만들지 않는다.
- 데스크톱 `Shadow 후보` 창은 인증 cursor API를 낮은 주기로 읽는다. 첫 연결 목록은 무음이고 실행 중 새 ACTIVE event ID만 PC 로컬 설정에 따라 화면·beep로 알린다. 후보 생성의 on/off와 전략 주요 조건은 NAS 운영 설정 DB에 저장하고 같은 서버 프로세스에서 감지 task만 즉시 시작·중지·교체한다. `.env`는 최초 기본값이며 창과 소리 수명은 NAS 후보 생성과 분리된다.
- 사용자가 NAS·로컬 병행 검증을 켠 경우에도 NAS 조회·실시간이 주 입력이다. 로컬 직접 결과는 `ParallelValidationClient`와 `RealtimeValidationRecorder`에서만 비교하며 주 화면·같은 연구 run에 재입력하지 않는다. 대조 JSONL은 공유 불변 참조가 있는 값 차이, 지연, 누락, 중복, 역순, 비교 불가를 구분하고 비교 가능 분모와 bounded 도착 간격 통계를 포함한다. 공급자 사건 ID가 없는 동일초 복수 체결은 n번째 사건 일치를 단정하지 않는다. 이 기능은 같은 키움 공급 경로의 전달·변환·저장 대조이며 독립 시장 데이터 공급자 검증으로 해석하지 않는다.

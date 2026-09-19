# CR3a2 — 독립 개발 입력과 명시 실행

2026-09-16. CR3a1의 후보 선택 근거에 이어 원본 입력/현금/품질 계산을 명시 개발 구간 하나씩 분리한다.
유한 탐색/자동 캠페인/최종 평가 전체 완료는 아니다.

## 목적과 수정 대상

기존 v1 runner는 전체 dataset과 하나의 연속 계좌를 사용한다. 보고서에서 OOS를 숨기는 것만으로
개발 입력/전체 품질의 영향을 분리할 수 없다. 기존 v1을 재해석하지 않고 새 명시 partition 계약을 추가한다.

- research_splits: DevelopmentPartitionSpec와 registered version/state 정책.
- research_data_source: 선택 원본 projection, 과거 상태 seed, 실행 전 경계 검증.
- run_research: 독립 입력 실행/warmup 금지/경계 censor/CLI.
- research_process: JSON finite 요청 연결.
- research_evaluation: 선택 자료의 warmup 가용 시각 확인. 기존 v1 품질 규칙은 그대로 둔다.

## 입력과 출력 계약

DevelopmentPartitionSpec의 version은 `development_partition/v2`, state_policy는
`reset_state_and_cash_per_partition/v1`이다. fold_name은 기존 evaluation의 TRAIN/VALIDATION 하나다.
OOS/없는 fold/알 수 없는 필드·version·state는 거부한다. final_holdout_accessed_at이 있는 정책을
미사용 구간으로 다시 표시하지 않는다. 실제 최종 노출 이력에 대한 권위 있는 판정 원장은 후속이다.

검증된 FrozenResearchDataset + 평가 정책 + 선택 spec + checkpoint를 받는다.
provider는 [fold.start-warmup, fold.end)의 available_at과 그 안에 들어오는 minute bar interval만 복사한다.
선택 기간 전에 받은 마지막 TOP20과 테마는 초기 상태로 보존한다. 시각/ID를 바꿔 새 수신 사실로 만들지 않는다.
TOP20 seed 한 건은 descriptor에서 명시하고 실행 전 대조한다. 기타 기간 밖 observation은 허용하지 않는다.
기간 안의 테마 업데이트는 모두 보존하며 end 이후·available_at 없는 테마는 전달하지 않는다.

runtime input version은 `independent_development_input/v1`이다. 선택 평가/warmup·active/end/seed descriptor,
선택 원본·테마와 과학적 context로 독립 dataset_id/count/revision hash를 만든다.
전체 source dataset_id/watermark/children/quality/미래 count를 복사하지 않는다. kinds도 선택 원본에서 계산한다.
dict/list는 복사하므로 원본을 변경해도 준비한 입력이 변하지 않는다. 원본 파일은 쓰지 않는다.
선택 single-fold 평가의 v1 표기/연속 state 정책은 **그 독립 구간 내부**의 의미이고, 외부 v2 spec이 구간별 reset을 기록한다.
전체 final window metadata/접근 정책 원장은 개발 입력 identity로 대신하지 않는다.

## 실행 계약

run 시작 전에 runtime version/descriptor/단일 평가 일치/명시 profile/시간 경계를 확인한다.
다른 평가, 미래 observation/theme 삽입, policy 누락은 DB run을 시작하기 전에 거부한다.
기존 PaperExecutionEngine은 매 execute_research마다 초기 현금/빈 상태로 생성된다. 구간 간 cash/position을 재사용하지 않는다.
동일 partition 내부 날짜는 같은 engine/replay cursor를 이어 사용한다. 날짜마다 별도 계좌를 만들지 않는다.

warmup에서는 과거 bar/universe replay만 진행한다. engine bar 처리·전략 판단·주문·후보 생성을 하지 않는다.
available_at이 active 이후더라도 bar_start가 active 전인 warming bar로 거래하지 않는다.
partition 종료 시각에 기존 finalize로 미체결/미청산을 censor하며 synthetic 청산·가격·실제 주문을 생성하지 않는다.
독립 partition 손익을 한 연속 계좌 손익으로 합치지 않는다. 전체 portfolio MDD 새 산식도 추가하지 않는다.

JSON single_run/rank_comparison의 optional development_partition과 CLI --development-partition을 연결한다.
명시 session_profile이 필수다. 기존 요청/CLI에서 이 옵션이 없으면 v1 실행을 유지한다.
limited_search는 parser와 실행 entry 양쪽에서 거부한다. 자동 campaign/source 및 UI 기본 요청은 아직 연결하지 않는다.
결과 manifest와 DB run.input_manifest에 선택 descriptor를 기록한다.

## 품질·무결성·자원 경계

기존 loader가 먼저 export/bundle의 전체 파일/ID/hash를 검증한다. provider는 이후 RAM에서 분리한다.
최종 구간의 원본을 실제 개발 엔진에 전달하지 않지만, 전체 파일의 무결성 검증/메모리 읽기를 생략하지는 않는다.
NAS 구간별 다운로드/저장 공간 감소가 아니다. source 파일 hash가 깨지면 필터로 우회하지 않고 실행 전 실패한다.

품질 count/reproducibility/strict KRX/universe/비용 적격은 선택 입력에서 계산한다.
새 partition의 warmup 양수일 때 시작 지점을 포함하는 strict 봉이 active 시작 이전까지 실제 수신됐어야 한다.
순위 seed/순위만 있는 시각 또는 active 이후 도착한 과거 봉을 warmup 분봉 근거로 삼지 않는다.
두 경우를 추가 테스트로 먼저 재현해 모두 실패하는 것을 확인한 뒤 새 partition 분기만 최소 수정했다
(`tmp/cr3a2-warmup-before.log`). 기존 v1 품질 의미는 변경하지 않는다.

quality PASS는 기본 적격 검사이며 전체 종목/기간 warmup 연속성·저장 coverage 완료 보증은 아니다.
coverage/테마 이력은 unknown/미검증 상태를 보존한다. 신호별 lookback/분봉 연속성의 기존 방어도 유지한다.
projection/실행 전 검증 루프와 replay에 기존 cancel/RSS/CPU checkpoint를 전달한다. 별도 전역 worker/lock은 없다.

## 검증

- 기존 관련 v1 focus 44개: 26.500초, OK/native exit 0.
- 새 partition 회귀 27개: 15.289초, OK/native exit 0 (`tmp/cr3a2-partitions.log`).
- partition/v1 자료·bundle·요청·성과·보고 focus 63개: 25.747초, OK/native exit 0.
- 회귀는 실제 원본 export hash/JSON pipeline과 CLI, final canary ±10^30·source identity 변경 불변성,
  immutable copy/시간·평가·profile 불일치/미래 삽입/seed/warmup 결측·late backfill/경계 censor/중간 취소를 포함한다.
  새 engine 간 초기 cash/빈 position과 단일 partition의 이틀 보유 유지도 확인한다.
- 전체 관련 회귀 330개: 184.988초, OK/native exit 0 (`tmp/cr3a2-regression.log`).
  partition/보고·성과·분할/준비 자료·용량·정리/worker·예산·캠페인/replay·bundle/자원/실행·탐색/offscreen UI를 포함한다.
- Python AST/변경 문서 공백 13개 파일과 tracked 변경의 git diff --check 통과.
- 실제 NAS/사용자 DB/계좌 대신 임시 파일·DB/합성 자료/가짜 API/offscreen Qt에서만 검증했다.

새 runtime 정책·data projection은 기존 policy/data source/runner 안에 추가했다. 단순 전달 Manager/Service는 없다.
수정 전후 기능을 이해하는 주요 파일은 같은 split/data source/runner/process/evaluation 경계다.
새 데이터 경계 검증 호출은 최종 입력 전달과 서로 다른 정책 실행을 막는 책임을 가진다.
DB v16/기존 migration/NAS API/TR 한도/실제 주문/실행 앱/사용자 DB·NAS·키는 변경하지 않았다.
동결된 이전 요청의 hash를 자동 교체하지 않으며 새 구현은 현재 코드로 만든 새 요청으로 실행한다.
이번 PC 변경은 NAS 소스 동기화/재빌드가 필요 없다. 모델 에스컬레이션 없음.

## 다음 단계

CR3a3에서 독립 개발 입력을 유한 탐색에 연결한다. no-trade/cost-stress/재시도/cache/후보 카드가
같은 동결 partition identity와 DevelopmentEvidence만 사용하는지 검증한다.
자동 source/campaign 연결은 원본·파생 identity/저장 계약을 먼저 확인한 후 별도 진행한다.
고정 종목 분할/최종 batch·접근/EXPOSED_DEVELOPMENT 원장/새 날짜 확장/자동 가설·일지 피드백은 후속이다.
CR3 전체·24시간 자동 연구·자동 모의운영은 완료되지 않았다. 실제 NAS 장시간 전체 부하는 V1이다.

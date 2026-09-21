> **과거 기록** · 원래 경로: `reports/CR3C1_FIXED_SYMBOL_PARTITIONS.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# CR3c1 — 고정 종목 분할 계약과 거래 대상 제한

2026-09-16. **고정 정책/v3 명시 partition/기존 독립 실행 연결 완료**.
기존 시장 DB/NAS/API/실제 계좌·주문은 변경하지 않는다. PC 누적 소스이며 NAS 재빌드 불필요.

## 목적과 경계

한 전략이 특정 종목에만 맞는지 개발 단계에서 검증할 수 있도록 거래할 종목 집합을 고정한다.
가격·순위·성과로 종목을 고르면 선택 편향이 생기므로 설계의 version/salt/stock_code 기준을 사용한다.
종목 개수가 늘 때마다 전체 목록을 재분배하지 않는다. 과거 이력 없는 층화/종목 자동 선정/최종 접근은 없다.

**원래 TOP20·peer 이력·테마는 그대로 유지하며 거래 대상만 제한한다.**
순위를 자르거나 다시 순위를 매기면 기존 Factor/시장 상황이 달라지기 때문이다.
선택한 시간 창의 다른 bucket 자료는 shared as-of 시장 context이고, 이 자료의 변경은 scientific 입력을 바꿀 수 있다.
따라서 이 기능은 미사용 최종 종목의 원시 정보까지 봉인하는 holdout 격리 완료가 아니다.
최종 접근/정보 노출 원장과 최종 원시 입력 격리는 후속 단계다.

## 요청과 안정된 배정

기존 single_run/rank_comparison/limited_search 요청의 development_partition 값으로 사용한다.
전체 객체는 기존 요청이며 dataset/strategy/execution/evaluation을 포함해야 한다.

```json
{
  "version": "development_partition/v3",
  "fold_name": "train",
  "state_policy": "reset_state_and_cash_per_partition/v1",
  "symbol_partition": {
    "version": "stock_hash_partition/v1",
    "salt": "research-2026",
    "bucket_count": 4,
    "bucket": 0
  }
}
```

- salt: 비어 있지 않은 1~128자 문자열, NUL 금지. 정확한 값을 고정한다.
- bucket_count: 정수 2~20. bucket: 정수 0~bucket_count-1. bool/float 거절.
- 버전/salt/기존 normalize_stock_code의 정규 6자리 영숫자 코드로 compact UTF-8 JSON 배열을 만들고
  SHA256 digest 전체를 big-endian 정수로 해석하여 bucket_count 나머지를 사용한다.
- 종목명/시장 접미사/현재 순위/가격/전체 목록 순서/성과는 배정에 넣지 않는다.
  기존 A prefix/_NX/_AL 코드 정규화 계약을 재사용하며 유효한 영문 포함 코드도 처리한다.
- 정책/salt/count가 고정이면 기존 배정과 새 종목 배정이 독립이다. 균등 종목 수/균등 유동성을 보장하지 않는다.
  salt/count 변경은 새 정책/새 scientific 입력이다. 비어 있는 bucket에서 가짜 표본을 만들지 않는다.
- 시간 role은 기존 TRAIN/VALIDATION fold가 정의한다. bucket을 TRAIN/OOS로 자동 승격하지 않는다.
  OOS/노출된 final 정책 선택은 기존대로 거절한다.

v2의 원래 세 필드 JSON은 그대로이며 symbol_partition 필드를 받지 않는다.
v3는 이 객체가 필수다. 기존 기본 요청/CLI의 --development-partition은 기존 시간 v2를 유지한다.
--validate-partitions v1과 순차 검증 창도 아직 시간 v2만이며 그룹×시간 batch 연결은 CR3c2다.

## 수정 대상과 실행 계약

1. research_splits.py: DevelopmentSymbolPartitionSpec과 명시 v3 DevelopmentPartitionSpec.
   순수 정책이며 DB/네트워크/종목 목록 상태를 소유하지 않는다. v2 직렬화는 bit-compatible다.
2. research_data_source.py: 기존 시간 projection을 그대로 사용한다. descriptor의 정책이 입력 identity에 들어간다.
   원래 as-of 관측/직전 순위 seed/테마를 보존하며 실행 전 v3 minute_bar 코드를 검사한다.
   전체 source hash/OOS/미래 listing/count는 projection identity로 넘어오지 않는다.
3. scripts/run_research.py: runtime 정책을 파싱하고 active/warmup 검사 뒤 target admission을 적용한다.
   종목 정규화 ranking.py도 명시 profile의 implementation hash에 포함하여 배정 동작 변경이 cache identity를 바꾼다.
   profile 없는 legacy 고정 hash는 유지한다.
   비허용 bar는 엔진 process_bar/전략 evaluate_bar/주문 결정에 넣지 않는다.
   replay cursor는 전체 관측을 진행하여 허용 전략에 원래 TOP20/peer history를 전달한다.
   그룹 내 여러 종목은 한 portfolio 현금을 공유한다. 별도 그룹 실행은 새 초기 현금/빈 상태다.
   기존 warmup 진입 금지/내부 날짜 상태 연속/종료 경계 censor는 유지한다.
4. research_evaluation.py: bucket 정책을 기존 조건 hash에 포함한다.
   서로 다른 거래 대상 bucket은 INCOMPARABLE이며 손익을 한 계좌로 합산하지 않는다.
   v3 by_symbol 체결 합은 closed count와 같아야 하며 양의 체결은 허용 종목이어야 한다. 모순은 INVALID다.

새 Manager/Service/데이터 복사 계층/엔진/테이블/마이그레이션은 없다. 연구 DB v17을 재사용한다.
새 정책은 원본 저장 문서가 아닌 실행 descriptor에 들어가므로 기존 Raw/사용자 메모를 변경하지 않는다.
엄격한 KRX 입력/profile과 기존 자원 guard/원본 hash 검증을 유지하며 새로운 TR을 요청하지 않는다.

## 완료 기준과 검증

정책 roundtrip/잘못된 필드·예산·코드/고정 배정·시장 코드 alias·신규 종목,
v2 직렬화/최종 선택 거절/전체 시장 context 보존/정책 scientific ID/OOS canary,
실제 엔진 target admission·fresh portfolio/peer 변경 영향/실행 전 코드 거절,
명시 single_run·유한 탐색 variants/cache/빈 bucket·조건 격리/모순 보고서를 검사한다.

- 초기 신규 계약 13개 중 실패 3건은 fixture의 callable 필드/이름과 기존 suffix 정규화 가정 차이였다.
  fixture를 실제 계약에 맞췄다. 코드 버그가 재현됐다고 오인하지 않는다.
- focused 79개 / 31.267초 / OK / native exit 0: tmp/cr3c1-focus.log, tmp/cr3c1-focus-exit.txt.
- 새 종목 배정에 사용한 정규화 함수의 실행 hash 의존성 누락을 가상 변경 테스트로 재현했다: tmp/cr3c1-hash-before.log.
- 수정 후 신규 계약 16개 / 6.243초 / OK / native exit 0: tmp/cr3c1-contract-complete.log, tmp/cr3c1-contract-complete-exit.txt.
- hash 보완 전 전체 연구 회귀 455개 / 280.118초 / OK / native exit 0: tmp/cr3c1-regression.log.
- hash 보완 후 최종 전체 회귀 456개 / 279.976초 / OK / native exit 0:
  tmp/cr3c1-regression-final.log, tmp/cr3c1-regression-final-exit.txt.
- 변경 소스/신규 테스트/문서 12개의 AST·공백 검사 및 git diff --check 통과: tmp/check_cr3c1.py.
- 실제 NAS/사용자 DB/계좌·주문/키/실행 앱에는 접근하지 않았다. 실제 규모/장시간 운용은 미검증이다.

## 남은 작업

다음 CR3c2: 종목 그룹×개발 시간 구간 순차 요청/실행 연결. 현재 명시 단일 그룹 실행만이다.
그룹별 진행/결측·편중/조건을 보여주는 비교와 화면 연결, 최종 접근 원장/새 날짜/자동 가설/일지 피드백은 후속이다.
기존 입력 품질/warmup 판정은 전체 시간 context 기준이다. 각 허용 종목의 전체 warmup/분봉 coverage 충분성을 보장하지 않는다.
개별 Factor의 history 부족과 그룹별 자료 결측/편중을 표시하는 보완은 후속 평가 단계에서 분리한다.
시간 v1 batch와 기존 화면은 그대로다. 종목 그룹을 자동으로 여러 번 실행하게 바뀌었다고 주장하지 않는다.
전체 source는 기존대로 한 번 검증/로딩한 뒤 시간 projection을 하므로 원시 전체 메모리 요구는 줄이지 않는다.
종목 단위 admission은 bucket 내 공통 현금으로 실행하며 종목별 PnL 합을 공통 portfolio 결과로 대체하지 않는다.
모델 에스컬레이션: 없음.

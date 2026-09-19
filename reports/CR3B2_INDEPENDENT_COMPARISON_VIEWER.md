# CR3b2 — 독립 개발 결과 조회 프로세스와 화면

2026-09-16. CR3b1의 집계 계약을 기존 연구 프로세스/화면에 연결했다.
저장 결과 조회 단계이며 새 시뮬레이션·후보 승격·주문은 실행하지 않는다.

## 사용하는 곳

전략 연구 → **독립 구간 결과 비교** → **비교 파일 선택** → **결과 비교**.
비교 요청 JSON은 연구 DB와 비교할 저장 run ID를 명시한다. 현재는 고급 요청 파일 방식이다.
저장된 두 결과가 같은 전략/비용/코드 조건인지 확인하고, 누락/실패/표본 부족/중복·기간 겹침도 표시한다.
표의 기간은 KST이며 셀에 마우스를 올리면 종목/날짜/시간대별 닫힌 거래와 손익을 볼 수 있다.
중앙값은 0.5원 단위도 보존한다. 구간 초기 현금이 각각 별개이므로 연속 계좌 수익률은 표시하지 않는다.
COMPLETE/적격은 수익성 승인이나 자동 승격이 아니다.

## 입출력 계약

```json
{
  "version": "independent_development_comparison_request/v1",
  "database": "research.sqlite3",
  "run_ids": ["저장된 TRAIN run ID", "저장된 VALIDATION run ID"]
}
```

database의 상대 경로 기준은 요청 JSON 폴더다. 요청은 64 KiB 이하, ID는 1~200개/각 256자 이하이며
version/database/run_ids 외 필드는 거절한다. 기존 single_run/rank_comparison/limited_search 요청은 바꾸지 않는다.

```text
python -m kiwoom_monitor.research_process --compare-runs comparison.json --result comparison-result.json --cancel comparison.cancel
```

--compare-runs는 --request/--campaign과 상호 배타적이며 campaign/worker/registration 옵션을 함께 받지 않는다.
요청은 한 번 파싱해 포착한다. result/cancel이 요청이나 DB 경로를 가리키면 결과 파일도 쓰지 않고 거절한다.
파싱/옵션 오류는 argparse 종료 2이며 결과 envelope가 없다. 유효 요청의 조회 성공은 종료 0,
취소는 2/status=cancelled, 조회 오류는 1/status=failed다. 자료 누락이나 조건 불일치는 조회 자체 실패가 아니다.

성공 envelope는 status=ok, kind=independent_development_comparison, 정규 database, 요청 순서 그대로의
run_ids 및 CR3b1 comparison을 담는다. 즉시 조회 실패/취소에는 기존 CLI의 reason envelope를 사용한다.
읽기 전/DB 열기 뒤/집계 뒤 취소를 확인한다. 한 SQL 조회 내부를 강제 중단하지는 않는다.

## DB와 프로세스 경계

- `ResearchRepository(path, read_only=True)`는 기존 DB만 SQLite URI mode=ro로 열며 timeout은 1초다.
  이미 마이그레이션된 연구 v17만 허용한다. DB/부모 폴더 생성, 마이그레이션, 연구/계좌/주문 쓰기는 하지 않는다.
  기본 생성자의 동작과 기존 DB schema/migration은 유지한다. 잘못된 DB/잠금 오류는 failure로 반환한다.
- `execute_independent_comparison`은 원본 입력 loader/runner/campaign worker를 호출하지 않고
  repository의 명시 1~200개 run/report 단일 SELECT 집계만 사용한다.
- `IndependentComparisonDialog`는 연구 화면이 소유하는 읽기 전용 viewer 수명과 자기 조회 프로세스를 소유한다.
  기존 AuxiliaryProcessManager를 재사용한다. 연구 실행/캠페인과 상태·취소 파일을 공유하지 않는다.
- UI는 제한된 작은 요청 JSON을 검증/절대경로 snapshot 저장하고 낮은 우선순위의 hidden child를 실행한다.
  UI에서 research DB나 원본 export/bundle을 열지 않는다. 결과 파일은 16 MiB 이하만 읽는다.
- 요청마다 UUID request/result/cancel 세 파일을 사용한다. 종료 코드/kind/DB/요청 ID 순서/
  내부 partition ID 순서·개수/version을 대조한다. 표를 변경하기 전에 표시 값/tooltip 전체를 준비한다.
  오류/취소에는 기존 표를 보존한다. 완료 직전 취소가 늦게 도착하면 이미 생성된 정상 조회 결과를 표시할 수 있다.
- 창 닫기는 취소 파일만 보내고 숨긴다. polling은 유지하여 child 종료 결과를 소비한다.
  앱 종료는 own cancel → 기존 graceful stop → 자기 임시 파일 정리 순서다.
  원본 요청과 DB를 보존한다. 임시 파일 제거 실패는 상태 tooltip에 남긴다.

기능 이해 경로는 기존 파일 4개: 화면 → 연구 프로세스 → 저장소 → 평가 순수 계산이다.
새 Manager/Service/일반 wrapper·DB 테이블은 추가하지 않았다. 기존 연구 실행 경로와 선택 알고리즘도 유지한다.

## 검증

- 신규 프로세스/화면 계약 테스트 21개. 읽기 전용/마이그레이션 금지/옛 DB·없는 DB/잠금 timeout,
  옵션·파일 경계/취소/중복·동시 viewer/종료/포착된 요청/잘못된 결과/누락/KST·중앙값을 확인했다.
- 기존 비교/프로세스/연구 화면 포함 focused **51개 / 21.132초 / OK / native exit 0**.
  기록: `tmp/cr3b2-focus-complete.log`, `tmp/cr3b2-focus-complete-exit.txt`.
- 실제 임시 child를 0.5초 지연한 뒤 조회하며 Qt 10ms heartbeat가 계속 실행되고 정상 표가 적용되는 것을 확인했다.
  이는 fixture의 응답성 검사이며 실제 전체 데이터 지연 측정은 아니다.
- 변경 13개 파일 AST/공백 및 `git diff --check` 통과. 신규 untracked Python도 검사에 포함했다.
- 연구 전체 회귀 **403개 / 247.145초 / OK / native exit 0**. 기존 검색·캠페인·저장·복구·Qt 화면을 포함했다.
  기록: `tmp/cr3b2-regression.log`, `tmp/cr3b2-regression-exit.txt`.
- 실제 NAS·사용자 DB·계좌·주문·키·실행 중인 앱은 건드리지 않았다.

## 남은 범위

요청 파일 대신 저장 결과를 선택하는 편의 UI, 큰 결과의 페이지 표시/실제 규모 지연 측정,
강제 프로세스 종료 후 남은 UUID 임시 파일의 장기 정리는 후속이다.
이번 단계는 여러 구간을 자동으로 생성·실행하는 기능이 아니다.
다음 CR3b3은 동일 전략의 여러 개발 구간 순차 검증 요청/실행 계약이다.
종목 분할/최종 접근 원장/자동 최종평가/새 날짜 확장/새 가설 생성은 이후 단계다.
PC 코드 변경이며 NAS 동기화·재빌드는 없다. 실제 앱 재시작/운영 확인은 수행하지 않았다.

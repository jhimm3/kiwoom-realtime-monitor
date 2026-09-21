> **과거 기록** · 원래 경로: `reports/CR1B_CONTINUOUS_BUNDLE_EXECUTION.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# CR1b — 여러 날짜 연구 실행 연결

2026-09-16. **실행 연결 완료, CR1 자원 검증은 미완료.**

후속 갱신: 아래의 미완료 자원 항목은 [CR1b 자원 구현/계측 보고서](CR1B_RESOURCE_LIMITS_AND_BENCHMARK.md)에서
최소 기능·fixture 검증을 완료했다. 실제 NAS 전체 규모/장시간 성능은 V1에서 검증한다. 아래는 실행 연결 단계 당시 기록이다.

## 사용자가 얻는 기능

CR1a로 준비한 묶음 디렉터리를 기존 연구 요청 JSON의 `dataset`으로 지정한다.
`session_profile`은 export와 같아야 한다. 전략·비용·시간순 평가 조건은 기존 형식 그대로다.
CLI와 앱의 별도 연구 프로세스 모두 동일한 통합 입력 reader를 사용한다.
여러 날짜를 한 번 실행하며 현금과 보유주식을 이어간다. 매일 강제 매도하거나 하루 수익을 단순 합산하지 않는다.

## 구현 계약

- 원본 일별 export와 manifest/ordinal은 수정하지 않는다. bundle 검증 후 runtime 입력만 만든다.
- UTC `available_at`, `accepted_sequence`, `revision_id` 순으로 고유 revision을 소비한다.
  같은 ID의 동일 내용은 처음 행만 쓰며, 내용 충돌은 CR1a 검증에서 거절한다.
- 다기간 실행은 현재 ingest까지의 prefix를 기존 replay에 제공한다. 같은 시각의 후속 정정도
  자기 순서가 오기 전에 앞선 분봉을 덮어쓰지 않는다. 과거 체결을 정정 봉으로 다시 체결하지 않는다.
- 다기간 순위 replay는 UTC 시각 정렬을 명시한다. 기존 하루/legacy 정렬 계약은 유지한다.
- 명시 profile 누락/불일치는 출력 DB 생성 전에 거절한다. runtime 입력을 직접 실행할 때도 같은 검사를 한다.
- 하나의 `PaperExecutionEngine`을 전체 기간에 사용한다. pending은 기존 profile의 세션 경계/다음 봉
  정책에 따라 CENSORED가 되고, 이미 체결된 보유는 이어진다. 종료 시에만 미완료 보유를 CENSORED로 기록한다.
- 하루 bundle은 검증 후 원래 `FrozenResearchDataset`을 반환해 단일 export의 identity/논리 결과와 일치한다.
- 다기간 manifest는 bundle ID·자식 manifest hash·정렬된 revision ID hash·원래 품질 정보를 고정한다.
  이동 가능한 상대 경로는 과학 identity에서 제외한다. 입력 reader 소스도 명시 profile의 구현 hash에 포함한다.
- 모든 날짜의 테마 sidecar를 기존 시점 reader에 제공한다. 시작 전 최신 구성과 기간 중 변경을 가용시점으로 선택한다.
  잘린 테마 이력은 성공 처리하지 않으며, 빈 날짜/수집 공백은 unknown으로 보존한다.

## 변경 범위와 검증

`infrastructure/research_data_source.py`, `scripts/run_research.py`, `research_process.py`,
`application/research_replay.py`와 신규 `test_research_bundle_execution.py`.
새 Manager/DB/API/서버 계층을 추가하지 않았다. 기존 실행 엔진과 replay를 재사용한다.

임시 파일·SQLite DB로 하루 결과 동일성, 다기간 재현성, 다음 날 현금/보유 유지,
미체결 주문 경계, 원본 파일/ordinal 보존, 중복 제거, UTC 순위, 같은 시각 후속 정정,
미래 자료 차단, 시작 전/기간 중 테마, 앱 프로세스 연결과 profile 실패 시 DB 미생성을 검증한다.
기존 export/replay/process/comparison/execution/search/queue/family 회귀도 실행한다.
최종 회귀 결과: `tmp/cr1b-regression.log`, **82개 / 13.233초 / OK / 종료 코드 0**.
실제 NAS·사용자 DB·API·키·주문·이미지 재빌드는 사용하지 않는다.

## 남은 범위와 다음 작업

자료를 모두 메모리에 읽으며 각 분봉에서 보이는 과거를 기존 replay로 다시 스캔한다.
큰 자료의 실행 적격성을 아직 보장하지 않는다. 다음 CR1b 자원 작업에서 같은 1/5/20일 fixture의
처리시간·최대 RSS·파일 증가량을 측정하고, 확인된 병목에만 정렬 인덱스/증분 읽기를 적용한다.
짧은 batch마다 CPU 양보, 입력 preflight와 실행 중 RSS 제한, RESOURCE_BLOCKED 처리도 남아 있다.
거래소 휴장일/predecessor 검증과 완전한 테마 pagination은 이 연결로 새로 구현된 기능이 아니다.
NXT/VI/호가/초자료 전략, 24시간 지속 campaign, 자동 가설·일지 피드백·자동 모의주문은 후속 단계다.

모델 에스컬레이션: 없음.

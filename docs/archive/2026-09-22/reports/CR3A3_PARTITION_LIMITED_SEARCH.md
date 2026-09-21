> **과거 기록** · 원래 경로: `reports/CR3A3_PARTITION_LIMITED_SEARCH.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# CR3a3 — 독립 개발 구간의 유한 탐색

2026-09-16. CR3a2의 독립 입력·새 초기 현금·warmup 거래 금지·경계 censor를 그대로 사용한다.
이번 단계는 한 TRAIN/VALIDATION 구간의 유한 탐색 연결이며 자동 캠페인·종목 분할·최종 평가 구현은 아니다.

## 구현과 입출력 계약

- JSON limited_search에 development_partition/v2를 명시한다. session_profile이 필수다.
- search.dataset_id/hash는 원본 export/bundle이다. parser는 원본 evaluation/전략/비용/현재 구현/profile/partition을 context에 고정한다.
- 기존 loader의 전체 파일/hash 검증 후 원본 ID/hash가 제출 spec과 일치하는지 검사한다.
- RAM projection 후 선택 데이터 ID/hash와 단일 평가로 유효 ExperimentSpec을 만든다. 제출 request/search 객체는 변경하지 않는다.
- 기존 DB v16의 experiment/job/trial/attempt/card 저장·lease·예산·완료 cache를 재사용한다. 반환 ID는 유효 spec의 ID다.
- 유효 scientific identity에는 전체 원본 ID·OOS 평가 범위·전체 품질을 넣지 않는다. 선택 payload/seed/theme/평가/profile/코드/전략/비용/탐색 정책을 고정한다.
- 기본 전략·no-trade·rank ablation·cost stress는 같은 독립 입력을 받는다. no-trade 보고서와 run.input_manifest에도 선택 정책을 보존한다.
- 후보 적격/성과는 기존 DevelopmentEvidence 경로로 선택 구간의 개발 근거만 사용한다.
- 원본 요청을 보관해 다시 실행한다. 파생 유효 spec을 원본 dataset 경로에 붙여 새 제출 요청으로 만들지 않는다.
- 완료 cache가 있어도 원본 검증을 먼저 수행한다. 손상된 원본이나 틀린 원본 ID는 cache로 우회하지 않는다.

## 변경 영역과 구조

- research_process.py: parser의 정책 고정, 원본 검증, 유효 spec 생성, campaign 진입 차단.
- research_repository.py: development_partition context를 가진 자동 campaign enqueue 거부.
- test_research_partition_search.py: 새 계약 회귀 12개.
- test_research_development_partitions.py: CR3a2의 탐색 금지 검사를 이번 계약에 맞춰 제거하고 최종 구간 거부는 유지.

새 Manager/Service/Handler나 DB migration은 없다. input loader → partition projection → 기존 finite search의 호출 깊이는 그대로다.
engine/search/evaluation/card의 책임을 이동하지 않고 process 경계의 spec만 변환했다.
실제 NAS·계좌·사용자 DB·실행 중 앱은 변경하지 않았다. NAS 재빌드는 필요 없다.

## 검증

새 테스트는 임시 실제 export/JSON/DB를 사용한다. 구간 안·밖 변경의 파일 hash를 올바르게 다시 만들어 loader 검증까지 통과시킨다.

- 네 비교 유형이 선택 train 보고서와 development input manifest를 공유한다.
- 완료 후 재실행 attempted_now=0, 카드 동일, 원본 파일과 제출 spec 불변.
- 중단 attempt 뒤 같은 job/experiment로 재시도하며 완료 trial은 한 개다.
- OOS payload/revision ID·원본 ID·전체 품질만 변경하면 기존 개발 cache를 재사용한다.
- 개발 payload를 같은 revision ID로 변경하면 다른 실험으로 실행한다. validation도 train cache를 재사용하지 않는다.
- 잘못된 원본 ID/hash·손상 manifest·partition context 불일치·최종 구간 선택·load 취소를 거부한다.
- campaign 직접 실행과 repository 등록은 계속 차단한다.

최초 fixture 검증 실패는 family 기본값을 누락한 테스트 작성 오류 9건, 이어서 수신 시각의 2초 지연과
동일 임시 export 재생성을 잘못 가정한 테스트 오류 2건이었다. 제품 결함으로 보고하거나 우회 코드를 추가하지 않았다.
최종 누적 회귀: **342개 / 195.196초 / OK / 네이티브 종료 코드 0**.
로그: `tmp/cr3a3-regression.log`. 연구 실행/보고/품질/split/검색/queue/repository/resource,
bundle/replay, campaign worker/budget/source/NAS 준비/저장 복구/정리/용량, offscreen Qt dialog를 함께 검증했다.
새 계약 테스트 12개가 포함된다. 이전 좁은 실행은 9개 / 7.717초 / OK였다.
변경 파일 11개의 AST/공백 검사와 Windows CRLF를 고려한 대상 파일 git diff --check도 종료 코드 0이다.

## 다음 한 단계와 한계

CR3a4에서 campaign/source의 원본 경로·불변 제출 spec·파생 실험 ID를 구분한다.
기존 자동 캠페인이 저장 request로 실행을 재구성하고 source가 원본 dataset identity를 대조하므로
이번 단계에서 임의로 연결하지 않는다. 등록·실행·재시작·새 자료 승인 원장 계약과 회귀를 함께 연결해야 한다.

여러 독립 fold의 성과 집계, 종목 분할, 최종 접근 원장, 자동 가설 생성·자동 모의운영·일지 피드백은 후속이다.
전체 원본을 읽은 뒤 RAM에서 나누므로 NAS 구간 다운로드나 대규모 메모리 절감 기능은 아니다.
실제 NAS 전체 규모/24시간 운전은 이번 fixture 검증으로 완료 판정하지 않는다.

모델 에스컬레이션: 없음.

# CR3a4a — 독립 개발 구간 캠페인의 원본·실행 계약

2026-09-16. CR3a3의 한 개발 구간 유한 탐색을 기존 자동 campaign/source에 연결한다.
화면 등록의 background 연결은 CR3a4b로 분리한다. 이번 단계에서 큰 입력을 UI 스레드에 읽는 경로를 추가하지 않는다.

## 확인된 구조와 최소 변경

캠페인의 request_json은 trial identity, 완료 판정, 예산 확대, budget revision에 쓰인다.
원본 spec으로 교체하면 scientific identity가 바뀌고 완료 trial/예산 대조가 깨진다.
따라서 기존 request_json에 유효 실행 spec을 유지하고 원본 spec은 새 source_request_json에 보존한다.
새 Manager/Service나 연구 엔진은 추가하지 않는다. 기존 process/repository/source discovery를 사용한다.

연구 SQLite v17 campaign_independent_source_request는 campaign job에 기본 빈 문자열인 source_request_json만 추가한다.
v1~v16 migration 함수/이름과 기존 행을 변경하지 않는다. 빈 source_request_json인 기존 job은 원래 실행 spec으로 재구성한다.
NAS/중앙 DB/API, 실제 계좌/주문/매매일지, 원본 사용자 DB는 이번 작업에서 변경하지 않았다.

## 입출력 계약

1. 별도 프로세스 register_campaign_request는 parsed limited_search 요청의 원본 export/bundle/profile/hash/ID를 검증한다.
2. 공통 _prepare_development_request로 선택 구간을 분리한다. 공통 search 입력 검사로 family/split/holdout 계약도 확인한다.
3. repository enqueue에는 유효 spec과 별도 원본 spec을 전달한다. 두 spec은 입력 ID/hash와 선택 evaluation 외의 정책이 같아야 한다.
4. 기존 experiment/job/trial/card/lease/완료 cache는 유효 spec을 사용한다. 원본 spec/input_path는 동결하며 덮어쓰지 않는다.
5. load jobs/claim cycle은 request와 source_request 두 명세를 제공한다. 현재 operating budget revision을 둘에 같은 값으로 합성한다.
6. 자동 실행은 source_request로 locked request를 다시 만들고 원본 loader 검증·source ID 대조·projection을 수행한다.
   유효 spec 전체와 job_id가 captured cycle의 값과 같아야 DB run/trial을 쓰거나 실행 lease를 진행한다.
7. 고정 범위 source는 원본 baseline으로 scope/fingerprint를 초기화한다. 새로운 원본도 같은 projection을 사용한다.
8. 새로운 원본의 개발 입력이 같으면 기존 job에 acceptance만 추가한다. 기존 job의 원본 명세/경로는 그대로다.
   예산 revision 후 동일 과학적 자료도 현재 예산 조회 명세로 대조한다.
9. 개발 입력이 달라지면 새로운 유효 job과 그 원본 spec을 원자 등록한다. 이미 승인한 경로/manifest 수정 방어는 유지한다.

CLI:

```text
python -m kiwoom_monitor.research_process --request request.json --register-campaign CAMPAIGN_ID --result registration.json
```

기존 campaign에 등록만 한다. campaign 생성/활성화/trial 실행/주문은 하지 않는다.
등록의 cancellation과 resource guard는 전체 원본 load/projection/checkpoint에 적용한다.
화면 등록은 현재 명시 안내로 종료하며 다음 CR3a4b에서 별도 process를 연결한다.

## 검증 범위

새 계약 회귀 13개: 원본 요청 파일 삭제 후 재구성, finite 완료 cache 재사용, pause/retry,
예산 revision 시 두 JSON 불변·두 명세의 현재 예산 일치, valid hash의 active payload 변경 거부,
source의 final-only 변경 멱등 승인과 active 변경 새 job 실행, budget revision 뒤 동등 자료 승인,
등록 취소/틀린 source ID/split, repository 정책 불일치, CLI 등록만 수행, 실제 v16 fixture 업그레이드.

v16 fixture는 이전 migration 16개로 실제 DB를 만들고 기존 campaign/job/budget/search 행을 복사한다.
v17 업그레이드 뒤 이전 migration 원장·job 상태/명세/경로를 보존하고 legacy job을 같은 입력으로 실행한다.
이전 v10~v15 업그레이드 테스트의 지원 최신 버전 기대값도 v17로 갱신했다.

최초 좁은 회귀 76개에서 실패 2개를 확인했다. 하나는 생성 조합이 더 남은 예산 종료를 조합 완료로
잘못 기대한 새 fixture였다. 다른 하나는 v10 job 비교가 새 필드 하나만 제외한다고 가정하던 기존 테스트였다.
기존 완료 정책은 변경하지 않았고 이전 컬럼 개수로 데이터를 비교하도록 보완했다.
실행 환경 갱신으로 이어진 전체 테스트 세션이 종료되어 동일 테스트 프로세스가 없음을 확인한 뒤 재실행한다.
전체 회귀 355개 / 226.459초에서는 기존 v11 이관 비교 한 개가 새 컬럼을 포함해 실패했다.
이전 DB의 컬럼 이름을 보존해 해당 컬럼으로 비교하도록 수정했다. 다른 354개는 통과했다.
최종 전체 회귀: **355개 / 221.741초 / OK / 네이티브 종료 코드 0**.
로그 `tmp/cr3a4a-regression-final.log`, 종료 코드 기록 `tmp/cr3a4a-regression-final-exit.txt`.
새 계약 13개와 기존 실행/평가/품질/split/보고/검색/queue/repository/resource/bundle/replay,
campaign worker/budget/source/NAS 준비/용량/임시 정리/완성 복구, offscreen Qt 회귀를 포함한다.
변경 파일 20개의 AST/공백 검사와 대상 git diff --check도 종료 코드 0이다.

## 남은 한 단계와 한계

다음 CR3a4b는 화면의 독립 구간 등록을 background process에 연결하고 성공/취소/복원 흐름을 검증한다.
현재 UI는 기존 v1 등록을 유지하며 독립 구간 등록은 CLI/worker API를 사용한다.
기간/scope는 기존 고정 범위다. 새 날짜 확장·여러 fold 집계·종목 분할·최종 접근 원장·자동 가설/모의주문/일지 피드백은 후속이다.
전체 원본을 먼저 읽는 비용은 유지한다. 실제 NAS 전체 규모/24시간 운전 검증은 수행하지 않았다.
NAS 재빌드는 필요 없다. 실제 사용자 research DB의 v17 적용은 다음 해당 실행기로 열 때 수행된다.

모델 에스컬레이션: 없음.

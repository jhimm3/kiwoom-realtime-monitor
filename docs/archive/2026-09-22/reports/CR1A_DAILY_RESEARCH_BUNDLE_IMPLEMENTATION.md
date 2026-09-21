> **과거 기록** · 원래 경로: `reports/CR1A_DAILY_RESEARCH_BUNDLE_IMPLEMENTATION.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# CR1a — 날짜별 연구 자료 준비

2026-09-16. CR1 전체와 24시간 자동 연구 완료 보고가 아니다.

## 구현

`export_research_dataset.py --dates`가 명시 KST 날짜를 정렬하고 일별 00:00~다음 00:00 범위를
기존 NAS 24시간 API로 추출한다. 기존 하루 `--start/--end` 계약/파일 형태는 유지한다.
`--session-profile`은 필수다. 일별 파일은 `days/YYYY-MM-DD`에 보관하고 bundle index만 추가한다.
서버/API/DB/새 수집 경로/주문 경로는 추가하지 않았다.

`--reuse-days`는 완료·검증된 같은 범위/종류/대상/profile의 파일을 API 없이 재사용한다.
새 날짜나 다른 조건은 완료된 index에 덧붙이지 않는다. 중단 후 첫 날짜 완료 파일은 보존하고
다음 미생성 날짜부터 추출한다. 이미 생성됐지만 손상/불완전한 날짜 파일은 임의 삭제/덮어쓰기하지 않고 오류다.

`research_data_source.py`의 별도 FrozenResearchBundle이 자식 hash와 일별 ordinal/ID를 검증한다.
같은 source revision의 동일 내용은 고유 개수로 계산하며 ordinal 외 내용 충돌은 거부한다.
테마 동일 ID 충돌/잘림, 빠진 파일, index/count/정책 변경, 혼합 입력과 잘못된 날짜 순서도 거부한다.
quality_summary/실제 조회 범위를 그대로 보존하며 빈 날짜를 휴장/완전 수집으로 단정하지 않는다.
소스 파일/일별 watermark는 그대로 두고 새 공통 ordinal을 만들지 않는다.

## 입출력 및 사용 범위

- 입력: 명시 KST 날짜 목록 + 기존 observation 종류(rank/membership/minute) + subject + 명시 session profile.
- 출력: 일별 D2 export와 bundle index. 자식 hash/범위/품질/고유 revision·테마 개수를 포함한다.
- 검증 reader: `load_frozen_research_bundle`; 기존 FrozenResearchDataset을 대체하지 않는다.
- 현재 하루 runner는 bundle을 명시 거절한다. 실험을 돌리기 위한 CR1b 연속 reader/실행 연결은 미구현이다.
- 날짜 목록은 거래소 휴장 달력 자동 선택이 아니며 실제 session predecessor도 아직 계산하지 않는다.
- 기존 테마 API limit=1000의 잘림 표시가 있으면 성공으로 묶지 않는다. 전체 테마 추출 개선은 후속이다.

## 검증

`tmp/cr1a-tests.log`: 69개, 7.073초, exit=0, OK. 신규 bundle/export 테스트 15개.
기존 reader/replay/process/comparison/execution/search/queue 회귀를 포함했다.
fixture API로 날짜 추출/두 번째 날짜 실패/재시도에서 첫 날짜 무호출·원본 보존을 확인했다.
변조/누락, 동일 revision dedup·충돌, 테마 충돌/잘림, 경로 탈출, legacy/profile/naive 시각,
출력 파일 보존, 완료 index 불변을 확인했다. 실제 NAS나 사용자 DB/API/키/주문은 사용하지 않았다.

## 다음 단계

CR1b: bundle의 available_at/ingest 순서 reader, 동일 revision 단일 소비, 테마 시작 전 snapshot/기간 변경,
보유·현금·pending의 거래일/세션 경계, 기존 하루 논리 결과 일치, 1/5/20일 처리시간/RSS 및 짧은 CPU 양보.
세션별 손익을 단순 합산하거나 하루 끝에 임의 청산하는 방식으로 연속 실행을 대체하지 않는다.
그 다음 CR2 지속 campaign을 구현한다. 현재 CR1a는 PC 자료 준비이므로 NAS 중간 배포를 하지 않는다.

모델 에스컬레이션 없음.

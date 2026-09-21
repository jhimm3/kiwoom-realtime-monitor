> **과거 기록** · 원래 경로: `reports/A5A_ACCOUNT_EXECUTION_EVENT_READ.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# A5a 계좌별 실행 원장 증분 읽기

완료일: 2026-09-16

## 구현 범위

- 중앙 SQLite/PostgreSQL 실행 event를 intent와 join해 `environment + account_ref`로 제한한다.
- `accepted_sequence` 이후 최대 1,000행을 정렬해 읽고 `next_cursor/has_more`를 반환한다.
- 각 행은 불변 중앙 event ID를 `source_event_id`로, run/decision을 계보로 보존한다.
- NAS API는 현재 mock credential profile과 binding revision을 재검증한다.
- PC 원격 클라이언트는 응답 계좌 문맥, 단조·중복 없는 sequence, cursor를 검사한다.

## 보호한 계약

- run이 바뀌어도 같은 계좌의 원장은 이어서 읽는다.
- 같은 cursor 재조회는 같은 행을 반환하며 다른 계좌 행이 섞이지 않는다.
- 조회는 Kiwoom TR, 주문 전송, 실행 원장 쓰기, 매매일지 쓰기를 만들지 않는다.
- `BROKER_FILL_AGGREGATE`를 상세 체결로 간주하지 않는다.

## 검증

- 직접 경계 3개 모듈: 25개 통과.
- 주문 수명주기, 중앙 서버·DB·설정, mock 실행, 전진평가, 일지 연구 링크·보완까지 넓힌 인접 회귀: 149개 통과.

## 다음 단계

상세 `FILL`만 매매일지 staging에 투영하고 source event ID 및 계좌·거래일·주문·체결 키로 멱등화한다.
cursor와 행 저장을 한 트랜잭션으로 확정하며 aggregate 누락량은 별도 상태로 유지한다.

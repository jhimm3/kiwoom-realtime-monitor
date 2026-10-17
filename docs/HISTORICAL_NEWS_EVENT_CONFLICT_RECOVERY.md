# 과거뉴스 사건 충돌 한 건 복구 결정

2026-10-16. 상태: **사건·조회 목록 생성 완료. 검사기 버전 추적 오류를 수정해 14:40 KST 전체 검사·봉인을 재개했다. NAS 전송은 사용자 요청으로 보류**.
사용자 승인 범위는 사건 연결 충돌 복구와 체크포인트 재개다. 분류 오판 수정, BODY/RULE 재처리, 기존 사건 병합, NAS 변경은 포함하지 않는다.

## 실제 적용과 현재 경계

2026-10-16 최종 검사 보완: 10월 15일 18:20의 실패는 v7에서 완료한 BODY/RULE 대상 지문을 사건 처리만 바꾼 현재 v86 digest로 비교한 검사기 오류였다. 검증된 finalization lineage의 prefix는 부모 digest, suffix는 해당 finalization 세대의 digest로 검증하도록 수정했다. 잘못된 지문을 만나도 같은 대상 스트림을 끝까지 읽어 검토 ID와 prefix root를 수집하므로 뒤쪽의 승인 내역을 누락한 연쇄 오류를 만들지 않는다. 실제 변조는 계속 차단하고, 최초 5개 대상 불일치와 사건 검토 실패 사유를 gate 결과에 남긴다.

연결 경로는 기존 v86 실행기 → `runner-performance-options.json`의 별도 `verification_code` → 기존 completion helper → 해시 고정된 v87 검사기·sealer → 기존 배타 잠금과 전체 검사·봉인이다. 생성 코드 545개 파일, 기존 BODY/RULE·사건·조회 자료와 8건 receipt를 보존했다. 기존 생성 code digest와 dataset ID는 변경하지 않았으며 과거 오류 로그·설정은 별도로 보존했다. 작업 디렉터리의 `verification-v87-finalization-lineage/verification-code.json`이 실제 검사 소스를 고정한다.

관련 회귀 53건 통과 후 부정 검증을 보완했고 최종 두 재현 검사가 통과했다. 실제 frozen v86 생성 코드와 새 검사기를 결합한 최종 24건도 통과했다. 초기 샌드박스 임시 DB 접근 오류와 테스트용 변조 복원 범위 오류는 최초 로그와 분리해 보존했다. 이는 all-local 전수나 실제 대용량 봉인 완료를 뜻하지 않는다. 진단·테스트·재개 근거는 `data/diagnostics/20261016-archive-audit-lineage/`에 있다. 14:40 실제 audit worker 실행과 NAS hold를 확인했다. 전체 검사 통과 및 봉인 완료는 아직 미확인이고 NAS 게시·서버 변경은 하지 않았다.

최신 실행 확인(2026-10-15 09:17 KST): 사용자가 두 번째 건과 이후 같은 계약의 시황뉴스 6건 연결·실행 설정 갱신·재개를 명시적으로 승인했다. v86 전환 및 총 8건의 검토 연결을 적용했고 각 DB receipt를 재검증했다. `approved-six-completion.json`에서 승인된 6건 완료·기존 2건 포함 receipt 8건·NAS hold를 확인했다. 이어받기 실행기는 `run-local-input-generation-v86.py`, 실행 코드 digest는 `aa5f70a87283cc5fc738c8b0143db2f1b8f436d4993b95df8d0ecb0fe21b6068`이다. 실제 runner/worker의 PID·생성시각·명령을 확인했고 사건 cursor가 11122922→11134422로 11,500개 전진했다. 이 확인 시점 대기 대상은 190,993개이고 conflict는 없었다. NAS 전송 보류 파일과 게시 진입점의 차단은 유지한다. 아래 승인 대기·거절 표현은 당시 기록이며 현재 중단 상태를 뜻하지 않는다.

- 사용자 추가 지시: **NAS에 보내기 전에 멈춘다.** PC 생성·조회 목록·검사·봉인까지는 허용하지만 NAS 전송은 명시적 재개 요청 전까지 보류한다. 작업 폴더의 `nas-transfer-hold.json`에 기록했고 실제 게시 진입점 `publish-archive-to-nas.ps1`이 NAS 접근 전에 이를 확인하도록 연결했다. `-CheckOnly`에서 `held_before_nas_transfer`, `nas_changed=false`를 확인했다. 현재 사건 충돌 정지는 이 NAS 전송 보류와 별개다.
- 기존 관련 회귀와 복구 검사 46건이 통과했다. 고정 v85에서도 신규 복구 검사 8건과 전체 세대 전환·중복 실행·재개의 작은 DB 검증을 통과했다. 테스트 자료 구성·Windows 임시 경로/인코딩 오류를 성공으로 계산하지 않았다.
- v84→v85 실행 계보 전환 후 첫 충돌을 적용했다. 생성한 event revision은 `39bacd3576b147feb5c5b10c0c4938a7`, membership은 `d5ba2cb98e0f478d94ee77890c179a7a`다. 기존 ID 9개와 기존 event/membership 행 각각 46개를 직접 대조했고 기사·본문·computed 규칙·ordinal/cursor 보존을 확인했다. [적용 대조](../data/diagnostics/20261015-archive-event-conflict-review/applied-verification.json).
- 제한 재개 250개가 완료됐고 v85 runner에서 추가 1,680개가 완료됐다. 이어지는 다음 대상 한 개는 다른 기사(`https://n.news.naver.com/article/018/0006364661`, 336260)의 동일 계약 후보 중복으로 차단됐다. 현재 `next_ordinal=11120592`, 두 번째 conflict ordinal `11120591`, 그 뒤 대기 대상 `204823`이다. 현재 conflict 한 개는 이 대기 수와 별개다. Runner는 blocked 결과를 기록하고 종료했으며 활성 writer는 없다.
- 두 번째 기사 본문도 하이엑시엄 PAFC·5,014억 원·미국 데이터센터 계약을 설명한다. 8,821억 원은 2027년 매출 전망치다. 이 기사만 별도 검토한 [근거](../data/diagnostics/20261015-archive-event-conflict-review/ordinal-11120591/evidence.json)와 [결정](../data/diagnostics/20261015-archive-event-conflict-review/ordinal-11120591/decision.json)을 준비했다. 앞선 결정을 다른 기사에 자동 재사용하지 않는다.
- 다음 검토 건마다 실행 소스를 다시 바꾸지 않도록 v86에 `register-event-conflict`를 추가했다. 현재 고정 코드의 writer lock/transaction에서 독립 검토 결정을 sparse manifest 원장에 고정하며 실제 target/event 행은 등록 단계에서 수정하지 않는다. 복구와 audit가 그 등록의 dataset·code digest·결정 hash 및 완료 receipt를 함께 검증한다. 첫 세대 전환의 decision pin도 계속 지원한다. 일반 ambiguous 차단, 본문/규칙, 기존 ID 보존은 유지한다.
- 추가 등록 경로 포함 관련 검사 13건과 고정 v86 신규 검사 9건 및 전체 실행 전환/재개 검증이 통과했다. 고정 v86 digest는 `aa5f70a87283cc5fc738c8b0143db2f1b8f436d4993b95df8d0ecb0fe21b6068`이다. [고정 실행 검증](../data/diagnostics/20261015-archive-event-conflict-review/verify-frozen-recovery-v86.json).
- **v85→v86 전환과 두 번째 연결 적용은 자동 승인 검토가 거절했다.** 최초 단일 충돌의 승인 범위를 넘는 고위험 실자료 변경이라는 이유다. 실행은 실제 DB 변경 전에 거절됐으므로 현재 아카이브는 v85이고 두 번째 target은 conflict 그대로다. 명시적 추가 승인을 요청했다. 우회 실행하지 않는다.
- NAS 접근/변경/게시, 예약 추적 생성, 원문 재수집 및 분류 오판 수정은 하지 않았다. 최초 설계 및 아래 구현 계약의 단계 설명은 이 실제 적용 상태와 함께 읽는다.

## 확인한 실행과 자료

- 실제 DB: `data/historical_collection/archive-v2-20261013/archive-rowid-range.building.sqlite3`.
- 실행 세대: `historical-news-20261015-v84-event-bounded-cache`, construction digest `1814f63194ca08e67b81bfe27eba95f6a457167564947538b57f59536a5589f1`.
- 현재 runner는 `run-local-input-generation-v84.py` 계열이다. 오래된 `run-local-build.py`는 다른 DB와 v3 소스를 지정하므로 재개에 사용하지 않는다.
- `events_v2.target_count=11325414`, `next_ordinal=11118661`. 충돌 대상의 **이미 저장된 ordinal은 11118660**이다. 따라서 앞선 11,118,660개 전부가 성공했다는 설명은 잘못이며 이 숫자에는 충돌 행 한 개가 들어 있다. 그 뒤 대기 대상은 206,754개다.
- 대상 PK: `(historical_market_backfill, GLOBAL, https://n.news.naver.com/article/016/0002693095, 336260)`.
- 기사 `a2130ba8b98d4c14a4ed9fd256a1eb80`, 본문 `b255c7cf8bad48099e1148dbdf5f2596`, fingerprint `fed33ec12950e76b897fa6b24965fe5a3af961ce454063b2b2b46a140f6387b5`.
- `archive_targets_v2` 및 `archive_event_ready_v2`에 conflict가 저장됐고, 이 대상의 event/membership ID는 아직 없다. 실행은 blocked이며 NAS 복사는 시작하지 않았다.

읽기 전용 transaction으로 보존한 근거: [decision-evidence.json](../data/diagnostics/20261015-archive-event-conflict-review/decision-evidence.json). 대상 before-image, 실제 후보 9개 및 event/membership 전체 행과 hash, 본문 근거, 기존 writer의 선택 ID를 포함한다. 이 파일은 **검토 근거**이며 그 자체가 실행 완료 receipt나 테스트 통과 증명은 아니다.

## 사건 판단과 선택

이 사건 지문은 종목·추출 상대방·금액에 의존한다. `데이터센터`는 실제 법적 계약 상대방을 식별하는 고유 이름이 아니므로 지문 일치만으로 모든 사건을 합치는 일반 정책을 만들지 않는다.

이번 9개 후보의 본문은 모두 하이엑시엄을 통한 5,014억 원 PAFC 공급과 미국 데이터센터를 설명한다. 날짜가 명시된 보도는 9월 2일 계약 발표를 가리킨다. 최신 지문이 바뀐 후보의 과거 matching revision도 이 계약을 설명한다. 8,821억 원은 관련 보도의 2027년 매출 전망치다. 따라서 최신 지문만 비교해 후보 2개를 자동 제거한다는 이전 제안은 채택하지 않는다.

**9개 ID 및 모든 기존 event/membership revision은 그대로 보존한다.** 이번 대상 한 건만 `4f29261cb5c846bd9dd8be7ff1af50ab`에 연결한다. 이 ID는 현재 `_save_sqlite_news_event`가 동일 종목·event_key 중 `accepted_sequence DESC`로 선택하는 ID이며 matching revision은 `4be2204c08aa465db4724aec83a23b98`, sequence 20952다. 이는 검토된 동일 계약의 여러 기존 ID 중 신규 연결 하나를 선택하는 기준이며, 과거 ID 전부를 하나로 정규화한다는 뜻이 아니다.

대상의 `computed_json`, `prepared_json`, fingerprint, rule version과 `TERMINATED` 결과는 보존한다. 알려진 `약해지면서`/`해지` 부분 문자열 오판은 이번 범위에서 수정하지 않는다. 이번 복구는 분류 정확성을 보증하지 않는다.

## 구현 계약

기존 builder의 명시적 복구 명령으로 구현한다. 보호 조건을 삭제하거나, 충돌 행을 수동으로 성공 처리하거나, 운영 writer에 전역 tie-break 정책을 추가하지 않는다.

1. `prepared_historical_archive_v2.py`에 대상 한 건의 검토 결정을 검증·적용하는 명령을 둔다. 결정에는 archive identity, 이전 dataset/digest, target PK/fingerprint와 immutable 필드 hash, ordinal/cursor/target_count, 전체 candidate set 및 관련 event/membership before hash, selected ID, 근거 파일 hash가 필요하다. 근거 스키마와 실행용 decision/receipt 스키마를 구분한다.
2. `transition_historical_archive_execution.py`의 일반 active-events 전환은 계속 conflict를 거절한다. **별도로 전달된 검토 결정이 정확히 현재 충돌 한 건과 일치할 때만** 검토 복구를 위한 새 세대 전환을 허용한다. 기존 OS lock, transaction, 부모 코드 파일 검증, dataset/code digest, lineage 검증은 유지한다. 결정 내용과 hash를 불변 transition plan 및 DB 계보에 고정한다. 다른 충돌·바뀐 후보·없는 ID·시작된 projection/audit/seal은 거절한다. 이 전환은 기존과 같이 manifest/계보만 변경하고 canonical 및 target 행을 바꾸지 않는다.
3. 허용할 코드 차이는 복구에 필요한 `prepared_historical_archive_v2.py`, `finalize_prepared_historical_news_events.py`, `transition_historical_archive_execution.py`, `audit_prepared_historical_archive_v2.py`로 한정한다. 이 확장은 검토 결정이 있는 복구 전환에만 적용한다. 이전 frozen 세대 파일은 수정하지 않는다. 기존 source/runtime와 NAS writer는 변경하지 않는다.
4. `_resolve_event`에 내부의 검토 선택 경로를 추가한다. 기본 경로의 ambiguous 차단은 그대로다. 복구 caller가 계보에 고정된 결정을 검증하고, resolver도 실제 후보 집합·선택 ID·종목·event_key 및 기존 writer가 선택할 ID를 다시 확인한다. 이번에는 `ambiguous_event_key`만 허용하며 identity와 key가 충돌하는 다른 경우로 확대하지 않는다. 기존 `_save_sqlite_news_event`, 규칙 input hash, 최근 기사 힌트와 revision/membership 검증을 그대로 재사용한다. 결과 event_id가 검토 선택과 다르면 같은 transaction을 rollback한다.
5. 복구는 `_writer`의 OS lock과 **한 transaction** 안에서 신규 event/membership을 만들고 target/ready 상태, 필요한 input progress 및 자식 ready 상태, DB receipt를 함께 저장한다. 완료된 다른 target과 기존 canonical 행은 수정하지 않는다. 대상의 ordinal 11118660과 manifest의 next_ordinal 11118661을 그대로 둔다. 커서를 되감거나 증가시키지 않는다. 복구 대상만 다시 처리한다.
6. input의 다른 pending/conflict target이 없을 때만 `event_done`으로 바꾸며, 해당 article의 모든 target이 끝났을 때만 자식의 ready를 해제한다. 현재 finalize_events의 조건을 동일하게 적용한다. 실패하면 신규 revision·상태·receipt를 모두 rollback한다. commit 직후 프로세스가 종료되어 외부 receipt가 없어도 DB receipt로 복구한다. 같은 결정 재호출은 출력 ID/상태를 대조해 no-op이며 변경된 결정은 거절한다.
7. 원래 후보 집합과 conflict 이유는 receipt에 보존하고 target에는 검토 연결임을 구분할 reason을 남긴다. audit는 검토 연결과 receipt의 양방향 대응, decision/plan hash, 원래 target 필드 보존, 출력 ID·membership·rule input 관계 및 기존 후보의 immutable before rows 보존을 검증한다. boolean 승인만으로 검사를 통과시키지 않는다. 기존 seed 전체 보존·본문·coverage·무결성·봉인 검사는 유지한다.
8. 전체 DB 복사나 전수 재처리를 하지 않는다. 충돌 검색은 state 선두 인덱스가 있는 `archive_event_ready_v2`에서 제한된 행을 읽고 target은 PK로 조회한다. candidate/revision 비교는 해당 9개 ID만 대상으로 한다. audit의 기존 전체 검사는 필요하지만 복구 사전 검사를 위해 대형 원자료를 반복 스캔하지 않는다.

## 검증과 실제 재개 순서

- 격리 fixture: seed 9개 후보·그중 최신 지문이 달라진 2개·이미 완료한 target·막힌 target·후속 의존 target을 재현한다. 기본 충돌 재현 → 검토 복구 → 기존 finalize_events 재개의 경로를 검사한다.
- 반드시 검증: 기존 9개 ID/모든 seed 행/본문·규칙 결과와 completed target 보존, 신규 event/membership 정확히 한 쌍, ordinal/cursor 불변, 의존성 준비 해제 조건, 미처리 건만 이어가기, 실패 rollback, commit 후 재호출 no-op.
- 거절 검사: 잘못된 선택 ID, 변경된 후보 집합/본문 증거/target fingerprint/cursor, 다른 stock/key, 다른 conflict, 누락/위조 receipt, 미고정 decision, 일반 전환에서 conflict, 활성 writer 잠금. 새 충돌을 자동 승인하지 않는다.
- 실제 적용 전 현재 프로세스 identity와 writer lock을 확인한다. 기본 샌드박스에서 CIM 조회 실패 또는 python 목록이 없다는 것만으로 호스트 전체에 writer가 없다고 단정하지 않는다.
- 좁은 회귀 통과 후 새 frozen 세대를 만들고 차이·hash를 검증한다. 검토 복구 전환 → 한 건 복구 → 제한된 후속 event batch로 진전 확인 → 해당 세대 runner 재개 순서다. 성공 전 재개했다고 보고하지 않는다.
- 현재 v84 runner/control/phase/options를 기준으로 새 세대만 가리키게 하고 완료된 입력·coverage·검색/시황 최종화를 다시 실행하지 않는다. 별도 verifier를 사용하는 completion gate의 audit 파일 경로/hash도 새 검증 코드와 맞춘다. 기존 independent verifier 설정을 무시하면 안 된다.
- NAS 복사/활성화와 예약 추적 재생성은 이 단계에 포함하지 않는다. 로컬 사건 완료 후 projection → audit/seal은 기존 경로로 이어간다. 다른 충돌은 별도 evidence로 검토하고 이번 결정을 전역 규칙으로 재사용하지 않는다.

## 인계

Astra High에서 구조·데이터 보존 판단을 완료한 뒤 GPT-6.1 Sol High에서 구현·검증과 최초 적용/재개를 수행했다. 승인된 총 8건을 복구하고 v86 PC runner를 재개했다. 남은 것은 진행 중인 사건 처리와 기존 projection/audit/seal이다. 다른 미승인 충돌은 자동 연결하지 않는다. NAS 게시/활성화는 전송 보류가 해제될 때까지 수행하지 않는다.

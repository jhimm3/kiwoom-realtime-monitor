# 테스트 계약·fixture 검토 결과 — 2026-10-09

## 범위와 실행 결과

409개 조사 원장을 다시 감사하지 않고, 남아 있던 계약/fixture 검토 6개를 같은 조건으로 재현했다.
제품 소스, DB 스키마, 운영 설정은 변경하지 않았다. 변경 전 78건은 failure 5/error 1이었으며,
변경 후 아래 81건은 failure/error/skip/expected failure/미실행 0으로 통과했다.
6개 worker 모두 process tree 종료를 확인했고 잔류 자손은 없었다.

| 모듈 (`tests.unit.` 생략) | 확인한 원인과 수정 | 변경 후 건수 / worker 시간 |
|---|---|---|
| `test_daily_bar_coverage` | fake가 `as_of`를 받지 않아 의도한 계산 중 입력 변경에 도달하지 않았다. 종목·날짜와 호출을 검증하고, 입력 변경 후 RuntimeError·결과 미저장·readiness=False 검사를 유지했다. | 22 / 5.36초 |
| `test_dart_credential_owner` | 조회 전용 search로 수집을 기대했고 고정 기사 날짜가 실행 날짜에 따라 기간 밖이었다. 시계를 고정하고 TOP20 membership → refresh_once → 저장 → search로 검증한다. search의 공급자 미호출·watchlist 무확대·저장 무변경도 검사한다. | 18 / 12.55초 |
| `test_diagnostic_trace_api` | 기대 capabilities가 현재 계약의 top20_inputs 옵션과 0w/0J/0U를 누락했다. 전체 목록을 확인하고 인증·schema·coverage/overhead의 미검증 표시를 유지한다. replay 지원 범위도 명시 검사한다. | 4 / 3.26초 |
| `test_audit_postgres_access` | 승인 50곳과 현재 53곳 차이였다. 아래 3곳의 격리·소유·종료를 검토해 정확한 연결만 승인했다. 미승인 연결과 stale 승인 실패 검사는 유지한다. | 5 / 16.14초 |
| `test_audit_query_store_consumers` | owned_to_thread import가 인식되지 않아 callable dispatch를 잃었다. 실제 import/alias를 인식하고 다른 모듈·인자·지역 변수·재import shadowing은 제외한다. 호출 수와 계약 차이는 별도 검토했다. | 4 / 6.08초 |
| `test_research_final_preparation` | 지연 개장 필드 3개 추가로 legacy/current 후보 식별자가 달라졌다. 기존 b3b3 해시를 보존한 정적 fixture와 current fixture를 함께 검사하며 호환 reader 및 잘못된 해시·비정규 세 필드를 거부하는 검사를 추가했다. | 28 / 14.09초 |

재현 증거는 `tmp/regression/contract-six-before-20261009/summary.json`, 수정 후 증거는
`tmp/regression/contract-three-after-20261009/summary.json`과
`tmp/regression/contract-audits-research-after-20261009/summary.json`이다. 임시 실행 자료는 커밋 대상이 아니다.

일봉 변경 알림을 메모리 patch로 누락한 대조군에서 원래 테스트가 `RuntimeError not raised`로
실패했다(1 failure, error/skip 0). 정상 통과 건수에 포함하지 않는다. 제품 파일 SHA256은 대조 전후
`9dd9332eb4d6588f8ec9f426446fb581aa4eb19a3eab6b7f2435762c3609e4ab`로 동일하다.
증거는 `contract-three-after-20261009/daily-invalidation-mutation.json`이다.

## 직접 PostgreSQL 연결 3곳의 승인 근거

- `scripts/check_replay_cache_baseline.py:run`: network-none disposable loopback cluster,
  postgres/admin/fixture 신원과 pristine databases를 확인한 뒤 provision한다. bounded timeout과
  connection context를 사용하며 운영 DSN을 받지 않는다.
- `scripts/nas_operator_worker.py:database_setup`: supervisor가 새 network-none PostgreSQL container와
  임의 admin 자격증명을 만든다. 고정 localhost/admin DB, 2초 연결 제한, pristine-cluster 확인 후
  연결을 닫고 fixture URL을 반환한다.
- 같은 worker의 `replay`: database_setup의 fixture URL만 사용하며 hash 확인된 bounded baseline을
  비-superuser replay role로 실행한다. context가 commit/rollback 및 close를 소유한다.

이는 실제 PostgreSQL 실행 성공의 근거가 아니라 정적 admission/소유권 검토다.

## QueryStore 기준선의 한정된 조정

이전 원장은 commit `fe9f246678729cc8f5f228481187ee37e23716bd`의
`docs/db_refactoring/baseline_query_store_consumers.json`에 남는다. 이전 SHA256은
`12800d6fd36696b41b3279a1bde36a175d990b02e7f0e4630e95ea9554b7b4a7`이다.
전체 현재 inventory를 자동 승인하지 않았다. identity Counter의 추가/삭제와 아래 8개 signature를
정확히 대조했으며, 예상 밖 multiplicity 차이가 나온 첫 조정은 쓰기 전에 거부했다.

- owned dispatch 48회 → 52회: 기존 wrapper의 소유 실행을 정규 이름으로 기록한다.
  historical-high의 NXT daily-bars 1회와 coverage 문서 1회, collector minute 읽기 2회를 확인했다.
- 뉴스 조회 4개 binding은 app에서 news_read_routes로 이동했다. 같은 route/method와 기존 HTTP
  기준선은 유지한다. app → create_news_read_router store 전달 edge 1개를 추가했다.
- minute API 2곳의 direct read는 공통 load_display_minute_bars reader 1곳으로 옮겨졌다.
  collector의 RAM overlay 또는 DB fallback 경로를 확인했다.
- diagnostic collector fixture 읽기 1개와 TOP20 flow fixture 읽기 2개는 새 candidate로 기록하지만
  `unresolved_candidate`를 유지한다. 실제 DB binding으로 승격하지 않는다.
- QueryStore/SQLite/Postgres `load_minute_bars` 3개: 선택 keyword-only `realtime_deltas=None`.
  기본 저장본 조회와 명시 delta의 동일 읽기 snapshot/완료 marker 구분을 확인했다.
- 같은 세 구현의 `replace_daily_bars` 3개 및 SQLite/Postgres private `_replace_bars` 2개:
  commit 이후 변경 (code, day, market) tuple 반환 및 no-op의 빈 결과 의미를 확인했다.
  invalidation, commit ACK 오류의 dirty 처리, revision/rollback 검증은 보존한다.

조정 후 정적 inventory는 pass, parse error/stale binding 0이다. protocol 99, PostgreSQL 104,
SQLite 103, reviewed site 341, internal delegate 4, unresolved candidate 26, forwarding edge 47이다.
정적 기준선 일치는 제품의 transaction·동시성 실행 검증을 대신하지 않는다.

## 연구 후보 식별자 보존

`tests/fixtures/research/final_candidate_identity_v1.json`은 자동 재생성하지 않는 정적 입력이다.
legacy SHA256 `b3b3b2343b53cf880dad7e8191a98bdf572bacf6a0c74679e9594d4a5217613e`와
current SHA256 `8b800c54c29d05f128a70248757052bacbd1129693e22f7fc1178ccd6bf6cba6`를 모두 검사한다.
세 지연 개장 필드만 제거하면 legacy 입력·해시가 재현되고 현재 validator는 둘 다 읽으며
입력을 다시 쓰지 않는다. 내부 validator 직접 호출은 식별/호환 계약 자체가 대상이므로 유지한다.
날짜·DB 경로·budget 제외 및 전략·비용·구현 hash 관련 기존 검사는 유지했다.

## CI 편입 및 전체 로컬 검증 결과

해결된 6개만 `dependency-audit-p0-contract-fixtures` profile로 기존 목록 뒤에 추가했다.
현재 manifest는 178개 등록/231개 미등록이며 미등록은 Windows 일반 후보 226개와 Linux 5개다.
이전 226+5+6 분류는 조사 당시 상태로 보존한다. 기존 profile과 core 순서는 바뀌지 않았다.
새 모듈 검사도 기준 commit `fe9f246678729cc8f5f228481187ee37e23716bd` 대비 새 test module 0개,
미등록 0개로 통과했다.

새 profile 격리 실행은 81건 통과, worker tree 종료 6/6이다. 이후 현재 수정본의 전체 `all-local`은
1,868건/38 worker 통과, failure/error/skip/expected failure/unexpected success/미실행 0,
process tree 종료 38/38, 잔류 자손 0이다. run은
`tmp/regression/dependency-audit-ci178-contract-fixtures-final-20261009/run.json`에 보존했다.
manifest SHA256은 `caf0634750c6964480da05690b6250c2f45ed2a21ac16ea0e3d9067d1e1e8ff3`,
runner SHA256은 `659c4c11fd3546dec156b77d5fb24b37245f7ddd48cb8ee8c8f9741b672c93d1`이다.
등록 모듈 178개의 해시가 기록됐고 현재 모두 일치한다. 변경된 테스트 파일 17개 중 이번 all-local의
해시/실행 범위에는 등록된 6개가 포함됐다. 나머지 11개 함수형 변환 파일은 등록 대상이 아니며,
별도 프로필 실행에서 발견 복구한 45건이 통과한 증거를 유지한다.

저장 invalidation, 공급자
자격증명/조회 분리, 진단 지원 범위, DB 연결/소비자 guard, 영속 후보 identity를 자동 보호한다.
현재 로컬 검증 단계는 완료했다. hosted GitHub CI와 disposable PostgreSQL 독립 환경 검증, Linux 5개,
`test_central_server_app`의 closure P2 개선 필요성 판단은 남아 있다. 검증 공백·제외 이유와 방법을
원장에 기록하고 이 고위험 계약들의 별도 환경 검증 결과를 확인하면 이번 감사를 종료한다.
409개 전체의 무기한 개선으로 확대하지 않는다. NAS 배포와 main 병합은 별도다.

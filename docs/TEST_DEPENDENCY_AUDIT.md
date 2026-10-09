# 전체 409개 테스트 의존성 조사

2026-10-08 · 제품 2.1.0 · 기준 소스 `641a821e45e4a5302fc985eeec6236accabed7cf`

## 현재 결정

2026-10-09 중복/상시 CI 후속 분석 및 구현 진행: [판정·수정·검증 상태](TEST_DUPLICATION_CI_FOLLOWUP.md).
최신 main 통합과 NAS/capture·replay·causal 종료·TOP20·trace 저장·진단 제어 선택 편입 뒤 1~5차
선택 묶음 122개를 추가해 현재 전체 413개 중 Windows `all-local` 등록 366개, 별도 Linux CI 5개,
미등록 후보 42개다. 기존 core 실행 순서는 보존했다. 최신 전체 로컬 결과와 hosted CI 진행 상태는
[후속 보고](TEST_DUPLICATION_CI_FOLLOWUP.md)를 따른다.
409개 조사 대상의 중복 선별에서 삭제할 완전 중복은 확정되지 않았다. 실행기의 빈 모듈 false-green을
차단했고, 검증된 선택 profile 26개를 추가했다. 관련 선택 profile 218건과 모듈 간 setup 결합 수정은
통과했다. 전체 `all-local`과 hosted CI 결과는 후속 문서의 최신 상태를 따른다. 아래의 당시 manifest,
'미커밋/hosted 미실행' 표현은 각 이전 검증 시점의 기록이다.

2026-10-09 최신 main의 CI 누락 검토: `3c42285fc490cba4faffad46c7ad307453930780`의 Windows CI는
새 unit module 3개의 미등록 때문에 전체 회귀 전에 실패했다. 해당 3개를 끝의 독립 profile로 등록했다.
원래 409개와 이후 RAM module의 조사·분류를 다시 시작하거나 남은 195개를 일괄 편입하지 않았다.
새 main module 15/15와 직접 관련된 DB·진단·실행기 243/243, 실제 이전 base를 지정한 새 모듈
등록 guard가 통과했다. 최신 전체 회귀는 작업 전용 branch의 hosted CI와 이전 로컬 기록을 구분해 판정한다.

2026-10-09 선택 편입 후속: catalog capture, replay DB CLI, recorded workload capture,
portable NAS operator 4개를 `dependency-audit-p2-nas-capture-safety`에 등록했다.
원래 미등록 260개에 속한 4개이며 기존 CI 149개나 assertion은 변경하지 않았다.
선택 검사 83/83, failure/error/skip/미실행/잔류 자손 0, worker와 process tree 종료 4/4다.
직전 commit `10e9080`의 hosted 전체 회귀는 Windows 2,154건/73 worker, Linux 65건,
disposable PostgreSQL 87건 및 저장 경계 검사 통과로 별도 확인했다.
새 4개를 포함한 최종 hosted 결과는 [후속 문서](TEST_DUPLICATION_CI_FOLLOWUP.md)와 해당 branch CI를 따른다.

### 2026-10-09 최종 후속 회귀

`test_real_account_monitor`의 주기 검사는 이벤트 수신 시 safety deadline이 재설정되는 동작을
실제 작업 시작 시간과 비교했다. 기존 고정 대기 검사는 테스트 프로세스 부하에 따라 cycle 사이에
간헐적으로 실패했다. 테스트에서만 asyncio loop clock/wait를 제어해 시간 조건을 결정적으로 만들고,
실제 DB 저장과 요청 경로는 유지했다. 모듈은 20/20 통과했으며 이벤트 deadline 재설정을 제거한
결함 주입 대조군은 의도한 assertion에서 실패했다.

이 수정과 실행기·26개 profile 변경을 포함한 Windows `all-local`은 2,136건/70 worker 통과했다.
failure, error, skip, expected failure, unexpected success, 미실행 worker는 모두 0이고,
process tree 종료는 70/70, 잔류 자손은 0이다. 실행 기록은
`tmp/regression/test-dedup-ci-all-local-stable-final/run.json`이다. Windows 210개 모듈과 신규
profile 26개 전체가 기록에 있고, 모듈별 발견 수와 총 실행 수가 일치한다. 기존 core 순서 hash는
`36DAE599F514EC9356B7DC3EAB9C7B4F1033C4A27076EB8A7FCD931FBEB1810A`로 유지됐다.

새 테스트 모듈 누락 검사도 현재 작업 트리 기준 통과했다(신규 파일 0개). 이 검사는 PR base 비교나
hosted CI 결과를 대신하지 않는다. GitHub hosted CI와 실제 NAS PostgreSQL 실행은 아직 검증하지
않았으며, 로컬 `all-local` 성공과 별도로 남겨 둔다.

2026-10-09 fixture follow-up: `test_research_final_preparation` no longer constructs
`DevelopmentValidationTests` or reaches through its nested partition fixture. The test now owns its
temporary directory and builds its request/source directly from the existing pure row, child-export,
and request-document helpers. Candidate hash, transaction, history, and projection assertions are
unchanged. The module passed 28/28, its validation/partition neighbors passed 47/47, and the full
`all-local` passed 1,906/43 workers with failure/error/skip/expected-failure/unexpected-success/unrun
all zero, process trees confirmed exited 43/43, and leaked descendants 0. See
`tmp/regression/all-local-fixture-refactor-user-context-20261009/run.json`. Hosted CI and live
PostgreSQL/NAS checks were not run for these uncommitted changes.

2026-10-09 후속 결함 주입으로 `test_build_prepared_historical_search_projection`의 rollback 공백을 확인했다.
기존 검사는 batch 첫 INSERT 실패만 주입해 rollback을 `commit()`으로 바꾼 결함도 통과했다. 부분 row 저장 후
INSERT 실패와 row batch 뒤 progress manifest 갱신 실패를 각각 주입하도록 강화하고, projection rows·전체 manifest·
source 목록 전 상태 보존, lease 해제, retry 완료와 idempotence를 검사한다. 두 결함 대조군은 모두 실패했다.
관련 4개 모듈 20/20, 격리 profile 38/38, 현재 `all-local` 1,906/43 worker 통과, 실패·오류·skip·
expected failure·unexpected success·미실행 0, process tree 종료 43/43, 자손 누수 0이다. 현재 manifest는
183개 등록/226개 미등록(Windows 221, Linux 5)이며 새 profile과 검사 기록은 로컬 미커밋 상태다.
`origin/main` 기준 새 unit test module coverage gate는 새 모듈 3개 모두 등록으로 통과했다.

기존 CI의 고유 테스트 파일 149개와 미등록 260개를 **같은 기준**으로 조사했다.
전체 AST/본문에서 import, patch, private 호출, 경로/디렉터리 검사, 테스트 간 fixture 공유를
수집하고, 실제 실패하거나 코드 이동에 따른 유지보수 부담이 드러난 파일만 상세 검토했다.
409개 모두를 깊게 재설계하거나 수정한 감사가 아니다. CI 통과 여부는 의존성의 적절성 판정에
사용하지 않았다. 일반적인 단위 테스트의 대상 클래스 import나 내부 상태 관측만으로 결함을 판정하지 않는다.

앞선 255개 Windows 일괄 편입/5개 POSIX matrix 제안은 보류했다. 일괄 patch는
`tmp/regression/broad-proposal-preserved/`에 보존하고 제품 코드와 기존 CI의 실행 순서는 유지했다.
초기 조사 기준선은 등록 149개/미등록 260개다. 2026-10-08 중간 상태에서 검증한 21개와
계좌 UI/API 통합 검사, 전역 자격증명 UI 검사를 반영해 manifest는 172개 등록/237개 미등록이었다.
이는 현재 수치가 아니며, 이후 계약·fixture 6개를 추가 검토한 최종 상태는 아래 후속 반영 결과를 따른다.
이번 감사에서 제품 동작·DB·주문·스키마는 수정하지 않았다.

### 후속 반영 상태

- **함수형 테스트 중 고위험 4개 선택 편입:** `test_article_text`, `test_news_ai`,
  `test_collect_historical_market_context`, `test_publish_historical_market_context_to_nas`를
  기존 core 순서를 바꾸지 않는 격리 profile로 등록했다. 본문·AI 해석 계약 외에 불완전 페이지의
  전체 rollback과 historical context 게시 완료 조건·4 MiB 인접 원본 DB 미복사를 자동 회귀로 보호한다.
  프로젝트 Python에서 4개 worker, 33건 통과; 실패·오류·skip·미실행 0이다. 새 테스트 모듈 누락 검사도 통과했다.
  최종 manifest는 182개 등록/227개 미등록(Windows 222, Linux 5)이다. 변경을 포함한 전체
  `all-local`은 1,901건/42 worker 통과, failure/error/skip/expected failure/unexpected success/미실행 0,
  process tree 종료 42/42, 잔류 자손 0이다. 새 4 MiB 제외 assertion을 포함한 최종 실행 결과는
  `tmp/regression/final-audit-authorized-20261009/run.json`이다.
  제한 실행 환경의 별도 전체 실행은 Windows Proactor `socketpair` 초기화에서 멈춰 incomplete로 기록했으며 성공으로 세지 않았다.
  승인된 사용자 Windows 환경의 최종 결과가 1,901건/42 worker 통과를 확인한다. manifest SHA256은
  `0547f1f132e99401ab57783b30f11ce9ab2d15e768971170a58faacb38462f57`이다. 새 테스트 모듈 누락 검사도
  통과했다. 나머지 7개 전문 역사 데이터·수집 보조 모듈은 용도를 확인했으며 관련 스크립트를 변경할 때
  선택 실행 대상으로 남긴다. 이 변경 뒤 GitHub hosted CI는 아직 실행하지 않았고 NAS 배포/main 병합은 없다.
- **2026-10-09 계약 검토 6개 해결 및 편입:** [원인·기준선 조정·실행 증거](TEST_CONTRACT_RECONCILIATION_20261009.md).
  변경 전 78건의 failure 5/error 1을 재현하고 테스트/fixture/감사 도구만 수정했다. 변경 후
  81건 통과, failure/error/skip/미실행 0, worker tree 종료 6/6, 자손 누수 0이다.
  일봉 invalidation 누락 대조군은 실제 실패했으며 정상 통과 건수에 넣지 않는다.
  legacy 후보 hash를 보존하고 QueryStore 차이와 직접 연결 3곳만 검토해 원장에 반영했다.
  6개만 전용 격리 profile로 편입한 현재 manifest는 178개 등록/231개 미등록(Windows 226, Linux 5)이다.
  이전 226+5+6 및 실패 기록은 편입 전 단계의 상태다. 178개 현재 목록의 all-local은 1,868건/38 worker,
  실패·오류·skip·expected failure·미실행 0, process tree 종료 38/38과 자손 누수 0으로 통과했다.
  새 모듈 coverage 검사도 미등록 0개다. Linux 권한/symlink/shell/fcntl 5개는 별도 Ubuntu job에서
  65/65 통과했다. 첫 시도는 fake Docker 실행 permission fixture 문제로 5건 실패했으나 POSIX 전용
  실행 bit 수정 뒤 최종 run에서 통과했다. hosted run [37805647697](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37805647697),
  commit `fe62396df07477e6d6fad81d5a69a2b3df96caa5`는 Windows 1,868/38 worker와 disposable PostgreSQL
  87건도 통과했다. PostgreSQL live/NAS 운영 검증을 뜻하지 않는다.
- 기존 149개 all-local: 9/9 worker, 1,467건 통과, 실패·오류·skip·미실행 0, 전체 process tree 종료 확인.
- 실행 소유권 검사 `test_mock_account_drain`: 내부 값 변환 함수 위치 patch를 SQLite authorizer 대기로
  교체했다. 원래 14건 중 1건이 gate 미도달로 실패하던 상태에서 14/14 통과하며, BEGIN IMMEDIATE
  누락 결함을 별도 주입했을 때 같은 트랜잭션 assertion이 실패하는 것도 확인했다.
- P1 첫 12개: 현재 소스에서 115/115 통과 후 `all-local`에 편입.
- P1 두 번째 8개: 격리 실행 178/178 통과 후 편입. 각 worker가 skip 없이 종료했다.
- 170개 manifest의 `all-local`: 30/30 worker, 1,774건 통과. 그 후 계좌 UI/API 통합 테스트를 추가하고
  최종 전체 실행에서 31/31 worker, 1,776건이 통과했다. 실패·오류·skip·expected failure·미실행 0,
  process tree 종료 확인 31/31, 잔류 자손 0이다. 최종 산출물은
  `tmp/regression/dependency-audit-ci171-final/run.json`이다. 기존 149개 산출물은
  `tmp/regression/dependency-audit-ci149-after/run.json`이다. PostgreSQL live/NAS 검증은 포함하지 않는다.
- 171개 통합 결과는 기준 HEAD `641a821e45e4a5302fc985eeec6236accabed7cf`의 워크트리에서 실행했다.
  manifest SHA256 `d30785e869fce85bf60a3d8007aa3e67b22ac3ed34bd5e5cc7e69db35a931254`,
  runner SHA256 `659c4c11fd3546dec156b77d5fb24b37245f7ddd48cb8ee8c8f9741b672c93d1`.
- 미등록 260개 중 일반 20개, 실행 소유권 계약 검사 1개, 계좌 UI/API 통합 검사 1개를 등록했다.
  이어 전역 자격증명 UI 통합 검사 1개를 P0 profile로 등록했다. 남은 분류는 일반 226,
  별도 Linux 5, 계약/fixture 검토 6이다. 기존 core batch와 기존 profile은 그대로다.
- 두 번째 묶음에서 직접 확인한 구현 결합은 credential owner와 DB transaction 경계 테스트가 실제
  저장·재검증·rollback 의미를 시험하기 위해 구체 owner/계약을 직접 호출하는 형태였다. 이 의존성은
  검증 대상이므로 유지한다. 임시 경로는 각 테스트가 자체 `TemporaryDirectory`로 만들며 운영 DB 경로를
  참조하지 않는다. `test_market_role_barrier`가 다른 테스트 클래스의 setup을 가져오고 일부 credential
  테스트가 다른 테스트 모듈의 `FakeClient`를 가져오던 결합은 `credential_owner_test_support.py`로
  분리했다. 연관된 8개 테스트 파일에서 120건이 통과했다. `test_nas_credentials_ui_integration`은
  기존 UI 계약대로 연결 해제 profile이 기본 목록에서 숨겨지는지, 표시 옵션을 켜면 연결 해제 상태로
  보이는지 각각 확인하도록 보정했다. 열린 SQLite의 정상 WAL/SHM과 별도 암호화 자격증명 파일도
  구분하며, 입력한 키가 암호문 파일에 평문으로 남지 않는지 검사한다. 단독 2/2와 P0 profile 2/2가
  통과해 171번째 등록 모듈이 됐고 해당 시점의 전체 all-local은 1,776건/31 worker로 통과했다.
  `test_global_credentials_ui`에서 열린 SQLite의 정상 `-wal`/`-shm`을 재현했다. 기존 DB와 secrets
  디렉터리 제한을 유지하며 이 두 sidecar만 허용했다. 해당 모듈의 Response/provider fake import를
  `credential_owner_test_support.py` 또는 모듈 내부 helper로 옮겼다. 단독/P0 11건 통과 후 172번째
  모듈로 등록했다. 변경을 포함한 전체 all-local은 32/32 worker, 1,787건 통과, 실패·오류·skip·
  expected failure·미실행 0으로 끝났다. process tree 종료 32/32, 잔류 자손 0이다. 결과는
  `tmp/regression/dependency-audit-ci172-global-credentials-final/run.json`, manifest SHA256은
  `c4bad473533024fcdc7ae6a6be2db17f1becba6c236e6a06a6b268e3cbc728bd`다. 단독으로 재현한
  `test_dart_credential_owner`는 18건 중 1건이 현재 search의 조회 전용 계약과 맞지 않는 fixture 기대에서
  실패해 미등록 유지한다. 테스트는 CI 성공으로 계산하지 않았고, 수정은 보류 원장에 남겼다.
  `test_scoped_account_api`는 기존 Starlette TestClient 경로에서 통과했으며 httpx2 경고만 남았다;
  저장소 지침에 따라 선택 의존성은 추가하지 않는다.

## 149개와 260개의 조사 결과

아래 숫자는 자동 수집한 **검토 신호가 있는 파일 수**다. 수정 대상 수가 아니다.
예를 들어 파일 읽기는 출력 JSON 확인도 포함하며, private 호출은 해당 클래스 자체를 검증하는
정당한 단위 테스트도 포함한다. 중복 집계가 있어 열의 합계를 파일 수로 사용하지 않는다.

| 검토 신호 | 기존 CI 149개 | 미등록 260개 |
|---|---:|---:|
| 다른 test 모듈의 fixture/helper import | 3 | 68 |
| 다른 TestCase를 생성해 setUp 재사용 | 0 | 24 |
| patch/patch.object 사용 | 34 | 132 |
| private 메서드 호출 | 35 | 94 |
| 소스 또는 출력 파일 읽기 | 9 | 68 |
| 디렉터리 파일 목록 검사 | 1 | 28 |
| 함수 closure 직접 조사/변경 | 1 | 0 |
| 특정 사용자 C:/X: 절대 경로 하드코딩 신호 | 0 | 0 |
| unittest에서 발견되지 않는 최상위 test 함수 | 0 | 11 |

### 기존 149개

**2개 파일을 개선 후보로 선정했다. 149개를 모두 유지하며 테스트 삭제·검증 범위 축소를 하지 않는다.**

| 파일/검사 | 확인한 유지보수 부담 | 개선 방향 | 우선순위 |
|---|---|---|---|
| [test_central_server_database.py:1015](../tests/unit/test_central_server_database.py#L1015), [1924](../tests/unit/test_central_server_database.py#L1924) | revision 실패/metadata 중단 검사가 `database_market_bars`의 private helper 위치를 patch해 DB 구현을 옮길 때 함께 수정해야 했음 | **완료:** SQLite는 임시 DB trigger로 실제 revision insert 뒤 실패시켜 bar/revision/metadata rollback을 확인한다. PostgreSQL fake cursor는 metadata UPSERT SQL/parameters와 commit/close를 직접 확인한다. 검사 57건 통과, 기존 test ID 유지. |
| [test_central_server_app.py:649](../tests/unit/test_central_server_app.py#L649) | 요청 중 fallback gateway 비활성화를 주입하려 closure의 freevar 이름/셀을 조사 | **유지 결정:** 이 검사는 await 중 계좌·gateway 전환이 발생해도 시작 시 선택한 gateway로 응답을 완성하는 동시성 계약이다. 현재 public test seam으로 같은 시점을 재현할 수 없고, 테스트 전용 생산 setter는 경계를 넓힌다. closure/route 구조를 옮길 때 이 한 검사를 다시 대조한다 | P2, 계좌 경계 수정 시 |

테스트 간 결합은 기존 149개에서도 별도 확인했다. `test_central_server_app` → 미등록
`test_daily_bar_coverage.window_with`, `test_entry_thesis` → `test_theme_leadership`의 고정 데이터,
`test_news_jobs` → `test_news_observation_history._article` 세 경로다. 모두 순수 helper 재사용으로
이번 실행에서 초기 상태 오염은 확인되지 않았다. 즉시 3개를 모두 재작성하지 않는다. 다음에 해당
fixture를 바꿀 때 공유 함수만 작은 `*_test_support.py`로 옮길지 검토한다.

그 밖의 147개 파일은 이번 조사에서 즉시 수정할 근거를 찾지 못했다. 내부 동시성·queue·worker
수명을 검증하는 patch, 호출별 연결/트랜잭션 검사를 위한 connection 접근, 앱 UI 수명 관측은
검증 대상과 관련되어 유지한다. 이는 147개가 영구적으로 구조 문제가 없다는 보장이 아니다.

### 미등록 260개 분류

파일별 용도와 본문 근거는 [260개 보존 근거](REGRESSION_COVERAGE_AUDIT.md)에 있다.
다음 분류는 파일별 주 분류이며 우선순위와 별개다. 후보라는 말은 즉시 PR CI에 전부 넣는다는 뜻이 아니다.

| 주 분류 | 파일 수 | 처리 |
|---|---:|---|
| 현재 기능/저장 결과를 보호하는 일반 CI 편입 후보 | 246 | 먼저 아래 12개, 그 다음 8개만 평가. 나머지는 변경 기능별 수동/정기 프로필 후보로 보존 |
| POSIX 권한·symlink·shell/fcntl 때문에 별도 환경 필요 | 5 | Windows에서 skip을 성공으로 세지 않고 Linux 격리 검증. 운영 NAS 실행과 분리 |
| 현재 계약·fixture·감사 기준선과 불일치하여 편입 전 검토 필요 | 9 | 기대값 일괄 교체 금지. 아래 원인과 동일한 재현 조건으로 처리 |
| 다른 파일이 모든 assertion을 완전히 대체한다고 확정된 중복 | 0 | 주제가 겹친다는 이유로 제외/삭제하지 않음 |
| 테스트의 용도 자체를 확정하지 못함 | 0 | 용도는 식별됨. 해시 호환성 등 해결 방법의 미확정은 별도로 기록 |
| 합계 | 260 | 일괄 등록하지 않음 |

11개 파일의 최상위 함수 45개는 unittest TestCase 메서드로 옮겨 발견 경로를 복구했다.
tmp_path가 필요했던 테스트는 테스트 메서드마다 TemporaryDirectory를 만들고 정리한다.
assertion·patch·검증 입력은 바꾸지 않았으며, 프로젝트 Python으로 11개 모듈을 함께 실행해
45/45 통과했다. 이 11개 파일은 여전히 미등록이므로 현재 all-local이나 hosted CI 건수에는
포함되지 않는다. CI 편입 여부는 위험도와 실행 비용을 검토한 뒤 별도로 정한다.

## 실제 실패와 상세 검토 대상

| 미등록 파일 | 재현/확인한 원인 | 필요한 조치와 경계 |
|---|---|---|
| `test_mock_account_drain` | root `database._execution_intent_values` patch는 leaf 실행의 실제 helper를 가로채지 못해 gate에 도달하지 않았다 | **완료:** connection 경계의 SQLite authorizer에서 INSERT 준비를 gate하고 `in_transaction`, 경쟁 owner의 선점 불가, 이후 저장 결과를 확인한다. 14건 통과. BEGIN IMMEDIATE를 제거한 대조군은 transaction assertion 실패. 현재 CI에 P0로 편입했다. |
| `test_global_credentials_ui` | 열린 SQLite의 정상 `-wal/-shm` 파일을 root 파일 목록의 unexpected 파일로 판정해 격리 실행 실패 | **완료:** 기존 root 파일 제한에 SQLite `-wal/-shm`만 추가 허용. Response/provider fake import를 제거하고 단독/P0 11건 통과 후 등록 |
| `test_nas_credentials_ui_integration` | 테스트가 기본 목록에서 숨겨지는 profile의 선택 상태를 검사했고, 열린 SQLite의 정상 WAL도 unexpected root 파일로 오인 | **완료:** 선택 여부 대신 기본 목록 숨김과 표시 옵션의 disabled label을 분리 검증. API의 active profile 해제·주문 OFF·apply 요청 수, 암호화 자격증명 파일과 평문 비노출 검사는 유지 |
| `test_daily_bar_coverage` | 과거 fake가 `as_of` keyword를 받지 않아 계산 중 입력 변경 경로에 도달하지 않음 | **완료·P0 편입:** 종목/날짜 호출 검증을 보강하고 retry-open·결과 미저장·readiness assertion 유지; 22건 통과 |
| `test_dart_credential_owner` | search는 저장 조회 전용이고 고정 기사 날짜가 수집 기간 밖이었음 | **완료·P0 편입:** 고정 시계, TOP20 membership, refresh→저장→search를 검증; 조회로 공급자 호출/watchlist/저장 변경이 없는 계약 포함; 18건 통과 |
| `test_diagnostic_trace_api` | 기대 options/event_types가 현재 API 계약과 달랐음 | **완료·P0 편입:** 전체 capability를 확인하고 인증·schema·미검증 coverage 표시 유지; 4건 통과 |
| `test_audit_postgres_access` | 승인 기준선 50곳에서 실제 격리 fixture 연결 3곳이 추가됨 | **완료·P0 편입:** 세 호출 owner와 disposable DB/container, connection 종료를 검토해 정확히 승인; 5건 통과 |
| `test_audit_query_store_consumers` | owned_to_thread wrapper import를 놓쳐 dispatch 추적 손실, 검토가 필요한 호출/signature 차이 | **완료·P0 편입:** wrapper alias/shadowing 인식을 보완하고 8개 계약 차이를 검토해 제한 반영; 4건 통과 |
| `test_research_final_preparation` | 지연 개장 세 필드 때문에 legacy/current 후보 식별자가 달라짐 | **완료·P0 편입:** 기존/현재 정적 hash fixture와 양쪽 reader 계약, 비정규 입력 거부를 검사; 28건 통과 |

9개를 모두 '제품 버그' 또는 '잘못된 테스트'로 단정하지 않는다. 특히 golden hash와 public
DB 반환 계약의 차이는 이전 저장 자료·소비자 의미를 검토해야 한다. 테스트를 통과시키기 위한
제품 기능/기대 결과 변경은 이번 단계에서 하지 않았다.

## 유지할 의존성과 fixture 개선 범위

- 임시 DB 경로는 테스트가 소유하고 공유 실제 data/NAS DB를 사용하지 않는다. 단순 임시 파일명은
  불필요한 구현 의존성으로 보지 않는다. 저장 결과를 검증하는 SQL/table/key, rollback 후 원본
  행/revision/metadata 상태는 검증 대상이므로 유지한다.
- DB 구조 감사(`test_query_store_source`, 두 DB audit 파일, storage ledger)는 물리적 owner와
  연결 경계가 검증 대상이다. 코드 이동 시 의도적으로 review_required가 되는 것을 없애지 않는다.
  기능 회귀의 성공과 구조 감사의 review_required는 따로 보고한다.
- 조사 당시 24개 미등록 파일이 다른 TestCase의 setUp을 가져다 썼다. 주로 research pipeline의 고정 데이터
  작성과 검증 fixture chain이다. 이것만으로 모두 재작성하지 않는다. 실제 결합이 확인된
  `test_research_final_preparation`은 자체 임시 경로와 입력 fixture를 소유하도록 바꿨다. 나머지
  cross-TestCase 연결은 해당 테스트별 실제 유지보수 부담이 확인될 때만 검토하며, runtime owner·
  트랜잭션은 fixture abstraction 뒤에 숨기지 않는다.
- API/기능 테스트는 요청·상태·JSON·저장 결과로 보호하고, 필요한 source architecture 검사는 별도
  성격으로 유지한다. unit이 검증하는 클래스 자체의 import 이름까지 없애기 위한 wrapper는 만들지 않는다.

## 단계적 CI 편입 우선순위와 미편입 검증

1. **P0:** 기존 149개 유지. 실행 소유권 및 자격증명 UI 계약 검사를 원인 수정과 검증 후 CI에 추가했다. 남은 계약/fixture 6개와 11개 함수형 발견 경로를 검토한다.
   해결이 불필요한 모듈을 수정하지 않는다. source-runtime shell의 executable fixture도 Linux에서만 확인한다.
2. **P1 첫 묶음 12개:** 계좌 신원·종료 경계, 봉 저장/revision/동시성, outbox, 체결 대사,
   뉴스 운영 설정 경계를 우선했다. 현재 소스로 115건이 통과한 뒤 manifest에 추가했고,
   확장된 기존 전체 `all-local` 1,596건도 통과했다. hosted CI 시간 보장은 별도다.
3. **P1 두 번째 묶음 8개:** credential runtime/provider 교체와 PostgreSQL 계측, scoped API·UI.
   첫 묶음 결과와 실행 시간을 보고 진행한다.
4. **별도 환경 5개:** Linux 권한/symlink/shell/fcntl suite. PostgreSQL unit fake를 실제 PostgreSQL
   통과로 표시하지 않으며 기존 disposable PostgreSQL job 범위를 유지한다.
5. **다음 6개 뒤 남는 일반 후보 226개:** 아직 편입하지 않는다. 보호 동작은 유효하지만 첫 20개의 핵심 공백을
   우선 채우므로 현재 작업 대상/실행 비용에 따라 후속 선택한다. 관련 기능 변경 때 모듈별 Windows
   격리 실행 또는 해당 script용 on-demand/정기 profile로 검증한다. 파일별 현재 미편입 이유와 방법은
   아래 행의 분류/우선순위에 연결된다. 실행하지 않은 모듈은 통과 건수에 포함하지 않는다.

P1 첫 묶음: `test_account_runtime_settings`, `test_credential_barrier`, `test_mock_runtime_barrier`, `test_real_reconnect_barrier`, `test_selected_account_api_integration`, `test_journal_fill_reconciliation`, `test_central_market_ingest`, `test_central_minute_bars`, `test_daily_bar_repository`, `test_storage_boundary_concurrency`, `test_persistent_outbox`, `test_news_query_operations`.

P1 두 번째 묶음: `test_credential_runtime`, `test_market_role_barrier`, `test_mock_credential_owner`, `test_real_credential_owner`, `test_naver_credential_owner`, `test_postgres_access`, `test_scoped_account_api`, `test_nas_credentials_dialog`.

## 변경 전후 비교와 실제 검증 한계

기존 기준선은 등록 고유 파일 149개, 의도적으로 중복된 core 143회 호출/142개 고유 모듈과
HTTP/ASGI news 응답 기준선을 그대로 유지한다. 행/값/ID·revision·완료/복구 의미를 바꿔 테스트를
녹색으로 만들지 않는다. baseline/hash fixture를 덮어쓰기 전에 새 결과를 별도 보존한다.

앞선 일괄 편입 제안안 전수 실행은 Windows 404개 고유 파일(149+255), 264개 worker, 3,612건이었다.
8개 모듈에서 failure 7개/error 2개, skip/expected failure/미실행 0개였다. 모든 process tree
종료가 확인됐고 잔류 자손 0이었다. 실패 8개를 다시 단독 실행했으며 모두 동일하게 실패했다.
이 결과는 11개 함수형 변환과 trace 기대값 보정을 포함한 **보존된 제안안의 결과**로,
현재 원본/HEAD 409개가 통과했다는 뜻이 아니다. 원본 11개는 unittest로 0건 발견된다.
Linux 5개는 미실행이며 실제 PostgreSQL/NAS live 검증도 수행하지 않았다.

로컬 증거: `tmp/regression/coverage-all-local-final/run.json`(source/test SHA256 포함),
`tmp/regression/recheck-8/summary.json`, `tmp/regression/test-dependency-inventory.json`,
`tmp/regression/query-consumer-after.json`, 보존 제안 patch. 이 파일들은 진단 산출물로 커밋하지 않는다.
NAS 운영 배포, main 병합, 새 CI 확대안 push는 수행하지 않았다.

## 409개 파일별 조사 원장

TS=다른 test 모듈 fixture import 수, TF=TestCase fixture 생성 수, P=patch 수,
I=private 호출 수, R=소스/출력 파일 읽기 수, D=디렉터리 나열 수다. 숫자는 필요/불필요 판정이 아니다.
기존 등록 파일의 검증 방법은 현재 CI 유지, 일반 후보는 선택 편입 전 모듈 단독 격리 실행,
계약 검토는 원인별 재현 후 같은 assertion 대조, Linux는 격리 OS suite다.

| 파일 (`tests/unit/`) | 현행 CI | 판정 / 현재 미편입 이유와 검증 방법 | TS/TF/P/I/R/D |
|---|---|---|---|
| [test_account_identity.py](../tests/unit/test_account_identity.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/1/0 |
| [test_account_query.py](../tests/unit/test_account_query.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_account_runtime_settings.py](../tests/unit/test_account_runtime_settings.py) | 기준선 미등록 → P1-1 등록 | 단독 115건·all-local 통합 통과 후 manifest 추가 | 0/0/0/6/0/0 |
| [test_ai_credential_owner.py](../tests/unit/test_ai_credential_owner.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/5/0/0/0 |
| [test_analyze_db_trace.py](../tests/unit/test_analyze_db_trace.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_api_settings_dialog.py](../tests/unit/test_api_settings_dialog.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/3/7/0/0 |
| [test_app_controller.py](../tests/unit/test_app_controller.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/40/12/0/0 |
| [test_article_text.py](../tests/unit/test_article_text.py) | 기준선 미등록 → 뉴스 계약 profile 등록 | 18건 발견 복구·단독 및 격리 통과; 본문 추출·원문 시각·본문 거부 계약 | 0/0/0/0/0/0 |
| [test_audit_historical_five_minute_clock.py](../tests/unit/test_audit_historical_five_minute_clock.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_audit_historical_monthly_gap_causes.py](../tests/unit/test_audit_historical_monthly_gap_causes.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_audit_historical_monthly_gap_raw.py](../tests/unit/test_audit_historical_monthly_gap_raw.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_audit_historical_nas_minute_alignment.py](../tests/unit/test_audit_historical_nas_minute_alignment.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_audit_kiwoom_adjusted_minute_overlap.py](../tests/unit/test_audit_kiwoom_adjusted_minute_overlap.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_audit_kiwoom_adjustment_candidates.py](../tests/unit/test_audit_kiwoom_adjustment_candidates.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_audit_postgres_access.py](../tests/unit/test_audit_postgres_access.py) | 기준선 미등록 → P0 계약/fixture 등록 | 격리 연결 3곳 owner 검토 후 5건 통과, all-local 포함 | 0/0/0/0/0/0 |
| [test_audit_prepared_historical_archive_readiness.py](../tests/unit/test_audit_prepared_historical_archive_readiness.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/2/0 |
| [test_audit_prepared_historical_body_provenance.py](../tests/unit/test_audit_prepared_historical_body_provenance.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_audit_query_store_consumers.py](../tests/unit/test_audit_query_store_consumers.py) | 기준선 미등록 → P0 계약/fixture 등록 | dispatcher alias/shadowing와 기준선 차이 검증; 4건 통과, all-local 포함 | 0/0/1/0/3/0 |
| [test_autonomous_top20.py](../tests/unit/test_autonomous_top20.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/52/101/3/0 |
| [test_auxiliary_window_geometry.py](../tests/unit/test_auxiliary_window_geometry.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/3/6/0/0 |
| [test_breakout_strategy.py](../tests/unit/test_breakout_strategy.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_build_historical_exchange_case_context.py](../tests/unit/test_build_historical_exchange_case_context.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_build_prepared_historical_search_projection.py](../tests/unit/test_build_prepared_historical_search_projection.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/0/0/2/0/0 |
| [test_candidate_alerts.py](../tests/unit/test_candidate_alerts.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/5/3/0/0 |
| [test_candidate_daily_nas_scripts.py](../tests/unit/test_candidate_daily_nas_scripts.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/7/5/2/1 |
| [test_candidate_exchange_effective_dates.py](../tests/unit/test_candidate_exchange_effective_dates.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_candidate_monitor.py](../tests/unit/test_candidate_monitor.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_catalog_capture_profile.py](../tests/unit/test_catalog_capture_profile.py) | 기준 미등록 → P2 등록 | P2 NAS/capture profile 편입; catalog budget·native 결과·durable 복원 10건 | 1/0/3/4/0/0 |
| [test_causal_capture_api.py](../tests/unit/test_causal_capture_api.py) | 기준 미등록 → P2 등록 | P2 diagnostic controls 편입; HTTP의 store/collector/TOP20 flags·deadline이 동일 recorder에 정확히 전달 1건; 실패 시 client cleanup 보강 | 0/0/2/0/0/0 |
| [test_causal_capture_capacity.py](../tests/unit/test_causal_capture_capacity.py) | 기준 미등록 → P2 등록 | P2 diagnostic controls 편입; 정상 memory/event 포화와 잘못된 입력 구분·peak/headroom·측정 불가 유지 3건 | 0/0/0/0/0/0 |
| [test_central_account_query.py](../tests/unit/test_central_account_query.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_central_ai_client.py](../tests/unit/test_central_ai_client.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_central_ai_service.py](../tests/unit/test_central_ai_service.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/5/0/0/0 |
| [test_central_content_client.py](../tests/unit/test_central_content_client.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_central_content_sync.py](../tests/unit/test_central_content_sync.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/4/7/0/0 |
| [test_central_credentials_client.py](../tests/unit/test_central_credentials_client.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/1/0/0/0 |
| [test_central_database_codec.py](../tests/unit/test_central_database_codec.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_central_deployment_check.py](../tests/unit/test_central_deployment_check.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_central_journal_sync.py](../tests/unit/test_central_journal_sync.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/2/0/0 |
| [test_central_market_ingest.py](../tests/unit/test_central_market_ingest.py) | 기준선 미등록 → P1-1 등록 | 단독 115건·all-local 통합 통과 후 manifest 추가 | 0/0/0/1/0/0 |
| [test_central_minute_bars.py](../tests/unit/test_central_minute_bars.py) | 기준선 미등록 → P1-1 등록 | 단독 115건·all-local 통합 통과 후 manifest 추가 | 0/0/0/0/0/0 |
| [test_central_news_client.py](../tests/unit/test_central_news_client.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_central_news_service.py](../tests/unit/test_central_news_service.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/1/0/0 |
| [test_central_operational_settings.py](../tests/unit/test_central_operational_settings.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/2/0/0/0 |
| [test_central_realtime_collector.py](../tests/unit/test_central_realtime_collector.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/1/52/0/0 |
| [test_central_realtime_hub.py](../tests/unit/test_central_realtime_hub.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_central_realtime_worker.py](../tests/unit/test_central_realtime_worker.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/3/15/0/0 |
| [test_central_resource_usage.py](../tests/unit/test_central_resource_usage.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_central_rest_broker.py](../tests/unit/test_central_rest_broker.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/3/3/0/0 |
| [test_central_schema.py](../tests/unit/test_central_schema.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_central_schema_migrations.py](../tests/unit/test_central_schema_migrations.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_central_server_app.py](../tests/unit/test_central_server_app.py) | 149 등록 | 유지; closure 검사는 의도된 동시성 주입으로 상세 검토 후 유지 결정 | 1/0/15/0/0/0 |
| [test_central_server_config.py](../tests/unit/test_central_server_config.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/3/0 |
| [test_central_server_database.py](../tests/unit/test_central_server_database.py) | 기준선 149 등록 | 유지; DB 실패 주입 위치 의존 개선 완료 | 0/0/23/9/0/0 |
| [test_central_server_db_api_connection.py](../tests/unit/test_central_server_db_api_connection.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/1/0/0/0 |
| [test_central_server_logging.py](../tests/unit/test_central_server_logging.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/3/1/0 |
| [test_central_server_process.py](../tests/unit/test_central_server_process.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/10/4/0/0 |
| [test_central_settings_sync.py](../tests/unit/test_central_settings_sync.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_central_sync_utils.py](../tests/unit/test_central_sync_utils.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_central_theme_sync.py](../tests/unit/test_central_theme_sync.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/1/0/0 |
| [test_check_postgres_integration.py](../tests/unit/test_check_postgres_integration.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_classify_historical_stock_adjustments.py](../tests/unit/test_classify_historical_stock_adjustments.py) | 미등록 유지 | 전문 연구 분류 스크립트의 DART 사건 판정·후보 작업 생성 검사; 해당 workflow 변경 때 선택 실행 | 0/0/0/0/0/0 |
| [test_collect_candidate_event_disclosures.py](../tests/unit/test_collect_candidate_event_disclosures.py) | 미등록 유지 | 과거 사건 후보 정리 및 검증 발행인 이름의 연구 보조 흐름; workflow 변경 때 선택 실행 | 0/0/0/0/0/0 |
| [test_collect_candidate_exchange_disclosures.py](../tests/unit/test_collect_candidate_exchange_disclosures.py) | 미등록 유지 | 거래소 공시 1개 수집 스크립트의 receipt date/raw snapshot 검사; 스크립트 변경 때 선택 실행 | 0/0/2/0/1/0 |
| [test_collect_historical_market_context.py](../tests/unit/test_collect_historical_market_context.py) | 기준선 미등록 → 역사 데이터 계약 profile 등록 | 7건 발견 복구·격리 통과; candidate 범위와 불완전 페이지 전체 rollback 보존 | 0/0/0/0/0/0 |
| [test_column_settings_repository.py](../tests/unit/test_column_settings_repository.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_condition_runtime.py](../tests/unit/test_condition_runtime.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 2/0/7/7/0/0 |
| [test_context_candidates.py](../tests/unit/test_context_candidates.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/7/0/0 |
| [test_credential_barrier.py](../tests/unit/test_credential_barrier.py) | 기준선 미등록 → P1-1 등록 | 단독 115건·all-local 통합 통과 후 manifest 추가 | 0/0/1/2/0/0 |
| [test_credential_runtime.py](../tests/unit/test_credential_runtime.py) | 기준선 미등록 → P1-2 등록 | 단독 22건·all-local 통합 통과; 자체 임시 DB/secret directory, runtime revision·commit/recovery 계약 | 0/0/3/0/0/0 |
| [test_credential_store.py](../tests/unit/test_credential_store.py) | 미등록 | 별도 환경; Windows skip 방지, Linux 격리 실행 전 보류 | 0/0/3/6/4/1 |
| [test_daily_bar_coverage.py](../tests/unit/test_daily_bar_coverage.py) | 기준선 미등록 → P0 계약/fixture 등록 | 계산 중 입력 변경·재시도·결과 미저장 검증; 22건 통과, all-local 포함 | 0/0/2/48/0/0 |
| [test_daily_bar_repository.py](../tests/unit/test_daily_bar_repository.py) | 기준선 미등록 → P1-1 등록 | 단독 115건·all-local 통합 통과 후 manifest 추가 | 0/0/0/0/0/0 |
| [test_daily_high_service.py](../tests/unit/test_daily_high_service.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_daily_high_worker_controller.py](../tests/unit/test_daily_high_worker_controller.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_daishin_candidate_collection.py](../tests/unit/test_daishin_candidate_collection.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/3/12/0/0 |
| [test_dart_credential_owner.py](../tests/unit/test_dart_credential_owner.py) | 기준선 미등록 → P0 계약/fixture 등록 | fixed clock과 저장 전용 search 경로; 18건 통과, all-local 포함 | 1/0/11/4/2/0 |
| [test_dart_disclosure_filter.py](../tests/unit/test_dart_disclosure_filter.py) | 미등록 유지 | 단일 DART 필터 옵션 검사; 직접 관련 기능 변경 시 선택 실행 | 0/0/0/0/0/0 |
| [test_detached_chart_window.py](../tests/unit/test_detached_chart_window.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/3/2/0/0 |
| [test_diagnostic_cli_controls.py](../tests/unit/test_diagnostic_cli_controls.py) | 미등록 | 별도 환경; Windows skip 방지, Linux 격리 실행 전 보류 | 0/0/30/58/0/0 |
| [test_diagnostic_collector_replay.py](../tests/unit/test_diagnostic_collector_replay.py) | 기준 미등록 → P2 등록 | P2 causal lifecycle 편입; 실제 parser·checkpoint·취소 후 저장 drain·연속성·운영 URL 차단 6건 | 0/0/2/0/0/0 |
| [test_diagnostic_delivery_record.py](../tests/unit/test_diagnostic_delivery_record.py) | 기준 미등록 → P2 등록 | P2 causal lifecycle 편입; durable delivery·공유 retention·실패 suffix·실제 writer 종료 7건 | 2/0/7/3/0/0 |
| [test_diagnostic_flush_metrics.py](../tests/unit/test_diagnostic_flush_metrics.py) | 기준 미등록 → P2 등록 | P2 trace persistence 편입; commit window와 wait 표본 정렬·불완전 probe·성공 cycle 집계·thread 간 flush ID 6건 | 0/0/3/0/0/0 |
| [test_diagnostic_replay.py](../tests/unit/test_diagnostic_replay.py) | 기준 미등록 → P2 등록 | P2 replay admission 편입; 입력 shape·운영 DB 차단·lane 순서/완료/누락 방지 11건 | 0/0/12/0/0/0 |
| [test_diagnostic_replay_database_cli.py](../tests/unit/test_diagnostic_replay_database_cli.py) | 기준 미등록 → P2 등록 | P2 NAS/capture profile 편입; DB 접근 전 입력 검증·lease/seal/restore·redaction 11건 | 0/0/17/0/0/0 |
| [test_diagnostic_rest_input.py](../tests/unit/test_diagnostic_rest_input.py) | 기준 미등록 → P2 등록 | P2 causal lifecycle 편입; 논리/transport/cache/ingest 인과 입력·소유권·tape 오류 차단 14건 | 0/0/11/4/0/0 |
| [test_diagnostic_runs.py](../tests/unit/test_diagnostic_runs.py) | 기준 미등록 → P2 등록 | P2 diagnostic controls 편입; 인증·revision/session·run 소유/취소·전용 DB·보고서 완료·shape 불일치 거부 11건; main의 8GiB/5M 계약 기대 갱신 | 1/0/21/1/0/0 |
| [test_diagnostic_sampling_api.py](../tests/unit/test_diagnostic_sampling_api.py) | 기준 미등록 → P2 등록 | P2 diagnostic controls 편입; device elapsed/rate·고정 read-only SQL·민감 query 제거·news table/index scope 3건 | 0/0/1/0/0/0 |
| [test_diagnostic_top20_flow_input.py](../tests/unit/test_diagnostic_top20_flow_input.py) | 기준 미등록 → P2 등록 | P2 TOP20 input contracts 편입; 실제 SQLite 수급 저장·완료 marker·baseline·취소 drain 12건; assertion 실패 시 task 회수 2곳 보강 | 0/0/19/7/0/0 |
| [test_diagnostic_top20_input.py](../tests/unit/test_diagnostic_top20_input.py) | 기준 미등록 → P2 등록 | P2 TOP20 input contracts 편입; 순위 freshness·20 slots·retry/error tape·OFF/ON 결과 동일·취소 incomplete 8건 | 0/0/13/0/0/0 |
| [test_diagnostic_trace.py](../tests/unit/test_diagnostic_trace.py) | 기준 미등록 → P2 등록 | P2 trace persistence 편입; 실제 chunk 순서·checksum·overflow·65분 envelope의 bounded burst·run lock·중단 복구 7건 | 0/0/17/2/4/0 |
| [test_diagnostic_trace_api.py](../tests/unit/test_diagnostic_trace_api.py) | 기준선 미등록 → P0 계약/fixture 등록 | capability/auth 응답 계약; 4건 통과, all-local 포함 | 0/0/3/1/4/0 |
| [test_diagnostic_trace_batches.py](../tests/unit/test_diagnostic_trace_batches.py) | 기준 미등록 → P2 등록 | P2 trace persistence 편입; payload fsync·chunk publication·최종 manifest 완료·실패·stop 중 도착 보존 8건; fixture writer 종료 확인 | 0/0/11/7/4/2 |
| [test_diagnostic_trace_deferred.py](../tests/unit/test_diagnostic_trace_deferred.py) | 기준 미등록 → P2 등록 | P2 trace persistence 편입; 33,001행 RAM→디스크 순서·deadline·중단·메모리/event 한도·host/container headroom 9건; fixture writer 종료 확인 | 0/0/8/6/1/2 |
| [test_diagnostic_workloads.py](../tests/unit/test_diagnostic_workloads.py) | 기준 미등록 → P2 등록 | P2 trace persistence 편입; master/child lease·독립 만료·pause 복구·capture off 초기화·측정 불가와 0 구분 9건 | 0/0/1/7/0/0 |
| [test_entry_snapshot_writer.py](../tests/unit/test_entry_snapshot_writer.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_entry_thesis.py](../tests/unit/test_entry_thesis.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 1/0/0/0/0/0 |
| [test_exchange_effective_dates.py](../tests/unit/test_exchange_effective_dates.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_execution_activation.py](../tests/unit/test_execution_activation.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_execution_repository.py](../tests/unit/test_execution_repository.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_export_historical_news_seed.py](../tests/unit/test_export_historical_news_seed.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_export_news_classification_corpus.py](../tests/unit/test_export_news_classification_corpus.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_external_market_collector.py](../tests/unit/test_external_market_collector.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_external_market_runtime.py](../tests/unit/test_external_market_runtime.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/0/12/8/0/0 |
| [test_failover_kiwoom_client.py](../tests/unit/test_failover_kiwoom_client.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_feedback_evidence.py](../tests/unit/test_feedback_evidence.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_finalize_prepared_historical_article_bodies.py](../tests/unit/test_finalize_prepared_historical_article_bodies.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/1/0 |
| [test_finalize_prepared_historical_assessments.py](../tests/unit/test_finalize_prepared_historical_assessments.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/0/0/0/0/0 |
| [test_finalize_prepared_historical_news_events.py](../tests/unit/test_finalize_prepared_historical_news_events.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/0/0/0/0/0 |
| [test_forward_report.py](../tests/unit/test_forward_report.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_fundamentals_worker_controller.py](../tests/unit/test_fundamentals_worker_controller.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_generic_strategy_evaluator.py](../tests/unit/test_generic_strategy_evaluator.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_global_credentials_ui.py](../tests/unit/test_global_credentials_ui.py) | 기준선 미등록 → P0 등록 | 단독 11/11·P0 profile 11/11 통과. SQLite `-wal/-shm`만 허용하며 DB/secrets 외 root 산출물 금지는 유지 | 4/0/11/9/0/1 |
| [test_google_drive_sync.py](../tests/unit/test_google_drive_sync.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/70/1/1/0 |
| [test_google_drive_worker_controller.py](../tests/unit/test_google_drive_worker_controller.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_high_price_policy.py](../tests/unit/test_high_price_policy.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_historical_backfill.py](../tests/unit/test_historical_backfill.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_historical_baseline_requests.py](../tests/unit/test_historical_baseline_requests.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/2/0/1/0 |
| [test_historical_collection_counts.py](../tests/unit/test_historical_collection_counts.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_historical_collection_monitor.py](../tests/unit/test_historical_collection_monitor.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/10/8/0/0 |
| [test_historical_high_service.py](../tests/unit/test_historical_high_service.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_historical_high_worker_controller.py](../tests/unit/test_historical_high_worker_controller.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_historical_learning_cases.py](../tests/unit/test_historical_learning_cases.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_historical_monthly_selection_coverage.py](../tests/unit/test_historical_monthly_selection_coverage.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_historical_news_archive_api.py](../tests/unit/test_historical_news_archive_api.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/2/0/3/2/0 |
| [test_historical_news_archive_dialog.py](../tests/unit/test_historical_news_archive_dialog.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/2/5/0/0 |
| [test_historical_news_archive_reader.py](../tests/unit/test_historical_news_archive_reader.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/0/0/7/2/0 |
| [test_historical_news_blind_validation.py](../tests/unit/test_historical_news_blind_validation.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_historical_news_collector_heartbeat.py](../tests/unit/test_historical_news_collector_heartbeat.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/1/1 |
| [test_historical_news_concurrent_preparation.py](../tests/unit/test_historical_news_concurrent_preparation.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/9/0/0/0 |
| [test_historical_news_deferred_pipeline.py](../tests/unit/test_historical_news_deferred_pipeline.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/10/0/0/0 |
| [test_historical_news_development_inputs.py](../tests/unit/test_historical_news_development_inputs.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_historical_news_event_split.py](../tests/unit/test_historical_news_event_split.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/1/0 |
| [test_historical_news_method_evaluation.py](../tests/unit/test_historical_news_method_evaluation.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_historical_news_parallel_fetch.py](../tests/unit/test_historical_news_parallel_fetch.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/3/0/1/0 |
| [test_historical_news_pc_jobs.py](../tests/unit/test_historical_news_pc_jobs.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/0/11/0/0/0 |
| [test_historical_news_purge_reset.py](../tests/unit/test_historical_news_purge_reset.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_historical_news_review_decisions.py](../tests/unit/test_historical_news_review_decisions.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_historical_news_review_dialog.py](../tests/unit/test_historical_news_review_dialog.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/4/4/1/3 |
| [test_historical_news_review_queue.py](../tests/unit/test_historical_news_review_queue.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_historical_news_timing.py](../tests/unit/test_historical_news_timing.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/7/0/0/0 |
| [test_historical_reconstruction.py](../tests/unit/test_historical_reconstruction.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_historical_research_readiness.py](../tests/unit/test_historical_research_readiness.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_historical_research_split.py](../tests/unit/test_historical_research_split.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/1/0 |
| [test_image_theme_ocr_worker_controller.py](../tests/unit/test_image_theme_ocr_worker_controller.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_import_historical_market_news_to_nas.py](../tests/unit/test_import_historical_market_news_to_nas.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_import_krx_vi_history.py](../tests/unit/test_import_krx_vi_history.py) | 미등록 유지 | 과거 KRX VI 파일 가져오기 전용 dedup·시각 보존 검사; import workflow 변경 때 선택 실행 | 0/0/0/0/0/0 |
| [test_inspect_research_operation_receipts.py](../tests/unit/test_inspect_research_operation_receipts.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/1/0 |
| [test_investor_flow_service.py](../tests/unit/test_investor_flow_service.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_journal_backup.py](../tests/unit/test_journal_backup.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/1/0/2/1 |
| [test_journal_chart_style.py](../tests/unit/test_journal_chart_style.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/3/0/0 |
| [test_journal_database.py](../tests/unit/test_journal_database.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/2/0/0/0 |
| [test_journal_detached_flow.py](../tests/unit/test_journal_detached_flow.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/2/30/0/0 |
| [test_journal_enrichment.py](../tests/unit/test_journal_enrichment.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_journal_execution_projection.py](../tests/unit/test_journal_execution_projection.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_journal_fill_reconciliation.py](../tests/unit/test_journal_fill_reconciliation.py) | 기준선 미등록 → P1-1 등록 | 단독 115건·all-local 통합 통과 후 manifest 추가 | 0/0/0/0/0/0 |
| [test_journal_research_links.py](../tests/unit/test_journal_research_links.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_journal_schema.py](../tests/unit/test_journal_schema.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_journal_selected_account.py](../tests/unit/test_journal_selected_account.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/8/0/0 |
| [test_journal_settings_dialogs.py](../tests/unit/test_journal_settings_dialogs.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/1/0/0 |
| [test_journal_snapshot_service.py](../tests/unit/test_journal_snapshot_service.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_journal_workers.py](../tests/unit/test_journal_workers.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_kind_name_history.py](../tests/unit/test_kind_name_history.py) | 미등록 유지 | 과거 KIND 공시에서 직접·이전 상호명 파싱; 해당 parser 변경 때 선택 실행 | 0/0/0/0/0/0 |
| [test_kiwoom_client_factory.py](../tests/unit/test_kiwoom_client_factory.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/3/0/0/0 |
| [test_kiwoom_rest_client.py](../tests/unit/test_kiwoom_rest_client.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/3/1/0/0 |
| [test_kiwoom_storage_audit.py](../tests/unit/test_kiwoom_storage_audit.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_krx_stock_catalog.py](../tests/unit/test_krx_stock_catalog.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_krx_stock_catalog_worker_controller.py](../tests/unit/test_krx_stock_catalog_worker_controller.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_local_api_config.py](../tests/unit/test_local_api_config.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/2/0 |
| [test_local_storage_diagnostics.py](../tests/unit/test_local_storage_diagnostics.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_main_table_column_controller.py](../tests/unit/test_main_table_column_controller.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_main_table_formatting.py](../tests/unit/test_main_table_formatting.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_main_window.py](../tests/unit/test_main_window.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/10/52/0/0 |
| [test_main_window_layout.py](../tests/unit/test_main_window_layout.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_market_cache_writer.py](../tests/unit/test_market_cache_writer.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_market_data_contract.py](../tests/unit/test_market_data_contract.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_market_data_coverage.py](../tests/unit/test_market_data_coverage.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_market_data_finalization.py](../tests/unit/test_market_data_finalization.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_market_data_metadata_repository.py](../tests/unit/test_market_data_metadata_repository.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_market_events.py](../tests/unit/test_market_events.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/4/11/0/0 |
| [test_market_index_chart_service.py](../tests/unit/test_market_index_chart_service.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_market_news_sources.py](../tests/unit/test_market_news_sources.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_market_news_window.py](../tests/unit/test_market_news_window.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/1/1/0/0 |
| [test_market_profile_settings.py](../tests/unit/test_market_profile_settings.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/1/7/0/0 |
| [test_market_research_features.py](../tests/unit/test_market_research_features.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/8/0/0 |
| [test_market_role_barrier.py](../tests/unit/test_market_role_barrier.py) | 기준선 미등록 → P1-2 등록 | 단독 12건·all-local 통합 통과; transaction/barrier 경계 필요, setup을 공용 fixture로 분리 | 1/0/4/1/0/0 |
| [test_market_role_change.py](../tests/unit/test_market_role_change.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/0/5/0/0/0 |
| [test_market_session.py](../tests/unit/test_market_session.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/3/3/0/0 |
| [test_market_session_schedule.py](../tests/unit/test_market_session_schedule.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_materialize_historical_news_seed.py](../tests/unit/test_materialize_historical_news_seed.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/1/0/0 |
| [test_minute_bar_repository.py](../tests/unit/test_minute_bar_repository.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/2/0/0/0 |
| [test_minute_bar_revisions.py](../tests/unit/test_minute_bar_revisions.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_minute_chart_service.py](../tests/unit/test_minute_chart_service.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_minute_history_worker_controller.py](../tests/unit/test_minute_history_worker_controller.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_minute_trade_value.py](../tests/unit/test_minute_trade_value.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_mock_account.py](../tests/unit/test_mock_account.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_mock_account_bundle.py](../tests/unit/test_mock_account_bundle.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/5/5/0/0 |
| [test_mock_account_drain.py](../tests/unit/test_mock_account_drain.py) | 기준선 미등록 → P0 등록 | SQLite authorizer gate로 원인 수정, 14건·all-local 통합 통과 후 manifest 추가 | 0/0/6/4/0/0 |
| [test_mock_account_monitor.py](../tests/unit/test_mock_account_monitor.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/4/0/0 |
| [test_mock_automation_admission.py](../tests/unit/test_mock_automation_admission.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/20/0/0 |
| [test_mock_automation_candidate.py](../tests/unit/test_mock_automation_candidate.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/1/0/0/0 |
| [test_mock_automation_dialog.py](../tests/unit/test_mock_automation_dialog.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/1/0/0 |
| [test_mock_automation_risk.py](../tests/unit/test_mock_automation_risk.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_mock_automation_runner.py](../tests/unit/test_mock_automation_runner.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/3/0/5/0/0 |
| [test_mock_automation_specification.py](../tests/unit/test_mock_automation_specification.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/1/0/0 |
| [test_mock_automation_supervisor.py](../tests/unit/test_mock_automation_supervisor.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/8/0/0/0 |
| [test_mock_credential_owner.py](../tests/unit/test_mock_credential_owner.py) | 기준선 미등록 → P1-2 등록 | 단독 32건·all-local 통합 통과; account binding·credential rotation·DB commit/rollback 계약, fake client를 공용 지원 파일에서 사용 | 0/0/11/5/0/0 |
| [test_mock_execution.py](../tests/unit/test_mock_execution.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_mock_runtime_barrier.py](../tests/unit/test_mock_runtime_barrier.py) | 기준선 미등록 → P1-1 등록 | 단독 115건·all-local 통합 통과 후 manifest 추가 | 0/0/1/0/0/0 |
| [test_nas_credentials_dialog.py](../tests/unit/test_nas_credentials_dialog.py) | 기준선 미등록 → P1-2 등록 | 단독 17건·all-local 통합 통과; Qt 응답성·인증 비밀 표시·중복 적용 확인 | 0/0/19/13/4/4 |
| [test_nas_credentials_ui_integration.py](../tests/unit/test_nas_credentials_ui_integration.py) | 기준선 미등록 → P0 등록 | 단독 2/2 통과 후 별도 P0 profile 편입. 숨김/표시 UX, API의 disable 저장, SQLite sidecar, 암호화 파일의 평문 비노출 검증 | 2/0/4/6/0/1 |
| [test_nas_diagnostic_commit_correlation.py](../tests/unit/test_nas_diagnostic_commit_correlation.py) | 미등록 | 별도 환경; Windows skip 방지, Linux 격리 실행 전 보류 | 0/0/9/20/0/0 |
| [test_nas_operator.py](../tests/unit/test_nas_operator.py) | 기준 미등록 → P2 등록 | P2 NAS/capture profile 편입; portable 운영 정책·rollback·readiness·skip 거부 44건; 실제 Linux ACL 검사는 별도 | 0/0/9/0/1/0 |
| [test_nas_scheduled_trace.py](../tests/unit/test_nas_scheduled_trace.py) | 기준 미등록 → P2 등록 | P2 replay admission 편입; release/revision/deadline 검증·불확실한 ACK 재시도 금지 7건 | 0/0/0/0/0/0 |
| [test_nas_source_runtime.py](../tests/unit/test_nas_source_runtime.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/7/0/6/3 |
| [test_nas_storage_mapping.py](../tests/unit/test_nas_storage_mapping.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_naver_credential_owner.py](../tests/unit/test_naver_credential_owner.py) | 기준선 미등록 → P1-2 등록 | 단독 15건·all-local 통합 통과; cancelled worker drain·revision 교체·저장 실패 복구 계약 | 2/0/5/2/0/0 |
| [test_naver_news_config.py](../tests/unit/test_naver_news_config.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_naver_stock_market_news.py](../tests/unit/test_naver_stock_market_news.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/1/0/0/0 |
| [test_naver_stock_news.py](../tests/unit/test_naver_stock_news.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_news_ai.py](../tests/unit/test_news_ai.py) | 기준선 미등록 → 뉴스 계약 profile 등록 | 5건 발견 복구·단독 및 격리 통과; 응답 해석·제공자 대체·요청 제한 계약 | 0/0/4/0/0/0 |
| [test_news_ai_repository.py](../tests/unit/test_news_ai_repository.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_news_analysis.py](../tests/unit/test_news_analysis.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/4/0/0/0 |
| [test_news_api_contract_baseline.py](../tests/unit/test_news_api_contract_baseline.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/1/2/0 |
| [test_news_auto_analysis.py](../tests/unit/test_news_auto_analysis.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_news_classification_benchmark.py](../tests/unit/test_news_classification_benchmark.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_news_database.py](../tests/unit/test_news_database.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/1/0/1/1 |
| [test_news_event_history.py](../tests/unit/test_news_event_history.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/12/0/0 |
| [test_news_evidence.py](../tests/unit/test_news_evidence.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_news_execution.py](../tests/unit/test_news_execution.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_news_grouping.py](../tests/unit/test_news_grouping.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_news_jobs.py](../tests/unit/test_news_jobs.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 1/0/8/3/0/0 |
| [test_news_observation_history.py](../tests/unit/test_news_observation_history.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/2/0/0/0 |
| [test_news_process.py](../tests/unit/test_news_process.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_news_query_operations.py](../tests/unit/test_news_query_operations.py) | 기준선 미등록 → P1-1 등록 | 단독 115건·all-local 통합 통과 후 manifest 추가 | 2/0/15/10/0/0 |
| [test_news_read_routes.py](../tests/unit/test_news_read_routes.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_news_rules.py](../tests/unit/test_news_rules.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_news_settings_dialog.py](../tests/unit/test_news_settings_dialog.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/7/0/0 |
| [test_news_source_collection.py](../tests/unit/test_news_source_collection.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/2/22/0/0 |
| [test_news_view_model.py](../tests/unit/test_news_view_model.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_news_window_coordinator.py](../tests/unit/test_news_window_coordinator.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/1/1/0/0 |
| [test_nxt_eligibility_worker_controller.py](../tests/unit/test_nxt_eligibility_worker_controller.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_order_lifecycle.py](../tests/unit/test_order_lifecycle.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_paddle_theme_ocr.py](../tests/unit/test_paddle_theme_ocr.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/1/0/0/0 |
| [test_parallel_validation_client.py](../tests/unit/test_parallel_validation_client.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/2/0/9/0 |
| [test_parse_historical_news_page_versions.py](../tests/unit/test_parse_historical_news_page_versions.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_persistent_outbox.py](../tests/unit/test_persistent_outbox.py) | 기준선 미등록 → P1-1 등록 | 단독 115건·all-local 통합 통과 후 manifest 추가 | 0/0/0/0/0/0 |
| [test_personal_trade_rules.py](../tests/unit/test_personal_trade_rules.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_plan_historical_monthly_case_selection.py](../tests/unit/test_plan_historical_monthly_case_selection.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_planned_reconnect.py](../tests/unit/test_planned_reconnect.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 2/0/13/21/0/0 |
| [test_postgres_access.py](../tests/unit/test_postgres_access.py) | 기준선 미등록 → P1-2 등록 | 단독 58건·all-local 통합 통과; private access instrumentation은 테스트 대상. fake/SQLite 경계만 확인하며 live PostgreSQL 증거는 아님 | 0/0/57/130/0/0 |
| [test_prepared_news_import_batch.py](../tests/unit/test_prepared_news_import_batch.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/5/0/0/0 |
| [test_process_control.py](../tests/unit/test_process_control.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/3/0/2/0 |
| [test_program_trade_service.py](../tests/unit/test_program_trade_service.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_project_historical_minute_exclusions.py](../tests/unit/test_project_historical_minute_exclusions.py) | 미등록 유지 | 과거 분봉 연구 projection의 날짜 제외 검증; projection 변경 때 선택 실행 | 0/0/0/0/0/0 |
| [test_publish_historical_daishin_raw_to_nas.py](../tests/unit/test_publish_historical_daishin_raw_to_nas.py) | 미등록 | 별도 환경; Windows skip 방지, Linux 격리 실행 전 보류 | 0/0/0/0/3/1 |
| [test_publish_historical_market_context_to_nas.py](../tests/unit/test_publish_historical_market_context_to_nas.py) | 기준선 미등록 → 역사 데이터 계약 profile 등록 | 3건 발견 복구·격리 통과; 게시 완료 조건과 4 MiB 인접 원본 DB 미복사 검증 | 0/0/0/0/2/0 |
| [test_query_store_source.py](../tests/unit/test_query_store_source.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/3/0 |
| [test_ranking_execution.py](../tests/unit/test_ranking_execution.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_ranking_schedule.py](../tests/unit/test_ranking_schedule.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_ranking_service.py](../tests/unit/test_ranking_service.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/4/1/0/0 |
| [test_ranking_worker_controller.py](../tests/unit/test_ranking_worker_controller.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_real_account_monitor.py](../tests/unit/test_real_account_monitor.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/0/6/8/0/0 |
| [test_real_account_reader.py](../tests/unit/test_real_account_reader.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/0/0/2/0/0 |
| [test_real_account_reads.py](../tests/unit/test_real_account_reads.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/0/5/0/0/0 |
| [test_real_account_realtime.py](../tests/unit/test_real_account_realtime.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/0/6/13/0/0 |
| [test_real_credential_owner.py](../tests/unit/test_real_credential_owner.py) | 기준선 미등록 → P1-2 등록 | 단독 14건·all-local 통합 통과; credential ownership·broker/revision/recovery 계약, 공통 setup은 중립 support class로 분리 | 1/0/6/3/0/0 |
| [test_real_reconnect_barrier.py](../tests/unit/test_real_reconnect_barrier.py) | 기준선 미등록 → P1-1 등록 | 단독 115건·all-local 통합 통과 후 manifest 추가 | 1/0/9/3/0/0 |
| [test_realtime.py](../tests/unit/test_realtime.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_realtime_parser.py](../tests/unit/test_realtime_parser.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_realtime_subscription.py](../tests/unit/test_realtime_subscription.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_realtime_worker_controller.py](../tests/unit/test_realtime_worker_controller.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_recorded_execution.py](../tests/unit/test_recorded_execution.py) | 기준 미등록 → P2 등록 | P2 causal lifecycle 편입; actor 순서·실제 SQLite 저장·취소 drain·입력 거부 15건; 실패 시 gate 해제와 task 종료 보강 | 0/0/3/0/0/0 |
| [test_recorded_replay_baseline.py](../tests/unit/test_recorded_replay_baseline.py) | 기준 미등록 → P2 등록 | P2 replay admission 편입; lease 소유권·실제 connection drain·commit 오류 close 14건; 소유 스레드 종료 assertion 보강 | 2/0/19/9/0/0 |
| [test_recorded_replay_operator.py](../tests/unit/test_recorded_replay_operator.py) | 기준 미등록 → P2 등록 | P2 replay admission 편입; 외부 DB·특권 role DDL 사전 거부와 secret redaction 4건 | 0/0/3/4/0/0 |
| [test_recorded_workload_capture.py](../tests/unit/test_recorded_workload_capture.py) | 기준 미등록 → P2 등록 | P2 NAS/capture profile 편입; payload 무결성·복사 drain·저장 실패 격리 18건 | 0/0/11/12/0/0 |
| [test_recover_development_validation_orphan.py](../tests/unit/test_recover_development_validation_orphan.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/1/3/0/0/0 |
| [test_recover_final_holdout_orphan.py](../tests/unit/test_recover_final_holdout_orphan.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/1/2/1/0/0 |
| [test_remote_kiwoom_rest_client.py](../tests/unit/test_remote_kiwoom_rest_client.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_replay_cache_clock.py](../tests/unit/test_replay_cache_clock.py) | 기준 미등록 → P2 등록 | P2 TOP20 replay boundaries 편입; 실제 SQLite cache TTL·source clock·lease/version/dirty baseline 거부 9건 | 3/0/13/6/0/0 |
| [test_report_historical_collection_status.py](../tests/unit/test_report_historical_collection_status.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/2/1/0/0 |
| [test_research_bundle.py](../tests/unit/test_research_bundle.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/8/0 |
| [test_research_bundle_execution.py](../tests/unit/test_research_bundle_execution.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 3/0/2/0/3/0 |
| [test_research_campaign.py](../tests/unit/test_research_campaign.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/0/0/0/0/0 |
| [test_research_campaign_budget.py](../tests/unit/test_research_campaign_budget.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/0/1/0/0/0 |
| [test_research_campaign_dialog.py](../tests/unit/test_research_campaign_dialog.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/0/25/72/0/0 |
| [test_research_campaign_execution.py](../tests/unit/test_research_campaign_execution.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/0/6/0/1/0 |
| [test_research_campaign_inputs.py](../tests/unit/test_research_campaign_inputs.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 2/0/6/0/10/0 |
| [test_research_campaign_nas.py](../tests/unit/test_research_campaign_nas.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 2/2/13/0/16/9 |
| [test_research_campaign_registration_dialog.py](../tests/unit/test_research_campaign_registration_dialog.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/1/14/23/4/1 |
| [test_research_campaign_worker.py](../tests/unit/test_research_campaign_worker.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/0/3/0/0/0 |
| [test_research_comparisons.py](../tests/unit/test_research_comparisons.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_research_completed_input_recovery.py](../tests/unit/test_research_completed_input_recovery.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/1/3/0/5/2 |
| [test_research_data_source.py](../tests/unit/test_research_data_source.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/1/0/0 |
| [test_research_development_partitions.py](../tests/unit/test_research_development_partitions.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 3/0/11/0/9/2 |
| [test_research_development_validation.py](../tests/unit/test_research_development_validation.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/1/32/0/6/0 |
| [test_research_development_validation_dialog.py](../tests/unit/test_research_development_validation_dialog.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/1/14/28/6/1 |
| [test_research_dialog.py](../tests/unit/test_research_dialog.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/1/0/0 |
| [test_research_evaluation.py](../tests/unit/test_research_evaluation.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_research_execution.py](../tests/unit/test_research_execution.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_research_extension.py](../tests/unit/test_research_extension.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_research_factors.py](../tests/unit/test_research_factors.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_research_final_cli.py](../tests/unit/test_research_final_cli.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/1/6/0/4/0 |
| [test_research_final_dialog.py](../tests/unit/test_research_final_dialog.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/1/15/20/6/0 |
| [test_research_final_execution.py](../tests/unit/test_research_final_execution.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/3/21/0/1/0 |
| [test_research_final_exposure_cli.py](../tests/unit/test_research_final_exposure_cli.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/1/3/0/1/0 |
| [test_research_final_holdout_ledger.py](../tests/unit/test_research_final_holdout_ledger.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/8/0 |
| [test_research_final_preparation.py](../tests/unit/test_research_final_preparation.py) | 기준선 미등록 → P0 계약/fixture 등록 | legacy/current candidate identity와 descriptor 검증; 28건 통과, all-local 포함 | 1/1/11/0/1/0 |
| [test_research_hypotheses.py](../tests/unit/test_research_hypotheses.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_research_hypothesis_campaign.py](../tests/unit/test_research_hypothesis_campaign.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/0/0/0/0/0 |
| [test_research_hypothesis_repository.py](../tests/unit/test_research_hypothesis_repository.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_research_independent_comparison_dialog.py](../tests/unit/test_research_independent_comparison_dialog.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/0/11/14/6/1 |
| [test_research_independent_comparison_process.py](../tests/unit/test_research_independent_comparison_process.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/0/9/0/18/0 |
| [test_research_independent_comparisons.py](../tests/unit/test_research_independent_comparisons.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/1/2/2/2/0 |
| [test_research_observation_history.py](../tests/unit/test_research_observation_history.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_research_partition_campaign.py](../tests/unit/test_research_partition_campaign.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 2/1/2/3/1/0 |
| [test_research_partition_search.py](../tests/unit/test_research_partition_search.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/1/1/0/8/2 |
| [test_research_process.py](../tests/unit/test_research_process.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/1/0/1/0 |
| [test_research_queue.py](../tests/unit/test_research_queue.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_research_replay.py](../tests/unit/test_research_replay.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_research_reports.py](../tests/unit/test_research_reports.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_research_repository.py](../tests/unit/test_research_repository.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_research_resources.py](../tests/unit/test_research_resources.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 2/0/2/0/0/0 |
| [test_research_result_publication.py](../tests/unit/test_research_result_publication.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/10/5/4/3 |
| [test_research_search.py](../tests/unit/test_research_search.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/1/0/0/0 |
| [test_research_splits.py](../tests/unit/test_research_splits.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_research_staging_cleanup.py](../tests/unit/test_research_staging_cleanup.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/1/7/7/7/1 |
| [test_research_storage.py](../tests/unit/test_research_storage.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/1/2/1/4/2 |
| [test_research_storage_capacity.py](../tests/unit/test_research_storage_capacity.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/1/4/2/3/5 |
| [test_research_symbol_partitions.py](../tests/unit/test_research_symbol_partitions.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 3/2/6/3/3/1 |
| [test_research_symbol_validation.py](../tests/unit/test_research_symbol_validation.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/1/22/1/4/0 |
| [test_research_symbol_validation_dialog.py](../tests/unit/test_research_symbol_validation_dialog.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 2/1/3/21/2/0 |
| [test_rest_request_lane_capacity.py](../tests/unit/test_rest_request_lane_capacity.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/4/12/0/0 |
| [test_retry_daishin_missing_5m.py](../tests/unit/test_retry_daishin_missing_5m.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/1/3/0/0 |
| [test_run_naver_stock_market_news.py](../tests/unit/test_run_naver_stock_market_news.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/2/0/4/1 |
| [test_run_postgres_access_integration.py](../tests/unit/test_run_postgres_access_integration.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/1/0/0/0 |
| [test_run_regression.py](../tests/unit/test_run_regression.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/5/30/6/0 |
| [test_run_test_app_with_data.py](../tests/unit/test_run_test_app_with_data.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_schema_migrations.py](../tests/unit/test_schema_migrations.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_scoped_account_api.py](../tests/unit/test_scoped_account_api.py) | 기준선 미등록 → P1-2 등록 | 단독 8건·all-local 통합 통과; authenticated API request/response와 foreign-account/order fence 확인 | 1/0/5/0/0/0 |
| [test_second_trade_aggregation.py](../tests/unit/test_second_trade_aggregation.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_second_trade_storage.py](../tests/unit/test_second_trade_storage.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/6/0/0 |
| [test_secondary_data_schedule.py](../tests/unit/test_secondary_data_schedule.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_select_historical_news_page_keys.py](../tests/unit/test_select_historical_news_page_keys.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_selected_account_api_integration.py](../tests/unit/test_selected_account_api_integration.py) | 기준선 미등록 → P1-1 등록 | 단독 115건·all-local 통합 통과 후 manifest 추가 | 3/0/0/0/0/0 |
| [test_selected_account_client.py](../tests/unit/test_selected_account_client.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/0/0/0/0/0 |
| [test_settings_api_hub.py](../tests/unit/test_settings_api_hub.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/1/0/0 |
| [test_settings_backup.py](../tests/unit/test_settings_backup.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/1/1/6/1 |
| [test_settings_repository.py](../tests/unit/test_settings_repository.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_shadow_checkpoint.py](../tests/unit/test_shadow_checkpoint.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_snapshot_prepared_historical_news.py](../tests/unit/test_snapshot_prepared_historical_news.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/2/0 |
| [test_snapshot_provenance.py](../tests/unit/test_snapshot_provenance.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_source_runtime_deploy.py](../tests/unit/test_source_runtime_deploy.py) | 미등록 | 별도 환경; Windows skip 방지, Linux 격리 실행 전 보류 | 0/0/0/0/4/0 |
| [test_sqlite_connections.py](../tests/unit/test_sqlite_connections.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_stage_prepared_historical_news_archive.py](../tests/unit/test_stage_prepared_historical_news_archive.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/1/0 |
| [test_stock_fundamentals_service.py](../tests/unit/test_stock_fundamentals_service.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_stock_news_repository.py](../tests/unit/test_stock_news_repository.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/4/0/0 |
| [test_stock_news_window.py](../tests/unit/test_stock_news_window.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/23/32/0/0 |
| [test_stock_repository.py](../tests/unit/test_stock_repository.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_storage_boundary_concurrency.py](../tests/unit/test_storage_boundary_concurrency.py) | 기준선 미등록 → P1-1 등록 | 단독 115건·all-local 통합 통과 후 manifest 추가 | 0/0/0/14/0/0 |
| [test_strategy_pack.py](../tests/unit/test_strategy_pack.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_strategy_pack_dialog.py](../tests/unit/test_strategy_pack_dialog.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/1/0/0 |
| [test_strategy_pack_extraction.py](../tests/unit/test_strategy_pack_extraction.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_strategy_review_context.py](../tests/unit/test_strategy_review_context.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_strict_restore.py](../tests/unit/test_strict_restore.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/3/21/8/0 |
| [test_theme_backup.py](../tests/unit/test_theme_backup.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/1/0/1/1 |
| [test_theme_color_repository.py](../tests/unit/test_theme_color_repository.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/1/0/0/0 |
| [test_theme_dialogs.py](../tests/unit/test_theme_dialogs.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/7/4/0/0 |
| [test_theme_history.py](../tests/unit/test_theme_history.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/2/0/0/0 |
| [test_theme_import.py](../tests/unit/test_theme_import.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_theme_leadership.py](../tests/unit/test_theme_leadership.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_theme_matching.py](../tests/unit/test_theme_matching.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_theme_parser.py](../tests/unit/test_theme_parser.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_theme_ranking.py](../tests/unit/test_theme_ranking.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_theme_suggestions.py](../tests/unit/test_theme_suggestions.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_theme_text_import.py](../tests/unit/test_theme_text_import.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_top20_delivery_provenance.py](../tests/unit/test_top20_delivery_provenance.py) | 기준 미등록 → P2 등록 | P2 TOP20 input contracts 편입; 실제 hub/parser 전달 원인·subscriber coverage·minute batch·chunk/tail/disk frontier 21건 | 0/0/14/24/1/0 |
| [test_top20_execution_boundaries.py](../tests/unit/test_top20_execution_boundaries.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_top20_fixture_seed.py](../tests/unit/test_top20_fixture_seed.py) | 기준 미등록 → P2 등록 | P2 TOP20 input contracts 편입; cold seed의 warm marker/cache/task/lock/outbox 거부·shared clock/frontier·금지 IO 10건 | 1/0/1/3/0/0 |
| [test_top20_lifecycle_inputs.py](../tests/unit/test_top20_lifecycle_inputs.py) | 기준 미등록 → P2 등록 | P2 TOP20 input contracts 편입; native 구독의 REG ACK·fresh READY·gap/epoch·0초 소비·shared effect owner 거부 11건 | 0/0/7/8/0/0 |
| [test_top20_market_repair_worker_controller.py](../tests/unit/test_top20_market_repair_worker_controller.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_top20_program_shutdown.py](../tests/unit/test_top20_program_shutdown.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/11/0/0 |
| [test_top20_replay_execution.py](../tests/unit/test_top20_replay_execution.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/2/0/0/0 |
| [test_top20_replay_outbox.py](../tests/unit/test_top20_replay_outbox.py) | 기준 미등록 → P2 등록 | P2 TOP20 replay boundaries 편입; 실제 파일 seed 복원·foreign/corrupt 거부·ACK loss restart 7건 | 1/1/2/0/2/2 |
| [test_top20_replay_runtime.py](../tests/unit/test_top20_replay_runtime.py) | 기준 미등록 → P2 등록 | P2 causal lifecycle 편입; 실제 executor pending·취소 drain·timeout quarantine·native broker late 완료 8건 | 1/0/1/4/0/0 |
| [test_top20_replay_transport.py](../tests/unit/test_top20_replay_transport.py) | 기준 미등록 → P2 등록 | P2 TOP20 replay boundaries 편입; lane/spawn identity·fresh ACK·gap/registration 거부 5건; peer 오류 시 task 종료 보강 | 2/0/15/2/0/0 |
| [test_top20_session_plan.py](../tests/unit/test_top20_session_plan.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/7/0/0/0 |
| [test_top20_shared_execution.py](../tests/unit/test_top20_shared_execution.py) | 기준 미등록 → P2 등록 | P2 TOP20 replay boundaries 편입; 같은 clock/runtime/lease 소유·native peer overlap·반복 취소 drain 7건 | 1/0/2/3/0/0 |
| [test_top20_trade_value_collector.py](../tests/unit/test_top20_trade_value_collector.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_trade_analysis_preparation_service.py](../tests/unit/test_trade_analysis_preparation_service.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_trade_chart.py](../tests/unit/test_trade_chart.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_trade_cost_service.py](../tests/unit/test_trade_cost_service.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_trade_episode_analysis_service.py](../tests/unit/test_trade_episode_analysis_service.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_trade_group_edit_service.py](../tests/unit/test_trade_group_edit_service.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_trade_history_query_service.py](../tests/unit/test_trade_history_query_service.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_trade_history_service.py](../tests/unit/test_trade_history_service.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_trade_journal_statistics.py](../tests/unit/test_trade_journal_statistics.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_trade_journal_summary.py](../tests/unit/test_trade_journal_summary.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_trade_review_analysis.py](../tests/unit/test_trade_review_analysis.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_trade_review_formatting.py](../tests/unit/test_trade_review_formatting.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_trade_review_view_model.py](../tests/unit/test_trade_review_view_model.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_trade_setup_classification.py](../tests/unit/test_trade_setup_classification.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/29/0/0 |
| [test_trade_snapshot_context.py](../tests/unit/test_trade_snapshot_context.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_trade_strategy_coordinator.py](../tests/unit/test_trade_strategy_coordinator.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_trade_strength.py](../tests/unit/test_trade_strength.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_update_helper.py](../tests/unit/test_update_helper.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/3/3/0 |
| [test_update_planner.py](../tests/unit/test_update_planner.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 0/0/0/0/0/0 |
| [test_update_worker_controller.py](../tests/unit/test_update_worker_controller.py) | 149 등록 | 유지; 현재 CI 검증, 즉시 수정 근거 없음 | 0/0/0/0/0/0 |
| [test_verify_prepared_historical_news_rules.py](../tests/unit/test_verify_prepared_historical_news_rules.py) | 미등록 | 일반 후보 후속; 핵심 20개 우선, 관련 기능 변경 시 단독/정기 검증 | 1/0/0/0/0/0 |

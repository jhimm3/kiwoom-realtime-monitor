# 테스트 중복과 상시 CI 후속 판단

2026-10-09 · 중복 분석 기준 `914635ca1c3b939761b6a43b38db403cf9d419f1`
· 후속 소스 통합 기준 `3c42285fc490cba4faffad46c7ad307453930780`

## 범위와 현재 상태

기존 [409개 의존성 조사](TEST_DEPENDENCY_AUDIT.md)를 재사용해 중복 후보와 CI 편입 우선순위를
추가 검토했다. 제품 코드·기능 계약·기존 assertion·core 실행 순서는 변경하지 않았다.
이 후속 작업에서는 실행기 발견 증거를 강화하고 선정한 26개 테스트 모듈을 격리 profile로 등록했다.
분석 기준 소스의 `all-local` 로컬 회귀는 통과했다. 최신 main 위의 독립 회귀는 별도 GitHub CI에서 판정한다.

정적 분석 시작 시점에는 위 commit과 `main`의 파일 차이가 0개였다. 이후 원격을 다시 갱신하니
다른 작업의 DB·진단 소스 통합이 main에 추가돼 있었다. 기존 작업 branch는 이미 병합·삭제된 상태여서
`codex/test-discovery-ci-followup`에서 기존 개선을 보존한 채 최신 main 위로 rebase했다.
제품 코드에 이번 작업 자체의 변경을 추가하지 않았다. 이전 소스의 pass를 최신 소스의 pass로 승계하지 않는다.

| 범위 | Windows all-local | 별도 Linux CI | 실제 CI 미편입 | 합계 |
|---|---:|---:|---:|---:|
| 원래 감사한 파일 | 183 | 5 | 221 | 409 |
| 이후 추가된 `test_diagnostic_trace_ram` | 1 | 0 | 0 | 1 |
| 26개 후속 profile 적용 시점 | 210 | 5 | 195 | 410 |
| 최신 main 신규 3개 등록 시점 | 213 | 5 | 195 | 413 |
| NAS/capture 안전 4개 등록 시점 | 217 | 5 | 191 | 413 |
| replay admission 안전 4개 등록 시점 | 221 | 5 | 187 | 413 |
| causal replay lifecycle 5개 등록 시점 | 226 | 5 | 182 | 413 |
| TOP20 replay boundaries 4개 등록 시점 | 230 | 5 | 178 | 413 |
| TOP20 input contracts 5개 등록 시점 | 235 | 5 | 173 | 413 |
| trace persistence 5개 등록 시점 | 240 | 5 | 168 | 413 |
| diagnostic controls 4개 등록 후 현재 | 244 | 5 | 164 | 413 |
| 미등록 선택 묶음 1·2 등록 후 | 290 | 5 | 118 | 413 |
| 미등록 선택 묶음 1·2·3 등록 후 | 314 | 5 | 94 | 413 |
| 미등록 선택 묶음 1·2·3·4 등록 후 | 338 | 5 | 70 | 413 |
| 미등록 선택 묶음 1·2·3·4·5 등록 후 | 365 | 5 | 43 | 413 |

기존 문서의 'manifest 미등록 226개' 중 5개는 CI 제외가 아니다. 다음 모듈은
`.github/workflows/dependency-regression.yml`의 Ubuntu job에서 이미 실행한다.

- `test_credential_store`
- `test_diagnostic_cli_controls`
- `test_nas_diagnostic_commit_correlation`
- `test_source_runtime_deploy`
- `test_publish_historical_daishin_raw_to_nas`

기준 commit의 [GitHub run 37877582253](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37877582253)을
다시 조회했다. Windows 1,915건/44 worker/미실행 worker 0, Linux 5개 모듈 65건
(failure/error/skip/expected failure/unexpected success 0), disposable PostgreSQL access 87건과
별도 PostgreSQL boundary step이 성공했다. 이는 기존 기준선의 hosted 결과다. 이번 후속 변경의
실행 결과나 NAS 운영 PostgreSQL 검증으로 사용하지 않는다.

## 전체 중복 선별 결과

중복 분석 기준의 410개 파일 모두를 AST로 선별했다. 원래 409개는 전부 포함한다.
후속 main에서 추가된 3개는 아래 CI 누락 원인과 보호 계약을 별도로 검토했으며 이 정적 선별 수치에는 포함하지 않는다.

- 완전히 동일한 파일 AST: 0쌍.
- 함수 이름·문서 문자열을 제외하고 동일한 test 본문 AST: 0쌍.
- 같은 클래스 안에서 test 메서드 이름을 다시 정의해 앞 검사를 덮어쓴 경우: 0건.
- 다른 테스트 모듈의 TestCase 클래스를 직접 top-level import한 후보: 0건.
  모듈 자체를 fixture로 import하는 경우와 함수 내부 import는 동일 TestCase의 자동 재발견과 구별했다.
- 교차 파일의 유사 본문 후보: 12쌍. AST 노드 유형·호출 속성·상수·읽는 이름의 5-token shingle
  Jaccard >= 0.72, sequence 유사도 >= 0.85인 30-node 이상 메서드를 선별하고 본문과 fixture를 검토했다.

위 수치는 정적 선별 결과다. 의미가 같은 모든 테스트를 수학적으로 증명하거나 410개를 실행했다는
뜻이 아니다. 유사 후보는 입력·대상 구현·fixture·assertion·실패 주입이 같은지 확인한 뒤 판정했다.

| 비교 대상 | 후보 쌍 | 확인한 차이 | 판정 |
|---|---:|---|---|
| `test_daily_high_worker_controller` / `test_fundamentals_worker_controller` | 3 | 서로 다른 controller/worker, `finished`와 `completed`, 결과 payload와 후속 context | 독립 구현마다 필요한 의도된 반복; 유지 |
| `test_historical_high_worker_controller` / `test_nxt_eligibility_worker_controller` | 3 | 역사 고가와 NXT 적격 controller, object와 bool 결과, 각 factory 검증 | 의도된 반복; 유지 |
| `test_google_drive_worker_controller` / `test_top20_market_repair_worker_controller` | 1 | Drive 작업 인수와 시장자료 repair repository의 서로 다른 start 계약 | 의도된 반복; 유지 |
| `test_dart_credential_owner` / `test_naver_credential_owner` | 2 | 공급자별 owner·pause 상태·credential revision, DART cache 보존과 Naver 호출 횟수 | 공통 drain 패턴이지만 별도 provider 계약; 유지 |
| `test_research_development_validation` / `test_research_symbol_validation` | 2 | v1 두 fold와 v2 symbol bucket을 포함한 네 step, cache 수와 취소 후 재개 결과 | 부분 중복; 버전별 보호 범위가 달라 유지 |
| `test_research_development_validation_dialog` / `test_research_independent_comparison_dialog` | 1 | 다른 dialog/child-result 계약, development 검사의 cancel-file assertion | 부분 중복; 수명과 결과 소비 계약별 유지 |

**이번 분석에서 안전하게 삭제할 완전 중복 테스트 파일·메서드는 확정되지 않았다.**
공통 형태만을 이유로 parameterization/공용 fixture를 새로 만들거나 assertion을 통합하지 않는다.

core의 `test_theme_color_repository` 두 차례 호출은 기존 순서 fingerprint와
`test_profile_plan_preserves_legacy_batches_and_adds_missing_lifecycle_modules`가 명시적으로 보존하는
반복이다. 이 후속 작업에서 core 호출 수 143/고유 모듈 142와 순서 hash를 바꾸지 않는다.
core 밖 profile 간 중복 등록은 기존 `_planned_batches`가 한 번만 실행하도록 처리한다.

진단 산출물은 `tmp/regression/dup-ci-audit/analyze.py`, `static.json`에 있다. 진단 스크립트와
전체 JSON은 커밋 대상이 아니며, 삭제하지 않는 판단 근거는 위 표에 보존했다.

## 재현한 실행기의 검증 공백

`scripts/run_regression.py::_worker`는 모듈별로 suite를 로드하지만, 0건인지는 합쳐진 suite에서만
검사한다. 빈 모듈과 정상 1건 모듈을 같은 batch에 전달한 실제 함수 재현 결과는 다음과 같다.

```text
empty_module_tests=0
nonempty_module_tests=1
exit_code=0
reported_status=passed
reported_tests=1
```

`tmp/regression/dup-ci-audit/reproduce_empty_module.py`는 source identity와 두 synthetic import만
대체하고 현재 `_worker`의 실제 로딩·실행·결과 저장을 호출했다. 기록은
`empty-module-result.json`이다. 현재 등록된 실제 모듈에서 0건이 발생했다고 확인한 것은 아니다.
**발견되지 않는 모듈을 같은 batch의 성공으로 가릴 수 있는 실행기 결함**을 확인한 것이다.

적용한 실행기 수정:

1. `_worker`가 각 요청 모듈의 발견 수와 계획 합계를 기록한다. 0건 모듈이 있거나 계획과 실행 수가
   다르면 `incomplete`와 nonzero exit를 반환한다. 중복 모듈은 순서 있는 list에 각각 남는다.
2. `_run_process`는 passed 결과의 모듈 목록·순서·양의 정수 count·합계가 요청 및 실제 실행 수와
   일치하는지 확인하고 이 증거를 `run.json`에 보존한다. 기존 source, skip, timeout, process tree 검증은
   유지했다.
3. `test_run_regression`에 빈 모듈과 정상 모듈 혼합의 양쪽 순서, 중복 모듈 실행, 불완전하거나
   위조된 발견 증거 거부를 추가했다. `tests.unit.test_run_regression` 22건이 통과했다.
4. 실제 empty-module reproduction은 이전의 false green을 재현했으며, 수정 후 두 순서 모두
   `incomplete`로 판정됐다. Windows Job Object와 제품 코드는 수정하지 않았다.

## 다음 CI 편입 묶음: 26개

나머지 221개를 한 번에 CI에 넣지 않는다. 기존 조사에서 유효한 보호 계약을 확인했고 지금 빠져 있는
계좌·주문 안전 19개와, 발견 경로를 이미 복구한 저비용 함수형 7개를 다음 구현 대상으로 선정한다.
아래 명칭은 모두 `tests.unit.test_` 뒤의 suffix다.

| 묶음 | 파일 | 편입 근거 |
|---|---|---|
| credential/client 3개 | `ai_credential_owner`, `central_credentials_client`, `selected_account_client` | 실제 작업 drain 뒤 key 공개, 잘못된 profile/revision·응답 신원 거부, 주문 timeout의 자동 재전송 금지 |
| 계좌 read/journal 5개 | `real_account_reader`, `real_account_reads`, `real_account_monitor`, `real_account_realtime`, `journal_selected_account` | 페이지 중복·계좌 혼입·부분 결과 거부, 취소 후 실제 DB 쓰기 종료, 늦은 UI 결과의 타 계좌 저장 금지 |
| 모의자동매매 8개 | `mock_account_bundle`, `mock_automation_admission`, `mock_automation_candidate`, `mock_automation_dialog`, `mock_automation_risk`, `mock_automation_runner`, `mock_automation_specification`, `mock_automation_supervisor` | 계좌/lease 고정, STOP revision 재확인, 손익 불명·대사 중 주문 차단, 중복 체결/checkpoint·재시작, 등록만으로 주문 활성화 금지 |
| 역할/재접속 3개 | `market_role_change`, `planned_reconnect`, `condition_runtime` | 역할 전환과 재접속 시 기존 소유 작업·구독·신호의 수명 경계 |
| 함수형 보조 7개 | `classify_historical_stock_adjustments`, `collect_candidate_event_disclosures`, `collect_candidate_exchange_disclosures`, `import_krx_vi_history`, `kind_name_history`, `project_historical_minute_exclusions`, `dart_disclosure_filter` | 기존 45건 발견 복구 중 미편입 12건; 보존 입력·공시 필터·VI/제외 projection 계약을 작은 fixture로 검증 |

19개와 7개는 각각 새 named profile을 기존 `profiles`의 끝에 추가했다. 두 profile은
`all-local`이 모두 순회하므로 GitHub Windows regression job에도 자동 포함된다. 기존 core 143개
실행 항목과 순서 fingerprint를 유지했다. 후보 실행은 모두 모듈별 worker로 격리했다.

첫 19개 실행에서 `test_real_account_reads` setup을 `RealAccountMonitorTests`와
`RealAccountRealtimeOwnerTests`가 재사용할 때 테스트 클래스의 `super()`가 원래 테스트 클래스
인스턴스를 요구해 32건이 setup 단계에서 실패했다. `RealAccountReadsTests.asyncSetUp`에서 공통
지원 클래스의 setup을 직접 호출하도록 고쳐 테스트 간 클래스 결합을 제거했다. 초기화 단계와
테스트 assertion은 유지했다. 이후 계좌 조회·모니터·실시간 관련 44/44, 19개 전체 206/206이
통과했다. 7개 함수형 후보도 12/12를 실제 발견해 통과했다. 이 두 profile 합계는 218건이다.

최초 제한 샌드박스 실행은 Windows Proactor `socketpair`가 loopback 제한에 막혀 TestClient
초기화에서 멈췄다. 프로젝트 임시 경로와 허용된 로컬 테스트 환경에서 해당 모듈을 다시 실행해
통과와 process-tree 종료를 확인했다. `httpx2` 설치나 테스트 의존성 변경은 하지 않았다.

26개를 검증·편입한 시점의 Windows all-local은 210개 고유 모듈, 별도 Linux 5개를 포함한
상시 CI 모듈은 215개다. 원래 409개 조사 대상 가운데 Windows와 별도 Linux CI를 제외한
미등록 테스트 모듈은 195개다. 이 수치의 미등록은 영구 제외 판정이 아니다.

## 최신 main의 새 모듈 등록 누락 해결

main commit `3c42285fc490cba4faffad46c7ad307453930780`의
[hosted run 37884968601](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37884968601)은
새 모듈 등록 guard에서 실패했고 Windows 전체 회귀는 실행되지 않았다. 별도 Linux/PostgreSQL job은 성공했다.
로그에 표시된 누락 3개만 `source-integration-registration` 격리 profile로 추가했다.

- `test_diagnostic_scoped_window`: 선택 범위·입력 누락 증거·payload 무결성·원본 manifest 보존.
- `test_prepare_nas_operator_update`: shell 정규화와 bundle identity·경로 이탈 차단.
- `test_prepare_nas_scoped_replay`: 원본 운영 소스·계약·manifest 보존과 후보 충돌/경로 이탈 거부.

3개는 임시 파일·fake store만 사용하는 15건으로 실제 NAS나 운영 DB를 변경하지 않는다.
최신 소스에서 15/15 및 관련 DB·진단·실행기 9개 모듈 243/243이 통과했다.
기존 계좌 안전 19개/206건과 함수형 7개/12건도 최신 소스에서 다시 통과했다.
이 변경 영향 검사 합계는 38개 worker/476건이며, failure/error/skip/expected failure/
unexpected success/미실행 0, worker·process tree 종료 38/38, 잔류 자손 0이다.
기록은 `tmp/regression/source-integration-registration/run.json`,
`tmp/regression/source-followup-targets/run.json`, `tmp/regression/account-order-on-current-main/run.json`,
`tmp/regression/functional-discovery-on-current-main/run.json`이다.
main CI와 같은 이전 base `f2ba98cba75600089a076e2b6b5a7f3b490ba2b0`를 지정한 등록 guard도
새 모듈 3개 모두 등록으로 통과했다. guard나 실패 판정을 완화하지 않았다.
기존 core 순서와 26개 후속 profile은 그대로 두고 새 profile만 끝에 추가했다.

## 보류를 해석하는 기준과 나머지 195개

- Linux 5개는 별도 환경에서 이미 CI를 수행한다. 보류나 실행 누락으로 분류하지 않는다.
- 완전 중복 삭제 후보 0개이므로 중복을 이유로 제외하는 목록도 만들지 않는다.
- 26개 이후 195개는 단계적 편입 후보로 유지한다. '필요 없음' 또는 '통과' 판정이 아니다.
  원래 파일별 보호 기능은 [보존 근거](REGRESSION_COVERAGE_AUDIT.md)에 있다.
- 이 중 `test_top20_replay_execution`과 `test_top20_session_plan`은 과거 worker 비용이 각각
  약 94초와 62초였다. 첫 편입 묶음에서는 보류하고, native 재생의 실제 시간 의미를 유지한 채
  다음 재생 영역 묶음에서 실행 비용을 재측정한다. sleep을 줄이거나 skip을 추가하지 않는다.
  비용만으로 영구 CI 제외를 확정하지 않았다.
- 나머지 193개도 별도 환경이나 고비용이라고 일괄 단정할 근거는 없다. 첫 묶음 검증 뒤
  기록/재생·뉴스/자료 보존·연구 pipeline 등 기존 영역별로 추가 편입한다. 현재 단계에서
  전체 195개에 대해 최종 CI 제외 사유가 확정됐다고 보고하지 않는다.
- 새로운 test 파일의 등록 guard는 유지한다. 이 guard는 base 대비 새 파일을 검사하므로,
  통과했다고 기존 미등록 후보까지 실행됐다는 뜻은 아니다.

## 실행 비용과 남은 검증

과거 전체 편입 제안의 `coverage-all-local-final/run.json`에는 현재 미편입 221개 worker의
총 시간이 약 1,006초로 남아 있다. test 파일 hash가 지금과 같은 것은 205개, 다른 것은 16개다.
제품 소스도 이후 변경됐으므로 과거 pass를 현재 pass로 승계하지 않는다. 비용 추정에만 사용한다.

- 19개: 현재 206건/19 worker 통과. 함수형 7개: 현재 12건/7 worker 통과.
- 전체 `all-local`: **2,136건/70 worker 통과**. failure/error/skip/expected failure/
  unexpected success/미실행 모두 0, process tree 종료 70/70, 확인되지 않은 worker·process tree와
  잔류 자손 0이다. 최종 `run.json`에서 Windows 210개 모듈과 신규 26개 전부의 실행,
  모듈별 발견 수 및 실행 수를 대조했다. 전체 기록은
  `tmp/regression/test-dedup-ci-all-local-stable-final/run.json`이다.
- 새 테스트 파일 등록 guard도 실행했다.
  `--check-new-test-modules --base-ref HEAD --fallback-ref HEAD`는 새 테스트 모듈 0개로 통과했다.
  이는 현재 작업 트리의 모듈 목록 확인이며 PR base 대비 GitHub 검증 결과를 뜻하지 않는다.
- 위 2,136건 로컬 실행 당시 `regression_profiles.json` manifest SHA256은
  `f8de91fa502e6933b9fc097cd1835223750ae833f6845e2b8bdf018ef1c7057e`, 실행기 SHA256은
  `bbc7c5dfa8bcf36f4e60a62d0ccfb26787e47eb570bd4b032a61fd613db3c7b4`다. 기존 core 실행 순서 hash는
  `36DAE599F514EC9356B7DC3EAB9C7B4F1033C4A27076EB8A7FCD931FBEB1810A`로 보존됐다.
- 검증된 변경을 게시한 뒤 GitHub Windows와 Linux/disposable PostgreSQL CI를 독립 검증으로 확인한다.
  이 로컬 결과는 hosted CI 성공이나 실제 NAS PostgreSQL 검증을 의미하지 않는다.
  NAS 배포와 `main` 병합은 이번 분석·구현의 완료 조건이 아니다.

완료된 후속 작업: 전체 정적 선별, 12쌍 수동 판정, 삭제할 완전 중복 0개 확인, 실행기 false-green
공백 수정, 26개 선택 profile 등록, 관련 fixture 결합 수정, 선택 profile 218건 통과, 전체
`all-local` 2,136건/70 worker 통과, 누락 guard 통과, 변경 목록과 CI 목록 대조다.
남은 195개를 모두 CI에 편입한 것으로 표시하지 않는다. 최신 main 위의 전체 회귀·Linux·disposable
PostgreSQL 검증은 이 작업 branch의 hosted CI 결과와 artifact를 기준으로 별도 판정한다.
운영 NAS PostgreSQL 검증·배포·main 병합은 이번 후속 작업의 완료 조건에 포함하지 않는다.

## 2026-10-09 hosted 기준선과 NAS/capture 안전 4개 선택 편입

직전 commit `10e9080b278e7ae84fe11bd4b1285c7104419090`의
[hosted CI 37887792801](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37887792801)은
두 job 모두 성공했다. Windows 전체 2,154건/73 worker/213개 모듈, 별도 Linux 5개 모듈 65건,
disposable PostgreSQL 87건 및 schema 21·rollback 저장 경계 검사가 통과했다.
Windows artifact의 게시 commit, 깨끗한 checkout, 전체 계획·모듈별 발견 수·실행 수,
manifest/실행기/테스트 파일 hash, 모든 worker·process tree 종료를 대조했다.
failure/error/skip/expected failure/unexpected success/미실행/잔류 자손은 0이다.
13개 파일의 로컬/hosted hash 차이는 committed blob의 LF/CRLF checkout 차이로 확인했다.
이 결과는 운영 NAS 검증이나 배포 완료를 뜻하지 않는다.

최근 main의 녹화·재생·운영 안전 경계와 직접 관련된 기존 미등록 4개만 다음으로 편입했다.

| 모듈 (`tests.unit.test_` 이후) | 검증 목적 | 실제 발견/실행 | 로컬 worker 비용 |
|---|---|---:|---:|
| `catalog_capture_profile` | catalog 전용 복사 예산·비밀값 차단·중지 시 reservation 해제·native 결과와 durable payload 복원 | 10 | 1.54초 |
| `diagnostic_replay_database_cli` | DB 접근 전 원본/입력 검증·lease/seal/status/restore·비밀값 redaction | 11 | 0.93초 |
| `recorded_workload_capture` | typed payload·checksum·원본 저장 결과 유지·복사 lane 종료 경합·disk 실패 격리 | 18 | 2.71초 |
| `nas_operator` | portable 경로/정책 차단·rollback/readiness·정확한 server pause/resume·worker 실패 및 skip-only 거부 | 44 | 0.47초 |

기존 trace API/RAM 검사는 API·수명·전체 메모리 경계를 보호하므로 함께 유지한다.
새 묶음의 catalog별 예산·durable 원본 복원·DB 이전 거부·operator 상태 전이는 같은 assertion의
완전 중복이 아니다. fake store, 임시 trace/control 경로, MemoryTree/fake Docker를 사용하며
운영 NAS·운영 DB를 실행하거나 변경하지 않는다. Linux fd/ACL 실환경 5개는 별도 CI에 유지한다.

의존성 판단: catalog가 재사용하는 `deferred_capture`/`wait_state`는 실제 trace 시작·종료와
임시 control 경로를 소유하는 같은 영역의 lifecycle fixture다. 현재의 공통 helper를 그대로
사용한다. `_SESSION`·lock·reservation·wake 확인은 실제 quota/종료 구현 경계가 검증 대상이므로
제거하지 않는다. DB CLI의 lease mock과 operator의 fake 경계도 의도된 실패 경로 검증에 필요하다.
disk 실패 주입은 실제 payload 파일 교체에서 OSError를 발생시키고 failed 상태·blob 부재·재생 거부와
성공한 native 저장 보존까지 확인한다. patch 적용 여부만으로 통과하는 검사가 아니다.
이번 단계에서 불필요한 구현 의존성 수정이나 fixture 계층 추가는 필요하지 않았다.

`dependency-audit-p2-nas-capture-safety`를 manifest 끝에 추가해 기존 모든 profile·core 목록과
순서를 보존했다. 선택 실행은 83/83, worker/process tree 종료 4/4이며 모든 비정상 결과와
잔류 자손은 0이다. 기록: `tmp/regression/nas-capture-safety-selected/run.json`.
테스트·제품 source·assertion·기대값은 변경하지 않았다. 현재 413개 중 Windows 217개,
별도 Linux 5개, 미등록 단계적 후보 191개다. 미등록 후보가 실행됐거나 불필요하다는 판정은 아니다.

완료 판정은 이 4개를 포함한 게시 commit의 hosted 전체 회귀로 확인한다. manifest 편입 뒤
각 수정마다 로컬 전체 회귀를 반복하지 않고 선택 검사와 최종 독립 환경 전체 검사를 구분한다.
남은 191개는 기존 보호 근거에 따른 선택 후보로 유지하며, 이번 단계의 완료 조건을 모두의 편입이나
중복 삭제로 확대하지 않는다.

## 2026-10-09 replay admission 안전 후속

직전 `df7f4e1d4b42c96453c5e7717629f050a335cfaf`의
[hosted run 37898538029](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37898538029)은
Windows 2,237건/77 worker/217개 모듈, Linux 65건, disposable PostgreSQL 87건과 63개 저장
경계 검사를 통과했다. 게시 commit·전체 실행 계획·모듈별 발견 수·파일 hash와 실제 종료를 대조했고
Windows 비정상 결과/미실행/잔류 자손은 0이다. NAS/capture 묶음의 최종 독립 검증은 완료됐다.

다음은 기존 조사에서 보호 계약이 확인된 저비용 4개다. 이전 CI와 유사한 입력 검증을 일부 수행하지만,
허용 shape/순서, baseline lease drain, provisioning DDL, capture start ACK의 서로 다른 경계를
검증하므로 완전 중복 삭제 대상으로 판단하지 않았다.

| 모듈 (`tests.unit.test_` 이후) | 추가로 보호하는 계약 | 실제 실행 | 로컬 worker 비용 |
|---|---|---:|---:|
| `diagnostic_replay` | 저장 전 shape/운영 DB 차단·상대 timing·독립 lane·busy 중 호출 누락 금지 | 11 | 0.73초 |
| `recorded_replay_baseline` | 연결별 소유 receipt·identity·retired generation·open/drain·commit 오류 뒤 close | 14 | 0.66초 |
| `recorded_replay_operator` | foreign DB/privileged role의 DDL 사전 거부·관리자 권한·오류의 secret 비노출 | 4 | 0.62초 |
| `nas_scheduled_trace` | 정확한 release/revision/flags/deadline·read-only preflight·불확실한 시작 ACK 재시도 금지 | 7 | 0.34초 |

`dependency-audit-p2-replay-admission-safety`를 끝에 추가했다. 기존 core와 모든 profile 항목·순서는
그대로 유지한다. patch는 connection/driver 경계에서 실제 admission 함수로 진입하며,
오류·DDL 미실행·lock 해제·receipt 해제·native exception 보존을 assertion으로 확인한다.
내부 condition/generation/lock/SQL 호출은 실제 소유권과 저장 안전 구현 경계가 검증 대상이므로
유지했다. 다른 테스트의 `events` helper는 순수 입력 생성만 공유하고 테스트 클래스·상태는 공유하지 않는다.
예약 trace fixture는 임시 경로와 fake API/clock을 사용하며 반복 setup의 cleanup도 등록되어 있다.
공용 fixture 계층을 늘릴 필요는 확인되지 않았다.

수정 대상은 `test_recorded_replay_baseline`의 opening/retirement 경합 두 곳이다.
기존 release/close와 join을 유지하고 `thread.is_alive()`의 명시적 실패 assertion을 추가했다.
기존 14건을 수정 전 코드로 다시 실행해 통과했고 수정 후에도 14건이 통과했다. 기대값을 변경하거나
기존 assertion을 제거하지 않았다. 다른 3개 테스트는 수정 없이 등록했다.

고위험 한 경계의 강도 확인: 별도 테스트 프로세스에서만 `_OwnedConnection.__exit__`를 native exit만
호출하도록 바꿔 commit ACK 오류 뒤 close를 누락시켰다. 해당 검사 1건은 오류/skip 없이 정확히
`Expected 'close' to be called once. Called 0 times.`에서 실패했다. 결함은 제품 파일에 쓰지 않았다.
기록: `tmp/regression/replay-admission-strength.json`.

선택 36/36, 모든 비정상 집계/미실행/잔류 자손 0, worker와 process tree 종료 4/4다.
기록: `tmp/regression/replay-admission-safety-selected/run.json`. 합계 worker 비용은 약 2.35초다.
현재 전체 413개 중 Windows 221개, 별도 Linux 5개, 단계적 미등록 후보 187개다.
mock 기반 소유권 검사를 실제 PostgreSQL rollback/sequence 또는 NAS 운영 검증으로 보고하지 않는다.
마지막 전체 검증은 이 변경을 게시한 commit의 hosted artifact로 판정한다.

## 2026-10-09 causal replay lifecycle 후속

직전 `a413b808cc600d54935caebb1be06848a1a4ed9b`의
[hosted run 37908128544](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37908128544)은
Windows 2,273건/81 worker/221개 모듈, Linux 65건, disposable PostgreSQL 87건과 63개 저장
경계 검사를 통과했다. 전체 계획·발견/실행 수·게시 commit·파일 hash·worker/process tree 종료를
대조했다. 비정상 결과/skip/미실행/잔류 자손은 0이다. replay admission 묶음의 최종 독립 검증은 완료됐다.

다음 묶음은 녹화된 입력이 실제 실행·저장·종료로 이어지는 경계를 보호하는 기존 미등록 5개다.
이미 등록된 payload/shape/lease 검사와 함께 유지한다. 전달 retention, REST tape 인과 관계,
collector parser/checkpoint, actor 실행 순서, executor의 실제 종료가 각각 검증 대상이므로
파일 이름이나 일부 입력이 비슷하다는 이유로 삭제·통합하지 않았다.

| 모듈 (`tests.unit.test_` 이후) | 보호하는 계약 | 현재 실제 실행 | 로컬 worker 비용 |
|---|---|---:|---:|
| `diagnostic_collector_replay` | 실제 parser·checkpoint 단계·취소 후 native 저장과 final flush·continuity·운영 URL 차단 | 6 | 2.75초 |
| `diagnostic_delivery_record` | 공유 retention·null/부재·순서·durable chunk·실패 suffix·새 epoch·거부의 incomplete 판정 | 7 | 1.99초 |
| `diagnostic_rest_input` | 논리 요청/transport/cache/ingest 인과 입력·immutable tape·공유 소유권 불명확성·누락/오류 차단 | 14 | 1.99초 |
| `recorded_execution` | 실제 SQLite natural key 저장·actor 순서/peer overlap·입력 선검증·실패/stop/cancel의 native drain | 15 | 2.93초 |
| `top20_replay_runtime` | 실제 executor queue 소유·취소 후 thread drain·timeout quarantine·late native cache 완료·종료 순서 | 8 | 1.03초 |

`dependency-audit-p2-causal-replay-lifecycle`를 manifest 끝에 추가했다. 기존 221개 등록 모듈,
모든 profile·core 목록과 순서를 유지했다. 변경 전 50/50과 변경 후 50/50의 모듈별 발견 수·실행 수가
같고, 두 실행 모두 모든 비정상 집계/미실행/잔류 자손 0, worker/process tree 종료 5/5다.
합계 추가 worker 비용은 이번 로컬 측정 약 10.69초이며 과거 실행 비용만으로 현재 pass를 승계하지 않았다.

확인된 수정 대상: `test_recorded_execution`의 stop/cancel·collector shutdown 취소 두 곳은
assertion이 gate 해제 전에 실패하면 `release.set()`과 작업 회수를 건너뛰었다.
별도 테스트 프로세스에서 기존 `assertFalse`에 의도한 assertion 실패를 주입했다.
변경 전 release 미전송으로 native fixture의 자체 2초 timeout까지 기다렸다(2.008초).
변경 후 동일 assertion 실패를 유지하면서 release 전송·native 작업 종료·active worker 0을
확인했다(0.038초, native 호출 1회). 이 비교는 테스트 cleanup 검증이며 제품 성능 개선 수치가 아니다.
기록: `tmp/regression/recorded-cleanup-before.json`, `recorded-cleanup-after.json`.

두 검사는 기존 stop/cancel·not-done·not-finished·CancelledError·완료·호출 수 assertion을 유지했다.
시작 대기는 bounded timeout과 조기 task 종료 확인으로 바꾸고, finally에서 gate를 항상 해제한 뒤
실제 task 종료를 기다린다. cleanup의 `gather(return_exceptions=True)`는 본문의 예외 assertion을
대체하지 않는다. assertion 실패는 그대로 실패로 기록된다. 제품 source와 기대값은 변경하지 않았다.
`test_diagnostic_delivery_record`에는 기존 실패 writer join 뒤 실제 thread 종료 assertion 1개를 추가했다.

의존성 판단: `_SESSION`/lock/refcount/queue/worker는 delivery 보존과 quota·실제 종료 구현 경계가
검증 대상이므로 유지했다. 실제 parser/ingestor 및 SQLite를 이용한 결과 검증을 mock으로 바꾸지 않았다.
deferred trace lifecycle fixture, 순수 입력 `events`/`identity`와 native test store의 공유는 유지했다.
공용 계층을 추가하거나 모듈 경로를 숨기는 래퍼는 필요하지 않았다. 이 helper를 직접 사용하는
trace RAM·baseline lease·cache clock·shared execution 4개도 별도 실행해 39/39 통과했고
각 worker와 process tree 종료를 확인했다. helper 구현은 변경하지 않았다.

기록: `tmp/regression/causal-replay-lifecycle-before/run.json`,
`causal-replay-lifecycle-after/run.json`, `causal-replay-helper-consumers/run.json`.
현재 전체 413개 중 Windows 226개, 별도 Linux 5개, 단계적 미등록 후보 182개다.
로컬 before/after와 helper 검사 합계는 각각 50건/50건/39건이며 마지막 전체 검증은 게시 commit의
hosted artifact로 판정한다. 운영 NAS PostgreSQL 검증·배포·main 병합은 별도로 유지한다.

## 2026-10-09 TOP20 replay boundaries 후속

직전 `8d7d6324cc9dedac06a97e52eefe052bdd686940`의
[hosted run 37910046205](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37910046205)은
Windows 2,323건/86 worker/226개 모듈, Linux 65건, disposable PostgreSQL 87건과 63개 저장
경계 검사를 통과했다. 전체 계획·발견/실행 수·게시 commit·파일 hash·worker/process tree 종료를
대조했다. 비정상 결과/skip/미실행/잔류 자손은 0이다. causal lifecycle 묶음의 독립 검증은 완료됐다.

다음은 기존 조사에서 확인된 source 시간·durable 파일·native transport·공유 실행 계약 4개다.
기존 payload/lease/lifecycle 검사와 일부 영역이 겹치지만 실제 cache TTL, outbox pending 파일,
spawn lane 및 구독 ACK, 공유 clock/runtime의 소유권을 각각 검증하므로 삭제할 완전 중복이 아니다.

| 모듈 (`tests.unit.test_` 이후) | 보호하는 계약 | 현재 실제 실행 | 로컬 worker 비용 |
|---|---|---:|---:|
| `replay_cache_clock` | 실제 SQLite cache TTL·source 시간과 07:00 경계·lease/version·dirty baseline 거부 | 9 | 1.01초 |
| `top20_replay_outbox` | 실제 native 파일의 동일 pending seed 복원·외부/손상 파일 거부·replace 실패·ACK loss 재시작 | 7 | 1.15초 |
| `top20_replay_transport` | 실제 broker의 lane/spawn identity·reverse arrival·native fresh ACK·gap 및 바뀐 registration 차단 | 5 | 0.89초 |
| `top20_shared_execution` | 한 source clock/runtime/lease 소유·실제 native thread overlap·반복 취소 drain·native 실패 판정 | 7 | 0.96초 |

`dependency-audit-p2-top20-replay-boundaries`를 끝에 추가했다. 기존 226개와 모든 core/profile
항목·순서를 유지했다. 전후 28/28의 모듈별 발견 수와 실행 수가 같으며 모든 비정상 집계/미실행/
잔류 자손 0, worker/process tree 종료 4/4다. 합계 추가 worker 비용은 이번 로컬 약 4.01초다.
기록: `tmp/regression/top20-replay-boundaries-before/run.json`, `top20-replay-boundaries-after/run.json`.

실제 수정 대상은 transport의 reverse request arrival 검사 한 곳이다. offline tape의 두 번째
native `request_with_continuation`에 OSError를 주입하자 기존 메서드는 오류를 전달하지만 첫 번째
테스트 task가 release를 기다리는 상태로 남았다. 이후 IsolatedAsyncioTestCase의 loop 정리에
의존하는 상태였으며 제품 task 잔류나 정상 검사의 false-green으로 단정하지 않는다.
테스트의 finally에서 release를 열고 미완료 소유 task를 취소·회수한 뒤 broker를 닫도록 수정했다.
같은 실패 주입에서 원래 OSError를 유지하고 테스트 메서드 종료 시 pending task는 1개→0개가 됐다.
native 요청은 실패한 종목 한 번뿐이며 뒤늦은 다른 요청은 실행하지 않았다.
기록: `tmp/regression/transport-peer-cleanup-before.json`, `transport-peer-cleanup-after.json`.
별도 프로세스의 in-memory 실패 주입이며 제품 source는 변경하지 않았다.

본문의 성공·lane/spawn·승인·gap assertion을 전부 유지했고 AST로 assertion 변경/삭제 0개를
대조했다. cleanup의 exception 회수는 본문에서 전파하는 원래 오류를 성공으로 바꾸지 않는다.
다른 3개는 수정 없이 등록했다. 실제 SQLite 저장/TTL과 native outbox 파일을 fake 결과로
대체하지 않았다. lease generation/clock identity/SQL/table scope/private RAM은 실제 소유·복원
경계의 검증 대상이므로 유지했다. 공유 fixture와 상수는 그대로 두고 공용 계층을 추가하지 않았다.
outbox의 cold seed helper는 IO나 cleanup 자원을 만들지 않는 메모리 fixture라 현재 단계에서
분리할 실제 근거가 없었다. PostgreSQL restore/sequence의 실환경 증거로 이 unit 결과를 사용하지 않는다.

현재 전체 413개 중 Windows 230개, 별도 Linux 5개, 단계적 미등록 후보 178개다.
새 모듈 누락 guard는 유지하며, 최종 전체 검증은 이 변경을 게시한 commit의 hosted artifact로 판정한다.
기존의 장시간 native session/execution 후보는 시간 의미를 줄이거나 skip으로 통과시키지 않고
별도의 비용/범위 판단 대상으로 유지한다. 남은 후보의 전부 편입·삭제를 이번 묶음의 완료 조건으로 늘리지 않는다.

## 2026-10-09 TOP20 input contracts 후속

직전 `1c059f20073bf52189c346f4ae38633d309a9f9c`의
[hosted run 37911629226](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37911629226)은
Windows 2,351건/90 worker/230개 모듈, Linux 65건, disposable PostgreSQL 87건과 63개 저장
경계 검사를 통과했다. 계획·발견/실행 수·게시 commit·파일 hash·worker/process tree 종료를
대조했다. 비정상 결과/skip/미실행/잔류 자손은 0이다.

기존 조사에서 미등록이었던 TOP20 순위·수급·출처·초기 상태·구독 수명 5개를 선택했다.
기존 재생 boundary와 일부 영역은 겹치지만 실제 입력 생산과 저장·전달 원인·cold baseline·ACK
상태 전이를 각각 검증하므로 삭제할 완전 중복으로 판단하지 않았다.

| 모듈 (`tests.unit.test_` 이후) | 보호하는 계약 | 현재 실제 실행 | 로컬 worker 비용 |
|---|---|---:|---:|
| `diagnostic_top20_flow_input` | 실제 SQLite 수급 저장·완료 marker·baseline·취소 drain 12건; assertion 실패 시 task 회수 2곳 보강 | 12 | 4.98초 |
| `diagnostic_top20_input` | 순위 freshness·20 slots·retry/error tape·OFF/ON 결과 동일·취소 incomplete 8건 | 8 | 4.33초 |
| `top20_delivery_provenance` | 실제 hub/parser 전달 원인·subscriber coverage·minute batch·chunk/tail/disk frontier 21건 | 21 | 3.35초 |
| `top20_fixture_seed` | cold seed의 warm marker/cache/task/lock/outbox 거부·shared clock/frontier·금지 IO 10건 | 10 | 0.63초 |
| `top20_lifecycle_inputs` | native 구독의 REG ACK·fresh READY·gap/epoch·0초 소비·shared effect owner 거부 11건 | 11 | 0.88초 |

`dependency-audit-p2-top20-input-contracts`를 manifest 끝에 추가했다. 기존 230개와 모든
core/profile 항목·순서를 유지했다. 전후 62/62의 모듈별 발견 수·실행 수가 같고 비정상 집계/
미실행/잔류 자손 0, worker/process tree 종료 5/5다. 추가 worker 비용은 이번 로컬
약 14.17초다. 파일 이름이나 과거 pass로 현재 결과를 승계하지 않았다.
기록: `tmp/regression/top20-input-contracts-before/run.json`, `top20-input-contracts-after/run.json`.

실제 수정 대상은 `test_diagnostic_top20_flow_input`의 native 저장 및 baseline 조회 취소 검사
두 곳이다. 저장·조회 경계의 `entered.wait(2)` 결과를 확인하지 않았고, 본문의 `assertFalse`가
실패하면 release와 task 회수를 건너뛰었다. 별도 프로세스의 assertion 실패 주입으로 두 곳 모두
native 경계 진입 뒤 release=false, 테스트 메서드 종료 시 pending task=1을 재현했다.
변경 후 같은 assertion 실패가 유지되면서 release=true, pending task=0을 확인했다.
기록: `tmp/regression/flow-cleanup-before.json`, `flow-cleanup-after.json`.

경계 진입의 성공 assertion 2개를 추가하고 finally에서 release 후 소유 task의 실제 종료를 기다린다.
기존 not-done·CancelledError·저장·완료 assertion을 AST로 대조해 모두 유지했다.
cleanup의 `gather(return_exceptions=True)`는 본문의 실패 판정을 대체하지 않는다. 제품 로직·
기대값·원본 데이터는 바꾸지 않았다. 정상 통과 결과를 false-green으로 단정하지 않는다.

의존성 판단: native ingestor·SQLite natural key와 완료 marker, hub/parser receipt·subscriber queue,
REG ACK/epoch/owner 및 fixture seed의 금지 상태가 실제 검증 경계이므로 필요한 내부 의존성을
유지했다. 순수 입력 helper·ExitStack capture fixture는 수정하지 않았고 이 파일을 직접 import하는
다른 테스트는 없다. 새로운 wrapper나 공용 fixture 계층이 필요하지 않았다.

현재 전체 413개 중 Windows 235개, 별도 Linux 5개, 미등록 후보 173개다. 전체 회귀는 이번
검증된 변경을 게시한 뒤 hosted artifact로 별도 확인한다. 로컬 62건 통과를 전체/hosted/운영 NAS
검증으로 대체하지 않는다. 미등록 실행 계획은 기존 coverage audit을 유지하며 장시간
`top20_replay_execution`(이전 약 94초), `top20_session_plan`(약 62초)은 이번 묶음에 넣지 않았다.
timeout·sleep·assertion을 줄여 실행 비용을 숨기지 않았다. 운영 배포·main 병합은 별도다.

## 2026-10-09 trace persistence 후속

직전 `ba835728381a9dc4039f1a0d9894358c46a94235`의
[hosted run 37913894768](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37913894768)은
Windows 2,413건/95 worker/235개 모듈, Linux 65건, disposable PostgreSQL 87건과 63개 저장
경계 검사를 통과했다. 실제 계획·발견/실행 수·게시 commit·파일 hash·소스 및 worker/process tree
종료를 대조했다. 비정상 결과/skip/미실행/잔류 자손은 0이다.

이번에는 기존 조사에서 미등록이었던 trace 자체와 durable/deferred 저장, commit 지표와 control
lease 5개를 선택했다. replay 소비 검사를 추가하는 것과 달리 입력 증거의 영속화·실패의 가시성·
진단 수명·측정 의미를 보호한다. RAM compression만 검사하는 기존 trace RAM이나 workload API와
일부 영역은 겹치지만 입력·assertion·실패 주입·실행 경계가 달라 삭제할 완전 중복은 아니다.

| 모듈 (`tests.unit.test_` 이후) | 보호하는 계약 | 현재 실제 실행 | 로컬 worker 비용 |
|---|---|---:|---:|
| `diagnostic_trace` | 실제 chunk 순서·checksum·overflow·65분 envelope의 bounded burst·run lock·중단 복구 7건 | 7 | 2.35초 |
| `diagnostic_trace_batches` | payload fsync·chunk publication·최종 manifest 완료·실패·stop 중 도착 보존 8건; fixture writer 종료 확인 | 8 | 1.78초 |
| `diagnostic_trace_deferred` | 33,001행 RAM→디스크 순서·deadline·중단·메모리/event 한도·host/container headroom 9건; fixture writer 종료 확인 | 9 | 3.91초 |
| `diagnostic_flush_metrics` | commit window와 wait 표본 정렬·불완전 probe·성공 cycle 집계·thread 간 flush ID 6건 | 6 | 0.37초 |
| `diagnostic_workloads` | master/child lease·독립 만료·pause 복구·capture off 초기화·측정 불가와 0 구분 9건 | 9 | 0.48초 |

`dependency-audit-p2-trace-persistence`를 manifest 끝에 추가했다. 기존 235개와 모든 core/profile
항목·순서는 그대로다. 전후 39/39의 모듈별 발견·실행 수가 같고 모든 비정상 집계/미실행/
잔류 자손 0, worker/process tree 종료 5/5다. 추가 worker 비용은 로컬 약
8.88초다. 65분 envelope와 33,001행은 통제 burst/fixture 검사이며
실제 65분 운영 부하나 8GiB 장중 수용을 검증했다는 뜻이 아니다.
기록: `tmp/regression/trace-persistence-before/run.json`, `trace-persistence-after/run.json`.

확인된 수정 대상은 `held_capture`/`deferred_capture`의 fixture 종료 판정이다. stop은 제한 시간
join 뒤 상태를 반환할 수 있는데 helper는 실제 writer가 살아 있는지 검사하지 않았다.
별도 프로세스에서 실제 final manifest 경계를 gate로 막고 stop의 join 시간을 0으로 줄이는 결함을
주입했다. 변경 전 두 helper는 writer가 살아 있는데도 정상 반환했다. 변경 후 두 helper 모두
AssertionError로 실패한다. probe는 외부 temporary directory를 유지하고 gate를 해제한 뒤 실제
writer 종료를 확인해 원본 데이터·제품 소스·잔류 작업을 남기지 않았다.
기록: `tmp/regression/trace-fixture-exit-before.json`, `trace-fixture-exit-after.json`.

helper는 start 직후 실제 소유 thread 참조를 보존하고 stop 뒤 is_alive를 검사한다. global 현재 thread
교체나 terminal 상태만으로 종료를 추정하지 않는다. master off는 finally에서 유지한다. 기존
본문의 assertion을 AST로 대조해 모두 동일하고 새 공용 계층·제품 수명 변경은 없다.
공유 helper 소비자인 trace RAM·catalog capture profile·delivery record 3개도 별도 격리 실행해
26/26 통과, 실제 발견/worker/process tree 종료·비정상 집계 0을 확인했다.
기록: `tmp/regression/trace-persistence-helper-consumers/run.json`.

실패 주입 검토: final manifest fsync 실패는 stopping→failed와 디스크 failed 상태를 검사한다.
payload fsync/chunk rename 실패는 written=0·queued=1·blobs/chunks 미공개·다운로드 거부까지
도달한다. durable sync gate는 새 도착 11행의 순서까지 확인한다. patch 적용 여부만 검사하거나
동일 내용을 검사하지 않는 성공 mock으로 바꾸지 않았다. 내부 fsync·manifest·queue·LOCK·quota는
실제 검증 대상이므로 유지했다. commit 지표의 private 함수도 wait-window 계산 경계가 검증 대상이다.
절대 운영 경로를 읽는 대신 temporary control/trace 파일과 통제 /proc/cgroup 표본을 사용한다.

현재 전체 413개 중 Windows 240개, 별도 Linux 5개, 미등록 후보 168개다. 168개는 기존 조사 원장의
관련 변경 시 모듈별 격리 실행/후속 선택 profile 후보이며 이 단계에서 일괄 편입하지 않았다.
장시간 native TOP20 execution/session 후보의 약 94초/62초 비용·선택 보류 근거도 유지한다.
이번 stage의 전체 회귀는 검증된 변경을 게시한 뒤 hosted artifact로 판정하며 NAS 운영 검증·배포·
main 병합은 별도다. 로컬 pass로 hosted나 운영 DB 검증을 대신하지 않는다.

## 2026-10-09 diagnostic controls 후속

직전 `4eec49487e0249b2338e5fe434598db3ac301703`의
[hosted run 37915653824](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37915653824)은
Windows 2,452건/100 worker/240개 모듈, Linux 65건, disposable PostgreSQL 87건과 63개 저장
경계 검사를 통과했다. 발견/실행 수·게시 commit·파일 hash·소스·worker/process tree 종료를
대조했다. 비정상 결과/skip/미실행/잔류 자손은 0이다.

이번 선택은 기존 조사에서 미등록이었던 실행 소유·인증 제어·조회 redaction·포화 판정 4개다.
trace API 일부와 겹치지만 run lock/retry/report DB 격리·실제 shape 비교, 고정 read-only probe,
전체 3 causal flag의 정확한 전달, 예상 포화와 잘못된 입력의 구분을 각각 보호한다.
삭제할 완전 중복으로 확정하지 않았고 이미 등록된 검사 범위도 줄이지 않았다.

| 모듈 (`tests.unit.test_` 이후) | 보호하는 계약 | 최종 실제 실행 | 로컬 worker 비용 |
|---|---|---:|---:|
| `diagnostic_runs` | 인증·revision/session·run 소유/취소·전용 DB·보고서 완료·shape 불일치 거부 11건; main의 8GiB/5M 계약 기대 갱신 | 11 | 3.18초 |
| `diagnostic_sampling_api` | device elapsed/rate·고정 read-only SQL·민감 query 제거·news table/index scope 3건 | 3 | 0.53초 |
| `causal_capture_api` | HTTP의 store/collector/TOP20 flags·deadline이 동일 recorder에 정확히 전달 1건; 실패 시 client cleanup 보강 | 1 | 1.22초 |
| `causal_capture_capacity` | 정상 memory/event 포화와 잘못된 입력 구분·peak/headroom·측정 불가 유지 3건 | 3 | 0.36초 |

`dependency-audit-p2-diagnostic-controls`를 manifest 끝에 추가했다. 기존 240개와 모든 core/profile
항목·순서를 유지했다. 변경 전 18건은 1 failure/0 error/skip, 수정 후 최종 18건은 비정상 집계/
미실행/잔류 자손 0이다. 모듈별 발견·실행 수는 전후 동일하고 worker/process tree 종료 4/4다.
이전 실패를 통과로 재분류하지 않는다. 최종 추가 worker 비용은 로컬 약
5.29초다. 기록: `tmp/regression/diagnostic-controls-before/run.json`,
`diagnostic-controls-after/run.json`, `diagnostic-controls-final/run.json`.

확정한 실패 원인은 `test_diagnostic_runs`의 capabilities 기대 2개다. 기존 memory=4GiB,
event=1M은 [main 통합 d8683b0](https://github.com/jhimm3/kiwoom-realtime-monitor/commit/d8683b0d733b17fd49b651a7212aaca2ea8d6919)의
trace `_DEFERRED_MEMORY_LIMIT`/`_CAPACITY` 및 authenticated capabilities가 8GiB/5M으로 바뀔 때
갱신되지 않았다. 같은 값은 현재 recorder와 deferred lifecycle 테스트에도 명시돼 있다.
현재 HTTP 응답에서 memory=8GiB를 확인했고 event=5M은 route/recorder 구현·통합 diff와
최종 HTTP assertion으로 확인했다. 현재 코드 값에 자동 연동하거나 assertion을 없애지 않고
명시적인 공개 계약 literal 2개만 갱신했다. 버전·0B/0w/0J/0U·deadline·409 guard는 그대로다.

제품 파일을 쓰지 않은 별도 프로세스에서 실제 ASGI capabilities endpoint의 HTTP 출력만
memory=4GiB 또는 event=1M으로 각각 변형했다. 최종 테스트는 두 대조군 모두 1 failure,
0 error/skip으로 거부했다. 이 의도적 실패는 정상 18건의 pass에 합산하지 않는다.
기록: `tmp/regression/diagnostic-capabilities-mutations.json`.

다른 실제 수정은 `test_causal_capture_api`의 client 정리다. 원래 정상 끝의 close만 있어 첫 HTTP
assertion에 오류를 주입하면 unittest cleanup 후에도 actual client.is_closed=false였다.
생성 직후 addCleanup을 등록하고 정상 끝의 중복 close를 제거했다. 같은 assertion failure가
유지되면서 cleanup 후 is_closed=true가 됐다. lifespan을 열어 collector/network 범위를
추가하지 않았다. 기록: `tmp/regression/causal-api-cleanup-before.json`, `causal-api-cleanup-after.json`.

AST로 전체 assertion을 대조해 위 두 literal 외에는 동일함을 확인했다. 변경 파일은 다른 테스트의
공유 helper가 아니며 직접 import 소비자는 없다. run `_worker`/`_current`/manifest patch는 실제
worker·실패 재시도·report publication 경계, sampling의 SQL은 read-only/redaction 경계가 검증
대상이므로 유지했다. SQL 행 수를 실제 PostgreSQL 수용 증거로 사용하지 않는다. control/DB 파일은
temporary path를 사용하고 실제 DB URL·토큰은 fixed fixture뿐이다.

이 환경에서 이미 지원하는 FastAPI/Starlette TestClient와 httpx 경로로 정상 HTTP 검증을 실행했다.
httpx2 안내는 deprecation warning이며 로딩 실패가 아니다. httpx2 설치·버전 변경·import 위장·
검증 생략은 하지 않았다. 앞선 ASGI helper의 일반 공용 계층 확장도 필요하지 않았다.

현재 전체 413개 중 Windows 244개, 별도 Linux 5개, 미등록 후보 164개다. 남은 항목은 기존 조사
원장의 관련 변경 시 모듈별 격리 실행/후속 선택 profile 후보를 유지한다. 장시간 TOP20 2개는
이전 약 94초/62초의 비용과 별도 판단 이유를 유지하고 이번 묶음에 넣지 않았다.
전체 회귀는 이번 검증된 변경을 게시한 뒤 hosted artifact로 판정한다. 운영 NAS 검증·배포·
main 병합은 별도이며 로컬 pass를 hosted 또는 실제 운영 DB 검증으로 대신하지 않는다.

## 2026-10-09 미등록 CI 선택 묶음 1

미등록 Windows 후보 164개 중 파일명 정렬 순서 첫 24개를 기능 목적·기존 회귀 중복·CI 비용·환경
의존성을 기준으로 순차 검토했다. 22개 모듈/83건은 공통 CI profile에 편입하고,
`test_analyze_db_trace`(별도 DB 진단 CLI)와 `test_central_resource_usage`(호스트별 실시간 자원 표본)는
각 진단 코드 변경 시 격리 실행하도록 남겼다. 선택 모듈은 임시 SQLite·가짜 HTTP/배포 transport·
offscreen Qt 경로에서 운영 비밀이나 NAS 접근 없이 실행된다. assertion과 제품 코드는 변경하지 않았다.

| 모듈 | 건수 | 편입 근거 |
|---|---:|---|
| `api_settings_dialog` | 11 | 설정 표시·failover·비동기 저장·충돌 동작 |
| `audit_historical_five_minute_clock` | 4 | 거래 세션의 5분 bin·종가·지연 세션 |
| `audit_historical_monthly_gap_causes` | 1 | 원시 거래량과 분봉 누락 원인의 구분 |
| `audit_historical_monthly_gap_raw` | 1 | 원시 분봉 누락과 저장된 월간 공백 대사 |
| `audit_historical_nas_minute_alignment` | 5 | CREON/NAS 시각 정렬·누락·중복·거래량 차이 |
| `audit_kiwoom_adjusted_minute_overlap` | 2 | 수정/비수정 분봉 가격·거래량 구분 |
| `audit_kiwoom_adjustment_candidates` | 6 | 기업행사 조정 배수 후보의 모호성·증거 |
| `audit_prepared_historical_archive_readiness` | 2 | stale/incomplete archive 차단과 원본 불변 |
| `audit_prepared_historical_body_provenance` | 1 | 본문 출처 증거와 누락 증거 구분 |
| `auxiliary_window_geometry` | 3 | 보조창 위치·크기 저장/복원 |
| `build_historical_exchange_case_context` | 1 | 공식 효력일 연결 및 sealed OOS 제외 |
| `candidate_daily_nas_scripts` | 6 | 파일 잠금·NAS 장애 지속성·빈 봉·보관 자격 |
| `candidate_exchange_effective_dates` | 2 | 거래소 정지와 발행사 상장폐지 구분 |
| `central_ai_client` | 1 | 중앙 AI 요청/응답 transport |
| `central_deployment_check` | 1 | health·인증·DB 읽기·실시간 transport 점검 |
| `central_news_client` | 3 | 뉴스 cursor·정확한 ID·응답 불일치 차단 |
| `central_operational_settings` | 2 | partial update·revision 호환성 |
| `central_server_config` | 22 | 서버 secret·계좌 식별·mock 주문 경계·query 한도 설정 |
| `central_server_db_api_connection` | 1 | 실제 ASGI 요청과 임시 SQLite composed store 연결 |
| `central_server_logging` | 2 | 민감 접근 로그 파일 전용·보존 한도 |
| `central_server_process` | 5 | 서버 실행 인자·재사용·종료 stream 계약 |
| `column_settings_repository` | 1 | 임시 SQLite 열 상태 저장/복원 |

profile은 기존 core batch/profile의 내용과 순서를 그대로 둔 채 manifest 끝에 추가했다. 선택 profile
실행은 22/22 worker·83건 통과, skip·오류·미실행 0, worker tree 종료 22/22다. 전체 `all-local`은
최종 manifest 기준 266개 모듈·2,553건·126 worker 통과, 실패·오류·skip·기대 실패·예상 밖 성공·
미실행·timeout·새 테스트 모듈 누락 0, worker process tree 종료 126/126이다. 실제 실행 기록은
`tmp/regression/batch01-final-targeted/run.json`과
`tmp/regression/batch01-all-local-600/run.json`이다. 첫 180초 전체 시도는 `desktop-and-server`
프로필이 제한 시간을 넘어 timeout됐고 worker tree 종료를 확인했다. 이를 pass로 계산하지 않았고,
600초 제한의 새 전체 run으로 다시 실행했다.

제한 실행 환경에서 `central_deployment_check` 단독 검사가 Windows Proactor event loop 생성 중
`socket.accept()`에서 멈춘 사실을 stack trace로 확인했다. 그 시도는 성공으로 세지 않고 중단했으며,
사용자 Windows 실행 권한에서 동일 선택 profile을 다시 수행해 83건 모두 통과했다. 제품 동작을 바꾸지
않았다. [GitHub hosted run 37921972310](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37921972310)은
동일 commit `4404f9dae608d3d2e3fe35f2c943e1bc64104dc0`에서 Windows 2,553건/126 worker/266개 모듈,
Linux 전용 65건/5개 모듈, disposable PostgreSQL 87건과 schema 21의 저장 경계 63개 및 rollback을
통과했다. hosted worker failure/error/skip/unrun/leaked descendant는 0이며 새 테스트 모듈 검사도
통과했다. hosted Windows artifact의 manifest hash는 local run과 동일한
`1827633b8ad51be6d88c53dd26d6835e783b05bbf3dc348145200d479b06448b`다. NAS 운영 검증·배포와 main 병합은
수행하지 않는다.

## 2026-10-09 미등록 CI 선택 묶음 2

1차 다음 정렬 구간 24개 모듈을 순차 검토했다. 과거 데이터 수집·보완과 archive read API, 외부 시장
runtime, 전략 정책·평가, feedback evidence를 검증하는 모듈로 모두 `dependency-audit-batch-02-historical-runtime-contracts`
profile에 편입했다. 기존 CI 범위는 266에서 290개 Windows 모듈로 늘고, 미등록 Windows 후보는 142에서
118개가 됐다. 선택 실행은 24 worker·160건 통과, failure/error/skip/unrun/worker 종료 이상 0이다.

| 모듈 | 건수 |
|---|---:|
| `test_daily_high_service` | 9 |
| `test_daishin_candidate_collection` | 8 |
| `test_exchange_effective_dates` | 3 |
| `test_export_historical_news_seed` | 4 |
| `test_export_news_classification_corpus` | 2 |
| `test_external_market_runtime` | 14 |
| `test_feedback_evidence` | 17 |
| `test_finalize_prepared_historical_article_bodies` | 2 |
| `test_finalize_prepared_historical_assessments` | 3 |
| `test_finalize_prepared_historical_news_events` | 9 |
| `test_generic_strategy_evaluator` | 2 |
| `test_google_drive_sync` | 15 |
| `test_high_price_policy` | 6 |
| `test_historical_backfill` | 27 |
| `test_historical_baseline_requests` | 2 |
| `test_historical_collection_counts` | 4 |
| `test_historical_collection_monitor` | 7 |
| `test_historical_high_service` | 12 |
| `test_historical_learning_cases` | 2 |
| `test_historical_monthly_selection_coverage` | 2 |
| `test_historical_news_archive_api` | 2 |
| `test_historical_news_archive_dialog` | 3 |
| `test_historical_news_archive_reader` | 3 |
| `test_historical_news_blind_validation` | 2 |

상세 의존성 점검에서 역사 뉴스/archive 계열의 테스트 모듈 간 fixture import를 확인했다.
공유 데이터 준비와 hash helper를 `tests/unit/historical_news_test_support.py`로 이동하고,
assessment·rule·event finalization·archive reader/API 및 이미 등록된 search-projection 테스트가 그
지원 모듈을 사용하도록 바꿨다. 관련 테스트 모듈 사이의 private fixture import 결합을 제거했다.
7개 변경 테스트 파일에서 이동 전후 assertion AST 호출 목록이 모두 동일했고, 해당 7개 모듈 27건이
worker 단위로 통과했다. archive reader의 production `_connect` 직접 사용은 read-only 경계 검증이므로
유지했다. 제품 코드는 수정하지 않았다.

선택 profile과 `--check-new-test-modules --base-ref HEAD`는 통과했다. 최종 local `all-local`은
Windows 290개 모듈·2,713건·150 worker 통과, 실패·오류·skip·expected failure·unexpected success·
미실행·timeout·missing module·미검증 source·잔류 자손 0이다. 기록은
`tmp/regression/batch02-targeted/run.json`, `tmp/regression/batch02-fixture-targeted/run.json`,
`tmp/regression/batch02-all-local/run.json`이다. 두 번째 묶음의 GitHub hosted Windows/Linux/
disposable PostgreSQL 결과도 [hosted run 37925187185](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37925187185)에서
통과했다. Windows artifact는 2,713건/150 worker/290개 모듈이며 local과 manifest hash가 같다.
Linux 65건/5개 모듈, disposable PostgreSQL 87건, schema 21의 저장 경계 63개와 rollback도 통과했다.
hosted worker failure/error/skip/unrun/leaked descendant는 0이다. NAS 운영 PostgreSQL은 이 결과에
포함하지 않으며 main 병합·NAS 배포도 하지 않는다.

## 2026-10-09 미등록 CI 선택 묶음 3

다음 정렬 구간 24개 모듈/113건을 순차 검토해 `dependency-audit-batch-03-historical-and-runtime-contracts`
profile에 편입했다. 이 묶음에는 historical news 수집·복구·사람 검토·누수 방지 평가, 연구 readiness와
reconstruction, investor flow, journal backup/projection, storage audit, DB trace/resource diagnostics가
포함된다. 파일별 이름과 검증 목적은 manifest profile에서 확인할 수 있다. 기존 CI 순서는 유지했고
선택 profile은 24 worker·113건 통과, failure/error/skip/unrun/worker 종료 이상 0이다.

의존성 검토에서 `test_historical_news_pc_jobs`가 CI 등록 뉴스 이력 테스트의 private `_article`
fixture를 import하는 결합을 확인했다. 입력 모양은 유지하면서 fixture를 해당 기능 테스트 안에 두었다.
같은 파일의 인증 API 검사는 이전에 `TestClient` 선택 의존성 오류를 skip으로 허용하는 형태였다.
기존 HTTPX `ASGITransport`와 앱의 lifespan을 사용해 요청 검증을 실행하도록 바꾸고, 인증 실패·제외 종목·
422 입력·claim·complete 결과 assertion을 보존했다. 같은 transport로
`test_import_historical_market_news_to_nas` API 검사도 실행한다. `httpx2`나 dependency 변경은 없으며,
직접 관련된 세 모듈 20건이 통과했다. 테스트를 HTTPX async 경로로 옮긴 뒤 deprecated TestClient
경고와 skip 조건이 없어졌다. 계약된 수신·DB 처리에 필요한 구현 경계 테스트는 유지했다.

최종 local `all-local`은 Windows 314개 모듈·2,826건·174 worker 통과, 실패·오류·skip·expected
failure·unexpected success·미실행·timeout·missing module·미검증 source·잔류 자손 0이다. 신규 테스트
모듈 누락 검사도 통과했다. 기록은 `tmp/regression/batch03-targeted/run.json`과
`tmp/regression/batch03-all-local/run.json`이다. [GitHub hosted run 37927654479](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37927654479)도
Windows 2,826건/174 worker/314개 모듈, Linux 65건/5개 모듈, disposable PostgreSQL 87건 및 schema
21 저장 경계 63개와 rollback으로 통과했다. Windows artifact manifest hash는 local과 일치하고
hosted failure/error/skip/unrun/leaked descendant는 0이다. 현재 미등록 Windows 후보는 94개다.
NAS 운영 검증·배포와 main 병합은 수행하지 않는다.

## 2026-10-09 미등록 CI 선택 묶음 4

다음 파일명 정렬 구간 24개/138건을 `dependency-audit-batch-04-market-news-and-storage-contracts`에
등록했다. 시장/종목 뉴스, API 설정·인증, DB 역할·revision·rollback, 프로세스 수명, OCR layout,
저장 진단, 데이터 계보·selection tests다. 이번 선택은 기존 consumer와의 기능 차이 및 로컬 전용
입력/DB fixture를 확인해 결정했다.

| 모듈 | 건수 |
|---|---:|
| `test_krx_stock_catalog` | 2 |
| `test_local_api_config` | 3 |
| `test_local_storage_diagnostics` | 1 |
| `test_market_index_chart_service` | 3 |
| `test_market_news_sources` | 2 |
| `test_market_news_window` | 3 |
| `test_market_profile_settings` | 17 |
| `test_market_research_features` | 7 |
| `test_market_session` | 6 |
| `test_materialize_historical_news_seed` | 2 |
| `test_minute_chart_service` | 9 |
| `test_nas_source_runtime` | 10 |
| `test_nas_storage_mapping` | 2 |
| `test_naver_news_config` | 2 |
| `test_naver_stock_market_news` | 9 |
| `test_naver_stock_news` | 3 |
| `test_news_classification_benchmark` | 10 |
| `test_news_grouping` | 10 |
| `test_news_process` | 6 |
| `test_news_settings_dialog` | 5 |
| `test_news_window_coordinator` | 4 |
| `test_paddle_theme_ocr` | 18 |
| `test_parse_historical_news_page_versions` | 2 |
| `test_plan_historical_monthly_case_selection` | 2 |

`test_market_profile_settings`의 인증 API 회귀는 `TestClient` 대신 HTTPX `ASGITransport`와 app lifespan
컨텍스트로 실행하도록 수정했다. 인증·요청 검증·읽기 응답·revision fail-closed assertion을 유지했다.
직접 관련된 17건과 24개 선택 profile 138건이 통과했다.

최초 local `all-local`은 2,964건 중 `test_historical_reconstruction`의 임시 경로
`.export-*/`를 `export`로 rename할 때 Windows `PermissionError` 한 건을 기록했다. worker exit와
process tree 종료는 확인했고 미실행은 없었다. 테스트 코드를 수정하지 않은 채 오류난 테스트 한 건과
모듈 8건을 다시 실행해 통과했다. 새로운 `all-local`도 Windows 338개 모듈·2,964건·198 worker
통과, failure/error/skip/expected failure/unexpected success/unrun/timeout/missing module/source
unverified/leaked descendant 0이다. 최초 rename 오류 원인은 직접 증거가 없어 확정하지 않았다.
실행 기록은 `tmp/regression/batch04-targeted/run.json`, `tmp/regression/batch04-all-local/run.json`,
`tmp/regression/batch04-all-local-retry/run.json`이다. 이 묶음의 hosted CI는 feature branch 게시 후
추가한다. 현재 Windows 미등록 후보는 70개이며 main 병합과 NAS 배포는 수행하지 않았다.

## 2026-10-09 미등록 CI 선택 묶음 5

다음 정렬 구간 24개 모듈을 검토하고, 공유 연구 fixture를 사용하는 4개 후속 모듈을 함께 확인했다.
28개 모듈/398건을 `dependency-audit-batch-05-research-campaign-and-recovery` profile에 넣었다.
이 중 `test_research_final_preparation`은 기존에 이미 등록돼 있어 실제 신규 편입은 27개 모듈/370건이다.
연구 campaign 입력·예산·등록·실행, 개발 검증과 final holdout 복구, process/queue 종료, DB 상태 저장과
failure recovery 계약을 보호한다. 기존 `test_research_process`와 `test_research_queue`도 fixture 결합
영향 검증을 위해 같은 targeted profile에 포함했지만 이미 core에서 실행되므로 all-local 집계에는 중복되지 않는다.

테스트 모듈끼리 request document, campaign request, queue spec helper를 import하던 결합을
`tests/unit/research_test_support.py`로 옮겼다. 영향을 받은 13개 모듈의 assertion 목록은 이동 전후
같다. `test_research_final_execution`에는 중첩되지 않은 fixture 속성 접근 오류가 있어, 준비된 평가의
`warmup_seconds`를 변경하는 실제 policy drift 입력으로 바로잡고 기존 거부 assertion을 유지했다.
이 변경은 제품 코드나 기대 동작을 바꾸지 않는다. transient Windows rename `PermissionError`가
`test_research_campaign_nas`에서 한 번 발생했으나 재실행과 전체 targeted run은 통과했다. 원인은 미확정이며
최초 실패 기록은 보존한다.

선택 profile은 이미 등록된 `test_research_final_preparation`과 core 두 모듈을 포함해 30개 모듈/415건 통과했다. 최종 fixture import 정리 후
`test_research_campaign_inputs`도 23/23 통과했다. 최종 전체 `all-local`은 Windows 365개 모듈/3,334건/
225 worker 통과, failure/error/skip/unexpected success/unrun/timeout/missing module/leaked descendant
0이며 worker process tree 종료는 225/225다. 신규 test module 누락 검사도 통과했다. 제한된 sandbox에서
Windows Proactor loopback socketpair 내부 accept가 멈춰 실행을 중단했지만, 동일 unchanged test는
사용자 Windows 실행 권한에서 통과했고 전체 검증도 그 권한에서 완료했다. 이는 테스트 실패로 계산하지 않는다.
실행 기록은 `tmp/regression/batch05-targeted-retry/run.json`,
`tmp/regression/batch05-focused-recheck/run.json`, `tmp/regression/batch05-all-local-elevated/run.json`이다.

B4 hosted run [37931617482](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37931617482)은
Windows 338개 모듈/2,964건/198 worker, Linux 전용 65건/5개 모듈, disposable PostgreSQL 87건과 schema 21
저장 경계 63개 및 rollback 검사를 통과했다. local/hosted manifest와 runner hash가 일치했다. 테스트 파일
13개의 raw hash 차이는 Windows CRLF와 hosted LF checkout 차이였고 CRLF 정규화 후 모두 같았다.

게시 commit `55935c1a677faffd677165205f240d2fdb1d375c`의 [B5 hosted run
37939207498](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37939207498)은 성공했다.
Windows 365개 모듈/3,334건/225 worker, Linux 전용 65건/5개 모듈, disposable PostgreSQL 87건과 schema
21의 63개 저장 경계 및 rollback 검사가 통과했다. Windows hosted와 local은 같은 profile manifest 및
runner hash, 365개 모듈 목록, 3,334건, 225 worker, 미실행 0을 보고했다. raw test-file hash 13개 차이는
LF와 CRLF checkout 차이만으로 모두 일치했다. artifact는
`tmp/regression/batch05-hosted/all-local-regression-37939207498/run.json`이다.

B5 검증 시점의 Windows 등록 범위는 365개, Linux 전용은 5개, Windows 후보는 43개였다. 현재 수치는 아래 B6 기록을 따른다. main 병합·NAS 검증 및 배포는 별도다.

## 2026-10-10 미등록 CI 선택 묶음 6

미등록 43개에서 연구 상태·symbol validation·설정·뉴스 페이지 선택·요청 용량 및 PostgreSQL 통합 진입점
등 24개 모듈/222건을 순차 검토해 `dependency-audit-batch-06-research-state-and-settings` profile에
편입했다. 중요 holdout·ledger·publication·cleanup·storage capacity 계약, partition과 symbol validation,
settings 저장·백업, 제한된 요청 lane 및 기존 PostgreSQL 통합 guard를 보호한다. 추가 편입 뒤 Windows
등록은 389개, Linux 전용은 5개, 남은 Windows 후보는 19개다. core 실행 순서는 바꾸지 않았다.

테스트 모듈 간 fixture 의존성도 확인했다. final-exposure CLI는 기존 CLI 테스트의 `TestCase` setup을,
independent-comparison dialog/process/partition 테스트는 다른 테스트 모듈의 seed·record·fixture를,
symbol validation dialog와 campaign storage/cleanup 테스트는 다른 모듈의 테스트 객체를 사용한다.
이번 단계에서는 이 연결을 기능 실패로 오인하거나 공용 추상화로 넓히지 않았다. 구체적인 실행 신뢰성 문제가
확인된 NAS 입력 fixture의 이동만 제거했다. campaign test fixture에 선택적 부모 경로를 추가하고 remote
dataset을 처음부터 감시 폴더 밖에 생성하도록 바꿨다. 기본 호출 경로와 데이터, assertion은 유지했다.

검증 결과:

- B6 선택 profile은 fixture 변경 전후 모두 24 modules/222 tests 통과했다. 변경 후 결과는
  `tmp/regression/batch06-targeted-after-fixture-change/run.json`이다.
- 입력/NAS/storage/staging 관련 5개 모듈 90건도 통과했다. 그중 NAS·staging fixture가 쓰던 경로 이동을
  제거했고, 전체 회귀에서 `test_research_campaign_nas` 23건도 모두 통과했다.
- 새 테스트 모듈 등록 guard와 `git diff --check`는 통과했다.
- 변경 포함 Windows `all-local`은 3,556건/249 worker를 모두 발견·실행했지만 **실패**했다. skip·미실행은
  0이고 process tree 249/249 종료, 누수 0이다. 첫 전체 시도에서는 `test_research_campaign_nas`의 임시
  directory rename이 `PermissionError`를 냈다. 이를 직접 이동하지 않는 fixture로 수정했다.
  후속 전체 실행에서는 이 모듈 23건이 통과했지만, 기존 `test_historical_learning_cases`와
  `test_historical_news_review_decisions`에서 각각 `os.replace`가 `WinError 5`를 반환해 2건 오류가 났다.
  두 모듈 전체 7건은 동일한 workspace-local TEMP 전략에서 별도 재실행해 통과했다. Windows가 해당 순간
  교체를 거부한 정확한 원인은 재현되지 않아 미확정이며, 이 전체 실행을 성공으로 세지 않는다.
  기록은 `tmp/regression/batch06-all-local/run.json`과
  `tmp/regression/batch06-all-local-final/run.json`이다.

게시 commit `0da3339f93c96f339ff7bec7cfe6898079576f5d`의 B6 hosted run
[37951316477](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37951316477)은
Windows all-local에서 `test_daily_high_service` 한 모듈이 실패했다. GitHub Windows runner의 UTC 날짜와
제품의 KST `daily_query_date()`가 날짜 경계에서 다를 수 있는데, fixture가 `date.today()`를 사용해
coverage basis와 저장 행 날짜가 제품 query date와 달라졌다. 제품 동작은 수정하지 않고 테스트 입력을
`daily_query_date()` 기준으로 맞췄다. 동일 모듈 9건과 B6 선택 profile 24개 모듈/222건이 통과했다.

날짜 fixture 수정까지 포함한 최종 Windows `all-local`은
`tmp/regression/batch06-all-local-post-ci-fix/run.json`에서 3,556건/249 worker 통과다. failure/error/skip/
unrun/timeout/missing module/source unverified/leaked descendant는 모두 0이고, worker process tree 종료는
249/249다. 수정된 `test_daily_high_service`도 최종 manifest에 포함됐다. 앞서 두 historical 모듈에서
관측한 일시적 `WinError 5`는 이 최종 실행에서 재발하지 않았다. B6 hosted 전체 CI는 수정 commit을 게시한
뒤 재실행해야 하며, 그 결과 전까지 hosted 검증은 미완료다. main 병합, NAS 운영 DB 검증·배포는 수행하지
않았다.

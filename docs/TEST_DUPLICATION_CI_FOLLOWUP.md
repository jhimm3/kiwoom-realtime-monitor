# 테스트 중복과 상시 CI 후속 판단

2026-10-09 · 분석 기준 `914635ca1c3b939761b6a43b38db403cf9d419f1`

## 범위와 현재 상태

기존 [409개 의존성 조사](TEST_DEPENDENCY_AUDIT.md)를 재사용해 중복 후보와 CI 편입 우선순위를
추가 검토했다. 제품 코드·기능 계약·기존 assertion·core 실행 순서는 변경하지 않았다.
이 후속 작업에서는 실행기 발견 증거를 강화하고 선정한 26개 테스트 모듈을 격리 profile로 등록했다.
최종 `all-local` 로컬 회귀는 통과했다. hosted GitHub CI는 아직 실행 전이다.

GitHub에서 위 commit과 `main`을 비교한 결과 `main`이 merge commit 2개 앞서 있고 파일 차이는
0개였다. 현재 `c32b`의 소스 내용으로 분석할 수 있다. 다른 작업 트리의 변경은 사용하지 않았다.

| 범위 | Windows all-local | 별도 Linux CI | 실제 CI 미편입 | 합계 |
|---|---:|---:|---:|---:|
| 원래 감사한 파일 | 183 | 5 | 221 | 409 |
| 이후 추가된 `test_diagnostic_trace_ram` | 1 | 0 | 0 | 1 |
| 후속 profile 적용 후 현재 파일 전체 | 210 | 5 | 195 | 410 |

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

현재 410개 파일 모두를 AST로 선별했다. 원래 409개는 전부 포함한다.

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

26개가 검증·편입된 현재 기준 Windows all-local은 210개 고유 모듈, 별도 Linux 5개를 포함한
상시 CI 모듈은 215개다. 원래 409개 조사 대상 가운데 Windows와 별도 Linux CI를 제외한
미등록 테스트 모듈은 195개다. 이 수치의 미등록은 영구 제외 판정이 아니다.

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
- 실행기 변경을 검증하기 위해 빈 테스트 모듈 누락 guard도 실행했다.
  `--check-new-test-modules --base-ref HEAD --fallback-ref HEAD`는 새 테스트 모듈 0개로 통과했다.
  이는 현재 작업 트리의 모듈 목록 확인이며 PR base 대비 GitHub 검증 결과를 뜻하지 않는다.
- 현재 `regression_profiles.json` manifest SHA256은
  `f8de91fa502e6933b9fc097cd1835223750ae833f6845e2b8bdf018ef1c7057e`, 실행기 SHA256은
  `bbc7c5dfa8bcf36f4e60a62d0ccfb26787e47eb570bd4b032a61fd613db3c7b4`다. 기존 core 실행 순서 hash는
  `36DAE599F514EC9356B7DC3EAB9C7B4F1033C4A27076EB8A7FCD931FBEB1810A`로 보존됐다.
- 검증된 변경을 게시한 뒤 GitHub Windows와 Linux/disposable PostgreSQL CI를 독립 검증으로 확인한다.
  이 로컬 결과는 hosted CI 성공이나 실제 NAS PostgreSQL 검증을 의미하지 않는다.
  NAS 배포와 `main` 병합은 이번 분석·구현의 완료 조건이 아니다.

완료된 후속 작업: 전체 정적 선별, 12쌍 수동 판정, 삭제할 완전 중복 0개 확인, 실행기 false-green
공백 수정, 26개 선택 profile 등록, 관련 fixture 결합 수정, 선택 profile 218건 통과, 전체
`all-local` 2,136건/70 worker 통과, 누락 guard 통과, 변경 목록과 CI 목록 대조다.
남은 195개를 모두 CI에 편입한 것으로 표시하지 않는다. GitHub hosted CI는 아직 실행되지 않았으며,
독립 환경 검증과 실제 PostgreSQL 검증은 완료 조건으로 남아 있다.

# 전체 테스트 최종 품질 감사와 CI 전환 설계

2026-10-10 KST. **감사·설계와 실행기·테스트·브랜치 CI 구현을 완료**했다.
통합 전 브랜치의 전체 hosted 검증은 통과했으며, 최신 main 통합 후의 최종 결과는
[PR #15의 검증 기록](https://github.com/jhimm3/kiwoom-realtime-monitor/pull/15/checks)으로 확인한다.
테스트 품질 작업은 제품 코드·DB 스키마를 변경하지 않았다. 폐기된 중간 visibility 기대값의
변경 근거와 보존한 최종 검증은 아래 PostgreSQL acceptance 보완 기록에 구분했다.
최신 main의 제품 변경은 그대로 통합했다. NAS 운영 배포는 이 테스트 작업의 완료 조건에 포함하지 않는다.

## 1. 이번 단계의 기준과 실제 검증 결과

작업 브랜치는 `codex/test-discovery-ci-followup`, 감사 기준 commit은
`8caf6221ec4d14704899415ee07a2ceaae38c735`이다. GitHub API로 확인한 main은
`4efd67239e0f332fa1c1ab2a62f3c39d60755636`, 두 소스의 merge-base는
`3c42285fc490cba4faffad46c7ad307453930780`이다. main의 동일 SHA 로컬 Git 객체도 대조했다.
아래 수치는 이 시점의 snapshot이며 이후 main 변경에 자동 적용되지 않는다.

| 범위 | 작업 브랜치 | 확인한 main |
|---|---:|---:|
| `tests/unit/test_*.py` | 413 | 417 |
| `tests/integration/test_*.py` | 17 | 21 |
| 실제 테스트 파일 합계 | **430** | **438** |
| Windows 등록 unit | 408 | 신규 4개 미등록으로 guard 실패 |
| 별도 Linux 등록 unit | 5 | 5 |
| 현재 hosted의 integration 모듈 | PostgreSQL access 1개 | PostgreSQL access 1개 |

이전의 “전체 413개”는 **unit 파일 수**였다. unit 미등록 후보 0개는 맞지만,
integration 17개 중 16개에 상시 hosted 경로가 없다는 별도 공백이 있었다.
`check_postgres_integration.py`의 실제 DB smoke도 현재 hosted에서 실행되므로
PostgreSQL 저장 의미가 전혀 검증되지 않았다고 해석하지 않는다.

| 실행 증거 | 결과 | 범위·한계 |
|---|---|---|
| [작업 브랜치 run 37972261647](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37972261647) | 두 job 성공 | 위 `8caf622` 정확한 commit, Windows source dirty=false |
| 같은 run Windows artifact | **3,670/3,670건, 408개 모듈, 268/268 worker** 통과 | failure/error/skip/expected failure/unexpected success/0건 모듈/미실행/timeout/잔류 자손 0, 모든 process tree 종료 |
| 같은 run Linux | **5개 모듈, 65/65건** 통과 | POSIX 검사; 현재는 별도 JSON 종료 증거 없이 job 로그에 기록 |
| 같은 run PostgreSQL | **87건** 통과 + DB smoke 성공 | 임시 PostgreSQL 17; NAS 운영 DB 결과 아님 |
| 직전 최종 로컬 `batch07-all-local-final-ci-fix/run.json` | **3,670건, 268 worker** 통과 | fixture 수정이 포함된 로컬 소스. 깨끗한 hosted commit 검증과 구분 |
| 이번 구현 로컬 `final-quality-implementation-local-verified/run.json` | **3,682/3,682건, 268/268 worker** 통과 | Windows 사용자 환경; 실패·오류·skip·미실행·timeout·잔류 자손 0, 작업자와 하위 프로세스 종료 확인. 시작·종료 LF 소스 지문 동일. 미커밋 수정본이므로 hosted 검증과 구분 |
| [첫 구현 CI run 37987257844](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37987257844) | **종합 실패** | `24aad5c`: Windows 전체 3,682건, Linux 65건, 필수 PG 119건, 확장 PG 24건, 확장 Windows 925건, 긴 Windows 및 Linux operator는 성공. 봉인 replay v1 9건 성공 후 v2 secret 접근 PermissionError로 중단. 종합 job은 이 실패를 거부 |
| 수정 후 로컬 `final-quality-ci-fix-local/run.json` | **3,682/3,682건, 268/268 worker** 통과 | `.gitkeep` 줄바꿈 지문 및 봉인 replay 임시 secret 소유자 수정 포함. 실패·오류·skip·미실행·timeout·잔류 자손 0, 시작·종료 LF 지문 동일. 수정 후 hosted 결과는 별도 확인 필요 |
| [main run 37954769147](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37954769147) | **Windows 실패 / PostgreSQL 성공** | Windows는 신규 모듈 guard에서 중단. main 전체 회귀 통과가 아님 |

main에서 누락된 unit은 `test_market_event_fact_delivery`, `test_observation_delivery`,
`test_observation_frame_recovery`, `test_replay_content_comparison`이다. 최신 제품 소스를 이 브랜치에
통합하지 않은 채 이 테스트 파일만 복사해 결과를 섞지 않는다. 후속 구현에서는 테스트 도구 수정부터
현재 브랜치에서 진행하고, 최종 source 통합 뒤 새 8개 파일과 기존 변경 테스트까지 검증해야 한다.

Windows artifact `11636533684`의 ZIP SHA256은
`81dcc1b5746ed1b9041adbd2c778397f177d19f5d9e140f5c852f476e1e44f04`다.
로컬 대조에서 408개 테스트 중 395개는 raw hash가 같고 13개는 CRLF/LF만 달랐다.
runner와 manifest raw hash는 같았다. 줄바꿈 이외 불일치는 없었다.
이 대조는 제품 전체 dirty 파일의 동일성을 증명하는 기능을 현재 runner가 갖췄다는 뜻은 아니다.

첫 구현 CI의 Windows와 Linux는 같은 976개 파일인데도 LF 소스 지문이 달랐다. Windows의
`tests/{contract,integration,unit}/.gitkeep` 세 파일은 checkout에서 CRLF이고 Linux에서는 LF였다.
`Path.suffix`가 `.gitkeep`에 대해 빈 문자열이어서 처음 지문 계산이 이 파일을 정규화하지 않았다.
수정은 해당 파일명만 텍스트 줄바꿈 정규화 대상으로 추가했다. 단위 검사에서 `.gitkeep`/Python/
Dockerfile의 LF↔CRLF는 같은 지문, 내용 변경은 다른 지문임을 확인했다. 봉인 replay v2는
0700/0600 임시 비밀 경계를 유지하면서 생성·검사 Python container를 host 임시 디렉터리 소유 UID/GID로
실행하도록 바꿨다. 로컬 Docker가 없어 이 권한 수정의 실제 통합 결과는 후속 hosted CI에서 판정한다.

[후속 CI run 37991461321](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37991461321)은
Linux 작업이 테스트 시작 전에 Docker Hub의 익명 이미지 pull 제한 `429 Too Many Requests`에 걸렸다.
필수·확장 PostgreSQL의 service container, 봉인 replay의 service container, Linux operator의
Python image 빌드가 같은 제한을 보였다. 이 작업은 기능 실패나 skip 성공으로 계산하지 않는다.
CI 전용 PostgreSQL 17 Alpine/Python 3.13 slim 이미지 참조를 ECR Public `docker/library` 경로로 바꿨고,
두 태그의 registry manifest가 HTTP 200으로 존재함을 읽기 전용으로 확인했다. 제품·NAS 이미지,
테스트 assertion, DB 계약은 변경하지 않았다. 변경 후 실제 hosted 실행 결과가 최종 판정이다.

## 2. 유지·중복·구조 판정

### 최신 main 통합 시 PostgreSQL fixture 결합 확인

통합 전 [run 37991977901](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37991977901)은
전체 9개 job이 성공했다. 이후 `4efd67239e0f332fa1c1ab2a62f3c39d60755636`을 통합하고,
main에서 빠졌던 unit 4개와 PostgreSQL integration 4개를 원장에 추가했다.
기존 unit 실행 순서·그룹을 유지했고, QueryStore API 101개·SQLite 105개·PostgreSQL 107개와
내부 위임 6개를 실제 소스 및 consumer 감사 결과에 맞춰 재계수했다.

[통합 run 37998172362](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37998172362)은
필수 PostgreSQL suite 151건 중 14건이 실패했다(오류·skip 0). 실패 파일은 bootstrap,
cursor-commit-order, sequence-fence 세 개다. 선행 `check_postgres_integration.py`의 cleanup은
`marker` subject만 삭제해 분봉의 `marker:KRX` revision을 남겼다. 또 기존 access suite의
여러 revision이 같은 DB에 있어, DB 전역 page/cursor를 검증하는 새 suite의 첫 페이지와
100회 frames-only recovery에 섞였다. 로그의 다른 subject와 복구 `rows_read=100`이 이를 확인한다.
격리 후 [run 37999161400](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/37999161400)에서는
151건 중 148건 통과, commit-order 3건 실패였다(오류·skip 0). 처음의 14건을 모두 fixture
결함으로 단정하지 않는다. 남은 세 건은 기존 frozen red의 중간 조건이었다: 낮은 COMMIT이
대기 중일 때 높은 peer를 전달하고 cursor를 앞서 기록할 것을 기대했다. 이는 main에 적용된
`RECORDED_WORKLOAD_EXPERIMENT_DESIGN.md`의 safe-prefix 계약과 충돌한다.
과거 red 보고서와 Git 원본은 보존하며, 현재 acceptance는 실제 peer COMMIT 확인 → pending
중 행/완료 checkpoint 공개 금지 → 두 입력의 정확한 순서·revision 전달 → 중복 0·재시작
checkpoint 일치·bootstrap 역사적 decision 0을 검증하도록 보완한다. 제품 동작을 변경하거나
실패를 expected-failure/skip으로 숨기지 않는다.

수정은 누락된 `marker:KRX` cleanup과 세 suite에만 적용하는 고유 schema fixture다.
fixture는 URL과 실제 연결 DB가 모두 `kiwoom_monitor_diagnostic_test`인지 확인하고,
기존 연결 옵션을 유지한 채 고유 schema를 search_path로 제공한다. class 종료 시 자신이 만든
schema만 제거하고 cleanup 오류는 실패로 전파한다. held native COMMIT/rollback, 페이지 수·순서,
revision 일치·재시작·결정 재계산 금지의 최종 검증 강도를 유지한다. 폐기된 조기 peer 노출
조건만 현재 문서의 명시적 pending 계약으로 대체한다.
공통 runner나 제품 저장 의미를 변경하지 않으며, 운영/NAS DB를 비우지 않는다.
수정본의 실제 전체 Windows·PostgreSQL·replay 결과는 위 PR의 최종 SHA에 게시된 CI로 판정한다.
`main-merge-final-dbec8f9` 로컬 전체 실행은 DB acceptance 보완 전 중단했으며 `incomplete`로
보존한다. 성공 수에 포함하지 않는다. 중단 후 해당 실행 경로를 가진 Python 부모·자식
프로세스가 없음을 확인했다. DB gate 통과 뒤 최종 소스로 전체 회귀를 다시 판정한다.

`a376176`의 PostgreSQL 151건은 통과했지만, 로컬 `main-merge-final-a376176`에서는
직접 접속 감사 1건이 실패했다. 새 `isolated_observation_schema`의 관리 접속이 승인 원장에
누락되어 `current=54 / approved=53`을 올바르게 거부했다. 이 실행은 중단 상태로 보존하며
전체 통과로 집계하지 않는다. URL·실제 DB 확인, 고유 schema 소유권, finally 정리와 오류 전파를
검토한 뒤 해당 함수의 접속 1개만 원장에 추가했다. 감사의 unapproved/stale 검증은 유지하며,
현재 callsite 수 assertion만 실제 검토된 54개로 재계수한다. 관련 감사·fixture 검사부터 재실행하고,
이 보완을 포함한 최종 commit에서 로컬 전체 및 hosted CI를 다시 확인한다.

[run 38001326253](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/38001326253)의
Windows 전체 3,725건/272 worker는 누락·skip 없이 실행됐으나 시장 fact cadence 검사 1건이 실패했다.
해당 worker 로그에 `held_save`의 10초 만료 `TimeoutError`와 정상 재시도 후 native 호출 4회가
확인됐다. 1,001번의 1ms yield가 느린 Windows 타이머에서는 10초를 넘길 수 있다.
같은 입력에서 15.625ms yield를 주입하자 19.238초 실행 중 동일한 fixture 만료를 재현했고,
retry 완료 시점 차이로 pending 3개 assertion도 실패했다. 제품 재시도 결함으로 해석하지 않는다.
해당 cadence 검사만 hold timeout을 60초로 조정하고, release 전 실패 0회·native 호출 1회
assertion을 추가한다. 원본 입력·해시·drop 0·pending 3·최종 native 호출 3·두 status 저장
검증은 그대로 유지한다. 다른 failure/ACK-loss fixture의 10초 한도는 변경하지 않는다.
수정 후 동일한 15.625ms cadence 주입은 **19.447초에 통과**했고 native 호출 3회·drop 0이었다.
시장 fact·관측 전달·프레임 복구의 관련 세 모듈 **26건도 30.503초에 통과**했다.
`main-merge-final-0795231` 로컬 전체는 수정 전에 중단해 `incomplete`로 보존한다.
전후 동일 cadence 재현과 관련 검사부터 확인하고, 최종 변경 전체가 포함된 로컬·hosted 결과로 판정한다.

[409개 의존성 원장](TEST_DEPENDENCY_AUDIT.md), [260개 보호 계약](REGRESSION_COVERAGE_AUDIT.md),
[후속 선택 편입·실패·개선 기록](TEST_DUPLICATION_CI_FOLLOWUP.md)을 재사용했다.
기존 149개도 동일 기준으로 감사했던 결과를 유지하며, 현재 통과한다는 이유로 구조가 적절하다고 추정하지 않았다.
이번에는 추가 integration 실행 경계와 아래 고위험 경로만 상세 대조했다.

| 필요성 분류 | 브랜치 430개 | main 추가 8개까지 반영한 제안 | 판단 |
|---|---:|---:|---|
| 영구 유지·매 PR 필수 | 283 | 289 | 기존 core/초기 안전 profile과 핵심 실패·복구·DB 계약 |
| 조건부 유지·자동 확장/정기 검사 | 147 | 149 | 유효한 CLI·연구·UI·운영·장시간·특수 환경 계약 |
| 일회성 후보 | 0 | 0 | 현재 보호 목적이 없는 것으로 입증된 파일 없음 |
| 삭제·통합 확정 후보 | 0 | 0 | 검증 강도가 유지된다는 증거 없음 |
| 용도 미확정 파일 | 0 | 0 | 연구 시간 경계의 정책 문제는 아래 별도 보류; 파일의 필요성은 확인 |

조건부는 삭제 예정이나 skip 허용이라는 뜻이 아니다. 모두 자동 실행 시점과 환경을 부여한다.
430개 각각의 소속은 [실행 범위 제안 JSON](TEST_CI_TIER_PROPOSAL.json)에 기록했다.
이 JSON은 **설계 snapshot이며 CI에서 읽지 않는다**. 현재 CI가 읽는 배정 원장은
`tests/ci_groups.json`이다. 기존 원장에 있는 파일별 보호 계약을 중복 작성하지 않았다.

- 완전히 같은 AST 파일/테스트 body, 덮어쓴 테스트 이름, import로 중복 발견된 TestCase를 찾지 못했던
  기존 조사 결과를 재사용했다. 유사한 12쌍도 backend·controller·입력/실패 경계가 달라 유지한다.
- `test_theme_color_repository`의 core 두 위치 실행은 기존 순서/격리 계약이다. core는
  143회 호출/142개 고유 모듈이며 순서 hash
  `36dae599f514ec9356b7dc3eab9c7b4f1033c4a27076eb8a7fcd931fbeb1810a`를 보존한다.
- 작은 모듈을 합치거나 긴 DB suite를 쪼개야 할 **실제 개선 근거는 이번 상세 대상에서 확인하지 못했다**.
  저장→실패→rollback→재시도 흐름은 하나의 상태 전이 계약일 수 있다. 줄 수만으로 분리하지 않는다.
- 파일 구조와 worker 격리는 별개다. 현재 core 두 worker 및 추가 모듈별 worker를 유지한다.
- 개선 완료한 final-preparation/뉴스·연구 support/작업 종료 fixture 변경은 유지한다.
  real-account realtime→monitor→reads의 setup 참조와 mock runner의 수동 TestCase 생성은 남아 있지만,
  이번에 누수나 기능 오류를 입증하지 못했다. 해당 fixture 변경 시 필요한 소비자만 함께 검사한다.
- API TestClient의 선택적 의존성 안내만으로 새 패키지나 호환 패치를 추가하지 않는다.
  현재 run의 skip은 0이다. 실제 로딩 실패가 생기면 지원되는 HTTP/ASGI 경로로 원인을 재현한다.

## 3. 실제 수정 대상과 보호 계약

### P0 — 실행기의 성공 판정과 누락 검사

현재 코드의 실제 함수를 호출한 한정 재현으로 다음을 확인했다. PG 연결은 전부 mock 처리했으며
실제 DB에 접속하지 않았다. 상세 출력은 `tmp/regression/final-quality-audit/runner-gate-probes.json`이다.

| 재현 | 현재 결과 | 필요한 변경 |
|---|---|---|
| PostgreSQL suite 0건 | exit 0 | suite별 발견 수 > 0, 발견·실행 수 일치 요구 |
| PostgreSQL skip 1건 | exit 0 | skip을 성공에서 제외 |
| PostgreSQL expectedFailure 1건 | exit 0 | 예상 실패도 완료된 계약 검증으로 세지 않음 |
| Windows 빈 named profile | passed, worker 0, exit 0 | manifest 및 실행 계획 양쪽에서 빈 선택을 거부 |
| 기존 unit의 manifest 등록 삭제 | 신규 파일 guard는 누락을 보고하지 않음 | 신규 차분에 더해 전체 현재 목록의 실행 경로 검사 |
| 새 integration 파일 | 신규 unit guard 대상 밖 | unit/integration 양쪽의 미배정·중복 배정·없는 파일 탐지 |

수정 위치는 `scripts/run_postgres_access_integration.py`, `scripts/run_regression.py`,
각 기존 runner unit test다. PostgreSQL의 전용 DB 이름 확인·읽기 전용 preflight는 유지한다.
로드 오류/expected failure/unexpected success/중단/timeout도 별개 항목으로 기록하고 nonzero로 끝낸다.
선택되지 않은 확장 group은 planner의 `not_selected`이며, 실행한 suite의 `skipped`를 성공으로 치환하지 않는다.

기존 Windows worker의 모듈별 발견 수·실행 수, import source, Job Object 자손 종료 검증은 유지한다.
전체 catalog 검사에는 제외가 필요한 유효 파일도 환경·실행 group을 반드시 기록한다.
새 파일이나 기존 등록 삭제가 조용히 통과하지 않도록 mutation/음성 사례로 확인한다.

### P1 — 주문 취소 실패의 검출력

`application/order_lifecycle.py:73-88`은 결과 불명과 명시 거절을 구별하지만
`test_order_lifecycle`의 fake cancel은 항상 성공한다. 현재 SQLite와 실제 lifecycle을 실행한
한정 probe로 기존 계약을 확인했다. live 주문은 보내지 않았다.

1. `SubmissionUnknown`: CANCEL_STARTED 이후 CANCEL_RESPONSE_UNKNOWN, CANCEL_PENDING 영속.
   저장소를 다시 열어 반복 취소해도 물리 전송이 추가되지 않고, 새 broker snapshot 대사로 해소한다.
2. `SubmissionRejected`: CANCEL_REJECTED 후 미체결이면 ACCEPTED, 일부 체결이면 PARTIALLY_FILLED.
   재시작 후 **사용자의 명시적 재시도는 가능**하다. 결과 불명의 재전송 차단과 혼동하지 않는다.
3. 이후 snapshot에서 취소/최종 체결을 반영하고 기존 fill을 중복 누적하지 않는다.

기존 `test_order_lifecycle.py`의 fake에 실패 선택을 추가하고 상태·사건·전송 횟수·재시작·대사 결과를
각각 assertion으로 보강한다. product 수정은 불필요하다. 한정 결함 대조군에서 결과 불명의 fallback을
ACCEPTED로 바꾸거나 실패 사건 저장을 누락하면 새 검사가 실패해야 한다. 원본 source를 지속 변경하지 말고
프로세스 내 주입 또는 임시 복사본에서 실행한다. 정상 결과와 의도된 실패를 별도 기록한다.

### P1 — 실제 PostgreSQL·재생·Linux 검사의 실행 공백

기존 SQLite와 fake tests는 DB 의미를 상당 부분 보호하지만 실제 PostgreSQL의 storage boundary,
revision/metadata/완료 표식 rollback·동시성, market-event commit/ACK, TOP20 저장 종료 검사는 현재 hosted에
직접 등록되지 않았다. 기존 tests를 해당 환경에 연결하는 작업을 새 제품 기능보다 우선한다.

| integration 경계 | 브랜치 파일/정적 선언 | main 파일/정적 선언 | 자동 실행 배정 |
|---|---:|---:|---|
| 전용 diagnostic PostgreSQL | 10 / 143 | 14 / 175 | 필수 4→7개, 확장 6→7개 |
| 봉인된 replay v1/v2 | 6 / 20 | 6 / 20 | 관련 변경 + 주간 종합 |
| Linux operator 권한/소유 | 1 / 21 | 1 / 21 | 관련 변경 + 주간 종합 |
| 합계 | **17 / 184** | **21 / 216** | 전부 명시 경로 보유 목표 |

이 표의 선언 수는 실행 성공 수가 아니다. 소스와 기존 gate를 확인했으며 새 환경 검사는 아직 실행하지 않았다.
main의 신규 observation bootstrap/cursor commit order/sequence fence 세 suite는 실제 DB 전달·복구 계약으로
필수에 배정한다. replay content comparison 한 suite는 확장에 배정한다.

### 제품 판단이 필요한 별도 보류

`historical_news_event_split.py:53-63,79-82,308-313`은 사건의 **첫 기사 시각**으로 구간을 정한다.
기존 문서도 그 정책을 명시한다. 합성 사건의 후속 기사를 2월 1일로 늦추면 TRAIN 사건에 그 기사가 남고
VALIDATION의 첫 기사는 1월 2일인 계획이 생성된다. `historical_news_development_inputs.py:54-71`도 후속
기사를 유지한다. 기존 test는 같은 날 사건을 사용하여 이 경우를 규정하지 않는다.

이는 **시간 구간이 교차하는 입력의 정책 미정 사항**이다. 실제 데이터의 누수나 현재 문서 위반을 확정한
제품 결함으로 보고하지 않는다. 엄격한 시간 분리가 필요한 연구에서 허용·purge·거부 중 무엇을 요구할지
별도 결정한 후 테스트를 보강한다. 이번에 임의의 기대값이나 제품 동작을 넣지 않는다.
기존 사건 원자성/OOS 봉인 검사는 required로 유지한다.

과거 `test_historical_learning_cases`와 `test_historical_news_review_decisions`의 `os.replace` WinError 5는
이후 단독·전체 검사에서 재발하지 않았지만 원인이 미확정이다. 해결된 flaky로 표시하지 않는다.
다시 발생하면 경로·열린 handle/소유 작업·종료 시점·실행 순서를 기록하고, 재실행 성공으로 실패를 덮지 않는다.
동일 값 실시간 tick을 일괄 중복 제거해야 한다는 근거도 확인하지 못했다. 현행 volume 계약을 유지한다.

## 4. 세 단계 CI의 확정 설계

아래 범위를 작업 브랜치 workflow에 연결했다. **현재 main의 required 설정에는 새 집계가 등록되지 않았으며,
이번 변경의 hosted 결과도 아직 확인하지 않았다.** Windows의 기존 필수 job은 전환 안전을 위해
계속 전체 `all-local`을 실행한다.

| group | 브랜치 파일 | main 반영 후 | 실행 |
|---|---:|---:|---|
| required-windows | 274 | 277 | 매 PR/브랜치 push; core + 기존 안전 profile 전체 + 추가 중요 30개 |
| required-linux | 5 | 5 | 매 PR; 기존 POSIX 5개 유지 |
| required-postgres | 4 | 7 | 매 PR; access/storage/market-events/TOP20 + main observation 3개 |
| extended-windows | 132 | 133 | 관련 변경 + 매일 |
| extended-windows-long | 2 | 2 | TOP20/replay·공통 경계 변경 + 주간 |
| extended-postgres | 6 | 7 | 관련 저장·연구·기록 변경 + 매일 |
| extended-sealed-replay | 6 | 6 | 기록/replay/TOP20·공통 경계 변경 + 주간 |
| extended-linux-operator | 1 | 1 | 운영 도구·권한·공통 경계 변경 + 주간 |

종합은 위 group의 **합집합**이다. 주간·릴리스 전·수동 comprehensive 요청에서 전부 실행한다.
nightly는 필수와 일반 확장까지, weekly는 특수 환경까지 포함한다. 주간 실행일에는 nightly를 추가 중복하지 않는다.
workflow는 기본 브랜치에 적용되기 전까지 정기 자동 실행 완료라고 보고하지 않는다.

Windows required의 244개 기존 core/안전 profile 선택은 제안 JSON에 명시했다. 추가 30개의 보호 이유도
같은 JSON에 기존 원장으로부터 옮겼다. 이 중 뉴스·연구 18개는 수집 미완료/원자성, 미래정보/OOS,
최종 실행 단일 소유, orphan 복구, staging 삭제 경계를 보호한다. 나머지는 DB 연결/출처, 실시간 파싱,
계좌별 일지 투영, 설정 revision, 비동기 종료, 실행기 안전 경계다. 개수나 속도로 선정하지 않았다.

### 목록과 변경 선택의 단순한 구현 경계

- `tests/regression_profiles.json`의 기존 `core_batches`/`profiles`를 보존하고 별도
  `tests/ci_groups.json`에 환경별 선택만 추가했다. 별도 범용 framework나 fixture 계층을 만들지 않았다.
  활성 profile manifest와 CI 배정 원장을 함께 검사해 전체 파일의 실행 경로를 판정한다.
- Windows 계획은 기존 `all-local` 계획을 group으로 필터한다. core 두 batch를 유지하고 나머지 singleton
  worker 순서도 유지한다. 확장에 required 모듈을 다시 넣지 않는다. 기존 로컬 profile 이름/순서도 유지한다.
- 현재 flat unit/integration만 보지 말고 해당 디렉터리 아래 `test_*.py`를 재귀 조사한다. 새 파일·삭제·이름 변경,
  없는 module, 중복 환경 배정, 발견되지 않는 함수형 테스트, 빈 group을 CI 계획 단계에서 거부한다.
  유효한 다중 환경 반복은 환경과 이유를 명시한다.
- 처음부터 복잡한 동적 import 분석기를 만들지 않는다. 소수의 경로 규칙과 명시적 환경 group을 사용한다.
  첫 버전의 일반 Windows 확장은 **문서만 바뀐 경우 외의 코드/테스트 변경에서 전체 132개**를 선택한다.
  기존 worker 합계 약 101초라 fixture 의존성 추적 시스템보다 이 보수적 선택이 적절하다.
- 장시간/replay/PostgreSQL/operator 제외는 명확한 좁은 UI/문서 변경 등 검토된 allowlist에서만 허용한다.
  `database*`, 공통 모델, realtime/TOP20, 기록/재생, runner·manifest·공용 support/fixture·의존성·workflow 변경은
  관련 특수 환경까지 포함한다. 알 수 없는 경로/동적 연결, rename/delete, base 미확인, 규칙 충돌은 종합으로 보낸다.
  테스트 간 fixture import를 좁게 추적한다고 주장하지 않고, test/support 변경은 종합 선택으로 안전하게 포함한다.
- selection 결과에는 기준/head SHA, changed paths, group별 선택 이유와 미선택 이유를 남긴다. docs-only도
  required job은 실행한다. workflow 전체를 path filter로 생략해 required check가 사라지게 만들지 않는다.
- fork PR도 운영 secret 없이 disposable 환경에서 검사한다. 실행할 PR 코드에 권한을 주는
  `pull_request_target` 경로를 추가하지 않는다. 실패를 `continue-on-error`로 숨기지 않는다.

### 환경별 실행·종료

1. **Windows:** 현재 Job Object 기반 종료, workspace-local TEMP, 모듈별 발견/실행 비교를 유지한다.
2. **Linux POSIX:** 기존 5개를 PostgreSQL job의 Python 프로세스에서 순서대로 실행하고 JSON 결과를 남긴다.
   모듈별 발견·실행 건수와 skip/expected failure를 판정한다. job timeout/cancel은 hosted runner가 처리하며,
   현재 별도 자손 프로세스 종료 증거는 기록하지 않는다.
3. **direct PostgreSQL:** required/extended job마다 새 `kiwoom_monitor_diagnostic_test` DB를 준비한다.
   `check_postgres_integration.py`로 스키마/smoke를 준비하고 명시한 suite를 **직렬** 실행한다.
   같은 DB에서 suite 병렬화를 새로 도입하지 않는다. preflight DB 이름, positive discovery, skip=0을 요구한다.
4. **sealed R1:** 별도 임시 cluster의 `/postgres` 관리 URL과 0700 secret directory를 사용한다.
   `check_recorded_replay_baseline.py --provision --secrets-directory <dir>`는 baseline 4건도 실행한다.
   이어 `--execution-gates --secrets-directory <dir>`가 execution 5건을 검사한다.
   direct extended에서 capture 4건을 실행하므로 `--recorded-capture-gates`를 중복 호출하지 않는다.
   선택한 provision/execution 두 모드에는 diagnostic DB를 별도로 만들 필요가 없다.
5. **sealed R2:** 또 다른 **빈 cluster**와 custom fixture ID, 0600 secret을 만든 뒤
   `check_replay_cache_baseline.py --top20-lifecycle`을 실행한다. baseline4/cache3+3/runtime3/session2,
   총 15건이다. R1/R2 합계는 **20개 고유 테스트/24회 실행**이며 별도 cluster의 baseline4 반복은 의도적이다.
   기존 NAS shell은 운영 경로에 결합되어 hosted에서 그대로 호출할 수 없다. hosted 전용 얇은 wrapper가
   기존 Python gate의 empty-cluster/owner/source-clock/restore/skip 검사를 유지하고, network-none 또는
   격리 namespace, tmpfs, 메모리/PID 제한, 운영 mount 없음, label·ID 확인 후 소유 container 정리를 담당한다.
   secret이나 관리 DSN은 artifact에 넣지 않는다.
6. **operator:** `deploy/synology/check-nas-operator.sh`에 검증된 로컬 image ID를 제공한다.
   root·network-none·read-only·cap-drop·tmpfs 경계에서 unit nas_operator와 integration21을 실행한다.
   Windows의 portable unit과 다른 환경이므로 이 반복은 중복 삭제 대상이 아니다.

R2/operator에 쓰는 Python/runtime 이미지는 해당 checkout SHA와 lock 파일로 빌드하고 ID를 고정한다.
operator gate는 scripts/tests를 mount해도 제품 src는 이미지에서 import하므로 설치 패키지와 checkout의 Python
파일을 비교한다. workflow에 빌드·비교 단계는 구현했으며 hosted 실행 결과는 아직 확인하지 않았다.
operator의 Docker socket, host affinity CPU 2개 이상, cgroup 메모리 제어 전제도 hosted에서 확인한다.
운영 NAS의 active image를 재사용하거나 변경하지 않는다.

R2와 operator hosted wrapper가 검증되지 않은 상태를 종합 CI 완료로 표시하지 않는다.
호스트 취소/timeout에서도 정리 단계는 항상 실행하고, 정상 종료 증거가 없는 결과는 incomplete로 남긴다.
Docker가 없는 로컬 Windows에서는 이 항목을 미실행으로 보고하고 hosted 결과를 따로 사용한다.

### 결과·소스 일치와 보호 규칙

- 현재 raw hash를 보존하면서 텍스트 source/runner/manifest/support/test의 LF 정규화 hash도 기록한다.
  바이너리 fixture와 golden byte 계약은 raw hash로만 판정한다. 실행 시작·종료의 source fingerprint가
  다르면 incomplete다. 실제 import 경로, Python/dependency 환경, Git SHA/dirty 상태를 함께 남긴다.
- reporter는 planned module/test IDs와 실제 시작·완료 목록, 실패 종류, worker/tree 종료 결과를 비교한다.
  정상 pass/실패/선택 안 됨/환경 준비 실패/실행 중단을 분리한다. 로그만 존재하거나 artifact가 누락되면 성공이 아니다.
- `Required regression` aggregate는 `if: always()`로 하위 작업 실패·취소 때도 실행하고,
  신뢰한 planner가 요구한 모든 group의 job 결과와 source/plan이 일치하는 report를 검사한다.
  선택된 확장 작업이 실패해도 병합을 막는다. 예상한 job이 skip되거나 report가 없으면 실패다.
  전체 workflow가 취소되어 aggregate 자체가 게시되지 않는 경우에도 required context 미충족으로 병합이
  차단되는지 확인한다. `always()`가 workflow 취소 후 결과 게시까지 보장한다고 가정하지 않는다.
- GitHub branch API에서 현재 main `protected=true`, enforcement `non_admins`, required contexts
  **Windows all-local regression / Disposable PostgreSQL integration**을 확인했다.
  상세 protection API는 **403**이어서 strict/up-to-date/admin bypass/review 설정은 확인하지 못했다.
- 전환 1차에서는 기존 두 context를 유지한 채 새 planner/report/aggregate를 도입한다.
  Windows 기존 full 결과를 group별로 분류해 새 aggregate의 전환 검증에 활용하여 같은 SHA의 Windows suite를
  두 번 실행하지 않는다. PostgreSQL 기존 context는 보강된 required PG 실행을 계속 보장한다.
- 새 aggregate가 실제 실패/취소/누락 사례를 차단하고 comprehensive가 통과한 뒤, 관리자가 branch protection에
  `Required regression`을 추가하여 저장한다. API/화면으로 확인한 뒤 기존 Windows context만 해제한다.
  `Disposable PostgreSQL integration`은 계속 유지한다. 이후 commit에서 Windows를 tier 실행으로 전환하고
  old full 중복을 없앤다. 관리자 확인 전에는 기존 required job 이름이나 실행 범위를 바꾸지 않는다.
  사용자 조치는 구체적인 새 check가 게시되고 검증된 뒤 마지막 설정 단계로 요청한다.

## 5. 예상 비용, 구현 순서와 종료 조건

현재 hosted Windows 전체 runner는 **601.414초**, job은 의존성 설치 등을 포함해 약 **12분 12초**였다.
로컬 최종 전체는 약 **26분 29초**다. 환경 차이가 있으므로 동일한 성능 측정값으로 섞지 않는다.

| 제안 lane | 기존 run에서 해당 worker 합계 | 기존 실행 건수 |
|---|---:|---:|
| required Windows 274개 | 351.371초 (약 5분 51초) | 2,739 |
| 일반 extended Windows 132개 | 100.744초 (약 1분 41초) | 925 |
| long Windows 2개 | 147.526초 (약 2분 28초) | 6 |

이는 **분리 실행의 실측값이 아닌 이전 worker 시간 합산 추정**이다. setup·동시 runner 수급·추가 테스트·main
변경·새 PG 환경 비용은 포함되지 않았다. 기존 PG87건은 10.684초였지만 추가 suite/R1/R2의 실행 시간은
미측정이다. 현재 90분 Windows/45분 PostgreSQL job 상한을 우선 유지하며, 시간 때문에 suite를 제외하지 않는다.
TOP20 장시간 두 모듈의 실제 시간 계약을 임의로 축소하지 않는다.

후속 구현·검증은 **GPT-6 Sol High**로 다음 순서를 따른다. Luna는 사용하지 않는다.

1. P0 실행기 판정과 전체 catalog guard, 취소 실패 세 경계 테스트는 브랜치에 구현했다.
   관련 runner 39건과 주문 14건이 통과했고, 고의 상태 결함 두 가지는 새 테스트에서 실패했다.
2. selector/report 및 환경 job은 브랜치에 구현했다. docs/화면/DB/unknown 선택과 빈 목록·등록 누락은
   로컬 단위 검사로 확인했다. R1/R2/operator는 각 hosted 격리 환경에서 아직 실행하지 않았다.
3. 공통 실행 기반 변경을 포함한 local `all-local`은 3,682건/268 worker 통과했다. 시작·종료 소스 지문,
   모든 worker의 계획·실행 수, 종료·누락·skip 판정이 일치했다.
4. 최신 source 통합이 필요하면 별도 범위로 처리하고 테스트 8개 추가와 기존 변경을 재계수한다.
   main에 병합하지 않는다. 현재 branch만 검증했으면 그 한계를 남기고 latest-main 완료로 보고하지 않는다.
5. 최종 변경 파일·manifest·source fingerprint를 포함한 local all-local과 hosted comprehensive를 실행한다.
   작은 수정마다 full을 반복하지 않는다. 실패 수정 후에는 관련 검사부터, 공통 기반/최종 경계에서는 full을 실행한다.
6. branch protection 전환을 마지막으로 확인하고 새 필수/확장/종합의 실제 건수·시간·주기·미선택 이유를 기록한다.

검증 완료 전 성능 개선율을 선언하지 않는다. audit 생성기·ZIP·로그·합성 DB는 tmp 진단 자료이며 commit하지 않는다.

종료 조건은 catalog의 모든 유효 파일에 자동 실행 경로가 있고, 중요 실패/복구 gap과 runner false-green이
해소되며, final source에서 Windows/Linux/disposable PG/특수 환경 결과와 선택 누락이 정확히 구분되는 것이다.
새 required check 보호 적용도 확인해야 **CI 전환 완료**라고 보고한다. 시간 구간 정책과 원인 미확정
WinError 5는 근거·담당 후속 조건을 갖춰 별도 보류할 수 있지만 “전체 기능에 공백이 전혀 없다”고 주장하지 않는다.
live Kiwoom 주문, NAS 운영 DB/배포는 이번 성공 수에 포함하지 않는다.

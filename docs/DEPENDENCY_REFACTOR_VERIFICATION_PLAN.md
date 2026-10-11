# 기능 보존 검증의 현재 판정

정리 기준: **2026-10-11**. 현재 소스 SHA·실제 배포 release·검증 결과의 공통 정본은 [CURRENT_STATUS 상단 표](CURRENT_STATUS.md#current-summary)다. 아래 판정은 이 계획의 완료 범위와 다음 검증만 다룬다.

| 범위 | 현재 판정과 남은 검증 |
|---|---|
| 검증 진입점·뉴스 조회 분리 | 완료. 기존 검사 목록, 실패 판정, 뉴스 세 경로의 ASGI/실제 HTTP 응답과 DB 보존 비교를 확인했다. |
| 후속 구조 변경 | API 라우트/WebSocket, 서버 수명 A+B, 최소 저장 계약, TOP20 writer, PC 최종 저장 실패 보완의 로컬 검증 완료. 당시 소스의 최종 `all-local` 3,758건·272 worker 통과. |
| 후보 감시 예외 종료 | 오류 checkpoint 저장 중 반복 종료의 조기 완료를 임시 SQLite에서 재현하고 기존 native drain으로 보완. 관련 35건 및 NAS Linux 실행 통과. 실제 운영 장애로 단정하지 않는다. |
| 실시간 누락 품질 | 범위→분봉/저장→합계·차트·체결 입력과 저장 ACK 복구를 구현. 관련 검사와 현재 전체 로컬 검증 통과. 구형 NAS·불충분한 빈 분 근거는 복구 대기를 유지한다. 실제 NAS/PG 검증은 남는다. |
| health 부분 업무 관측 | 실제 저장 중/실패/ACK 유실·같은 종류 재시도·waiter 취소, 7종 저장 완료, 후보군 현재 만료/마지막 평가 분리, ASGI 공개/인증·동적 후보 교체를 포함한 관련 92건 통과. 미계측은 unknown/null이며 전체 업무/queue 대기 시간·실제 NAS 검증은 별도다. |
| 모의 상태 공개 읽기 | 관련 72건, 원래 HTTP 3건을 포함한 Windows 전수와 NAS Linux/배포 health 확인 완료. 실제 계좌별 모의 업무 상태 변화의 장중 관찰은 별도다. |
| 현재 전체 로컬 검증 | 최종 E 3,914건·279 worker 전체 실행 중 파일 교체 오류 1건으로 최초 실패. 동일 소스의 해당 worker 549건을 AppData temp에서 전부 재검증해 통과했고, 원본을 보존한 전체 범위 보고서와 현재 소스 대조 통과. 새 제품 commit의 Actions에서도 3,914건·279 worker 전부 성공했다. 자동 재시도·실패 허용은 추가하지 않았다. |
| hosted 자동 실행 | [제품 소스 `44af48d`](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/38137039403)의 9개 job 실제 성공. Windows/Linux/PG 동일 커밋·정규화 소스 지문 대조 통과. main 필수 검사에 Required regression을 추가해 3개 확인. 관리자 예외 등 기존 정책은 유지한다. |
| 다음 검증 | NAS PG required 163건·extended 24건·후보 종료 14건·native API·Linux 필수 65건과 최종 storage 114건, 제품 소스 배포·실제 health/source·동일 조건 예약 재등록 확인 완료. 실제 장중 누락/복구·수동 앱·장시간·public operator Replay·성능은 OPEN_ITEMS O12와 기능별 잔여를 따른다. |

검증은 실행 SHA/소스 지문·입력·환경과 함께 기록한다. 관련 검사 수를 전체 회귀 수로 합산하거나, 이전 소스·통제 입력의 통과를 현재 운영 환경의 완료로 옮기지 않는다. 현재 절차는 아래에, 이전 실행 근거는 날짜별 기록에 유지한다.

## 목표 경계와 보존할 의미

| 경계 | 책임과 의존성 | 변경 시 함께 검증할 의미 |
|---|---|---|
| NAS 조립 | `app.create_app`이 설정, 저장소, 서비스 생성과 연결을 소유 | 같은 설정의 활성 기능, 인증, 실제 연결 인스턴스 |
| API | 기능별 라우트가 요청 검증과 응답 변환을 소유하고 필요한 조회 계약만 사용 | 경로/메서드, 상태 코드, 필드/단위, 인증 실패, 입력 제한 |
| DB | 기존 QueryStore와 두 backend를 유지하고 소비자별 필요한 계약만 좁힘 | per-call 연결, transaction/rollback, lock, revision, 완료 표식, wake-up |
| PC | MainWindow가 표시, AppController가 작업자 수명과 조정, 기존 feature controller가 해당 작업을 소유 | 신호 전달과 스레드, 최신 결과, 중복 시작, 종료 전 저장, 재시작 |
| TOP20와 실시간 | 현재 owner와 task/queue 경계를 보존하며 독립 가능한 책임만 단계적으로 분리 | 순위 시각, 구독 ACK/READY, 수신 공백, pending 복원, commit 전후 재시도 |

AppController를 단순 전달자로 바꾸지 않는다. 현재 독립 상태와 종료 책임이 있는
controller를 이름이나 크기 때문에 재분리하지 않는다. Protocol은 의존성 범위를
표현할 뿐 transaction이나 실패 격리를 자동 보장하지 않는다.

각 변경은 `호출자 → 처리/저장 owner → 테이블/키 → 조회자 → API/화면/후속 작업`을
한 줄로 적고, 같은 입력의 값·순서·부수효과를 전후 비교한다. 테스트용 대역은
외부 시스템과 실패 주입에 쓰고, 저장 동작 검증은 실제 임시 DB를 사용한다.
정적 호출 수나 mock 호출 성공만으로 기능 보존을 판정하지 않는다.

## 현재 검증 진입점

검사 목록의 정본은 `tests/regression_profiles.json`, 단계/환경 선택은 `tests/ci_groups.json`, 실행과 발견/등록 검사는 `scripts/run_regression.py`다. 실제 CI job은 `.github/workflows/`를 따른다. 문서의 이전 파일/worker 수를 새 소스의 전체 수로 사용하지 않는다.

```powershell
.\.venv\Scripts\python.exe scripts/run_regression.py --list
.\.venv\Scripts\python.exe scripts/run_regression.py --check-test-catalog
.\.venv\Scripts\python.exe scripts/run_regression.py --profile pc-lifecycle --output tmp/regression/<새-실행-ID>
.\.venv\Scripts\python.exe scripts/run_regression.py --profile all-local --output tmp/regression/<새-전체-실행-ID>
.\.venv\Scripts\python.exe -m scripts.check_required_ci_results --windows-only --local --windows tmp/regression/<새-전체-실행-ID>/run.json
```

독립 프로필·Windows/Linux·disposable PostgreSQL·NAS operator 검증을 서로 대신하지 않는다. 현재 환경의 HTTP/ASGI 경로를 사용하고 `httpx2` 추가·버전 변경·인증/요청 검사 생략은 하지 않는다. 통제 SQLite/Qt/ASGI 검사와 실제 NAS/PostgreSQL·수동 UI·장시간 acceptance를 구분한다.

### 실행과 결과 판정

1. 자식 cwd를 저장소 루트로 고정하고 `src`, 루트, `tests/unit` 경로를 명시한다.
   실제 `kiwoom_monitor` 로딩 위치가 이 워크트리의 `src`인지 확인한다.
   실행별 임시 디렉터리를 만들고 `TEMP/TMP/TMPDIR`, `QT_QPA_PLATFORM=offscreen`을 지정한다.
2. 결과에 commit, 변경 유무, 테스트 대상 파일의 hash, Python 실행기/버전, 실제 소스 경로,
   프로필, 시작/종료시각, batch별 실행 수·실패·오류·skip·expected failure·unexpected success,
   종료 코드와 로그 위치를 기록한다. 환경변수 전체나 접속 문자열은 저장하지 않는다.
3. 테스트 발견 0건, import/의존성 오류, assertion 실패, unexpected success,
   worker 결과 파일 누락/손상, timeout, 중단, 비정상 프로세스 종료는 성공이 아니다.
   worker가 `OK`나 통과 JSON을 썼어도 실제 프로세스 종료 코드가 0이 아니면 실패다.
4. skip 또는 expected failure가 있으면 `incomplete`로 보고하고 비영 종료한다.
   실행기에는 이를 성공으로 바꾸는 허용 목록이나 `ignore-failures` 옵션을 만들지 않는다.
   의도적인 환경별 제외는 해당 기능을 포함하지 않는 별도 프로필로 표현한다.
5. batch 제한시간은 명시된 양수 `--timeout-seconds`로 제어하며 기본값은 batch당 1800초다.
   Windows worker의 실행 과정에서 멈추면 worker와 그 자손 프로세스 전체를 종료하고,
   실제 종료된 PID를 확인한 뒤 `timeout`을 기록한다. 시작 직후 종료 전 자손을 놓치는
   경쟁 조건도 검사한다. 종료를 확인하지 못하면 남은 batch를 시작하지 않고 보고한다.
   정상 종료한 batch의 실패는 기록한 뒤 다음 독립 batch를 실행할 수 있다.
6. 최종 상태는 `passed/failed/incomplete`다. 필수 batch가 모두 실행되고 실패·미검증 없이
   종료한 경우만 `passed`, exit 0이다. 부모는 첫 batch 전에 예정 목록을 `run.json`에 남기고
   완료 때마다 진행과 남은 목록을 갱신한다. 프로세스가 강제 종료돼 최종 보고가 없으면
   최신 상태와 남은 batch가 `incomplete`로 남는다.

실행기 회귀는 정상 1건, 실패, import 오류, 0건, skip, expected failure,
unexpected success, 결과 작성 후 비정상 종료, timeout, 사용자 중단, 소스 경로 불일치를 포함한다.
Windows에서 timeout과 중단 때 worker가 만든 자식과 손자 프로세스가 모두 종료됐는지
별도 생성시각과 PID를 가진 fixture로 확인한다. 종료 확인에 실패하면 다음 테스트를 실행하지 않고
기존 로그와 미완료 batch 목록을 남긴다.
목록 이관 검사는 기존 143회 호출과 두 batch의 순서·중복 구성이 유지됨을 확인한다.

## 단계별 근거

- [2026-10-11까지의 분리·보존 계약과 실행 기록](archive/2026-10-11/history/DEPENDENCY_REFACTOR_VERIFICATION_PLAN_through_20261011.md).
- [현재 CI 계약과 최종 감사](TEST_FINAL_QUALITY_AUDIT.md).
- [현재 완료 판정](CURRENT_STATUS.md#current-summary) · [실제 잔여](OPEN_ITEMS.md).

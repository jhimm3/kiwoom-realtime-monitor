# NAS 제한 운영 명령 구현 계약

2026-10-08 · NAS Linux gate 45개 통과, 최초 설치 및 실제 `k379` 무암호 status 검증 완료.

`kiwoom-nas status`로 설치 상태를 확인했다. 운영 릴리즈와 두 컨테이너는 그대로다.
이번 무암호 명령은 반복 개발 기간에만 사용하고, 개발 종료 때 권한을 회수한다.

## 목적과 선택

반복 개발에서 SSH로 `kiwoom-nas test`, `replay`, `deploy`, `rollback`을 호출할 수 있도록
한다. 최초 설치만 관리자가 수행하고, 이후 고정 명령은 `sudo -n`으로 호출한다.
Docker 전체, shell, Python 전체를 NOPASSWD로 열지 않는다.
개발 종료에는 `kiwoom-nas revoke`를 실행한다. 새 상시 서비스나 만료 timer를 추가하지 않는다.

**root가 실행하는 코드는 설치 시 검토한 supervisor뿐이다.** 새 release의 Python·shell은
host root에서 실행하지 않는다. release 코드는 테스트/replay 컨테이너 또는 명시적으로
배포한 기존 운영 서버 컨테이너에서 실행한다. 배포 권한은 운영 앱·DB에 영향을 줄 수 있는
권한이며, 이를 단순 읽기 권한이라고 설명하지 않는다. 컨테이너 격리가 커널 취약점까지
제거한다고 주장하지 않는다.

기존 source-runtime bind mount는 유지한다. 새로운 운영 서비스, 상시 daemon,
웹 배포 API, 자동 reload, 운영 DB 재생성은 만들지 않는다. 공유 폴더의 Compose나
shell을 root가 실행하는 방식은 사용하지 않는다.

## 확인한 기존 경계

- `deploy/synology/source-runtime.sh`: 공유 경로의 script/Compose를 사용한다. `test`는
  운영 서버의 `docker exec`에서 candidate test runner를 실행한다. NOPASSWD 대상으로
  그대로 지정하지 않는다. 기존 수동 관리자 절차는 삭제하지 않는다.
- `scripts/nas_source_runtime.py`: manifest/file hash/build/contract 검사를 재사용할 기준이다.
  `check`는 candidate import를 실행하므로 host root에서 호출할 수 없다. 현재 Python 구문도
  NAS host Python 3.8과 다르다. host supervisor는 Python 3.8 stdlib로 작성한다.
- `scripts/check_replay_cache_baseline.sh` 및 `.py`: 외부망 없는 임시 PostgreSQL과 candidate
  worker의 기존 fixture/acceptance 경로다. host shell을 candidate에서 가져와 실행하지 않고,
  검토한 container 생성·정리 절차를 supervisor가 소유한다.
- `diagnostic_replay_database_cli.py`: baseline/owner token/복원 및 recorded 실행 계약을 유지한다.
  신규 operator가 별도 SQL replay engine을 만들거나 기존 connection/commit 경계를 바꾸지 않는다.
- `diagnostic_workloads.diagnostic_run_lock`: `diagnostic-run.lock`의 nonblocking flock이다.
  main 및 현재 활성 후보 `...e1cc01dde5bacbb9`의 trace drain은 지연 저장과 최종 manifest 처리 후
  잠금을 해제한다. diagnostic run도 동일 잠금을 사용한다.
- **잠금 해제만으로 안전 판정하지 않는다.** trace 저장 실패 시 RAM이 남아 있어도 finally에서
  잠금을 해제할 수 있다. API 상태와 retained/pending 카운터를 함께 확인해야 한다.
- 유지보수의 저장 완료 판정은 `off` 또는 최종 manifest 동기화 뒤 공개되는
  `complete`/`incomplete` 상태다. 종료 상태에서는 모든 retained/pending 카운터가 알려진 0이고
  비음수 정수 `accepted == written`이어야 한다. 입력 거부로 replay coverage가 부족한
  `incomplete`도 이 조건을 충족하면 설치를 허용한다. `failed`/`interrupted`는 허용하지 않는다.
  이 판정은 replay 자격을 부여하지 않으며 trace 등록의 무손실·무거부 검사는 유지한다.

## 설치와 신뢰 경계

예정 파일:

| 저장소 파일 | 책임 |
|---|---|
| `scripts/nas_operator.py` | Python 3.8 stdlib, 인자 검증·잠금·검사·고정 Docker 실행·정리·배포 복구 |
| `deploy/synology/install-nas-operator.sh` | 최초 관리자 설치, 보호 경로/설정/sudoers 검증·백업·복구 |
| `deploy/synology/kiwoom-nas` | 사용자가 호출하는 작은 client; 절대 경로의 helper에 `sudo -n` 전달 |
| `tests/unit/test_nas_operator.py` | portable argv/manifest/state machine/recovery/실패 주입 검증; 26개 통과 |
| `tests/integration/test_nas_operator_linux.py` | Linux directory-fd, 권한, symlink/hardlink, atomic write, lock 검증 |
| `deploy/synology/check-nas-operator.sh` | 고정 로컬 image ID를 쓰는 network-none 임시 컨테이너 acceptance |

host helper/launcher는 `/usr/local/libexec/kiwoom-nas/`와 `/usr/local/sbin/kiwoom-nas-root`,
설정은 `/etc/kiwoom-nas/operator.json`, 사설 상태는 `/volume1/@kiwoom-nas-operator/`에 둔다.
모두 root 소유로 설치하며 non-root 쓰기를 금지한다. parent 경로와 Synology ACL까지 검사한다.
보호되지 않는 경로나 실행기면 설치 실패로 처리하고 권한 검사를 생략하지 않는다.
현재 NAS의 `synoacltool -get`은 POSIX 권한 모드에 대해 종료 코드 255와 단일 stdout
`(synoacltool.c, 596)It's Linux mode`를 반환한다. 이 정확한 응답 형식만 허용하고 target의
root 소유·group/other 쓰기 금지·regular file/directory·non-symlink·file hardlink 금지를
확인한다. 부모 경로의 protected Tree 검사도 유지한다. 다른 nonzero 응답, 경고 또는
stderr는 허용하지 않으며, ACL 모드에서는 기존 non-root 쓰기 권한 거부 검사를 유지한다.

설정에는 허용 UID/계정명, 검증한 Python/Docker/sudo 절대 경로와 권한 검증 방식,
runtime/PG image **ID**, 서버·DB container
ID 및 허용 mount/network fingerprint, NAS project/진단 경로, test profile, resource/timeout
상한을 저장한다. token/DSN 전체를 출력하지 않는다. `.env`를 shell source하지 않는다.
설정·supervisor·image·container 신원 갱신은 최초 설치와 같은 관리자 작업이다.

설치기는 reviewed bundle을 보호 위치로 복사·재검증한 뒤 sudoers를 마지막에 적용한다.
기존 파일 백업과 실패 시 정확한 이전 파일 복원을 수행한다. `visudo`가 있으면
candidate에 `visudo -cf`, 적용 후 전체 `visudo -c`를 실행한다. 실제 NAS sudo 1.9.5p2에는
`visudo`가 없으므로 `native_fixed_rule` 방식도 지원한다. installer/self-update는 NOPASSWD
범위에 넣지 않는다.
대상 계정은 설치 때 확정한 한 계정이며, Docker 그룹 가입이나 전체 Docker 권한은 추가하지 않는다.
이미 설치 완료된 operator의 백업을 재설치로 덮어쓰지 않는다. 같은 sudoers 파일이 기존에
있으면 설치를 거부하고 관리자 판단을 요구한다. 설치 snapshot format 2는 원래 파일과
새 설치 파일의 hash·mode를 함께 보존한다.

`native_fixed_rule`은 일반 sudoers 문법 검사기를 대신하지 않는다. 검증된 소문자 계정명과
고정 launcher 경로로 생성한 한 줄의 규칙만 허용한다. 적용 전 native `sudo -n -l -U USER`가
경고 없이 읽히고 기존 launcher 규칙이 없음을 확인한다. 적용 뒤에는 동일 검사에서
`(root) NOPASSWD: <고정 launcher>`가 정확히 한 번 나타나야 한다. 종료코드 0이어도 stderr,
누락·중복·다른 권한 태그·모호한 목록은 실패다. 저장된 규칙 bytes도 재확인한다.

두 방식 모두 마지막에는 installer의 자식 프로세스에서 대상 계정의 supplementary groups와
real/effective/saved UID/GID를 완전히 적용하고 `sudo -k -n <고정 launcher> status`를 실행한다.
캐시된 인증을 무시하고 대화식 비밀번호를 금지한 실제 실행의 성공 JSON과 active release까지
확인해야 설치 완료다. root 자신의 권한 목록만으로 NOPASSWD를 승인하지 않는다. 설치 중
operator lock은 유지된다. 실패하면 새 sudoers 규칙부터 제거하고 이전 파일·mode를 복구하며,
복구 뒤 정책 검사도 실패하면 prepared journal을 보존해 관리자 복구가 필요함을 표시한다.
인증과 정책 검사 출력 원문은 공개 보고서에 넣지 않는다.

sudoers에는 고정 root launcher 한 경로만 등록한다. 인자 생략은 '인자 금지'가 아니라 모든
인자 허용이므로 **helper의 엄격한 인자 검사**가 필수다. NAS sudo 버전에 의존하는 정규식
정책 대신 parser에서 unknown argument, 임의 path/env/command/image/container/DSN을 거부한다.
launcher는 검증된 절대 Python으로 `-I -S` 실행하고 cwd·환경을 초기화한다. subprocess는
argv 배열과 제한 환경을 사용하고 shell 실행, Docker context/env override를 허용하지 않는다.

## 명령 계약

| 명령 | 동작 |
|---|---|
| `status` | release/build, capture 상태, 진행 작업, blocker를 비밀 없이 출력. 제어 변경 없음 |
| `test RELEASE --profile NAME` | 허용된 검증 profile을 임시 DB/worker에서 실행. skip도 pass와 분리 |
| `replay RELEASE TRACE --baseline ID ...` | 등록된 고정 입력/기준 상태와 선택 옵션으로 전용 실험 실행 |
| `deploy RELEASE` | 검증된 동일 runtime/schema 계약의 release 선택 후 기존 server만 재시작 |
| `rollback` | 사설 journal에 기록된 검증된 직전 release로 같은 배포 절차 수행 |
| `revoke` | 개발 종료 시 무암호 sudoers 규칙 회수 및 사용자 client/root launcher의 설치 전 상태 복구 |

`test`에는 arbitrary shell 대신 설치된 profile 또는 검증한 `tests.*` dotted test 이름만 허용한다.
추가 test 인자와 resource 상한은 supervisor가 결정한다. 입력이 코드로 실행되는 곳은 격리된
worker이며 운영 서버/운영 DB 환경변수를 전달하지 않는다.

`replay`는 유한한 시간 구간, 허용 mode/workload/component, 양의 concurrency 상한만 받는다.
미지원 workload·잘못된 baseline·window·coverage는 실행 전에 명시적으로 실패한다.
전체/단독/제외/조합과 descendant 제외는 기존 recorded executor가 소유한다.
결과를 맞추기 위해 제외한 workload의 과거 결과를 주입하지 않는다.

## 개발 종료와 권한 회수

`kiwoom-nas revoke`에는 path·shell·resource 인자를 받지 않는다. operator flock을 획득해
진행 중인 job/deploy와 충돌하지 않게 하고, 설치 snapshot의 고정 대상·hash·mode 및
백업을 먼저 검증한다. 삭제/복구 대상은 `/etc/sudoers.d/kiwoom-nas-operator`,
`/usr/local/bin/kiwoom-nas`, `/usr/local/sbin/kiwoom-nas-root` 세 경로다.
기존 client/launcher가 있었다면 원래 bytes·mode를 복구한다.

prepared journal을 durable하게 기록한 다음 sudoers 규칙을 먼저 삭제·fsync하고 설치 때
고정한 정책 검사를 실행한다. `visudo` 방식은 `visudo -c`, native 방식은 경고 없는 사용자
정책 목록에서 고정 launcher가 사라졌음을 확인한다. 그 뒤 client/launcher를 복구하고
revoked를 기록한다. 정리 실패나
취소 뒤에도 NOPASSWD 규칙을 다시 설치하지 않는다. prepared/revoked 상태는 일반 변경
명령을 차단한다. 중단 후 재시도는 이미 복구된 파일도 검증하여 같은 결과로 수렴한다.

권한 회수는 운영 API의 정상 응답이나 CPU controller 지원에 의존하지 않는다. 운영
서비스와 데이터에 접근하지 않는 고정 파일 정리이므로 capture를 중단하거나 서버를
재시작하지 않는다. 실행 중인 operator와 미완료 job/deploy는 먼저 drain/복구해야 한다.

관리자 복구용 root-owned supervisor/config/백업과 사설 reports·trace·baseline은 보존한다.
이는 무암호 실행 경로가 아니다. 회수 후 실패한 정리를 재시도하려면 관리자가 기존
일반 sudo 인증으로 검증된 설치 Python과 보호 경로의 `nas_operator.py revoke`를 호출한다.
재설치와 사설 진단 자료 삭제는 별도 관리자 작업이며 자동 수행하지 않는다.

## 입력 수입과 경로 안전

PC stage는 기존 공유 `source-runtime/releases`에 candidate를 게시한다. helper는 이를
신뢰된 executable이 아닌 **입력 데이터**로 읽어 사설 admission 경로에 동결 복사한다.
release ID 문법, file allowlist, manifest/build/contract/content hash를 모두 검증한다.
manifest가 맞다는 사실만으로 shell이나 root 코드 실행을 허용하지 않는다.

path traversal, symlink(모든 구성요소), special file, hard link, 허용되지 않은 소유자를
거부한다. Linux dirfd/O_NOFOLLOW와 fd stat을 사용하고 크기/파일 수/전체 복사 상한을 둔다.
복사 도중 변경도 검출하여 사설 snapshot 전체를 재검증한 후 원자적으로 게시한다.
artifact 출력은 고정 사설 job ID 디렉터리에만 생성하고 candidate 반환 path를 root 작업에
사용하지 않는다. root cleanup은 자신이 만든 사설 job과 일치하는 container label/ID만 대상으로 한다.

trace와 baseline도 등록 시 사설 read-only snapshot으로 고정하고 checksum과 version을 기록한다.
운영 `server-data` 전체를 test worker에 mount하지 않는다. source/trace/baseline ID는 데이터
식별자이며 host path 또는 Docker bind 인자가 아니다. 등록 입력이 없으면 명확히 실패한다.
기존 controlled fixture 등록을 제공할 수 있지만 `source_state_equivalent=false`를 유지한다.
실제 시작 상태가 없는 trace를 자동으로 '장중 동일 상태'로 승격하지 않는다.

## capture 보존과 동시 실행

변경/고부하 명령 순서: 사설 operator flock → 운영 mount 검증 → 기존 diagnostic-run flock
nonblocking 획득 → authenticated API 재검사 → 실행 → 결과/정리 확인 → 잠금 해제.
같은 host bind inode임을 검사하고, 잠금 파일을 unlink/replace하지 않는다.
API에 잠금을 확인한 뒤 놓고 다시 배포하는 check-then-act 구조를 쓰지 않는다.

master/trace/run 활성, workload pause, `running/stopping/awaiting_persistence/persisting`,
미배출 queued/pending/copy/retained, 실패 상태의 남은 RAM, 상태 조회 실패/신원 변경은 blocker다.
`off` 또는 정상 durable terminal만 허용한다. `failed`의 자동 discard/stop은 제공하지 않는다.
저장 완료 시각으로 추측하거나 master TTL 만료로 안전하다고 판단하지 않는다.
일반 operator 명령은 diagnostic control을 OFF로 바꿔서 길을 만들지 않는다.

기존 NAS 예약 starter flock도 확인하고 예약/대기 starter가 있으면 작업을 거부한다.
현재 one-shot lock 외에 향후 scheduler가 추가되면 같은 운영 fence 계약을 먼저 연결한다.
해당 검사가 아직 지원되지 않는 예약 형태에서는 자동 배포를 허용하지 않는다.
관리자가 기존 수동 sudo 절차로 fence를 우회하는 경우까지 helper가 막는다고 주장하지 않는다.

## test/replay 컨테이너

이미 설치된 image ID만 사용하며 pull/build/임의 entrypoint 옵션은 받지 않는다.
임시 PG는 `--network none`; worker는 해당 임시 PG의 network namespace만 공유한다.
운영 network, socket, host PID/IPC, 장치, 운영 secrets/data, Docker socket을 mount하지 않는다.
candidate source/trace/baseline은 read-only, workspace/report/tmp만 작업별 writable이다.
worker는 non-root, no-new-privileges/cap-drop 및 memory/CPU/timeout 상한을 적용한다.
이 NAS에서는 CFS quota가 지원되지 않아 `--cpuset-cpus`를 사용한다. 설치 시 허용 CPU 중
최대 2개를 worker에 지정하고 PG는 그중 1개만 사용한다. 두 job의 합계도 같은 2개 코어
안에 머무른다. 이는 코어 독점이나 CPU 사용시간 quota를 뜻하지 않는다. 설치 전 별도
컨테이너 probe, 실제 PG의 Cpus_allowed_list, worker affinity 및 Docker 설정을 검증하며
메모리 제한도 컨테이너 내부에서 확인한다.
PG 초기화에 필요한 권한은 별도 고정 profile로 두고 worker 권한과 혼합하지 않는다.
지원 안 되는 필수 격리/메모리 제한은 gate 실패로 처리한다. NAS의 pids 제한 미지원은 별도
표시하고, timeout/메모리 한도를 실제 확인하며 이를 지원됨으로 출력하지 않는다.

correctness gate는 기존 tmpfs PG를 사용할 수 있다. **스토리지 성능 비교는 동일 NAS 디스크의
사설 PG 경로**에서 한다. RAM PG 결과를 운영 WAL/스토리지 latency 개선 근거로 쓰지 않는다.
report에는 storage profile, image/source/input/baseline hash, clock, concurrency, 실제 test 수,
skip, cleanup, source_state_equivalent, 지원/미지원 workload를 기록한다.
OS cache 및 운영 NAS의 외부 I/O가 달라질 수 있는 한계도 유지한다.
후보 stdout의 `passed`만 신뢰하지 않고 프로세스 exit와 외부 resource/cleanup 검증을 구분한다.
기존 executor의 DB lease·baseline restore·lost-ack drain 계약을 보존한다.

## deploy/rollback 상태 전이

운영 container/image/mount/command/network가 설치 시 승인한 fingerprint와 다르면 중단한다.
동일 runtime/dependency/schema 계약만 자동 배포한다. `pyproject.toml`, Dockerfile 계약,
`central_schema.py`, `schema_migrations.py` 변경은 별도 관리자 절차다. candidate의 migration
script를 supervisor가 운영 DB 자격증명으로 먼저 실행하는 우회로를 만들지 않는다.

1. 사설 snapshot 검증 및 exact-source gate 결과 확인. 현재/이전 release, DB container ID,
   운영 source store의 inode를 기록하고 durable journal을 쓴다.
2. 같은 운영 mount의 새 release 디렉터리에 snapshot을 안전하게 게시한다. 공유 부모 경로를
   root executable 신뢰 경계로 취급하지 않으며, fd 기준 한정 파일 작업만 수행한다.
3. 고정 server ID를 graceful stop하고 실제 정지를 확인한다. stop 미완료 시 선택 파일을 바꾸지 않는다.
4. anchored dirfd에서 `active.json`을 atomic replace·fsync하고 고정 server를 start한다.
5. `/health` build, 실제 PID1 source path, release/hash, DB container 불변을 검사한다.
   HTTP ready와 실시간 재구독/데이터 무손실은 별개로 보고한다.
6. 성공 후에만 previous/active journal을 확정한다. 실패 시 server 정지 확인 후 이전 snapshot으로
   복귀하고 health를 다시 확인한다. 복귀도 실패하면 stopped/needs_admin으로 남긴다.

공유 source store의 기존 편집 권한을 완전히 회수하는 작업은 이번 범위에 넣지 않는다.
그 경로의 runner/app는 항상 **컨테이너 코드**이며 host에서 import/source하지 않는다.
수동 파일 변경을 통한 운영 코드 교체 권한까지 없애는 보안 시스템으로 설명하지 않는다.
공유 경로 변경이 감지되면 fail closed한다. 기존 source-runtime.sh와의 동시 작업도 별도
deploy.lock에 참여하여 거부하고, 잠금을 다른 구현으로 교체하지 않는다.
중간 host process 종료는 journal을 남긴다. 다음 변경 명령은 먼저 journal과 실제 container/
pointer를 대조하여 복구하고, 모호하면 자동 진행하지 않는다. 무조건 restart하는 trap은 금지한다.

## 구현 순서와 완료 기준

1. 인자/config/path admission·잠금·Docker argv를 로컬 failure-injection으로 검증한다.
2. 격리 test/replay job, report, timeout/cancel/cleanup을 구현한다. 기존 fixture/실행기를 사용한다.
3. 같은 container를 사용하는 deploy/rollback journal과 실패 복구를 구현한다.
4. 설치기·client·실행 안내와 root-protection/visudo 복구 검증을 완성한다.
5. PC에서 가능한 회귀를 실행하고 Linux/NAS 전용 미검증 항목을 정확히 남긴다.
6. 녹화 및 durable persistence 완료를 재확인하고 NAS 격리 acceptance,
   최초 설치, 실제 최소 명령 검증을 수행한다. 사용자의 설치 진행 승인은 확보됐다.
7. 개발 종료 시 `revoke`로 임시 무암호 실행 규칙을 회수한다.

필수 공격/회귀 사례: shell metacharacter/option injection, inherited DOCKER_HOST/PYTHONPATH,
symlink/hardlink/parent 교체, manifest 중도 변경, 임의 mount/DSN, 다른 UID, 부족한 memory/disk,
동시 명령, capture start-vs-deploy, failed trace retained RAM, 예약 대기, 권한/ACL 이상,
DB container 불변, commit-ack loss, child timeout, label mismatch, stop/start/health/rollback 실패,
active pointer/journal fsync 실패, interrupted install/visudo 실패와 이전 상태 복원.
권한 회수에는 unlink/restore/final journal 취소·실패, visudo 오류·timeout, 변경된 파일·백업,
기존 sudoers 충돌, active operator 잠금 및 회수 후 일반 변경 명령 차단을 검증한다.
skip만 있는 suite 및 테스트 로딩 오류를 성공으로 처리하지 않는다. `httpx2`를 추가하지 않는다.

Linux gate와 최초 NAS 설치 acceptance가 완료됐다. 현재 capture의 `input_rejected`가 0이
아니므로 그 trace는 전체 lossless replay 입력으로 등록되지 않는다. 전용 명령의 실제
test/replay/deploy 실행 acceptance는 아직 별도다.

근거: [sudo 공식 sudoers 문서](https://github.com/sudo-project/sudo/blob/main/docs/sudoers.man.in),
[Docker 공식 보안 문서](https://docs.docker.com/engine/security/).
NAS에 실제 설치된 버전/ACL/cgroup 지원은 설치 acceptance에서 별도로 확인한다.

# NAS 제한 운영 명령 구현 계약

2026-10-08 · NAS Linux gate 45개 통과, 최초 설치 및 실제 `k379` 무암호 status 검증 완료.

2026-10-09 identity-fence 보완: Docker inspect의 `Mounts` 배열은 순서가 고정되지 않는다.
새 fingerprint는 모든 mount 필드를 보존한 채 객체 순서를 정규화하고, 이미 설치된 root-owned
legacy approval은 mount가 7개 이하일 때 순열을 제한 비교한다. container ID/image/config,
mount source·destination·RW나 네트워크가 실제로 바뀌면 계속 거부한다. 이 보완은 기존 승인 설정을
수정하지 않는다.

`kiwoom-nas status`로 설치 상태를 확인했다. 운영 릴리즈와 두 컨테이너는 그대로다.
이번 무암호 명령은 반복 개발 기간에만 사용하고, 개발 종료 때 권한을 회수한다.

## 목적과 선택

2026-10-10 최종 recorder 릴리즈6390e0b7de1a5189의 exact storage gate105/105 통과,
같은 앱 코드의 account/lease/VI/large native Replay17입력 x2와 baseline restore 통과 후 배포 완료.
운영 PG 보존, 실제 health/build/source path를 확인했다. 기존 f448 storage3오류는 클래스 patch가
설치된 instance wrapper를 지나치던 테스트5줄로 수정했고 실패 기록은 보존했다.
root deploy의 active.json mode0600 때문에 기존 일반 계정 예약 preflight가 PermissionError를
냈다. 현재 scheduler는 고정 NAS root에서 이 오류만 승인된 읽기 전용 kiwoom-nas status로
대체한다. 파일/권한/sudoers는 바꾸지 않았다.16관련 검사와 실제 NAS 읽기 preflight가 통과했다.
현재 standalone 예약 소스: artifacts/nas-scheduled-trace-operator-status-20261010.py,
고정 시작 파일: artifacts/arm-recorder-monday-status-20261010.py,
계획: artifacts/nas-trace-start-20261012-storage-fixtures.plan.json.
상태/lock은 기존 operator fence가 인식하는 nas-trace-start-20261012.status.json[.lock]이다.
실제 NAS preflight와 예약 활성화 완료: PID2815,10/12 08:59:50~09:59:50 KST/20:10 저장,
5개 capture flag·코드/계획 hash·SSH 종료 후 동일 PID 확인. 현재 제어/trace OFF, 서버 정상이다.
NAS 재부팅 뒤에는 이 one-shot 프로세스가 유지되지 않는다. helper test/deploy는 예약 lock을
존중하므로 예약 중 우회 실행하지 않는다. 실제 미래 녹화/저장 결과는 별도 수용 판정한다.
이하의 과거 비활성/미설치/진행 중 상태는 해당 시점 기록이며 현재 실행 지시가 아니다.

2026-10-10 capacity jobc9def38d8077b4880e7853ffb0f3ef48은 실패했다(worker exit1,
RuntimeError/OOM=false). cleanup/정확한 운영 서버 복구/health=ok/운영 PG 보존은 확인됐다.
아래 실행 중 기록은 이전 표본이다. lifecycle complete를 gate 합격으로 해석하지 않는다.
worker의 제한 failure_reason을 supervisor report에 보존하는 수정은 관련71건 통과했고,
이후 관리자80건 갱신 및 실제 설치 hash 확인을 완료했다. 고정 root 전용 숫자 로그 명령:
`sudo python3 /volume1/docker/kiwoom-monitor/artifacts/read-recorder-failure-20261010.py`.
운영 제어나 로그 원문 공개 없이 마지막 capture 건수/메모리와 최종 결과 출력 유무를 확인한다.
권한 제한 때문에 이 읽기는 관리자 실행이 필요하다. 원인 판별 전60분 반복은 하지 않는다.
해당 관리자 읽기는 완료됐다: 마지막71,001메시지/대형119회/큐0·정상 여유,
최종 결과 출력 없음. 실패-code helper 후보f8b5ec1d6f44d410는 NAS58건을 통과하고
정리/fence/운영 보존을 확인했다(job31df1001576192365da01ced4f631af3).
부재 packaging module을 지정한 이전 로딩 실패는 따로 보존하며 합격으로 세지 않는다.
갱신 번들b83ab32e6f3a1697는 root-owned helper에 적용됐고 실제 hash를 확인했다.
아래 bootstrap/hash는 설치 근거이며 반복 갱신 지시가 아니다.
Bootstrap SHA2563098b6f734f10095bdf19207c593907b4136aa178b937fa314956bce0ee05906.
supervisor 예상 hash859e35a64ca47e831aaa3c32a353099661fa18bc974fcd80a371b6fa3d90f16c,
worker 예상 hash27a7c5510d4b919b4538811ac5adbb8e5aeea2884b39aabdf3f7873f942e1d3b.
private observer 후보2026.10.10-recorder-capacity-v1-dc53163063baf0f0는 실제 앱 src 그대로,
checker/test만 갱신해 게시했다. 설치 hash 확인 뒤 짧은 영속화 단계 판별에 사용한다.
이 후보의 실제 NAS A와 recorder cooldown B의 짧은 A/B는 통과했다(저장24.476% 개선).
누적 job9262267814daf923ac68e69f2754438d는2329089건 저장 완료 후 기존 verify 단계에서
4시간 제한으로 종료됐고 정리/동일 서버 복구/health/운영 PG 보존을 확인했다.
동일 저장본은 관리자 전용으로 보존했다. 고정 읽기 전용 재검증 명령:
`sudo python3 /volume1/docker/kiwoom-monitor/artifacts/verify-saved-recorder-92622678-20261010.py`.
hash로 묶은 검사기/기존 source/runtime만 사용한다. 네트워크 차단·read-only mount·4GiB/CPU0,1/
30분 제한이며 생성한 컨테이너 ID/label이 일치할 때만 정리한다. 운영 제어/DB/설정은 접근하지 않는다.
위 고정 읽기 검증은 같은 보존본으로 NAS184.287초에 통과했다. 2,329,089events/
72,360collector messages/721,080closed deliveries/120large inputs와 manifest 동일성을 확인했다.
native writer/운영 DB 접근 없음, 검증 컨테이너 정리 완료. 원래 timeout job은 실패 기록을 유지한다.
운영 배포/월요일 예약·전체 causal Replay는 미완료다. 새60분 녹화는 하지 않는다.

월요일 one-shot plan은 capture_flags에 store_inputs/collector_inputs/top20_inputs와
large_inputs/account_inputs를 명시한다. 기존 세 옵션 plan은 schema3를 유지한다.
새 옵션은 schema4 capability 및 account-context/v2를 제어 변경 전에 검사하고,
한 번의 POST 응답에서 실제 옵션·schema·릴리즈·instance/session을 확인한다.
불확실 ACK나 응답 불일치에서 자동 재시도/임의 capture stop은 하지 않는다.
이번 수정12건 통과; 운영 후보/계획은 로컬 준비 상태이며 실제 예약 활성화는 미완료다.

### 2026-10-10 고정 녹화기 용량 시험 후보

`test RELEASE --profile recorder-capacity-smoke --pause-operational`은 실제60초,
`--profile recorder-capacity`는 실제3600초 입력 창을 사용한다. 관리자 helper 갱신과
정확한 후보 gate 이후 사용한다. 일반 test/replay의 설치 자원 설정은 그대로다.
고정 worker12GiB/swap12GiB, 디스크 여유12GiB 이상, 시작 전 host 여유
worker+PG+1GiB, recorder 시작 전 실제 host/cgroup 여유9GiB 이상을 요구한다.
임의 memory/duration/script 인수나 selected test 이름을 이 profile에 전달할 수 없다.
CPU/비root/read-only/cap-drop/network 격리는 기존과 같다. 운영 서버만 승인된 절차로
정지하며 임시 작업 정리 후 같은 서버/릴리즈를 복구한다. 운영 PG는 계속 실행한다.

제어 입력은20Hz×10행(초당200체결), REST/구독/저장10초마다, 계좌/lease/VI 및
서로 다른 encoded8MiB 초과 Shadow30초마다다. 60분은72,000메시지/720,000체결/
대형120입력이다. RAM8GiB/500만 이벤트를 유지한다. 기존1MiB/s 영속화 속도를
높이지 않고 전용 작업 deadline만14400초, private persistence9600초로 고정한다.
따라서 전체 시험에는 녹화60분 외 저장/검증 시간이 추가된다. private 저장 시각만
입력 창 종료 후 앞당기며 운영20:10 KST 예약의 실제 벽시계 실행은 별도 검증이다.

모든 파일/블록 checksum, seq, native operation/구독/전달 쌍과 계좌 context/v2,
대형 입력 전체 내용을 순차 검사한다. 전체 payload를 한꺼번에 펼치지 않는다.
실제 처리된 체결+혼합 메시지 수와 durable 원본 수, receipt 지원 전달 수와 closed
전달 수, 계좌/일반 저장 호출 수와 durable native 쌍 수가 각각 같아야 한다.
기존 receipt 대상 밖 market_state 전달은 별도 native_delivery_uncovered 수로
표시한다(원본 혼합 메시지 자체는 녹화된다). 전체 causal coverage로 확대하지 않는다.
이는 용량 시험이며 whole causal compiler/Replay, 원본 DB 동등성, PostgreSQL
COMMIT/전체 앱2~5배 부하 승인이 아니다. 로컬 관련97건 및 최종65건 통과(중복 포함),
설치 hash 확인 및 실제 NAS60초 gate는 통과했고3600초 gate는 실행 중이다.
60초 시험은38,827/38,827 사건·reject/drop0·대형2개·지원전달12,018쌍과 실제9GiB
시작 여유·전체 영속화/정리/같은 운영 서버 복구를 확인했다. 수신p95/max5.953/30.524ms.
별도 입력 스케줄 지연p95/max451.803/1187.940ms에는 inline 혼합 준비 대기가 포함된다.
3600초 jobc9def38d8077b4880e7853ffb0f3ef48의 전체 저장/복구와 운영 배포·월요일 예약은 미완료다.

최종 후보 `2026.10.10-recorder-capacity-v1-90b3b846dd902ace`의 변경64건 NAS 회귀는
job59cffb65d3ecffe6288c0cbb26d57aa0에서 통과(오류/실패/skip0), 정리/운영 보존을
확인했다. 신규 helper bundle90ddfbb1e58cf999는 설치 전이다. root 소유 helper의
교체는 기존 제한 NOPASSWD 명령에 포함되지 않으므로 관리자 bootstrap이 필요하다:
`sudo sh /volume1/docker/kiwoom-monitor/artifacts/nas-operator-update-replay-pause-90ddfbb1e58cf999.sh`.
Bootstrap SHA256은c0156235a4ad9a574c86235535b1e3b94d0d59415ca59b79bea7b78192091252이다.
설치 뒤 두 helper hash를 확인하고 고정60초/3600초 profile을 순서대로 실행한다.

### 2026-10-10 PostgreSQL 시작 실패 진단 후보

임시 PG의 start/readiness 실패는 cleanup 전에 해당 job label을 검사한 컨테이너만
읽는다. Running/OOMKilled/ExitCode/status와 마지막 100줄 중 최대 64 KiB의 hash,
고정 오류 표식(permission/no-space/initdb 등)을 보고한다. 로그 원문, 환경변수,
host 경로, PostgreSQL 비밀번호는 report에 넣지 않는다. 표식은 수집된 증거이며
단독으로 근본 원인을 확정하지 않는다. 읽기 실패도 error type으로 남긴다.
readiness 시도 수/경과 시간을 기록하며 기존 60초 deadline과 cleanup/resume 순서를
유지한다. 로컬 operator/packaging49 및 비활성 후보 NAS portable47 통과;
사용자의 관리자 실행 결과 Linux68 OK와 helper 갱신·무암호 status 검증을 확인했다.
설치된 supervisor hash는 준비 번들의 fae8714f50cf29b2180a667a529b4e2eb28fb07113c8af28cf0314f9e5b119ec와
일치한다. 동일 disk 대조 job50d3a349dd961f0eb5fb8b8723d88c12에서 PG exit1/OOM=false/
permission_denied와 cleanup을 확인했다.83바이트 전체 로그 hash가 PGDATA mkdir 권한
거부 문장과 같아 실패 작업까지 확정했지만, 거부 UID/owner/ACL/userns는 미확정이다.

후속 후보는 job label 확인 뒤 고정 data_root/pgdata/password_file 역할의 UID/GID/mode,
container user/userns와 daemon userns 사용 여부만 기록한다. parent fd/no-follow 검사로
다른 경로를 열지 않고 file content/host path/환경변수를 내보내지 않는다. 권한/소유권은
변경하지 않는다. permission 로그는 작업/역할 enum만 반환한다. 로컬50/NAS portable48 통과,
후속 번들e4aed8ab8bd26442의 관리자 Linux69 OK/helper 갱신과 설치 hash 일치를 확인했다.
NAS 모의 진단 검사 통과와 실제 disk 원인 확인은 구분한다.

동일 disk job29286678b2c75e5f90aa9667e5a0dff5는 root:root/0700 data_root와
UID70/GID0/0700 pgdata를 보고했다(userns=false, 같은 mkdir 권한 거부). root0700 상위는
UID70의 통과를 막는다. 수정 후보는 생성된 이 data_root의 root 소유권과 non-writable
검사를 유지하고, anchored O_DIRECTORY/O_NOFOLLOW fd에 fchmod0711 및 적용 확인을 한다.
0711은 group/other 통과만 허용하며 읽기/쓰기 권한을 주지 않는다. protected private 상위,
비밀번호0600, PostgreSQL이 소유한 PGDATA0700은 유지한다. umask077에 의해 mkdir의
통과 비트가 제거될 수 있으므로 fchmod를 사용한다. 로컬51 통과; 동일 실제 disk replay 검증은
완료됐다. 운영 데이터나 원래 권한 전체에 chmod/chown하지 않는다.
수정 후보95f22a1d0c5913af/번들6e7efb301580f541의 NAS portable49 통과
(job60c99d718bf5d055f082c525fe2cce05, 오류/실패/skip0, cleanup/fence/운영 보존 확인).
이는 RAM 기반 모의 권한 계약 검사이며 실제 disk startup 검증과 구분한다.
관리자 bootstrap은 `/volume1/docker/kiwoom-monitor/artifacts/nas-operator-update-replay-pause-6e7efb301580f541.sh`다.
사용자의 관리자 Linux70 OK/helper 갱신과 설치 supervisor hash34c861fe8747a6ce 일치를 확인했다.
실제 disk job42e7cb83fe64b0f0a33a7264c0946ddc는 PG13회/6.780초 준비와 replay23건,
baseline/cleanup/fence 및 같은 운영 앱 재개를 확인했다. 정지223.929초의 수집 공백과
무손실 미검증을 명시한다. input_timing_preserved=false인 성능 대조는 별도 한계다.

### 2026-10-09 scoped store replay 후보 계약

아래 옵션은 NAS 비활성 후보 `2026.10.09-partial-store-replay-v2-61eabc1096dd4940`에 게시되었으며
설치된 helper에 아직 적용되지 않았다. 로컬 combined 회귀 98건은 통과했다.
정확한 후보 gate와 관리자 helper update 이후에 사용한다. 기본 complete 정책은 유지된다.

- `register-trace TRACE --capture-policy scoped-operations`: 원본 incomplete 상태를 유지한 채
  durable 원본 파일과 manifest hash를 고정한다. 등록 성공은 실행 자격 확인과 별개다.
- `replay RELEASE TRACE --capture-policy scoped-operations --include-workload realtime ...`:
  명시적 선택 owner의 native store 인자만 재생한다. collector mode는 이 정책에서 지원하지 않는다.
  선택 window의 거부/불명 귀속/미지원 method는 실행 전에 실패한다. 제외된 결과는 seed하지 않는다.
- `--baseline-profile empty-v1 --preflight-only`: fresh 격리 DB에 empty baseline을 seal/restore하여
  ID를 반환한다. 녹화 작업은 실행하지 않으며 `--pause-operational`과 함께 쓸 수 없다.
- 실제 empty profile 실행에는 preflight ID를 `--expected-baseline-id ID`로 지정해야 한다.
  기존 `--baseline ID` 등록 기준 상태와 상호 배타적이며 mismatch이면 replay 전에 실패한다.
- 병목 탐색에는 등록과 실행 양쪽에서 `--capture-policy partial-operations`를 명시할 수 있다.
  이 정책은 선택 workload에 인자 거부가 있어도 남은 실제 입력을 실행하고, 누락 위치·종류·이유를
  보고한다. 거부 operation의 옛 저장 결과를 주입하지 않는다. 완전 재현을 주장하지 않으며
  source-state/누락 부하의 한계는 결과에 남긴다. 손상된 파일이나 실제 남은 인자의 불일치는 여전히 실패한다.

첫 후보는 09:00~09:02 KST realtime 저장 경계 18건이며 capture offset은
`--window-start 2.8988919258117676 --window-end 122.89889192581177`이다.
`source_state_equivalent=false`의 empty-state 부분 실험이므로 원본 DB 상태와 같거나 전체 앱의
장초 부하를 재현했다고 보고하지 않는다. 디스크 실험은 `--storage disk --pause-operational`을
사용하고 기존 앱 stop → 격리 replay drain/cleanup → 같은 앱 resume 순서를 유지한다.
운영 PostgreSQL은 중단하지 않는다. 실제 원본 reader 검사·NAS gate·replay 결과는 CURRENT_STATUS에 기록한다.

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
| `replay RELEASE TRACE --baseline ID ...` | 등록된 고정 입력/기준 상태와 선택 옵션으로 전용 실험 실행. 기본은 운영 서버를 유지하며, `--pause-operational`을 지정하면 replay 동안 운영 서버만 정지 |
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

`--pause-operational`은 기본 동작이 아니다. 명시한 경우 supervisor가 시작 전에 운영 API의
진단·trace·workload가 idle인지 확인하고, 고정 승인된 server container/release/source hash를
사설 `replay-maintenance.json`에 기록한 다음 해당 server container만 graceful stop한다.
PostgreSQL 운영 container는 stop/recreate하지 않는다. 운영 server가 실제로 멈춘 것을 확인한
뒤 격리 replay worker를 시작한다. replay와 임시 DB/worker 정리가 끝난 다음 동일 container와
기록된 active release를 확인하고 server를 시작해 health/build/source/DB identity를 검증한다.

이 모드는 의도적인 수집 공백을 만든다. 재개 뒤 구독 복구와 모든 이벤트의 무손실은 보장하거나
검증하지 않으며 report에 `collection_gap_expected=true`, `realtime_loss_verified=false`를 남긴다.
임시 replay container 정리가 실패하면 운영 server를 재개하지 않고 `awaiting_job_cleanup`으로
남겨, `recover`가 replay container 정리를 확인한 다음에만 server를 재개하게 한다. start/readiness,
release/pointer 또는 container identity가 모호하면 journal을 남기고 일반 변경을 차단한다.
`status`에서 `operational_pause`를 확인하고 `recover`로 복구를 수행한다. 이 절차는 현재
후보 코드의 동작 계약이며, 실행은 exact-source gate와 NAS 검증이 끝난 뒤 별도로 해야 한다.

### 관리 명령 자체의 갱신

앱 소스 `deploy`와 root 소유 관리 명령의 갱신은 별개다. 설치된 무암호 명령은 임의 host
코드 실행이나 자기 갱신을 허용하지 않는다. 관리 명령 갱신은 관리자 1회 실행으로만 수행한다.
`scripts/prepare_nas_operator_update.py --nas-root X:\kiwoom-monitor`는 활성 NAS release의
앱 소스·의존성·스키마를 그대로 복사하고 관리 코드·테스트만 교체한 비활성 검증 후보와
checksum 고정 번들을 게시한다. PC main과 NAS runtime의 계약이 다를 때 이를 무시하지 않는다.
후보의 앱 build와 src hash는 활성 NAS 버전이며 최신 PC 앱 배포를 의미하지 않는다.

번들 스크립트는 root 소유 임시 경로로 검증 복사하고, 외부망·Docker socket·운영 data mount가
없는 컨테이너에서 portable/Linux gate를 skip 없이 통과시킨 뒤 installer의 `--update`를 실행한다.
갱신은 기존 operator/diagnostic/scheduler fence 안에서 고정 helper 두 파일만 교체한다.
config·client·launcher·sudoers·운영 앱/DB는 변경하지 않고 원래 revoke 백업도 보존한다.
실패하면 이전 helper와 설치 digest 원장을 복원한다. 중단된 prepared 갱신은 일반 변경 및
revoke를 차단하고, 같은 관리자 갱신 절차를 다시 실행해야 복구한다. 설치 후 실제 대상 계정의
cache-independent 무암호 status를 확인한다. 이후 일반 test/replay/deploy는 기존 전용 명령을 쓴다.

테스트 내부 성공과 전체 명령 성공은 구분한다. 임시 작업 정리 및 최종 운영 신원 fence까지
통과한 뒤에만 `post_job_fence_verified=true` gate를 게시한다. 마지막 fence 실패는 내부 테스트가
통과했더라도 최종 보고서를 failed로 갱신한다. 이 표식이 없는 구형 gate는 배포 승인에 쓰지 않는다.

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

# 앱 전체 시각의 KST 통일

2026-10-18 R3-002 로컬: 기존 PC 가격 cache writer에 AppController `ranking_now`를 주입하고 가격·시총·고가 신규 timestamp3필드를 한 job의 KST ISO(+09:00)로 저장한다. standalone/legacy clock 없는 진입점은 공통 KST fallback이며 키움 동기화로 보고하지 않는다. 기존 저장 자료는 변환하지 않는다. 후속 공유 시계 변경의 aware 현재 시각과 legacy naive KST 분봉 비교가 실제 writer/controller 검사에서 실패해 비교 시에만 `as_kst(bar.minute)`를 적용했다. 거래일/분 키는 보존한다. 현재 검증·실패 기록은 [R3-002](../../REFACTOR_BACKLOG.md#r3-002--한-가격-캐시-job의-다중-transaction)에 모으며 이 항목으로 전체 시계 감사 완료를 선언하지 않는다.

2026-10-15 · 사용자 요청: 앱이 사용하는 시각 전체, 신규 저장과 기존 저장 자료를 KST로 통일한다. 표시만 바꾸는 작업으로 축소하지 않는다.

## 2026-10-17 기존 저장 경로 변환 완료

실제 NAS v5는01:32:52 KST에 완료됐다. **15,013개 DB 셀을 변환**했고 **22개 대상 전체 후검사에서 변환 대상 UTC 잔여0**을 확인했다. 보존 복원본166행·12트랜잭션의 변환/전체 행 대조/롤백, 새0700 백업 폴더에 변경 전 원본0600 보존, 운영 적용·컨테이너 정리·post-fence를 통과했다. 원본 백업은 `/volume1/docker/kiwoom-monitor/deploy/synology/server-data/maintenance/kst-storage-20261017-v5-migrate`에 보존한다. canonical 원본/ID/hash 입력·스키마·운영 서버·기존 폴더 권한은 바꾸지 않았다. 이 셀 수는 중첩 JSON 시각 문자열의 개수가 아니다.

원격 결과 파일을 직접 다시 읽어 state=complete·변환15013·검증22개·잔여0·cleanup_errors=[]·post_fence_verified=true를 대조했다. 로컬 근거는 `data/diagnostics/kst-existing-storage-20261016/v5-result.json`이다. v5 전용 PostgreSQL9건도 오류/실패/skip0·정리/후검사를 통과했다(job `077874b83f2af1d7b97e293c99d6a585`). PC28,813셀과 함께 앞서 변경한 저장 경로의 기존 UTC 변환을 완료했다. 실제 순간을9시간 이동하거나 불변 canonical 원본까지 덮어쓴 것은 아니다. 원래 통합 수락 검증은 별도로 이어간다. 아래 실행 중·거절·미완료 표현은 각 단계 당시의 기록이다.

## 2026-10-17 이전 변경 경로 대조·누락 보완과 NAS 시험 중단

최신: 무암호 도구는 DNS 빈 값3개(null→빈 배열)의 동시 표현 변화 때문에 기존 해시 비교가 실패했음을 **실제 서버·DB의 기존 승인 해시 일치**로 확정했다. 승인값/설정 바이트를 보존하는 비교 수정과 NAS Linux100건·실제 무암호 status를 완료했다([별도 원인·수정 원장](../NAS_OPERATOR_COMMANDS.md#2026-10-17-재부팅-후-dns-빈-값-표현의-승인-비교-오탐-수정)). KST PostgreSQL8건도 무암호로 오류/실패/skip0·정리/후검사를 통과했다(job `c091be002a8ebd0a5d491619e92b7fb6`).

사용자가 v4를 실행해 보존 복원본166행·12트랜잭션의 변환/전체 행 대조/롤백을 통과했다. 운영 실행은 새 백업 폴더가 요청0700과 달리0777로 만들어져 DB 연결 전 `private_backup_mode_invalid`에서 중단됐다. 오류 원문은 `SERVER_CODE`17행이며 DB 연결은 그 뒤이므로 운영 변경0이다. 기존 보고서의 null과 실패 자료는 덮어쓰지 않는다. 실패 후 무암호 status에서 health=ok·진단 idle·동일 release/DB를 확인했다. 실제 NAS의 새 fixture만으로 mkdir 후0777→FD fchmod 후0700과 파일0600을 재현했고 fixture는 정리했다. 기존 폴더나 운영 권한을 바꾸지 않고 새 백업 폴더만 FD로 권한을 확정하는 v5를 준비해 NAS PostgreSQL9건을 실행 중이다. 이 검사는0777 상속 재현·백업 보호·권한 설정 실패 시 DB 미접속을 포함한다. 근거: `v4-passwordless-test-after-dns.json`, `v4-failure-analysis.json`, `nas-backup-mode-probe.json`. 아래 거절·원인 미확정은 해당 시점의 이전 기록이며 NAS 실제 변환과 원래 통합 수락은 남아 있다.

범위는 **앞서 UTC→KST로 바꾼 생성 코드 → 실제 writer/저장 필드 → 기존 UTC 자료 → reader·비교·화면**이다. 이전의 명시 UTC 기본값 변경26개와 현지 시각 변경111개 기록을 clock diff85개 파일의 경로 분류와 대조했고, 분류에서 빠진 파일은0이다. 정적 경로 분류가 끝난 것과 해당 자료의 실제 변환·전수 후검사가 끝난 것은 구분한다. 경로별 분류·저장 위치·보존 이유와 추가 NAS 대상은 `data/diagnostics/kst-existing-storage-20261016/scope-audit.json`/`scope-audit.md`에 남겼다.

누락은 일반 timestamp 열 밖에서도 발견됐다. `strict_restore`가 `settings.value`에 기록하는 `google_drive_local_changed_at`/`google_drive_last_upload_success_at`의 기존 UTC2셀을 별도 원본 셀 파일에 먼저 보존한 뒤 KST 표현으로 바꿨다. 기존28,811셀과 합쳐 PC의 확인된 변환은28,813셀이다. 앱 종료 상태에서 처리했고3개 DB quick_check·추가 변환0·2셀 readback을 확인했다. `main_window`의 같은 두 값 최신 비교를 실제 순간 비교로 바꿨으며 로컬 marker 생성 때 UTC로 재직렬화하던 부분도 KST로 유지한다. Drive가 제공하는 원격 modifiedTime과 기존 백업 원본은 바꾸지 않는다. `stock_news_repository.revised_at`은 변수명이며 실제 저장 열은 이미 변환한 `journal_news_links.updated_at`이므로 별도 `revised_at` 열을 만들거나 중복 변환하지 않는다. `central_journal_sync`의 해당 값은 `imported_at`에 기록돼 기존 변환에 포함돼 있다.

NAS v2 목록에는 `news_sync.site_last_page_limit_at`, `external_market_collection_status.checked_at`, mutable checkpoint의 `quality.last_error_at`, runner checkpoint의 `saved_at`/`status.updated_at`, 계좌 snapshot의 안전한 `received_at` 표현 등의 경로가 빠져 있었다. 반면 runner `last_poll_completed_at`은 RAM 상태 응답이며 저장 checkpoint에 복사되지 않는다. 계좌 snapshot ID는 기존 UTC canonical received_at으로 재계산 가능한 구조를 유지한다. 주문 event·admission·stop/resume처럼 시각 문자열이 기존 content ID에 포함된 원본은 별도로 분류했으며 불변 문서의 ID/hash를 임의로 다시 만들지 않는다. TIMESTAMPTZ는 이미 같은 순간을 저장하므로 연결의 Asia/Seoul 표현을 사용하고9시간 UPDATE를 하지 않는다. 원본 기사·수집 cursor의 UTC canonical 직렬화는 이전에 바꾼 업무 시계 producer가 아니다. 과거 내보내기/복원 백업 원본과 변경 대상 live store를 구분한다.

사용자가 실행한 v2는 **운영 변환에 진입하기 전** 복원본 `psql` 시험 subprocess의60초 한도로 종료됐다. traceback의 실패 위치는 `nas_migration.py:163`이며 격리 컨테이너 정리·운영 자료 미변경을 확인했다. 이 로그만으로 개별 SQL5초 취소, 잠금, 디스크 대기, Docker stdin 문제 중 무엇이 원인인지 확정할 수 없다. 시험 SQL의 `to_jsonb(PK)` 비교와20개 묶음도 조사 대상이지 아직 확정 원인이 아니다. 단순히 전체 timeout을 늘리거나 같은 변환을 반복하지 않는다.

사용자가 `kst-storage-20261017-v3-trial-diagnostic`를 실행해 동일 v2 SQL의20행 시험에서 합계60초 초과를 재현했다. 기존 `to_jsonb(PK)` 조건은 Index Cond 없이 넓게 읽고 네이티브 PK 조건은 `idx_central_documents_lookup`의 Index Cond를 사용했다. 관찰한 blocking PID는 모두 비었고 대다수 표본은 active/no wait였다. 비효율적인 점 조회는 확인됐으나 최초 v2의 모든 대기 원인까지 입증한 것은 아니다. 시험은 롤백했고 컨테이너 정리·post-fence를 통과했으며 운영 조회/변환·새 DB 복사·전체 복원·운영 재시작은 없었다.

v4는 PK 열에 함수를 씌우지 않고 PostgreSQL 행 타입으로 우변 키를 변환하며, 누락 JSON 경로와 전체 대상의 잔여 UTC 후검사를 보완했다. 기존 SQL5초·시험60초·백업·전체 행 대조·경합 거부·롤백 경계는 유지한다. 로컬 묶음7건은 통과했다. 실제 PostgreSQL8건은 기존 무암호 test의 전용 DB에 합성 자료만 만드는 후보로 준비했으나 `approved_container_changed`에서 job 시작 전에 거절됐다. 이8건을 NAS 통과로 보고하지 않는다. 기존 v2 변환 묶음은 재실행하지 않는다.

관리자 읽기 결과 NAS는10월17일00:27:27 KST에 부팅했고 Docker는00:29:53, 서버/DB는00:31:01/02에 시작했다. 둘 다 실행 중이고 OOM 표식은 없지만 기존 승인 비교가 실패한다. 재부팅 원인·실행자는 미확정이다. 디스크 설정/API의 유일한 관찰 차이 `IPAMOperational`은 기존 지문 입력이 아니므로 승인 실패 원인으로 단정하지 않는다. 기존 설정 원문 없이 저장 해시만 남아 정확히 어떤 입력이 달라졌는지는 미확정이며, 승인 갱신·권한 변경·새 게이트·컨테이너 재시작은 수행하지 않았다. 빈 값/생략 표현을 메모리에서 한 항목씩 대조해 기존 해시와 정확히 일치하는지만 확인하는 읽기 전용 진단을 준비했다. 이 진단은 승인 또는 실행 허용 경로가 아니다.

최신 검증은 PC 변환·설정/콘텐츠 동기화·Drive 업로드/복원·화면 비교 관련64건, 계측3건, v4묶음7건과 읽기 전용 비교4건이 각각 통과했다. 실제 NAS v3 진단은 완료됐고 무암호 PostgreSQL8건은 시작 전 거절됐다. 재부팅 후00:38 KST HTTP health=ok/운영 누적v2를 읽었으며 새 commit/push/제품 배포·전수 회귀는 실행하지 않았다. NAS 실제 변환과 원래 통합 수락은 남아 있다. 근거: `pc-additional-applied-20261017.json`, `storage-scope-related-tests-20261017.log`, `diagnostic-v3-result.json`, `v4-bundle-local-tests.log`, `v4-passwordless-test-launch.json`, `operator-reboot-evidence.json`.

## 2026-10-16 기존 저장 자료 변환 재개

사용자가 기존 저장 자료의 KST 통일을 먼저 끝내고 원래 통합 검증으로 돌아가도록 명시했다. UTC offset을 가진 일반 시각 문자열만 같은 순간의 `+09:00` 표현으로 바꾸며 이미 KST인 문자열·naive 시장 시각·epoch·봉 날짜/분 키·수치·ID는 이동하지 않는다. offset 없는 값의 원천을 추정해 UTC로 간주하지 않는다. 원본 기사/수집 커서·불변 관측/판단·연구 내보내기·검증 입력의 canonical UTC 직렬화는 기존 계약 경계에 보존한다. 원본의 hash/ID를 새로 만들거나 기존 자료를 새 관측/분석으로 승격하지 않는다.

PC에서 실제 사용하는 `monitor.sqlite3`125개·`news.sqlite3`26,237개·`journal.sqlite3`2,449개, 총28,811개 명시 UTC 시각 셀을 변환했다. 변경 전 PK/값은 DB별 별도 원본 셀 파일에 먼저 기록하고 fsync한 뒤 DB별 한 트랜잭션에서 적용했다. KST/epoch/naive/JSON/hash 입력·키는 변경하지 않았고 각 DB의 재계획 결과 추가 변환은0이다. 일반 변환/rollover/소수 초 보존/원본 보존·경합 거부/no-op과 설정·콘텐츠 동기화 관련36건이 통과했다. 최초 sandbox의 임시 SQLite 생성/정리 오류는 사용자 환경 재실행과 구분한다.

연결 경로 확인 중 `central_content_sync._merge_journal_link`의 서로 다른 UTC/KST offset 문자열 직접 비교를 재현했다. 실제 순간으로 비교하도록 최소 수정해 더 새로운 UTC 자료의 거부·같은 순간의 삭제 복구를 방지했다. 미정 매매 조건·새 Runtime/스키마/주문·서버 배포는 추가하지 않았다. 이 PC 코드는 다음 앱 기동 때 사용된다.

사용자가 NAS 읽기 전용 조사 묶음을 실행했고45테이블·59컬렉션의 열 형식/최대20행 표본을 오류0·post-fence 정상으로 확인했다. 이는 전수 변환 건수 확정이 아니다. `kst-storage-20261016-v2-migrate`는 기존 검증 PGDATA의 PK별 변환·전체 행 값 대조·롤백 시험 뒤 운영의 같은 필드에 적용하도록 준비했다. 관련36건·유지보수 묶음4건과 NAS Python/셸 구문·게시 SHA256을 확인했으나 이후 실제 복원본 시험 subprocess60초 한도로 실패했다. **운영 변환은 시작되지 않았다.** 누락 대상 보완과 실패 진단은 위10월17일 기록을 따른다.

근거: `data/diagnostics/kst-existing-storage-20261016/`의 `pc-applied.json`, 원본 셀 파일3개, `storage-and-sync-tests-final.log`, `nas-inventory.json`, `nas-preparation.json`, `nas-bundle-local-tests.log`, `migration-preparation.json`. 아래 운영 자료 미변경 설명은 이전 단계 기록이다. 전체 완료 후 분봉 후보 변화·뉴스 분석/저장·07:55 실행 검증으로 복귀하며 전수 회귀는 자동 실행하지 않는다.

## 앞으로의 코드 작성 규칙

2026-10-16 사용자 지시에 따라 새 시간 관련 코드는 **기존 키움 보정 시계를 주입받아 KST로 사용**하도록 [AGENTS.md](../../AGENTS.md#앱의-시간-기준--기존-키움-보정-시계kst)와 [개발 불변 규칙](../../DEVELOPMENT_GUARDRAILS.md#시각의-공통-기준)에 명시했다. 서버 공통 시계→기존 now_provider→저장/판단 경계를 사용하고, PC는 기존 NAS 응답의 서버 시계를 재사용한다. 시계용 추가 조회·UTC/호스트 별도 현재 시계 생성은 금지한다. 기동/오프라인/테스트 fallback의 출처, 기존 naive KST 호환 경계, 원래 사건의 순간·epoch·monotonic/canonical hash 보존을 함께 규정한다. 이 규칙을 문서에 추가한 것이 기존 DB 변환·NAS 배포 완료를 뜻하지는 않는다.

## 2026-10-16 전수 회귀 완료·오류 수집 후 재검사

사용자 지시에 따라 일반 실패·오류가 나와도 같은 소스로 Windows `all-local` 첫 실행을 끝까지 진행했다. 313개 worker·5,344건을 실행했고 미실행·skip은0, 실행 중 소스 변경은 없었다. 실패264건·오류4건은 하위 사례를 포함한19개 테스트 메서드/5개 worker에 모였다. 모든 worker와 자식 프로세스의 종료를 확인했으며 최초 기록과 실패 임시 자료는 보존했다. 전체 실행→오류 수집→원인 확인·수정→해당 검사 재실행 순서는 [AGENTS.md](../../AGENTS.md#전수-회귀의-실행과-오류-수집)와 개발 불변 규칙에 명시했다.

재현으로 확인한 시계 누락은 주 broker가 없는 모의 전용 앱, 아침 시황 schedule, coverage의 기본 조회 시각이었다. 이들은 이미 존재하는 키움 보정 시계를 공통 provider로 받는다. 명시적 provider가 없는 기존 replay v1/v2에 실행 시계 속성이 새로 노출된 차이는 제거했다. 테스트의 고정 시계도 새 주입 경계에 연결하고, 인위적으로7시간을 건너뛰는 fixture에는 정상 heartbeat를 반영했다. 계좌 snapshot의 수신 시각은 KST로 저장하되 ID 계산의 기존 UTC canonical 표현을 유지해 동일 순간의 중복 수락·ID 변경을 막는다.

기존 API 기준 결과 파일은 바꾸지 않았다. HTTP 전후 원본 DB 지문 검사를 유지하고, 별도의 메모리 복사본에서 확인된 시계 열과 snapshot 수신 필드만 UTC 비교 표현으로 정규화해 기존 지문과 대조한다. 실제 fixture의 해당 서버 생성 값은 aware KST인지도 검사한다. 공개 연구 응답의 `received_at`은 기존 순간 그대로 KST를 기대한다. 최초 실패19개 메서드는 실패·오류0으로 재통과했으며 인접15개 모듈446건도 실패·오류·skip0으로 통과했고 실행 중 소스 변경과 잔존 프로세스는 없었다. 일부 재검증을 현재 소스의 전수 통과로 대신하지 않는다.

근거는 `data/diagnostics/nas-integrated-validation-20261015/kst-storage/v1/regression-error-summary.json`과 그 파일이 가리키는 최초 전수 결과·오류 원장·재실행 결과다. 운영 DB 기존 자료 변환·NAS 배포는 수행하지 않았고 앱 전체 KST 통일 작업은 계속 남아 있다.

## 로컬 구현 진행 — 운영 적용 전

공통 Kiwoom 시계를 DB·collector·뉴스/AI·외부시세·자격정보·모의실행에 주입하는 중이다. `received_at`/분봉 `processed_at`과 lease 갱신/DB 소유권 fence의 시계를 연결했고 PostgreSQL 연결은 기존 timeout/read-only 옵션을 보존한 채 Asia/Seoul을 요청한다. PC remote/content client는 선택적으로 요청한 기존 응답 시각을 보관하고 monotonic 경과만 더한다. `domain/time_basis.py`는 KST 변환과 기존 naive KST/날짜 fallback만 공유하며 네트워크·전역 시계 상태·새 task를 소유하지 않는다. PC 설정·열 설정·뉴스/AI 저장 시각과 UTC/KST 혼합 버전 비교를 변경했다. 기존 호스트 현지 시각/날짜111호출은 KST fallback으로 명시했으며 epoch/aware 경계는 별도 확인한다.

첫 주입136건, 저장/remote127건, 확장 실패 경계 재검사180건, PC 관련80건이 각 실행 소스에서 통과했다. 확장480건 실행의22오류는 owner clock fixture/runner 주입 인자/직접 호출 검사에서 발생했고 해당 실패 경계를 수정해180건에서 확인했다. 이 검사는 전체 통일 완료나 NAS 검증을 뜻하지 않는다. 이후 호출 경로 수정의 최종 회귀, 남은 UTC 생성/저장·SQL 기본값·기존 TEXT/JSON/SQLite 변환과 운영 적용은 진행 중이다. 운영 DB 기존 자료는 아직 변경하지 않았다.

## 2026-10-16 범위 재확인과 실제 시계 호출 경로 감사

사용자는 시각 차이에서 원인을 유추하지 말고 시계를 만드는 함수부터 실제 호출자를 따라 UTC/KST/키움 기준을 확인하도록 지시했다. 키움 시계는 이미 KST이므로 시간값을 바꾸지 않는다. 서버의 현재 시각은 기존 키움 보정 시계를 공통 기준으로 사용하고 앱의 생성/전달/저장/조회/로그/화면까지 같은 KST 기준으로 연결한다. 발생 시각이 있는 체결/기사 등은 원래 순간을 보존해 KST로 표현한다. 과거 정상 UTC 표현의 KST 변환과 잘못 해석한 과거 값의 교정은 구분한다. 경과 시간용 monotonic과 시간대 독립적인 epoch에 일괄9시간을 가감하지 않는다.

현재 NAS 통합v5 manifest와10개 실제 소스 파일의 hash를 대조했고, 보존된17개 배포본의 collector 시계 주입도 확인했다. `KiwoomRestClient._update_server_time`은 HTTP `Date`를 읽고 `server_now()`는 보정된 **시간대 정보 없는 KST**를 반환한다. `app.py`는 이를 `as_kst`로 명시적+09:00으로 만든 뒤 collector에 전달한다. collector는 수신/분봉 확정 때 그 값의 `.timestamp()`를 `updated_at`/closure `available_at`으로 사용한다. 키움 체결 HHMMSS는 분봉 키를 만들며 가용 시각의 직접 원천이 아니다. `_bar_available_at`은 epoch를 KST datetime으로 읽는다. 이후 revision의 `ObservationRevisionSource.from_observation`은 `_as_utc`로 정규화하고, `_observation_revision_values`의 별도 `received_at`은 서버 `datetime.now(timezone.utc)`로 생성한다. 따라서 키움 시계의 숫자를 바꾸는 대신 저장 직전의 변환/별도 UTC 시계를 구분해야 한다.

현재 checkout392개 Python 파일의 AST 목록에서 명시 UTC 현재 시각175곳, UTC naive1곳, 명시 KST24곳, host-local 현재 시각84곳/날짜27곳과 주입 시계/키움 시계 호출을 기록했다. 이는 호출 지점 목록이며 각각이 별도 시계/오류라는 뜻도 전체 caller→writer→reader 감사 완료라는 뜻도 아니다. `RemoteKiwoomRestClient.server_now()`는 현재 PC의 UTC에9시간을 더한 naive KST 호환값이므로 NAS 키움 보정 시각을 직접 받는 경로는 아직 없다. 이를 서버 공통 기준과 연결하는 작업이 남는다. 근거는 `data/diagnostics/nas-integrated-validation-20261015/kst-storage/v1/source-audit/`의 manifest receipt/배포 원문/clock-sites.json에 보존했다.

추가 projection535,058건 조사 실행기는 단위17/PostgreSQL13=NAS30건을 통과했지만 원래 요청을 좁힌 후속이므로 실자료 실행/관리자 명령 배포는 중단한다. 이30건을 전체 KST 통일 검증으로 재사용하지 않는다. DB 연결 KST 설정의 로컬 시험안은68건을 통과한 뒤 호출 경로 감사를 먼저 하도록 원복했다. 이 시험안도 운영 반영/현재 소스 통과를 뜻하지 않는다. 이번 단계의 제품/NAS/운영 DB 시각 변경은 없다.

## 표시와 저장 경계

### 2026-10-16 사용자 확정 — 완료 분봉의 분석 기준

분봉 기반 역사 분석은 해당 분이 끝난 이후 그 분의 확정 OHLCV를 사용한다. 조회 보완분봉의 최종 가격·거래량 우선 규칙을 유지하며, 장후 수집했다는 이유만으로 미래 정보로 분류하거나 분봉을 배제하거나 분석 사용 시각을 장후로 미루지 않는다. 미완료 분의 최종 고가·저가·종가·거래량을 그 분이 끝나기 전에 쓰는 것은 이 기준에 포함하지 않는다. 원래 수신/저장 시각은 별도 이력으로 보존한다.

이번 시각 조사에서 14:20 분봉에 23:21이 기록되어 있다는 사실만으로 오류를 확정하지 않는다. 실제 23:21의 보완 조회와 실제 14:21 수신을 23:21로 잘못 저장한 경우를 구분해 저장 시각의 수정 필요성만 판단한다. 이 대조는 분봉의 가격 정확도 평가나 분석 적격성 gate가 아니다. 정상 query 보완값을 realtime 값으로 되돌리거나 OHLCV를 변경하지 않는다. 기존 엔진의 API/소비 계약을 이번 진단에서 임의 변경하지 않는다.

후속 `clock-relations/v1`의 추가 projection 조사 준비는 위 범위 재확인에 따라 실자료 실행 전에 중단했다. 기존 전수 집계/백업/표본 원장은 보존한다. 정상 query 보완값과 분봉 가격·거래량을 되돌리거나 시간차만으로 후보를 승인하지 않는다.

사람이 보는 앱 화면·PC/NAS 로그·진단 보고의 시각 기준은 Asia/Seoul(KST)이다. 키움의 날짜/분/체결시각은 시장 발생시각이고 수신·가용·저장 시각은 시스템의 처리시각이므로 필드의 의미는 계속 구분한다. 동일 순간은 UTC 문자열, +09:00 문자열, epoch 중 어느 표현이든 같은 한국시간으로 표시한다. 날짜만 알려진 원문에는 00:00을 만들어 붙이지 않는다.

`infrastructure/time_display.py::format_kst_time`은 표시용 datetime/ISO/epoch를 받아 KST 문구만 만든다. 기존 offset 없는 키움·일지 시각은 KST로 해석한다. offset이 있으면 해당 순간을 보존하고, 원본 객체·DB·문서 hash·정렬 키·판단 cut은 수정하지 않는다. 서버의 기존 aware KST 시계 주입과 KST 로그 formatter는 유지한다.

PC 로그는 명시적 KST formatter와 한국 날짜의 파일명을 사용하도록 보완했다. 뉴스/시황/과거 기사/후보/테마/매매일지/모의 후보 및 실행 상태/TOP20 게시 시각의 사람용 문자열을 같은 표시 경계로 연결했다. API의 원본 offset·epoch 및 검증용 원본 JSON은 기존 기계 계약을 유지한다. 보이는 시각과 원본 저장값을 같은 숫자로 만들기 위해 epoch를 이동시키지 않는다.

공통 formatter 한 파일이 표시 책임을 소유한다. 각 UI의 중복 변환 대신 같은 helper를 한 번 호출하므로 기존 데이터 owner·API·저장·판단·주문 호출 깊이는 유지되고, UI 표시에는 공통 파일 하나와 함수 호출 한 단계가 추가된다. 별도 시계 owner/Manager/Service/worker/전역 변경은 없다.

## DB에 남아 있는 값의 실제 구분

현재 `central_schema.py`에는 다음 저장 형식이 있다. 이는 소스 계약 조사이며 운영 DB 전체를 스캔해 보정 대상 건수를 확정한 결과는 아니다.

| 종류 | 예 | 한국시간으로 조회할 때 | 원본 재작성 |
|---|---|---|---|
| epoch REAL/DOUBLE PRECISION | minute updated_at, snapshot saved_at, 뉴스 received_at/available_at | epoch를 Asia/Seoul로 변환 | 정상 값은 이동하지 않음 |
| PostgreSQL TIMESTAMPTZ | 시장 metadata effective_at/available_at, 외부 bar_time | 조회 timezone 또는 `AT TIME ZONE 'Asia/Seoul'` | 정상 순간의 값을 이동하지 않음 |
| 날짜·분 키 | trading_date/minute | 기존 한국 시장 날짜/분 그대로 | 시간대 변환 대상이 아님 |
| JSON/TEXT의 offset 있는 ISO | 뉴스·시황·관찰 document | offset을 읽어 KST로 표시 | 다시 쓰면 문서 hash·동기화·revision 계약 검토 필요 |
| 과거 잘못 기록된 시각 | KST14:20 분봉에 available_at=14:21+00:00 | 변환하면 KST23:21이므로 정상 UTC와 구분 | 확인된 기간·writer·필드만 별도 보정 대상 |

예를 들어 정상 `2026-10-15T05:21:02+00:00`과 `2026-10-15T14:21:02+09:00`은 같은 순간이다. 반면 실제 관찰된 과거 `2026-10-15T14:21:02.719446+00:00`은 KST23:21이어서 KST14:20 봉의 정상 처리시각이 아니다. 같은 행의 `effective_at=05:20+00:00`은 정상 시장 발생시각이다. 새 배포 뒤에는 KST14:25 봉의 가용시각이 `05:26:03.155671+00:00`→KST14:26:03으로 일치했다. 이 근거는 전체 날짜/모든 writer의 동일 오류를 증명하지 않는다.

따라서 표시·조회 통일은 제한된 수정이고, 모든 저장 문자열 재작성은 이력 정합성 검증을 포함한 별도 작업이다. 과거 보정은 대상 기간/코드/필드와 중복되지 않는 writer 경계 확정 → 읽기 전용 건수/표본 → 원본 백업·별도 보정본 → epoch/정렬/중복·lineage/hash·동기화와 당시 수신 근거 대조 순서가 필요하다. 정상 UTC 값과 잘못 태그된 값에 같은 +9/-9 처리를 하지 않는다. **이번 작업은 기존 운영 DB를 보정하거나 마이그레이션하지 않았다.**

## 검증과 반영 범위

### v9 실측 — 초봉 기간 집계 완료

사용자 완료 통지 후 NAS 결과 파일을 읽어 v9의 read_only/보정 없음/post-audit fence·parent canonical hash 일치·이전 부분 건수 미합산을 검증했다. KST16:54:45.767308의 새 repeatable-read snapshot에서10/13~15 초봉2,270,843행/228페이지를44.716초에 모두 읽었고 complete/full_census_performed=true다. 마지막 키는10/15 16:52:59·950260/KRX다. 이 완료 플래그는 **초봉의 지정 기간**에만 해당한다. v8 자체의 complete=false/원본 결과는 그대로 보존한다.

| 초봉 날짜 | 읽은 행 | 가용시각 약9시간 후보 | 봉 시작 뒤0~60초 | 그 외 시간차 |
|---|---:|---:|---:|---:|
| 10/13 | 804,257 | 804,257 | 0 | 0 |
| 10/14 | 765,381 | 765,381 | 0 | 0 |
| 10/15 | 701,205 | 559,425 | 108,507 | 33,273 |
| 합계 | 2,270,843 | 2,129,063 | 108,507 | 33,273 |

후보 기준은 epoch available_at과 KST 초봉 시작의 차이32,390~32,430초이며, 단순 offset 표기 변환이 아니다. 33,273행은 기타 lag로 남기며 정상/오류로 임의 분류하지 않는다. v9은 초봉 available_at만 읽었으므로 출력에 존재하는 effective_key_mismatch/metadata_missing의0은 초봉 메타데이터 검증 결과가 아니라 공통 집계 구조의 기본값이다. 날짜별 code 수를 합산해 전체 종목 수로 만들지 않는다.

단계230개가 모두 완료됐고 실행 시간 합계13.553초/최장 페이지0.066초다. 모든 개별 조회가100ms 관측 시작보다 먼저 끝나 wait 표본은0개다. 이를 잠금/IO 대기 부재나 cold-cache 성능 증명으로 쓰지 않는다. 원본 `v9/result.json` SHA256은 `d891834def006ca93d47d8d43429448cf825fda86c11b8b6896bacf9b33016ae`, 집계와 검증 receipt는 `v9/census-summary.json`에 보존했다. 후속 운영 status는 정확한 통합 v5/source path·health=ok·diagnostics idle·trace OFF·paused workload/active run 없음이다. runtime/source 배포·commit/push·새 CI·DB 보정·재시작은 하지 않았다.

분봉은 앞선 v8 KST16:42:40 snapshot에서10/1~15의1,678,587행을 완료했고 완료 realtime/actual 분봉 가용시각 후보48,176행을 얻었다. 초봉은 이번 새 snapshot의 완결 수치를 사용한다. 서로 다른 snapshot·분/초 단위를 더해 하나의 고유 오류/수정 행 수로 만들지 않으며 조사 기간 밖 DB 전체와 원본 revision의 전수 확인은 완료로 표시하지 않는다.

후속 복구는 아래 저장 경계를 기준으로 준비한다. 이는 현재 소스 호출 경로 확인이며 운영 DB의 과거 revision 존재/수신 원본 일치/백업 복구가 검증됐다는 뜻은 아니다.

| 대상 | 후보 근거 | 복구 전 필요한 확인 |
|---|---|---|
| `central_second_trade_bars.available_at` | 지정 기간2,129,063행의 약9시간 lag | 원본 수신/봉 종료 근거와 동일 PK·가격/거래량 hash 보존. 기존 `save_second_trade_bars`는 더 늦은 available_at 우선 UPSERT여서9시간 낮춘 값을 정상 재저장하는 것만으로 기존 행이 교체되지 않음 |
| 분봉 metadata `available_at` | 완료 realtime/actual48,176행, partial/in_progress 별도 | 봉 종료/closure operation 원본 및 source·completeness 대조. history가 존재하면 `central_observation_revisions`의 revision_of/payload_hash/시간과 최신 projection의 일관성까지 확인 |
| 분봉 `updated_at` | 완료 realtime/actual33,821행, 다른 분류 별도 | metadata와 후보 수가 다르므로 독립 판정. 원래 저장시각인지 closure 가용시각인지 해당 writer 경로와 원본 근거로 확인 |
| query/estimated·결측·기타 lag | 별도 집계 | 장후 보완/수신 지연을 실제 오류와 구분하고 근거 없는 시간 이동 제외 |

실제 수정 전에 원본 백업과 복원 확인 → PK/현재 값/hash를 담은 수정 manifest 확정 → 격리 DB 복사본에서 필드만 선별 보정 → 가격·거래량·키·정렬·가용시각 cut·revision/lineage·동기화·재실행 무변경 검증 → 운영 적용 결과 확인 순서를 따른다. 이 감사 시점에는 승인된 수정 행0·백업/보정/적용 미실행이었다. 이후 백업 복원 검증은 아래 recovery v8에서 완료했으며 역사 시각 보정/적용은 여전히 미실행이다. 초봉 writer의 최신성 조건이나 분봉 history hash 계약을 일반 실행 코드에서 완화하지 않는다.

### v8 실측 — 분봉 기간 완료와 초봉 부분 집계

사용자 실행 v8은 KST16:42:40.184512의 repeatable-read snapshot을 사용했다. 같은1,500키 전체 hash/행 수가 일치한 control에서 기존 분봉 root Shared Read94,533+Hit1,333=95,866접근/실행1,209.614ms, 시작 tuple 조건을 뺀 조회는 Read1+Hit1,474=1,475접근/3.843ms였다. 캐시 read→hit 전환을 포함한 합계 자체가 약65분의1로 줄어 gate를 통과했다. **이번 감사 SQL의 중복 시작 조건에 따른 접근증폭을 실제로 축소했다.** cold-cache 성능·제품 전체 조회·v5의 당시15초 취소 원인을 모두 해결했다는 뜻은 아니다. 제품 SQL/DB 인덱스·설정은 변경하지 않았다.

분봉은10/1~15 범위1,678,587행/1,120페이지를 모두 읽었고 state=complete다. 마지막 키는10/15 16:41:00·452280/KRX다. 실제 실행 단계 합계78.238초, 해당 단계 구간의 wall136.659초로 Python 분류·페이지 간 yield·관측과 DB 실행을 구분한다. 최장 페이지7.524초, 실패 단계/관측 blocker0이다. 이 수치는 당시 snapshot의 **범위 건수**이며 DB 전체1,081만 추정이나 이후 유입 건수와 다르다.

| 분봉 분류 | 읽은 행 | 가용시각 약9시간 후보 | 분 종료 뒤0~60초 | updated_at 약9시간 후보 |
|---|---:|---:|---:|---:|
| realtime/actual/complete | 52,393 | 48,176 | 4,173 | 33,821 |
| realtime/actual/partial | 2,978 | 2,821 | 157 | 1,231 |
| realtime/actual/in_progress | 87 | 34 | 0 | 73 |
| query/estimated/complete | 1,623,108 | 852 | 1,299 | 90,802 |
| query/estimated/in_progress | 4 | 0 | 0 | 0 |
| source/origin 등 메타정보 없음 | 17 | 0 | 0 | 0 |

완료 realtime/actual의 날짜별 가용시각 후보는10/1 5,521·10/2 8,481·10/6 3,439·10/7 7,186·10/8 3,371·10/12 3,401·10/13 1,070·10/14 2,547·10/15 13,160행이다. 각 group의 code 수는 날짜/분류별 distinct이며 합산해 전체 고유 종목 수로 표시하지 않는다. 가용시각 후보의 분 종료 대비 기준은32,390~32,430초이고 updated_at은 봉 시작 대비32,390~32,460초다. 나머지 행을 전부 정상으로 취급하지 않는다. metadata.effective_at의 봉 키 불일치는0개이고 가용/메타정보 결측17행은 별도다. query 보완의 수신/갱신 시각은 봉 발생시각보다 실제로 늦을 수 있어9시간 근접만으로 보정하지 않는다. 현재 집계는 최신 projection의 후보이며 수신 원본/lineage 검증이나 과거 복구 승인을 대신하지 않는다.

초봉은10/13~15 범위1,790,000행/179페이지를 읽었고 모두 봉 시각 대비32,390~32,430초의 가용시각 후보였다. 날짜별10/13 804,257·10/14 765,381·10/15 220,362행이며 마지막 키는10/15 10:32:39·233740/KRX다. 이는 날짜 순서로 읽은 **부분 건수**이고10/15 전체나 전체 초봉 건수로 확장하지 않는다. 실행 단계 합계15.790초/wall40.273초·최장2.711초·실패 단계0이다. 총180.202초의 전체 한도에 도달해 stop_reason=overall_time_budget으로 마쳤다. statement_timeout/오류가 아니며 두 범위 전체 complete/full_census_performed=false를 유지한다.

원본 `v8/result.json` SHA256 `a1c1e4781191aed007d500972c1ca62c492009b49684c7e0e3d6a55c2db20a46`, 집계 `v8/census-summary.json`을 보존했다. read_only/보정 없음/post-audit fence를 확인했고 후속 기존 인증 GET /health=ok·realtime READY/paused=false, 운영 도구 status=ok·정확한 통합 v5 활성/source path·diagnostics idle·trace OFF·paused workload/active run 없음도 확인했다. source commit/push/새 CI·운영 설정·서버 재시작·DB 원본 수정은 이번 진단에서 수행하지 않았다.

후속 immutable v9은 **초봉10/13~15만** 새 read-only repeatable-read snapshot에서 처음부터 집계한다. v8 부분 결과와 수를 합치지 않고 snapshot 기준을 독립적으로 표시하며 완료된 분봉/control을 재실행하지 않는다. 본/관측 연결15초/500ms·lock1초·전체약180초, 동일 keyset/분류/부분 결과·계획/wait guard를 그대로 재사용한다. root 보관 v8 결과의 canonical SHA256 `6fb3ac69ddfa6e994daf8662f9d363d38bee6bd861d7d8383783218e935425fa`가 동일해야 실행한다. 새 main/wrapper의 실제 모의 실행10건은 초봉1회만 수행·부분/오류 미완료·read-only 선행조건·오류 비노출·v8 scanner 보존·parent 변경 시 Docker 실행 금지를 통과했다. 실제 NAS 집계는 아직 남는다.

v9 audit.py SHA256 `3e46ef450acbfaa59bf11ca88c3d814929ed7e168acda0e846608739bc01258e`, 셸 `e183476e8126290e7f7b418b349379ecaa9d69f3a3c6ae991a41ac478c530126`와 원격 셸 구문을 확인했고 관리자 인증/실행을 요청했다. v8 이전 결과·묶음은 변경하지 않았다. 과거 복구는 이후 대상 필드/원본 수신 근거를 확정하고 원본 백업·격리 보정본·hash/revision/정렬·동기화 검증을 거치는 별도 단계다. 정상 UTC/epoch 순간이나 한국 시장 봉 키를 일괄 이동하지 않는다.

### v6/v7 실측과 v8 조건부 집계 준비

사용자가 v6와 v7을 모두 실행했고 원본 결과를 각각 `clock-audit/v6/result.json`, `v7/result.json`에 보존했다. 활성 source는 통합 v5이며 두 실행 모두 read-only/보정 없음/post-audit fence가 확인됐다. complete=true는 세 분리 단계의 완료이며 full_census_performed=false다. 동일한 첫1,500키의 SHA256은 `f61cc6b1ceee78f05e5f09184a1478a20bead2b73b2a44d5c074884fc2e5f12f`다.

| 단계 | v6 KST16:02:06 | v7 KST16:04:38 | 확인 범위 |
|---|---:|---:|---|
| 키 조회 | 3.673초 | 1.788초 | v6는 날짜 출력 문자열 Sort/Gather Merge, v7은 native PK Index Only Scan/Limit으로 Sort 없음 |
| 분봉 값 | 2.571초 | 1.074초 | 두 실행 모두 root Shared Read94,533/Hit1,333, 실제1,500행. Index Scan 첫 행 전 각각 약2.565/1.068초 |
| metadata 값 | 0.162초 | 0.032초 | 두 실행 모두 root Read406/Hit7,094, 실제1,500행/내부조회1,500회 |

관측된 blocking PID는 두 실행 모두0개다. v7 metadata는100ms 관측 시작 전 종료돼 표본0개이며 대기가 없었다는 증거로 쓰지 않는다. 캐시 순서·동시 부하가 통제되지 않아 v6/v7 시간 차이를 수정의 성능 효과나 v5 시간 초과 해결로 확장하지 않는다. 이번 측정에서는 분봉 값 읽기의 접근량/첫 행 이전 시간이 metadata보다 컸다. 인덱스 손상·디스크 고장·특정 writer lock은 확인하지 않았다.

buffer 통계는 상위 노드가 자식 값을 포함하므로 root와 자식을 합산하지 않는다. Shared Read는 PostgreSQL 캐시 밖 읽기이며 OS 캐시를 포함한 물리 디스크 누락 횟수와 같지 않다. Shared Written도 분봉에서239/9블록 관측됐다. 이는 조회 backend가 기존 dirty buffer를 배출한 수치일 수 있으므로 SQL read-only를 물리 파일 쓰기0과 동일시하지 않는다. 업무 데이터 DML/보정은 수행하지 않았다([PostgreSQL EXPLAIN 정의](https://www.postgresql.org/docs/current/sql-explain.html)).

후속 가설은 감사 SQL의 날짜 하한(`2026-10-01`)과 별도 tuple 하한(`1900-01-01`)이 함께 있을 때 불필요한 앞 구간 읽기가 발생한다는 것이다. 아직 확정 원인이 아니다. immutable v8은 같은 repeatable-read snapshot에서 기존/단일 시작 조건의 키와 분봉 값을 비교한다. 반환 키 전체와 행 수가 같고 root Shared Hit+Read 합계가 기존의1/10 미만이어야만 다음 census를 허용한다. 시간 단축이나 read→hit 전환만으로 통과하지 않는다. 첫 census의 키 전체 hash도 control과 일치해야 한다.

통과 시 분봉10/1~15·초봉10/13~15의 정해진 범위만 PK keyset으로 읽는다. 첫 페이지는 날짜 하한/상한만, 이후는 마지막 tuple 하한과 날짜 상한만 사용하며 cursor의 범위·고유성·순서·진행을 검사한다. 분봉1,500/초봉10,000행 묶음, 처음/이후 쿼리 shape의 인덱스 및 큰 Sort 거부를 유지한다. 각 SELECT15초/lock1초, 전체 약180초 한도이며 경계에서 실행 중인 조회는15초 안에 끝나므로 전체 시간이 조금 늘 수 있다. 실행 단계·계획·wait 관측을 남기고 취소/전체 한도면 부분 건수와 마지막 키를 보존한다. 두 범위가 모두 소진돼야 complete/full_census_performed=true다. 수집된 약9시간 lag는 source/origin/value_kind/completeness별 후보 분류이며 승인된 복구 수가 아니다.

v8 로컬22건은 시작/후속 조건 동등성, 캐시만 변한 경우 gate 거절, 전체 키 차이/취소 시 census 금지, 페이지 경계 중복·누락 방지, 첫 키 hash, timeout 부분 결과/실패 관측 보존, 정렬 거부, read-only timeout·host payload 동일성을 확인했다. 준비 검증에서 host 문자열 치환이 payload의 statement_timeout까지 바꾼 오류를 검출해 고쳤으며 최종 payload/host 일치와15초 제한을 다시 통과했다. 이는 로컬 모의 검증이며 실제 PostgreSQL 동등성/개선/전체 건수는 관리자 실행 후 판정한다.

NAS의 새 `clock-audit-20261015-v8` 경로만 게시했다. audit.py SHA256 `89fd1890c57a369c3be3c34ec0a4b528a75c468daa3dff9ccf219f7318d5b29e`, 셸 `276d0cbe231d3c770c9c9a5a5afdf864a2b1a47ddfc133e1c438d2c9f5bc8842`가 원격에서 일치하고 셸 구문도 통과했다. 관리자 인증/실행을 요청했다. 운영 src·DB schema/index·원본 데이터·설정·서버 수명은 변경하지 않았다. 이전 v2~v7 결과와 실패 기록은 보존한다.

### v7 분리 읽기 진단 준비 — v6 실행 보류

사용자의 다음 진행 요청에 따라 v5의 첫1,500행을 키 조회/분봉 값/metadata 값으로 나누는 별도 진단을 준비했다. v6 로컬14건/업로드 검증 뒤 최종 검토에서 키 조회의 `trading_date::text AS trading_date`와 비한정 ORDER BY 이름이 겹치는 문제를 발견했다. 원본 날짜 열 대신 출력 문자열 정렬이 선택되면 LIMIT 앞의 큰 정렬이 추가될 수 있으므로 v6 실행 보류를 안내하고 기존 묶음을 보존했다. 확인 시점의 v6 결과 파일은 없었다. 이는 NAS 제품 조회의 새 결함이나 v5 시간 초과의 확정 원인으로 간주하지 않는다.

새 immutable `clock-audit-20261015-v7`은 키 정렬을 `central_minute_bars.trading_date/minute/code/market`으로 명시하고, 필요한 관계의 Index Scan/Index Only Scan을 요구하며 1,500행을 넘는 Sort/Incremental Sort 계획은 실제 조회 전에 거절한다. 분봉 값 단계의 native PK 범위/조건/매개변수는 v5와 같고, metadata는 확보한 같은 키를 HH:MM으로 변환해 배열 매개변수와 LATERAL PK 조회로 읽는다. 본 연결은 read-only repeatable-read, 관측 연결도 read-only다. 각 조회15초 제한이며 같은 스냅샷에서 순서대로 수행한다. 시간 초과는 savepoint로 복구한 뒤 독립 metadata 단계를 수행하고 복구 실패면 즉시 중단한다. 성공한 분봉/metadata SELECT는 `EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON)`으로 실행 통계만 받는다. 테이블 ANALYZE/인덱스 변경/DB 보정/서비스 재시작은 없다.

새 정렬/큰 계획 거절을 포함한 로컬17건이 실패·오류·skip0으로 통과했다. 원래 분봉 조건·실패 뒤 트랜잭션 복구·같은 metadata 키 전달·실패 단계/plan/wait 보존·row budget·오류 원문 비노출을 포함한다. 로컬 모의 검증이며 NAS 실측 통과를 뜻하지 않는다. 키→분봉→metadata 순서의 캐시 영향은 기록하고 cold-cache 비교나 근본 해결로 확대하지 않는다. 전체 census는 실행하지 않는다.

v7 audit.py SHA256 `c340b78e6bd87a2d2cf4c845858d2602cee9f6d94c2e4113160b9b0a99b13db9`, 셸 `a1e6affcf29759dd17a2ae5f7de8e1497026e9fc2c2f8c37753405ece015721b`를 NAS에서 재확인했다. 새 경로에만 게시했고 v2~v6 원본은 유지한다. 비대화식 실행은 NAS 암호 필요로 거절돼 관리자 SSH 터미널 인증/실행이 필요하다. 운영 source/DB/설정/로그/기존 writer 연결·transaction은 변경하지 않았다. 근거는 `clock-audit/v7/`이며 단계별 수락은 실제 결과로 별도 기록한다.

### v5 실측 — 첫 페이지의 DB 파일 읽기 대기

사용자가 계측 v5를 실행했다(KST15:31:15). EXPLAIN은0.007초에 완료됐고 분봉/metadata 모두 PK Index Scan·Nested Loop 계획이었다. 같은 첫1,500행 실행은15.002초 뒤 statement_timeout(SQLSTATE57014)으로 종료됐다. 따라서 이번 재현에서 느린 구간은 계획 작성이 아니라 페이지 실행이다. 실행 중 표본577개 중544개가 IO/DataFileRead,33개는 wait event 없음이며 blocking PID가 있는 표본은0개다. 표본 누락/절단/probe error는 없었다. 표본 비율을 실제 경과시간 비율이나 CPU 사용률로 바꾸지 않는다.

확인된 사실은 **인덱스 계획에서도 해당 감사 조회가 DB 관계파일 읽기를 기다리다가 제한에 도달했다**는 것이다. 관찰된 범위에서 잠금 차단은 없었다. 어떤 관계/인덱스 파일의 읽기인지, 캐시/접근 패턴/스토리지 처리량/동시 부하 중 무엇이 근본 원인인지는 이 기록만으로 구분되지 않는다. 디스크 고장·테이블 손상·인덱스 부재·특정 writer의 lock을 원인으로 단정하지 않는다. 다음 원인 축소에는 동일 키의 base bar 읽기와 metadata 읽기를 따로 계측하거나 원본 백업의 별도 복사본에서 buffer/IO를 비교하는 증거가 필요하다. 장중 같은 전체 census를 반복하거나 timeout을 늘려 해결했다고 표시하지 않는다.

계측 수집은 됐지만 전체 census는 실행하지 않았고 complete=false다. 전체 영향 건수와 과거 DB 복구는 미완료이며 두 종목의381개 후보를 전체 건수로 확장하지 않는다. read_only/보정 없음/post-audit fence, 후속 health=ok·diagnostics idle·trace OFF·기존 통합 v5 활성 유지를 확인했다. 감사 v2~v5 원본은 유지하고 단계/대기 집계는 `clock-audit/measured-analysis.json`에 기록했다. 이번 작업에서 제품 src/DB/index/schema/운영 설정/서버 수명은 바꾸지 않았다.

### v3 실제 계획·API 표본 대조와 v4 취소

사용자가 실행한 v3(KST15:15:01, 활성 source 통합 v5)는 여섯 조회 모두 성공했다(각0.016~0.231초). 전체 테이블의 `reltuples`는 분봉 약1,081만/초봉1,575만/메타데이터1,610만 **통계 추정**이며 실제 건수가 아니다. 이전 넓은 범위 집계의 EXPLAIN에는 병렬 Seq Scan·Hash Join·Sort가 계획됐다. ANALYZE를 실행하지 않았으므로 그 계획만으로 실제 취소의 병목/lock owner를 확정하지 않는다.

직접 분봉 PK 표본10행의 메타데이터 null은 감사 SQL의 연결식 오류였다. PostgreSQL time을 그대로 text로 연결한 `HH:MM:SS`와 저장 observation_key의 `HH:MM`이 달랐다. 실제 제품 `PostgresMarketBarsStoreMixin.load_minute_bars`는 이미 `to_char(b.minute,'HH24:MI')`로 연결하므로 제품 코드를 바꾸지 않았다. 기존 인증 GET 6회로 두 종목(005930/475150), 10월13~15일 KRX 분봉2,635행을 읽었고 같은 표본10행의 updated_at이 DB 감사와 전부 같으며 메타데이터도10/10 존재했다. 키움 요청·수집·DB 보정은0이다.

두 종목의 완료 실시간/actual 표본에서 가용시각이 분 종료 뒤 약9시간인 후보는10/13일11행·10/14일13행·10/15일357행, 합381행이다. 10/15 정상 지연0~60초인 완료 실시간 행은96개다. 이는 두 종목 최신 projection 표본의 기계적 분류이며 전체 DB 건수/보정 승인 수가 아니다. partial/in_progress와 장후 query/estimated는 별도로 기록했다. 장후 조회의 수신/저장 시각은 봉 발생시각과 같을 필요가 없으며 updated_at과 metadata.available_at의 후보 수 역시 다르므로 필드를 일괄 이동하지 않는다. API의 현재 RAM overlay2행도 저장 확정 행과 구분한다. 상세는 `clock-audit/api-v3/analysis.json`이다.

사용자는 작은 PK 묶음·LATERAL metadata 조회의 v4를 실행했다. KST15:24:38, read-only repeatable-read에서15.05초 뒤 SQLSTATE57014/statement_timeout으로 종료했고 complete=false/queries={}다. 보정 없음·post-audit fence는 확인했다. 전체 건수는 얻지 못했다. v4는 쿼리 종료 뒤에만 계획/부분 결과를 붙이는 구조여서 이번 실패가 EXPLAIN인지 첫 페이지 실행인지 기록이 없다. 이 계측 누락을 인정하며 인덱스/IO/잠금 중 특정 원인을 단정하거나 시간 제한을 늘려 재실행하지 않았다.

후속 감사 v5는 **같은 SQL/매개변수의 첫 분봉 페이지만** 재현한다. EXPLAIN과 페이지 실행 단계를 각각 기록하고, 첫 페이지 실행 전에 계획을 보존·인덱스 조건을 검사한다. 활성 릴리즈의 기존 `_execute_with_postgres_wait_probe`를 재사용해 같은 backend의 state/wait_event/pg_blocking_pids를 관찰한다. 본 조회15초/관측조회500ms 제한이며 두 연결 모두 명시적 read-only다. 전체 census·보정·서비스 재시작·프로덕션 코드 변경은 없다. 진단5건이 통과했고 실제 관측 함수의 실패 callback 보존도 DB 연결 없이 확인했다. v4의 묶음/분키/계획/순서/범위 제한10건은 별도 통과했다. 이들은 로컬 진단 검증이며 NAS 실제 계측 성공은 아니다. 관리자 실행 후 단계/대기 증거가 필요하다.

v5 감사 파일 SHA256은 `9d594658930839f45dd23490b9717819d7298df19e073675f4c14d5398081850`, 셸은 `5f553465ce727fd558154188854d1550376df766590c339fdf3d278ac6042eae`다. NAS 셸 구문과 해시를 확인했고 재사용 관측 코드의 로컬/활성 NAS SHA256은 모두 `082d2c93c30b3ab6f39ff3897d5ecb36b2df6868b8f7a9172a2e65294d21f43a`다. 비대화식 `sudo -n id -u`는 암호 필요로 거절돼 관리자 SSH 터미널 인증이 필요하다. 이전 v2/v3/v4 묶음과 실패 결과는 보존한다.

### 운영 DB 읽기 감사의 실패와 후속 범위

관리자 읽기 감사 v2는 `2026-10-15T14:54:08+09:00`에 통합 v3에서 실행됐다. 분봉·초봉 범위 집계는 `QueryCanceled`, 컬럼 조회는 `ProgrammingError`로 종료됐고, read_only/repairs_performed=false/post-audit fence가 확인됐다. 영향 건수는 얻지 못했으며 0건으로 해석하지 않는다. v2는 각 SELECT에 statement_timeout=15초/lock_timeout=1초를 설정했지만 오류 SQLSTATE와 서버 사유를 저장하지 않아 취소의 세부 원인·blocking owner·실행 계획은 미확정이다.

컬럼 조회 오류는 매개변수가 없는 `LIKE 'central_%'` 쿼리에 빈 tuple을 전달해 psycopg의 `%` placeholder 처리가 개입하는 진단 코드 문제다. 새 immutable v3는 비어 있는 매개변수를 `None`으로 전달한다. 큰 집계는 다시 실행하지 않고 `EXPLAIN (FORMAT JSON)`만 읽으며 ANALYZE는 사용하지 않는다. 컬럼/인덱스/통계 추정과 10월13~15일·14:20/14:25·475150/005930의 최대12개 정확한 PK 분봉만 조회한다. 통계 추정과 표본을 전체 영향 건수로 사용하지 않는다. 각 쿼리의 elapsed/SQLSTATE와 허용 목록의 statement_timeout/lock_timeout 사유만 기록하며 임의 예외 원문은 출력하지 않는다.

v3의 정상·취소 두 모의 실행은 빈 매개변수와 `%` literal, 읽기 전용 옵션, 계획 조회만 수행, 12개 PK 제한, 실패 뒤 다음 조회, 안전한 오류 분류를 확인했다. NAS 셸 구문/파일 hash도 일치한다. 실제 v3 실행·계획 평가·전체 영향 범위 산정은 남고 운영 DB 보정은 하지 않았다. 근거는 `data/diagnostics/nas-integrated-validation-20261015/clock-audit/`다.

추가로 실제 로컬 psycopg의 쿼리 변환기에 동일 SQL을 DB 연결 없이 전달했다. 빈 tuple은 `ProgrammingError`를 재현했고 `None`은 `%` literal을 그대로 보존해 통과했다(`v3/parameter-reproduction.json`). 이 재현은 컬럼 조회의 진단 코드 원인을 뒷받침하며 두 집계 취소의 원인 증거로 사용하지 않는다.

관련 정식 Runner 12 worker/115건이 실패·오류·skip0, 모든 프로세스 종료, 실행 전후 LF 지문 동일로 통과했다. UTC 날짜 경계→KST, epoch 동일 순간, offset 없는 기존 KST, 날짜 정밀도 유지, 누락/불명 문자열, 잘못 태그된 과거 값의 임의 보정 금지, 원본 run 불변과 실제 Qt 보고서 렌더링을 포함한다. catalog unit461/integration27도 통과했다.

직접 unittest로 먼저 실행한 15건 중 로그 temp 테스트6건은 sandbox AppData temp 권한 오류로 실패했다. 이 실행을 통과로 바꾸지 않았고, 테스트 조건을 낮추지 않고 기존 Runner의 승인된 사용자/격리 temp 경로에서 위 115건을 실행했다. 결과는 `data/diagnostics/nas-integrated-validation-20261015/kst-display/regression/run.json`이다.

KST 표시 수정은 현재 로컬 작업본에 반영됐으며 앱 재실행 시 사용한다. NAS의 통합 v3 활성화는 그보다 앞선 고정 후보이고, 이 후속 PC 표시 변경이 NAS release에 포함됐다고 표시하지 않는다. 서버 시계 수정은 이미 그 후보에 포함돼 새 정상 기록을 확인했다. 전체 all-local·commit/push·새 외부 CI와 DB 원본 변경은 이번 표시 회귀와 구분한다.

후속 v5 `2026.10.15-integrated-market-news-v5-6fafc127b0b95b1f`에는 이 표시 변경이 포함됐다. 동일 runtime의 NAS selected175/storage114 통과 후 활성화했고 새 build/source path·health 정상·운영 PG 보존을 확인했다. PC 화면은 이 checkout을 사용하는 앱 재실행 때 반영된다. 과거 DB 감사와 원본 보정은 배포와 별개로 위 미확정 범위를 유지한다.
### v10 장 종료 후 전체 저장 범위 확인 준비

사용자 장 종료 확인에 따라 KST20:27의 실제 NAS 상태를 읽었다. 활성 source는 `2026.10.15-integrated-market-news-v5-6fafc127b0b95b1f`, health 정상·diagnostics idle·trace OFF다. realtime phase는 WAITING_MARKET, observation_expected=false, 마지막 수신은20:00:00.020580·마지막 초봉 저장 성공은20:00:45.904648이며 pending/inflight/waiting_flushes=0·미해결 저장 실패는 없다. 원문은 `data/diagnostics/nas-integrated-validation-20261015/clock-audit/after-close/runtime-before.json`에 보존했다.

장후 보완의 configured/effective=true는 설정 활성 상태만 나타낸다. 현재 서버 로그의20시대 장후 재계산 게시·보완 미완료·보완 실패 표식은 모두0개지만, 완료 callback은 성공 로그를 쓰지 않으므로 이를 완료 증거로 해석하지 않는다. 고정 로그의 시각·분류 건수만 기록한 `after-close/backfill-markers.json`을 보존했다. 보완 완료와 데이터 고정 여부는 미확정이며 이 단계는 값을 변경하지 않는 조사다.

v10은 전체 테이블 COUNT나 전체 시각 census를 실행하지 않는다. 같은 읽기 전용 REPEATABLE READ snapshot에서 분봉·초봉의 PK 순서 최초/최종1행씩, revision 최초/최종 accepted_sequence1행씩, 카탈로그 통계/저장 용량/컬럼/인덱스, 고정4개 분봉 identity의 최신 revision 최대3개씩, 당일 장후 재계산 snapshot 최대1개를 읽는다. revision sequence 경계는 전체 시계열의 최소/최대 시각을 뜻하지 않는다. 재계산 snapshot 존재도 전체 보완 완료 증거로 사용하지 않는다. 네 표본의 revision 유무는 전체 lineage 검증이 아니다. 카탈로그 행 수는 통계 추정치이며 기존 v8/v9 집계에 합산하지 않는다.

실제 자료 조회는 EXPLAIN(ANALYZE 없음) 뒤 대상 Index Scan/Index Only Scan을 요구하고 큰 Sort·Seq Scan·Bitmap Heap Scan이면 실행 전에 거절한다. 조회15초/lock1초·전체90초 제한이며 남은 전체 예산에 맞춰 statement_timeout을 줄인다. 실패별 savepoint 복구·제한 초과/오류 결과 보존·오류 문자열 비노출·완료 여부 엄격 판정·호스트 source/fence는 유지한다. DB 백업·수정·보정·설정 변경·서버 재시작은 없다.

로컬 `v10/verify.py`의9건이 실패/오류/skip0으로 통과했고, NAS 고정 새 경로 `/volume1/docker/kiwoom-monitor/artifacts/clock-audit-20261015-v10`의 셸 문법과 파일 hash를 대조했다. audit.py SHA256은 `9a290c35d8ea54f02e3668b4fc6b6923ef6472e98565bd7ea14792fb8ee3801b`, audit.sh는 `831fbe57381c9171a0c2f480da6c6fae576ea99681a2c2f8ff82448c7975429c`다. 관리자 인증 실행과 실제 저장 범위/규모는 아직 대기다. 운영 코드는 수정하지 않았으며 앞선 정해진 기간의 후보 수를 DB 전체 오류 수로 확대하지 않는다.

### v10 실측 — 전 기간 저장 범위와 revision 표본

사용자가 관리자 인증 실행을 완료했고 결과를 SSH 읽기로 회수했다. KST `2026-10-15T20:32:23.082697+09:00` snapshot의15조회는0.791초에 모두 완료됐다. result SHA256은 `547ef2ce9680ad41783c86bc7344cb77c0af2677e538cf7f645c4b001eb7f703`이며 실제 결과의15조회/완료 상태·읽기 전용·보정/백업 없음·fence·정확한 활성 v5와 원본 bytes hash를 `v10/summarize.py`로 확인했다. 요약은 `data/diagnostics/nas-integrated-validation-20261015/clock-audit/v10/inventory-summary.json`이다. 전체 census를 수행하지 않았으며 이전 집계와 합산하지 않는다.

| 자료 | 실제 최초 PK 시각(KST) | 실제 최종 PK 시각(KST) | 카탈로그 추정 행 수 | 인덱스 포함 바이트 |
| --- | --- | --- | ---: | ---: |
| 분봉 | 2025-09-01 08:00:00 | 2026-10-15 19:59:00 | 10,809,818 | 3,116,195,840 |
| 초봉 | 2026-09-14 08:00:01 | 2026-10-15 19:59:59 | 17,330,238 | 4,467,597,312 |
| 관측 metadata | 이번 조사에서 시각 경계 미조회 | 이번 조사에서 시각 경계 미조회 | 16,095,736 | 4,908,539,904 |
| 관측 revisions | accepted_sequence 경계만 조회 | accepted_sequence 경계만 조회 | 18,433,152 | 26,810,105,856 |

봉 PK 경계는 정확한 snapshot 내 최소/최대 키다. `reltuples` 추정과 별도로 통계상 n_live_tup은 분봉10,927,606·초봉17,337,230이며 이 역시 정확한 COUNT가 아니다. sequence1~18,835,390은 revision 시간 범위·행 수를 증명하지 않는다. DB 전체 크기는51,959,101,107바이트(48.391GiB)로 인덱스와 관련 저장을 포함하며 압축 백업 파일 크기와 같지 않다. NAS `df -Pk`의 Available은34,676,078,348KiB였지만 백업 파일이나 실제 복원 검증은 아직 생성/수행하지 않았다.

고정4개 분봉 identity/source 중005930의10/13·10/15와475150의10/15는 각2개 revision(complete/in_progress)이 존재한다. 이6개의 available_at-received_at은32,395.874983~32,399.289004초다. 예를 들어005930의10/13 14:20분 완료 revision은 effective_at=05:20:00+00, received_at=05:21:04.129435+00, available_at=14:21:03.418439+00으로 수신보다 가용시각이 약9시간 미래다. source_id=kiwoom-websocket-0B, origin=realtime, value_kind=actual이며 clock_quality 표기는 source_timezone_confirmed다. 그 표기만으로 가용시각의 정확성을 보증할 수 없다. 이번에는 payload_json/source_ref 원문, payload hash 재계산·현재 metadata/projection과의 완전한 대응은 검사하지 않았다. 475150의10/13 동일 source/key 조회0개는 그 표본 결과이며 전체 원본 유실을 뜻하지 않는다.

당일 top20_index_reconciled snapshot 조회는0개다. 장후 보완이 실행 중/재시도 중/미실행 중 어느 상태인지는 이 결과로 구분하지 못하며 완료도 확정하지 않는다. 후속 KST20:34 health는 정상/WAITING_MARKET, 마지막 초봉 저장20:00:45.904648, pending/inflight/waiting_flushes=0·미해결 저장 실패 없음이었다. operator status도 정확한 통합 v5/source·diagnostics idle·trace OFF·active_runs 없음이다. 수집/운영 소스·설정·DB 값·서버 수명은 변경하지 않았다.

다음 복구 범위는 **원본 백업과 격리 복원 검증 → 실제 저장 기간의 필드별 분류 및 projection/revision/source payload·hash 대조 → 수정 전 키/값 manifest → 격리 보정본 검증 → 운영 적용**이다. 앞선 분봉10/1~15·초봉10/13~15 집계는 전체 DB보다 작은 기간과 다른 snapshot이므로 전 기간 오류 수로 사용하지 않는다. 정상적인 과거 query backfill의 늦은 가용시각, 정상 UTC/epoch, 봉 날짜/시각 키는 일괄 이동하지 않는다. 현재 승인된 보정 행 수는0이며 전체 복구 완료가 아니다.

### 원본 백업과 격리 복원 검증 준비

사용자의 다음 진행 지시에 따라 원본 백업과 격리 복원 검증 묶음 `clock-recovery-20261015-v1`을 준비했다. 현재 운영 source/runtime/API/writer/스키마는 수정하지 않았다. 기존 [NAS 백업 지침](../../deploy/synology/README.md#백업)의 온라인 pg_dump·실제 복원 시험 조건을 따른다. [PostgreSQL17 pg_dump](https://www.postgresql.org/docs/17/app-pgdump.html)와 [pg_restore](https://www.postgresql.org/docs/17/app-pgrestore.html)의 snapshot/custom archive/복원 오류 처리 계약도 확인했다.

운영 server의 기존 psycopg로 읽기 전용 REPEATABLE READ 연결을 열고 export한 snapshot을 같은 DB/user로 확인된 고정 운영 database 컨테이너의 단일 pg_dump에 전달한다. dump와 원본 내용 지문은 이 snapshot을 공유한다. 원본 지문을 완료한 뒤 연결을 rollback/종료한다. session 전용 읽기 전용·timeout·병렬0·명시 UTC/JSON 표현을 사용하며 운영 DB의 영속 설정을 바꾸지 않는다. PostgreSQL table snapshot은 진행 중인 writer를 정지시키지 않으므로 장후 보완 완료를 가정하지 않는다. 긴 snapshot에 따른 I/O·CPU와 오래된 tuple 유지 비용은 존재한다. 전체2시간, dump30분·복원45분·원본/복원 지문 각30분 이내이며 각 단계는 남은 전체 예산에도 제한된다. 생산 쿼리의15초 감사 한도를 전역으로 올리지 않는다.

단일 DB의 전체 논리 archive는 `/volume1/@kiwoom-nas-operator/clock-recovery-20261015-v1/database.dump`에만 보관한다. root 소유0700 새 디렉터리/0600 새 파일·no-follow/exclusive 열기·부분 이름·fsync·PGDMP header·성공/경고 확인 후 rename·전체 파일 SHA256을 사용한다. 이전 백업을 덮어쓰거나 재귀 삭제하지 않는다. shared artifacts에는 코드와 비밀 없는 진행/결과 JSON만 둔다. DB 내 보존 데이터는 archive에 포함되지만 cluster global role/tablespace 및 외부 `.env`/server-secrets/master/HMAC key 파일은 포함하지 않는다. 압축 archive 크기를 사전에52GB로 단정하지 않는다. 백업과 복원 공간은 시작 시 DB 크기의3배 또는128GiB 중 큰 값 이상의 headroom을 요구한다.

복원은 고정 PG image ID의 새 컨테이너에만 수행한다. 운영 postgres-data/server-data/socket/네트워크/포트를 mount하거나 공유하지 않는다. 네트워크 none·listen localhost·포트 게시 없음, PG 메모리768MiB(config 고정 값)·swap 동일 한도·1CPU/기존 PG cpuset·PID128 제한이다. 임시 PostgreSQL의 localhost trust는 이 namespace 내부에만 적용하며 운영 인증을 변경하지 않는다. 비교 reader는 고정 runtime image의 별도 비특권/read-only·cap-drop ALL 컨테이너이며 임시 PG의 loopback namespace만 공유한다(384MiB/1CPU/PID64, 운영 mount 없음). 전체 archive를 `pg_restore --exit-on-error --no-owner --no-acl`로 복원하므로 검증용 소유자/권한을 사용하고 운영 role/권한 복원까지 증명하지 않는다.

원본과 복원본에서 분봉·초봉·관측 metadata·관측 revisions·분봉 operation 원장의5개 테이블을 streaming COPY로 읽어 정확한 행 수와 행별 canonical JSONB의 SHA256 합(mod2^256)/XOR, 컬럼 계약 SHA256을 대조한다. 순서에 무관하고 중복 행의 개수는 보존한다. archive 자체 SHA256도 복원 뒤 다시 비교한다. 이는5개 테이블의 내용 지문 대조이며 다른 전체 테이블의 별도 내용 지문 전수 비교나 동시 변경 sequence 값의 단일시점 증명은 아니다. 다른 객체의 복원은 pg_restore 전체 성공/오류0으로 확인한다. 이 단계에서는 시간값을 고치거나 후보/승인 수를 산정하지 않는다.

오류/timeout/중단 때 이 job 고유 application_name과 같은 user에 속한 snapshot/dump backend만 종료하고, 생성 label/image/container ID가 확인된 임시 reader/PG만 정지·제거한다. 운영 server/database 컨테이너를 정지/제거하지 않는다. archive·부분 파일·stderr·manifest와 복원 PG 자료 디렉터리는 보존한다. 진단 잠금/source·운영 container identity·후속 health/idle fence와 임시 컨테이너 정리까지 통과해야 `valid_restored_backup=true/state=complete`다. 실패 시 자동 재실행·이전 파일 삭제는 하지 않는다. 한 번 실행한 고정 디렉터리는 후속 실행으로 덮어쓰지 않는다.

로컬16건이 실패/오류/skip0으로 통과했다. export snapshot 전달/원본 dump stream과 stderr 분리/내용 지문의 순서·중복·chunk 경계/모든 주요 단계 실패와 mismatch의 완료 거절/cleanup/운영 대상 보호/격리 mount·자원 제한/reader namespace/미확정 create 추적을 확인했다. 추가 검사의 최초 실패1개는 Windows Path의 역슬래시를 Linux 슬래시 문자열과 비교한 테스트 문제였으며 실패 결과를 `local-verification-before-path-fix.json`에 보존하고 테스트의 경로 비교만 정규화했다. 운영 검증 조건은 낮추지 않았다. NAS 셸 문법과 Python3.8.15 호스트/내장 bridge 구문 및 실제 파일 hash도 통과했다. 최초 원격 syntax 확인 명령의 quoting 오류는 실제 코드 실행 전 발생했고 shlex를 쓰는 읽기 전용 검증으로 바로잡았다.

근거 위치는 `data/diagnostics/nas-integrated-validation-20261015/clock-recovery/v1/`다. NAS 실행 코드는 `/volume1/docker/kiwoom-monitor/artifacts/clock-recovery-20261015-v1/run.py` SHA256 `3fb0c93a93b5df551444df71793938cd551c411b13673d42dbbfe83b9456542e`, backup.sh는 `eb38d9746122d2b4101c6c20c043aa67876b4cb7227770f1ecfa1370a98e1098`다. 후속 operator status는 통합 v5/정확한 source·health 정상·diagnostics idle·trace OFF를 유지했다. 관리자 인증 실행을 요청했으며 **실제 원본 백업/격리 복원/내용 일치 수락은 미확인**이다. 제품 배포·commit/push·새 CI는 수행하지 않았다.

### 백업 v1 실패 실측과 기존 archive 복원 v2

사용자가 실행한 v1은 KST2026-10-15 21:56:03에 시작했다. read-only export snapshot은21:56:05.087584, backend PID6587, DB kiwoom_monitor다. pg_dump는 성공·stderr0/header/fsync 후 archive5,648,562,856바이트와 SHA256 `2b3f2502178953e6517c4b7a5835d16dc332e2b623c45ac893799b3d395e9883`를 기록했다. 파일은 `/volume1/@kiwoom-nas-operator/clock-recovery-20261015-v1/database.dump`에 보존된다. 현재 회수한 결과의 archive 지문 기록이며, 후속 시점의 파일 bytes 재해시는 v2에서 다시 수행한다.

뒤이은 source_manifest_running의 마지막 table_progress는 central_observation_revisions/table_started다. KST22:39:12에 RuntimeError/bridge_failed로 종료했고 restore_completed=false·valid_restored_backup=false·cleanup_errors=[]·temporary_containers_removed=true다. 운영 보정/재시작은 없다. 후속 operator status는 health 정상·정확한 통합 v5/source 유지·diagnostics idle·active_runs 없음·trace OFF다. v1 실패 결과의 canonical SHA256은 `01ff327f44b5e6c735117de1dd67aadd3d036791a321693768eeba0cc6d446ee`다.

v1 bridge는 예외를 event/error_type으로만 출력하고 host event()는 failed 이벤트의 세부 정보를 저장하지 않고 bridge_failed를 던진다. table manifest도 전체 완료 이벤트를 받은 뒤에만 파일에 저장한다. 이 계측 누락 때문에 현재 공개 결과만으로 statement timeout·메모리·연결 등 실제 실패 원인을 구분하지 못한다. 15분 session timeout이 설정돼 있었다는 사실만으로 그 원인으로 확정하지 않는다. 실패를 반복 재현하거나 시간 제한을 올려 해결 처리하지 않았다. 고정 PID6587·실행 시각의 DB 로그에서 허용 목록의 사유만 추출하는 root 읽기 진단을 준비했지만 비대화식 sudo는 암호 필요로 거절돼 실제 로그 사유는 아직 미확인이다. 원본 SQL/비밀/stderr 원문은 공개하지 않는다.

원본 snapshot 연결은 이미 끝났고 완성 archive는 있으므로 v2는 운영 DB를 다시 dump/전수 읽기 하지 않는다. 부모 실패 결과 canonical hash와 archive bytes/hash를 모두 확인한 뒤, 새 관리자 전용 v2 디렉터리의 별도 PG에 기존 전체 archive를 복원한다. 기존 v1 파일·결과·경고 로그는 바꾸지 않는다. `pg_restore --exit-on-error --no-owner --no-acl`의 전체 성공과 정리/후속 운영 fence를 요구하고 기존 네트워크 none·포트 없음·고정 image/PG768MiB/1CPU 제한을 유지한다. 임시 PG 데이터는 후속 조사용으로 보존하고 생성 label/image/ID를 확인한 컨테이너만 제거한다.

검증 방법은 명시적으로 바뀐다. archive에서 선택 테이블의 SQL script를 출력만 하는 `pg_restore --data-only --schema=public --table=<고정 이름> --file=-`와, 격리 복원 DB에서만 수행하는 읽기 전용 pg_dump의 COPY 블록을 대조한다. [pg_restore 문서](https://www.postgresql.org/docs/17/app-pgrestore.html)의 DB 이름 없이 script를 출력하는 경로를 사용하며 archive script를 운영 DB에 실행하지 않는다. 분봉·초봉·관측 metadata·관측 revisions·분봉 operation의5개 고정 테이블에서 COPY header(컬럼 순서 포함) SHA256·정확한 행 수·행별 원문 bytes의 SHA256 합(mod2^256)/XOR를 비교한다. SQL 서문/주석은 제외하고 COPY 데이터의 escaping/NULL/UTF8/중복 행은 보존한다. 누락/중복 COPY 블록·다른 테이블·미완료 종료·컬럼 순서 차이·행 차이는 거절한다. 원래 운영 DB에 대한 별도의 JSONB 지문 검증은 여전히 미완료이며, 이 COPY 지문을 v1의 JSONB 지문과 직접 비교하거나 동일 검증이라고 표시하지 않는다. 이는 보존된 archive에 담긴 내용과 복원본의 일치를 검증하는 단계다.

v2는 테이블별 시작·종료·오류/returncode·elapsed와 부분 manifest를 즉시 남기며 원본 v1 failed 결과는 그대로 유지한다. 전체2시간/복원45분·각 COPY stream30분(남은 전체 한도에도 제한), 중단 시 자료 보존·완료 거절은 유지한다. 새로운 production DSN·psycopg·export snapshot·production backend 종료 코드가 포함되지 않음을 검사했다. PostgreSQL 로그 조회는 고정 실패 backend/시각의 읽기만 수행한다. 사유가 로그에 없으면 미확정으로 남기고 복원 결과와 분리한다.

로컬15건이 실패/오류/skip0으로 통과했다. 실제 COPY stream driver의 archive script 출력/격리 DB 대상, chunk/순서/중복/NULL/escaping/UTF8, 누락·미완료·다른 table·변경 컬럼 거절, 부모 결과/파일 변경 시 복원 전 차단, 단계 실패·내용 mismatch·정리와 후속 fence, 로그의 고정 PID 귀속/비밀 비노출을 확인했다. 준비 중 AST 추출 메서드의 들여쓰기 구문 오류는 로컬 compile에서 거절됐고 생성기에서 수정한 뒤에만 묶음을 게시했다. NAS Python3.8.15·셸 구문·파일 SHA256이 통과했다.

근거는 `data/diagnostics/nas-integrated-validation-20261015/clock-recovery/v1/result.json`과 `v2/`다. 새 실행 경로는 `/volume1/docker/kiwoom-monitor/artifacts/clock-recovery-20261015-v2/restore.sh`, run.py SHA256은 `d2756d4600ac869daf388d16bcf7d7fb725391e9e592f5ad786a9b83fa6b38e0`, 셸은 `df62294a3d84751607cbfa3a797b00b05176c3cb1336075a41170a1b68445549`다. 관리자 인증 실행을 요청했으며 실제 원인 로그·archive 재해시·격리 복원·지문 일치 수락은 아직 미확인이다. 운영 데이터 보정·제품 변경·재시작·commit/push·새 CI는 없다.

### v2 실측 — 원본 timeout 확정과 복원 전 Docker 거절

v2 사용자 실행은 KST2026-10-15 23:14:30.850~23:14:55.819다. incident_collected 뒤 보존 archive5,648,562,856바이트/SHA256 `2b3f2502178953e6517c4b7a5835d16dc332e2b623c45ac893799b3d395e9883`가 다시 일치했고, 자원 검사 이후 restore_start의 컨테이너 준비 구간에서 error_type=Rejected로 종료됐다. restore_running 단계는 시작하지 않았으며 restore_completed/valid_restored_backup=false, cleanup_errors=[], temporary_containers_removed=true다. 운영 재덤프/전수 읽기/보정/재시작은 없다. 후속 operator status의 정확한 통합 v5/source·health 정상·diagnostics idle·trace OFF는 유지됐다.

수집된 고정 PID6587의 DB 로그2줄은 UTC13:39:11.330(KST22:39:11.330)의 `statement_timeout`과 central_observation_revisions COPY를 확인했다. 따라서 v1 원본 지문 단계가 session statement timeout으로 취소된 사실은 확정됐다. 메모리/연결 장애로 확대하지 않으며 운영 timeout을 올려 반복하지 않는다.

v2의 host 오류 보존은 type(error) is RuntimeError일 때만 안전 사유를 저장해 nas_operator.Rejected의 문자열을 unclassified로 기록했다. operator의 last_docker_action/last_docker_failure_class도 cleanup Docker 호출에 앞서 저장하지 않아 실제 최초 거절의 구체 분류가 남지 않았다. 이 실행 코드의 계측 누락을 인정한다. 따라서 현재 결과만으로 어떤 Docker 옵션/환경/단계가 거절됐는지는 미확정이다. 기존 operator는 단일 CPU cpuset을 사용하고 새 복원 코드에는 추가 --cpus1 quota도 있으나, 그 차이만으로 원인이라고 단정하거나 무조건 제거하지 않는다.

후속 v3는 복원 재시도가 아니라 **동일 생성 인수의 create/inspect/remove만 수행하고 start하지 않는 사전 점검**이다. 실제 v2 restore_start가 생성하는 인수를 로컬에서 직접 추출해 이름/label 외 동등성을 검사했다. 최초 결과가 명시적 NanoCPUs/CFS 미지원 사유일 때에만 --cpus1 한 쌍을 뺀 기존 operator cpuset 프로필을 추가 생성 비교한다. 두 프로필 모두 single CPU affinity·동일 메모리/swap·network none·포트 없음·비특권을 요구한다. 알 수 없는 사유면 다른 프로필을 시도하지 않는다. 비교가 성공해도 커널의 실제 실행 시 affinity/메모리 강제까지 검증됐다고 보고하지 않으며, 컨테이너 기동과 복원은 수행하지 않는다.

새 root 전용 v3 경로의 빈 postgres 하위 디렉터리만 생성하고 기존 archive는 readonly mount 대상으로 참조한다. DB 읽기·dump·복원·보정·원본 파일 변경이 없다. Docker create stderr는 root0600 파일로 보존하고 공유 JSON에는 반환코드·분류·bytes/hash와 비밀 없는 profile 검사 결과만 기록한다. label/image/기동되지 않은 상태를 확인한 시험 컨테이너만 제거하며 운영 container와 기존 자료는 건드리지 않는다. 각 Docker 호출은 짧은 timeout을 가지며 예상 작업은 약1~2분이다.

로컬7건은 실패/오류/skip0이다. 실제 v2 생성 인수 일치, 명시 quota 거절 때에만 단일 옵션 비교, 미확정 사유의 추가 시도 금지, 원래 옵션 성공 시 변경 금지, foreign label 정리 거절, 비밀 비노출·기동/DB 호출 부재를 검사했다. NAS Python/셸 구문·파일 hash도 일치한다. 파일은 `data/diagnostics/nas-integrated-validation-20261015/clock-recovery/v3/`, 실행 경로는 `/volume1/docker/kiwoom-monitor/artifacts/clock-recovery-20261015-v3-preflight/preflight.sh`다. Python SHA256은 `7892f20196a31d15d3c17de2111427ff7897db4353e68a51f92f19c2690c4f1f`, 셸은 `d27a6d382a5d462e11d0ae2ed29dbcab4f2324e305a41009d70b69dd544074bf`다. 관리자 인증 실행을 요청했으며 실제 Docker 거절 사유·복원·내용 대조·유효 백업 수락은 아직 남는다. 제품 코드·스키마·운영 설정·commit/push·CI는 변경하지 않았다.

### v3 실측과 cpuset 복원 v4 준비

v3 사전 점검은 KST2026-10-15 23:21:01에 diagnostic_complete로 끝났다. v2와 같은 원래 create 인수는 반환코드1/cpu_quota_unavailable, stderr132바이트/SHA256 `d8ae5e658e38b86901abcaf1a29402fa0c4b830c423af798ddbafb0106504b91`였다. 그 확인 후 --cpus1 한 쌍만 제거한 기존 operator cpuset 프로필은 생성 성공·cpuset0·Memory805,306,368바이트·NanoCpus0·PidsLimitnull이었다. 생성 시 stderr114바이트/SHA256 `c55aa197fe1bf8a9032eb7b5fbd7b9ac68789867e74c4917277cefdd09d7dc45`는 private에 보존되며 공개 결과는 그 원문을 출력하지 않았다. 모든 시험 컨테이너는 시작하지 않고 정리했고 cleanup_errors=[]/post_fence_verified=true·운영 DB 작업/원본 변경 없음이다. 실제 CPU quota 기능 미지원이 v2 생성 거절 원인으로 확인됐다. PID의 실행 강제는 이 create-only 결과로 증명하지 않는다.

v4는 이 확인 결과를 canonical SHA256 `2d58ccbf197f70b7b671234afd43b8f4f249b4580b7cc8c504c304c3c9b76d81`에 고정한다. private preflight 결과나 config의 pg_cpuset이 달라지면 복원 전에 중단한다. 실제 create 인수는 --cpus1 제거 외에는 v2와 동등함을 AST 인수 대조로 검증했다. 기존 operator의 단일 논리 CPU cpuset 제한을 그대로 사용하고, PG 기동 뒤 /proc/self/status의 Cpus_allowed_list와 cgroup 메모리 제한이 단일0/768MiB 이내인지 확인한 뒤에만 expensive pg_restore로 진행한다. namespace none·포트 없음·고정 image·메모리/swap 요청·동일 archive readonly mount는 유지한다.

기존 nas_operator.run_job의 disk fixture는 root 소유 postgres 부모를0711로 만들고 내부 PGDATA는 PostgreSQL UID가 생성/소유한다. 새 복원 디렉터리에도 이 조건을 적용했다. fresh 자식만 no-follow fd로 열어 root 소유·group/other 쓰기 없음·정확한0711 적용을 확인한다. 바깥 private/job 부모는0700이며 archive는0600, 원본 파일/기존 디렉터리 권한은 바꾸지 않는다. 이 변경은 알려진 PGDATA 접근 계약을 반영한 것으로 v2 생성 거절의 별도 실제 원인이라고 주장하지 않는다.

Docker info의 PidsLimit capability를 실행 시 확인한다. 지원되면 실제 pids cgroup이128 이내여야 하고, capability가 불명확하면 중단한다. 미지원이면 기존 operator가 지원 여부를 보고하는 방식과 같이 pids_limit_supported/enforced=false로 명시한다. 이를128 PID가 강제됐다고 표시하지 않는다. 단일 PG 컨테이너·동시 복원 job1·max_connections10과 CPU/메모리의 실제 강제는 유지한다. PID hard limit 미지원은 결과의 실제 제약으로 남긴다.

계측도 좁게 보완했다. create/start/readiness/resource check 단계를 결과에 먼저 쓰고, Docker 거절 stderr는 root 전용 파일에 보존하며 비밀 없는 분류/bytes/hash/returncode를 즉시 기록한다. 첫 실제 실패의 단계와 operator.last_docker_action/failure_class는 cleanup 전에 저장한다. 안전한 Rejected의 사유도 RuntimeError 하위 타입으로 보존해 unclassified로 잃지 않는다. 정상적인 readiness 재시도는 실제 실패 기록을 가리지 않는다. 원본 live DB 재덤프/전수 조회·보정/재시작은 없고, v2의 archive/복원본5개 COPY 블록 대조와 SHA256 재검사·전체 복원 성공/정리/fence 완료 조건은 유지한다.

로컬27건은 실패/오류/skip0으로 통과했다. 기존15개 COPY/부모/archive/실패/정리 검사에 실제 메모리/CPU 한도 거절, PID 지원·미지원·불명/잘못 강제 상태, create 인수의 단일 변경, PGDATA 부모 소유·0711, private stderr/실패 분류 보존·preflight 변경 차단을 추가했다. 최초의 이식된 기존15건 중2건은 새 preflight fixture를 추가하지 않아 guard에 거절됐고 그 실패를 `base-before-preflight-fixture.json`에 보존했다. fixture에 실제 v3 결과와 cpuset0을 반영한 뒤 전체27건이 통과했으며 운영 guard는 낮추지 않았다. NAS Python3.8·셸 구문·파일 hash도 확인했다.

근거는 `data/diagnostics/nas-integrated-validation-20261015/clock-recovery/v3/result.json`과 `v4/`다. 새 실행 경로는 `/volume1/docker/kiwoom-monitor/artifacts/clock-recovery-20261015-v4/restore.sh`, run.py SHA256 `27c13eb4d1af305aaed831f5821025f2a9fcb413ba9084fa013fd28ea4d68af8`, 셸은 `9de466181a01b96ad7246c573e41df19ba8fd30b1ec589bac281f4346724cbee`다. 관리자 인증 실행을 요청했으며 실제 기동 후 자원 강제·전체 복원·내용 대조·유효 백업 수락은 아직 미확인이다. 기존 v1/v2 실패 결과와5.65GB 원본 archive는 보존한다. 제품 코드·스키마·운영 설정·commit/push·새 CI는 변경하지 않았다.

### 전수 v4 실측 — 대상4테이블 집계와 시각 후보 분류 완료

v4는 KST2026-10-16 05:07:47.591650~05:30:31.174035(1363.582385초/22분44초)에 census_complete로 완료했다. 원본 result SHA256 `2ca7d02e5253cb5ea59b5a51a7aa47a3226595d3efecb8a808ab494cd2b7eff1`다. v3 result pin·완료2단계/기존 원장 구간 지문/분봉406건의 gzip EOF·stream hash·246,007바이트/유일 복합 키·preimage 검증을 통과한 뒤, 남은 minute4,693,784행과 second17,337,169행을 끝까지 읽었다. v3 complete revision/metadata는 DB 재스캔 없이 그대로 인계했다. 로컬 capture에서도 carried_from_job 필드를 제외한 두 상태가 v3와 완전히 같고 기존 minute 구간도 동일함을 확인했다. 모든4테이블의 v8 전체 행 수/EOF·그룹 합계/후보 합계/구간 scanned/candidate 합계와 최종 지문 대조·cleanup_errors=[]/temporary_containers_removed/post fence가 통과해 snapshot_table_counts_verified/all_target_clock_history_scanned=true다. full_history_census_performed=false는 minute 외 모든 dataset kind/전체45테이블의 시각 의미를 검증한 것은 아니라는 기존 범위를 유지한다.

기준 자료는 **2026-10-15 21:56:05.087584 KST의 검증된 백업 snapshot**이며 현재 운영 DB 총량이 아니다. 분봉 범위2025-09-01~2026-10-15, 초봉2026-09-14~10-15를 읽었다.

| 테이블/필드 | 테이블 전체 행 | 시각 분류 범위 행 | 약9h 후보 | 후보 날짜 |
|---|---:|---:|---:|---|
| revisions.available_at 대 received_at | 18,894,683 | minute_bar18,756,047 | 809,872 | 9/14~10/15 |
| metadata.available_at 대 effective_at | 16,910,571 | minute_bar11,319,685 | 360,427 | 9/14~10/15 |
| minute.updated_at 대 KST 봉 키 | 11,698,784 | 11,698,784 | 174,631 | 9/8~10/15 |
| second.available_at 대 KST 봉 키 | 17,337,169 | 17,337,169 | 5,541,914 | 10/6~10/15 |
| 전체 조사 행 합계 | **64,841,207** | — | 고유 오류 합산 불가 | — |

revision 후보809,872건은 모두 실제 received_at 대비 약9h±60초의 realtime이며 후보 payload hash/effective·payload key/제안 시각이 effective 이후 조건을 통과했다. 이는 현재 projection과 operation 입력 hash/전체 계보를 대조한 수정 승인이 아니다. metadata의 event 기준 약9h±600초 후보360,427건은 realtime305,727/query54,700건이다. 분봉 updated_at/초봉 available_at도 event 기준이어서 독립 수신 근거가 없고 정상 backfill·봉 키 오류·실제 시계 오류를 이 집계만으로 확정하지 않는다. 서로 다른 revision/현재 metadata/봉 행은 같은 관찰에 중복 대응할 수 있으므로 후보 수를 더해 전체 고유 오류 건수로 표시하지 않는다. 후보 이외의 모든 행을 정상으로 선언하지도 않는다.

| 시각 분류 | revision(minute scope) | metadata(minute scope) | minute | second |
|---|---:|---:|---:|---:|
| 수신 또는 event 근접 | 17,943,605 | 3,031 | 389,198 | 11,706,704 |
| 약9h 후보 | 809,872 | 360,427 | 174,631 | 5,541,914 |
| 음수 gap | 940 | 0 | 12 | 88,551 |
| 기타 차이/맥락 필요 | 1,630 | 10,956,227 | 11,134,943 | 0 |

초봉 음수 gap88,551행과 분봉 음수12행은 모두10/15다. revision 음수940행은9/22 431·9/23 367·9/30 35·10/15 107행이다. 음수 gap의 정확한 크기/수신 경위는 이번 그룹 집계에 없으므로 음수9h나 특정 내부 원인으로 추정하지 않는다. 기타 metadata/분봉 차이는 오래된 query 보완을 포함할 수 있어 일괄9시간 이동하지 않는다.

새 minute 구간174,225후보/압축16,747,034바이트/SHA256 `10845d4c162a8d65bfbb47b8eb8e9638a9257596f031e06dc08c71c0fe01ce6a`, 전체 second 후보5,541,914건/압축536,755,713바이트/SHA256 `1906c7ef4a6eecb8d3eab90ef6836d4222643cc496a08368f8d57e1cd82cfa4d`다. gzip 닫기/fsync/최종 재해시와 모든 신구6구간의 compressed 지문 대조를 통과했다. 새 구간의 gzip을 끝까지 다시 해제하는 별도 EOF 재검증은 수행하지 않았으며 gzip_closed_and_fsynced와 verified_gzip_eof를 구분한다. 기존 minute406건은 이번에 EOF까지 재검증했으나 이전 결과 파일의 verified_gzip_eof 값을 덮어쓰지 않았다.

분봉 누적2,340페이지/이번 새 구간 최장 성공0.811초(이전 prefix 최장0.959초), 초봉3,468페이지/최장3.009초다. 이번 v4의5천행 단위에는 timeout 축소 재시도0회였고 v3 metadata의1회 축소 이력은 선행 결과에 남겼다. 이 값은 현재 조건의 조회 관측이며 cold-cache 성능·상위 저장장치 지연의 원인 제거를 증명하지 않는다. 별도 현재 status는 정확한 통합v5·health ok·diagnostics idle·trace OFF·active runs/paused workloads 없음이다. 운영 DB 조회/설정/보정/재시작·재덤프/새 전체 복원·권한 확장·제품 source 수정/commit/push/새 CI는 없다. approved_correction_rows=0/historical_clock_corrections_applied=false다.

대상4테이블의 전체 집계·시각 후보 분류 단계는 완료다. 다음 잔여는 후보별 projection/수신·revision/operation 관계, 음수/기타 gap 맥락, 확정된 PK/원본 값/hash 수정 manifest, 격리 선택 보정·cut/동기화/replay/재실행 무변경 검증과 운영 적용이다. 근거는 clock-census/v4-remaining/result.json·analysis.json·daily-summary.json·post-census-status.json이며 이전 partial/실패 결과는 그대로 보존한다.

### 전수 v3 실측과 잔여 분봉/초봉의 전체 조사

v3 실제 실행은 KST2026-10-16 03:57:41.852237~04:54:49.544281(3427.692044초)에 작업3420초 한도/정리 여유를 적용해 partial/stop_reason=scan_work_budget으로 종료했다. 정리 오류0·임시 컨테이너 제거·post fence는 정상이며 원본 archive/source/선행 결과와 기존 후보 파일 불변을 확인했다. 원본 결과 SHA256 `0f3984a3afcee145e3f80e52402277836713b59c624fb7ab0852b9e565dde4e4`다. v1b의 원장203,035건은 gzip EOF/CRC·stream SHA/438,719,251바이트·개별 preimage/유일키/미승인 상태가 일치했고 실제 compressed 파일52,657,563바이트/SHA256 `4a32f6e074a8f46af595cddb993a7f9b242e1db70f338e515d6fe5265bd58854`로 확정했다. 기존 실패 상태와 파일은 변경하지 않았다.

| 단계 | 상태 | 읽은 행 / v8 전체 | minute scope | 약9h 후보 |
|---|---|---:|---:|---:|
| revisions | complete | 18,894,683 / 18,894,683 | 18,756,047 | 809,872 |
| metadata | complete | 16,910,571 / 16,910,571 | 11,319,685 | 360,427 |
| minute | partial | 7,005,000 / 11,698,784 | 7,005,000 | 406 |
| second | 미시작 | 0 / 17,337,169 | 미시작 | 미조사 |

revision의 수신 기준 후보809,872건은 모두 receipt_backed_realtime_revision_candidate다. 수신 근접17,943,605행·음수940행·기타 차이1,630행을 별도로 남겼다. 현재 projection/operation hash와 전 계보를 대조한 운영 적용 승인은 아니다. metadata의 event 기준 후보360,427건은 realtime305,727/query54,700건이며 독립 received 기준이 없어 정상 늦은 query를 오류로 단정하지 않는다. metadata 기타10,956,227행·event 근접3,031행도 별도다. 서로 다른 revision/현재 metadata/봉 행 수를 합산해 고유 오류 건수로 만들지 않는다. 조회한4개 테이블의 다른 kind는 행 수만 확인했으며 minute 외 kind의 시각 의미까지 검증한 것은 아니다.

분봉 partial의 마지막 키는2026-09-09 15:07:00/123690/KRX,1,401페이지·최장 성공 조회0.959초다. 약9h event 후보406건/기타7,003,546행/event 근접1,048행이며2025-09-01~09-09 prefix에만 해당한다. revision은1천행 단위/15,215누적 페이지·최장 성공2.385초, metadata는초기5천행 이후 candidate_set/1/2026-09-18T13:32:00 cursor의5초 취소1회(5.721초)를 같은 cursor의1천행으로 줄여16,907페이지·최장 성공3.218초로 끝냈다. 취소 조회는 후보/건수에 반영하지 않았다. 새 revision 구간606,837건의 compressed SHA256 `521ac929e8571b5bdd9819548a0e75c0fb420b25488cedace3871bd9b31086a5`, metadata 구간 `f1914a67320aeeff0117f4828a83111c2bb6e32707b5e2baab50614293af4116`, minute 구간 `f57fbea189a4d8767679251c5bf4f945dfc0c9258b9ecf44a06205cc6e616d1c`다. 새 구간은 gzip 닫기/fsync/최종 파일 재해시를 통과했고 EOF 해제 재검증은 별도로 표시한다. 운영 DB 조회/보정/재시작·재덤프/새 전체 복원은 없고 승인0이다. 별도 current status도 정확한 통합v5·health ok·diagnostics idle·trace OFF·active runs/paused workloads 없음이다. 근거는 clock-census/v3-resume/result.json·analysis.json·post-census-status.json이다.

사용자의 나머지 전체 조사 지시에 따라 소규모 보정 전환은 보류한다. `clock-census-20261016-v4-remaining`은 기존 complete revision/metadata를 result pin·원장 구간 compressed SHA/건수/그룹 합계로 인계하며 해당 DB 행을 다시 스캔하지 않는다. minute 후보406건은 gzip EOF/CRC·stream hash/바이트/유일 복합 키/상한·preimage hash와 미승인 상태를 확인한 뒤7,005,000행 prefix 다음부터 읽는다. 남은 minute4,693,784행과 second17,337,169행, 합계22,030,953행만 새로 조사한다. 이전 원장 파일에 이어쓰지 않고 별도 구간을 기록하며 같은 accepted v8 snapshot에서만 합계를 유지한다. 종료 때 모든 구간 지문·4테이블 EOF/전체64,841,207행·정리/fence가 통과해야 대상 전수 완료다. 다른 시점 운영 DB의 현재 총량이나 minute 외 kind의 전체 시각 검증으로 확대하지 않는다. SQL5초·전체3600초/작업3420초 제한과 정확한 timeout의 같은 cursor 축소, CPU0/768MiB/network none/readonly/내구성 ON을 유지한다.

v4의 실제 준비는 로컬13건·기존 무암호 NAS23건(동일 단위13건+PostgreSQL10건)이 실패/오류/skip0으로 통과했다. 기존9개 PG 검사에 실제 분봉 복합 키의 앞2행 prefix 뒤에서 나머지3행만 읽고, 기존 후보1건/구간을 유지하며 신규 중복 후보0·전체5행/그룹/마지막 키/EOF가 일치하는 검사를 추가했다. 테스트용 합성 입력이며 실제700만행 prefix의 EOF 해제 검증을 수행했다고 확대하지 않는다. unit 검사는 잘못된 이전 결과/완료 건수/분봉 cursor/구간 합계·경로/hash/중복 key 거절, 기존 구간 분리, 완료2단계 스캔 금지, partial 정리/fence·정리 실패 미수락을 확인했다. test release `2026.10.15-integrated-market-news-v5-542e165c1d7ac4c9`는 제품 source hash가 같고 fixture/테스트3파일만 추가했다. job `bf490a7f9e66da2a8ab55427a8f75aba`는 cleanup_complete/post_job_fence_verified/active_release_and_containers_unchanged=true다.

`/volume1/docker/kiwoom-monitor/artifacts/clock-census-20261016-v4-remaining/audit.sh`의 runner byte는 해당 NAS23건 fixture와 동일하다. run.py SHA256 `89581d9b6087799078fa1697a7217a0b4fe3022a6c1986f516bcb2157238b8de`, 셸 `c9a2ab1914b9c54a79309f936d8db74b38b6141fba6c208d306baef0345d44e4`다. NAS Python3.8 import/소수 초 파서/분봉 cursor SQL·셸 구문/해시도 통과했다. 관리자 전용 원장과 보존 PGDATA는 일반 test worker mount 밖이므로 실자료 실행만 관리자 인증 대기다. 기존 결과/원장 보존·신규 별도 후보 구간·SQL5초/전체3600초·정리/fence·미승인0 경계를 유지하며 보정 SQL/운영 DB 조회·보정·재시작·재덤프/새 전체 복원/제품 source 변경/권한 확장/commit·push/새 CI는 없다. 근거는 clock-census/v4-remaining/verification.json·operator-test-reply.json·preparation.json이다.

### 전수 v2 실측과 동일 snapshot의 검증된 구간 재개

v2는 KST2026-10-16 03:44:07.284482~03:44:53.531265(46.246783초)에 diagnostic_complete로 완료했고 정리 오류0/post fence 정상이다. 결과 raw SHA256 `644614c99c55347d195bfda3372edeb47bba8a5436a837b0c3aff3b3703470fc`다. 보존된 v1b 오류 원문도 실제 root 파일에서 statement timeout과 일치했다. 기준 PK는4600064~4605063의5천행, 키 배열 SHA256 `51dd4a1dafdde5715c09590078cec5fb1550317a0000ef9126b6090042782856`이다. 원 조회가5.223초에 동일 취소로 끝났고 관찰20건 모두active/IO/DataFileRead였다. blocking PID와 observer 오류는 없었다. 같은 범위의1천행 분할은0.245/0.228/0.244/2.801/1.969초에 각각 완료됐고 뒤 두 구간에서도DataFileRead가 관찰됐다. 이후 원 조회는0.255초에 완료됐다. 분할 합계와 후속 원 조회의 전체5천행/minute scope3,716행/마지막 키/17개 분류 그룹이 일치했고 후보0건으로 빈 후보 지문도 일치했다. 이 구간에서 비어 있지 않은 후보 preimage 일치까지 실증했다고 확대하지 않는다.

후속 EXPLAIN ANALYZE는67.275ms·PK Index Scan이며 전체buffer hit3373/read0, IO read time0이었다. 앞선 읽기로 캐시가 채워진 순서의 측정이므로 cold 성능·분할 자체의 처리 속도 향상·디스크 고장/호스트 부하 원인으로 해석하지 않는다. 재현한 조회 취소에 데이터 파일 읽기 대기가 관여함은 확인됐지만, v1 당시 wait를 소급 증명하거나 그 아래 저장장치 지연 원인을 확정한 것은 아니다. 전수 재개·기존 원장 쓰기·운영 DB 조회/보정/재시작·재덤프/전체 복원은 없으며 승인0이다. 별도 현재 status는 정확한 통합v5·health ok·diagnostics idle·trace OFF·active runs/paused workloads 없음이다. 근거는 clock-census/v2-probe/result.json·analysis.json·post-diagnostic-status.json이다.

후속 재개 실행기는 기존 실패 실행을 성공으로 바꾸지 않는다. 동일 v8 accepted 복원본과 기존 source/backup/lineage/v2 결과에 고정한다. v1b 관리자 전용 gzip을 끝까지 해제해 trailer/CRC·전체 stream SHA256/바이트·후보203,035건·분류별 건수·유일 accepted_sequence/상한·각 preimage hash·미승인 상태를 확인한 뒤에만 cursor4600063 다음부터 읽는다. 이전 파일은 덮어쓰거나 이어쓰지 않고 그 최종 compressed 지문을 구간 참조로 보존한다. 새 후보는 새 관리자 전용 gzip 구간에만 기록한다. 서로 다른 snapshot의 부분 결과를 합산하지 않으며 기존4,600,000행과 새 구간의 행 수·그룹·후보 합계는 별도 구간 지문과 함께 기록한다.

revision은 검증된1천행 단위로 시작하며 다른3테이블은 기존5천행 단위를 쓴다. SQL 제한은5초로 유지하고 정확한 timeout 오류일 때만 같은 cursor에서 단위를1/5로 줄인다. 취소된 조회는 행 수/그룹/후보 원장에 반영하지 않으며 성공한 페이지의 마지막 native PK 뒤로만 진행한다. 단일 행도 한도를 넘으면 건너뛰지 않고 partial로 종료한다. EOF·v8의4테이블64,841,207행·후보 구간 지문·원본/소스/선행 진단 불변·정리/후 fence가 모두 통과해야 대상 전수 완료다. 시간 한도도 정상 완료로 승격하지 않는다. 이 방식은 읽기 지연을 제한된 단위로 처리하는 감사 실행 방식이며 저장장치 지연의 원인 제거를 뜻하지 않는다.

재개 v3의 검증은 로컬17건·기존 무암호 `kiwoom-nas test`의26건(동일 단위17건+임시 PostgreSQL9건)이 실패/오류/skip0으로 통과했다. 로컬과 NAS 중복17건을 별개의43개 검사로 합산하지 않는다. 실제 PG 검사에는 최초 data 조회에 timeout 오류를 주입한 뒤 같은 cursor의 축소 조회/EOF까지 완료해 정확한5행·후보1건만 gzip에 남는지 대조하는 검사가 추가됐다. 이는 결정적 오류 주입을 통한 재개 경계 검증이며 실제 디스크 지연을 합성 PG에서 재현했다고 주장하지 않는다. 이전8개 SQL/readonly/NULL/후보/hash/plan 검사도 함께 통과했다. 후보 test release `2026.10.15-integrated-market-news-v5-c49fdc034f2e5ec4`는 기존 제품 source hash를 유지하고 fixture/테스트3파일만 추가했다. 활성 pointer를 바꾸지 않았으며 job `b1e86713cfb277eb71e03eb42d8725ba`는 cleanup_complete/post_job_fence_verified/active_release_and_containers_unchanged=true다. 호출 대기 중 일반 process snapshot에서 operator Python의D(disk sleep)를 관찰했지만 상위 디스크 원인을 단정하지 않는다.

실제 준비된 `/volume1/docker/kiwoom-monitor/artifacts/clock-census-20261016-v3-resume/audit.sh`는 전체 runner byte가 NAS26건을 실행한 fixture와 동일하다. run.py SHA256 `ff1968c797c31adc6d58a589592b54bab647383df6b9b5c12c1ef22aa9797b28`, 셸 `96256565bdf1ff9fa081d2da1171782b753f21776f48cea3470552d3a94bce3a`다. NAS Python3.8 import/파서/timeout 오류 hash·셸 구문/해시를 통과했다. SQL5초·전체3600초(작업3420초+정리 여유180초), 기존 CPU0/768MiB/network none/readonly/autovacuum OFF/내구성 ON 프로필을 유지하며 신구 candidate 압축 구간은 각2GiB/해제64GiB 한도다. 기존 원장의 실제 EOF/지문 검증·실자료 재개·전수 완료는 아직 실행하지 않았다. 기존 test worker가 관리자 전용 원장/보존 PGDATA를 mount하지 않으므로 이 실자료 접근만 관리자 인증 실행 대기다. 운영 DB 조회/설정/보정/재시작·재덤프/새 전체 복원·권한 확장·제품 source 수정/commit/push·새 CI는 없다. 보정 승인0이며 정적 준비와 NAS 합성 검사 성공을 역사 시각 보정 수락으로 승격하지 않는다. 근거는 clock-census/v3-resume/verification.json·operator-test-reply.json·preparation.json이다.

### 전수 v1b의 개별 SQL 시간 초과와 실패 페이지 진단

v1b 실제 실행은 KST2026-10-16 03:31:29.401821~03:35:49.116650에 실패했다. 고정 결과 파일 SHA256은 `8c52812a517b32be66cf547aff8c7e93ee5f204178e14d03b3661020d9a37736`이다. revision4,600,000행/920페이지, minute_bar 범위4,558,888행, 마지막 accepted_sequence4600063, 후보203,035건까지의 부분 상태를 보존했다. 마지막 완료 조회의 최대 시간4.105초이며 다음 Docker exec는 returncode1, stderr53바이트/SHA256 `782b2ef42c44a5dd22332a5b3706728bc8bfa7ceb666bef1c3a9c169a3150c8f`다. 이 길이와 hash는 정확한 `ERROR:  canceling statement due to statement timeout`+LF 원문과 일치한다. 전체3600초 한도가 아닌 개별 SQL5000ms 취소로 확인했으며, 해당 조회의 실제 wait/IO/blocking 계측이 없어 지연 원인은 아직 미확정이다. 초기 first/next 실행계획은 PK Index Scan이며 전체 SeqScan이나 특정 WAL/디스크 원인을 단정하지 않는다.

metadata/분봉/초봉 단계는 미시작이다. 후보203,035건의 분류는 receipt_backed_realtime_revision_candidate이나 현재 projection/operation hash·계보 전체 대조와 보정 승인 근거는 아니다. 관리자 전용 gzip 원장의 compressed 크기는 마지막 진행 보고 시점 값이며 최종 gzip 파일 hash/무결성·행 수의 재확인은 아직 하지 않았다. 부분 결과를 전체 건수로 승격하거나 새로운 snapshot 건수와 합산하지 않는다. 임시 컨테이너는 제거됐고 cleanup_errors=[]다. 실패 경로의 post_fence_verified는 기록되지 않아 미완료이며, 별도 새 status 조회는 정확한 통합v5·health ok·diagnostics idle·trace OFF·active runs/paused workloads 없음이다. 원본 백업 복원 수락은 유지하고 운영 DB 조회/보정/재시작·재덤프/전체 복원·시각 보정은 없었다. 근거는 로컬 clock-census/v1/result.json·failure-summary.json·post-failure-status.json이다.

후속 `clock-census-20261016-v2-page-probe`는 전수 실행을 다시 시작하지 않는다. 보존 v1b 결과 hash/정확한 오류 원문/마지막 cursor·건수가 일치할 때만 동일 accepted v8 격리 PGDATA를 잠시 재기동한다. PK cursor4600063 다음5천개의 키를 좁게 읽어 범위를 고정하고, 원래5천행 SQL→1천행씩5개의 같은 범위 SQL→원래 SQL→EXPLAIN(ANALYZE,BUFFERS,FORMAT JSON)8개 대상 조회만 수행한다. 각 SQL5초 제한은 유지하고 별도 pg_stat_activity observer에서 pid/state/wait type/event/blocking PID/시작 후 시간을 기록한다. observer 실패는 따로 명시하며 표본 수를 대기 시간 비율로 해석하지 않는다. 성공한 원래 조회와 분할 합계의 행 수·scope·마지막 키·후보 preimage 정렬 hash·분류 그룹이 일치해야 비교를 수락한다. ordered/cache 영향과 원래 장애 당시 wait를 소급 확정할 수 없는 한계를 유지하며, 이 진단만으로 성능 수정/전체 집계 완료를 수락하지 않는다. 기존 원장은 이어쓰거나 수정하지 않고 SQL 출력 원문/오류는 새 관리자 전용 job에만 저장한다.

로컬12건은 실제 사건 pin·다른 오류 거절·고정 범위 분할/후보 불일치 거절·관찰 표본·실패 정리/fence를 확인했다. page_sql/validate_page/add_groups/plan 검사 함수는 기존 무암호 PostgreSQL8건을 통과한 테스트 fixture와 AST source가 동일하다. 신규 계측 자체의 실자료 실행은 아직 하지 않았다. NAS Python3.8 import/파서/오류 hash/분할 SQL·셸 구문·파일 hash는 통과했다. run.py SHA256 `a4783059e45fc4c8f8e2ba3abe8f75f17ebebf98f19f99fa2148e205cefd4089`, 셸 `921cdc21c3b8091d0af065c81e1ef2851d83587f25ad61a94dfebc6e0737a762`다. 전체420초(7분) 제한, CPU0/768MiB/network none/read-only/default readonly/fsync ON·정리/후 fence 조건을 유지한다. 이 보존 PGDATA와 원문 로그는 기존 test worker mount에 없으므로 이 실자료 진단만 관리자 인증 실행 대기다. 제품 코드/운영 설정/권한·commit/push/새 CI 변경은 없다.

### 전수 시각 분류와 후보 원장 준비

사용자의 다음 진행 지시에 따라 `clock-census-20261016-v1b-candidates`를 준비했다. 무암호 테스트를 사용자에게 넘기지 않고 기존 `/usr/local/bin/kiwoom-nas test RELEASE --profile selected --test tests.integration.test_clock_census_postgres.ClockCensusPostgresTests`로 실제 격리 PostgreSQL8건을 직접 실행했다. job `06f807b37832669695304e6c0e53b175`는 state=passed/tests8/failures0/errors0/skipped0·cleanup_complete/post_job_fence_verified/active_release_and_containers_unchanged=true다. 테스트 전용 source 후보는 실제 통합v5의 모든 제품 source/hash/contract/build를 유지하고 tests/fixtures/clock_census_logic.py와 tests/integration/test_clock_census_postgres.py만 추가한 `2026.10.15-integrated-market-news-v5-95a8c2d6e9ab4f16`이다. 활성 release는 기존 `...6fafc127b0b95b1f` 그대로이며 이 후보를 deploy/select하지 않았다. worker4GiB/CPU0,1·임시PG CPU0·RAM storage, source_state_equivalent=false로 합성 검증과 실자료 검증을 구분한다.

NAS 검사8건은4개 테이블의 실제 스키마에서 native composite key의 여러 페이지·종료/전체/분류 건수, 정상 늦은 query와 수신 차이 후보의 분리, NULL/음수 시각 차이, PostgreSQL JSON 직렬화 뒤 payload hash 재계산, 수신 근거 없는 초봉의 미승인, 읽기 전용 transaction의 UPDATE 거절을 확인했다. 작은 fixture의 plan 검사는 fixture에서만 enable_seqscan=off를 사용해 LIMIT/index 모양을 확인했으며 실제 대용량 계획/성능을 증명하지 않는다. 실자료 실행은 자신의 첫/다음 페이지 계획에서 중앙 테이블 Seq Scan을 거절한다. 최초 테스트 후보 게시 스크립트의 원격 문자열 개행 SyntaxError는 test-stage-error.txt에 보존했고 원격 실행 전 실패였다. 새 고정 ZIP(v1b)에서 재게시했으며 기존 ZIP은 덮어쓰지 않았다.

전수 실행기는 v2에서 검증된 root 격리 PG 생명주기/자원/소스/복원본 pin을 재사용한다. accepted v8 결과 canonical `1cec52712adfe63b024757968ca2fc0e0097ded8f7e5bd75bab42d7342f22170`·v2 계보 결과 `45eca0ea6070dc1e3d2acc357697f226875636b2ea311b0c0dc2192d22960e48`·archive 전후 bytes/hash·배포 source7파일에 고정한다. 기존 PGDATA의 보호 경로/PG_VERSION/pg_control·중복 mount·메모리 여유·빈 디스크12GiB 이상을 확인한다. 새 전체 복원·운영 DB 조회·운영 restart는 없다. 기존 검증 복원본을 CPU0/768MiB/network none/포트 없음/read-only/autovacuum OFF/fsync 등 ON으로 재기동한다. 이 격리 PGDATA에는 recovery/checkpoint 등의 물리 쓰기가 가능하다.

읽는4개 테이블은 revision18,894,683행·metadata16,910,571행·분봉11,698,784행·초봉17,337,169행으로 총64,841,207행이다.5천행씩 native PK keyset으로 읽고 DB 자체 비교로 cursor 진행을 검증하며 첫 페이지에 임의1900 sentinel/중복 시작 조건을 넣지 않는다. 분류는 minute_bar revision의 available_at-received_at(약9h±60초), minute_bar metadata의 available_at-effective_at, 분봉 updated_at/초봉 available_at과 KST 봉 키 시각의 차이(약9h±600초)를 각각 기록한다. revision/metadata 테이블의 다른 kind는 전체 건수 확인에만 포함하고 시각 적합성을 검증했다고 주장하지 않는다. 정상 근접·음수·시각 누락·기타 차이도 날짜/출처/완전성/값 종류별 집계한다. event 기준의 차이는 독립 receipt가 없어 늦은 정상 backfill과 오류를 확정하지 않으며 모든 기타 차이를 정상 처리하지 않는다.

약9h 후보는 전체 원래 행/PK/시각 필드/row canonical SHA256·제안 -9h 값을 관리자 전용 `<phase>-candidates.jsonl.gz`에 보존한다. revision 후보는 배포본 방식 payload hash/effective·payload key를 재계산하며 realtime/키/hash/보정 후보 시각의 effective 이후 조건이 맞으면 receipt_backed_realtime_revision_candidate로만 분류한다. current projection join·operation 입력 hash 재구성은 아직 없고 approved=false/production_applicable=false다. 확정 적용 manifest나 운영 보정 승인이 아니다. 모든 후보를 제안 값으로 바꾸는 SQL은 없다. 파일은 gzip level1, uncompressed stream SHA256/바이트와 compressed file SHA256/바이트를 별도 기록하고 gzip trailer까지 닫은 뒤 fsync/재해시한다. 단계당 압축2GiB·비압축64GiB 한도이며 원문은 공개 artifact에 쓰지 않는다. 초기 비압축 준비 초안 `...v1-candidates`는 실행 요청 대상이 아니며 v1b만 사용한다.

각 SQL5초/lock1초·전체3600초이며 마지막180초는 정리/후검사 여유로 둔다. 작업 한도는partial, 오류는failed로 남기고 원문 traceback·부분 원장·마지막 완료 페이지·집계를 보존한다.4단계 모두 EOF와 accepted exact count 일치, 후보 파일 hash·원본 archive/소스/이전 결과 후검사·소유 컨테이너 정리·운영 identities/idle/fence가 통과해야 전체 대상 필드의 snapshot census 완료다. 전체45개 DB 테이블이나 정상 payload 전수 hash 검증까지 완료했다고 확대하지 않는다.

실행기 로컬19건은 실패/오류/skip0(기존 분류12건 포함)이다. preimage 비변경·미승인, 잘못된 key/hash·수신 없는 후보·정상query 구분, 페이지 count/cursor/종료 검증, 압축 해제 뒤 원본행/두 SHA의 일치, table count mismatch·시간 한도 partial·query/cleanup 실패 시 미수락을 확인했다. NAS8건에서 실행한 SQL/후보/페이지 검증 함수는 최종 실행기와 AST source가 정확히 같으며 그 뒤 추가한 압축 저장/실행 생명주기는 로컬19건 범위다. NAS host Python3.8 import/가변 소수 초 파서·셸 구문·hash도 통과했다. 근거는 clock-census/v1/operator-test-reply.json·runner-verification.json·preparation.json이다.

기존 무암호 도구의 실제 test worker는 candidate/helper/작업별 결과만 mount하고 임시 PG를 만든다. parser는 test에 임의 host path/mount를 받지 않으므로 root 전용 accepted 복원본 PGDATA를 이 경로로 조회할 수 없다. 일반 검증8건은 직접 끝냈고, 이 보존 실자료 접근에 한해서만 관리자 인증을 요청한다. 실행 경로 `/volume1/docker/kiwoom-monitor/artifacts/clock-census-20261016-v1b-candidates/audit.sh`, run.py SHA256 `ee6bb0d8027d0f1e94fea6ef2439165e710a58f161e36fbd04e17ba4d3923f42`, 셸 SHA256 `abedc5cc509fb082dabe45a33507668a3688ff5fb302e7ba80571c518ab22596`이다. 실제 전수 결과·후보 계보 대조/manifest 확정·격리 보정/replay/no-op·운영 적용은 미완료이며 보정 승인0이다. 제품 코드/스키마/운영 설정·sudoers/일반 Docker 권한·commit/push·새 CI는 변경하지 않았다.

### 표본 v2 실측 — 정상 query와 과거 realtime 이력의 분리

v2는 KST2026-10-16 03:07:07.245833~03:08:04.770313(57.524480초)에 diagnostic_complete로 완료했다.61개 제한 자료 조회·소유 컨테이너 정리·원본 archive/accepted v8 결과/배포 소스 후검사·운영 post fence가 통과했다. 결과 SHA256 `0e955bea094ab9e008a48d34111dba64d7d84f2508953baaa86f27934f5ad6e0`, canonical SHA256 `45eca0ea6070dc1e3d2acc357697f226875636b2ea311b0c0dc2192d22960e48`이며 근거는 clock-lineage/v2/result.json·analysis.json·post-diagnostic-status.json이다. 표본은10/15 21:56:05 KST의 검증된 archive snapshot에 한정하며 현재 운영 전 기간 오류 건수가 아니다.

v1의 실제 실패는 `2026-09-19T14:26:21.56539+00:00`의5자리 소수 초를 Python3.8 datetime.fromisoformat이 거절한 것으로 확인됐다. 원본 traceback(802바이트/SHA256 `f5ea65ef5e62cc49f456f7db55ad2b159b11fec579c932d7e7283de961a92642`)·실패 당시 metadata·구형 파서 재현/별도 strptime의 exact microsecond가 모두 일치했다. `.565390`으로 파싱해 같은 시각을 보존했다. 이 수정은 진단 도구의 호환 결함에 한정하며 DB 시각 보정이 아니다.

요청 분봉10키 중9키의 봉/metadata가 존재하고10/13 14:20 475150 KRX는 이 PK 표본에서 봉/metadata/revision 모두 없었다. 이를 종목 전체 자료 유실로 해석하지 않는다. revision15건 전부 배포본 방식 payload hash 재계산과 effective/payload 키가 일치했다. 표본 source chain의 한도 도달·표본 밖 parent·parent sequence 오류는0이다.9개 query revision은 현재 봉 숫자와 모두 일치하고 metadata source/최신 가용시각도 대응한다. available_at-received_at은 -4.036794~-0.255042초다. 오래된 봉을 훨씬 뒤에 수집한 정상 backfill 표본도 포함하므로 effective 시각과의 큰 간격만으로 이동하지 않는다.

3개 분봉 키의 과거 realtime revision6건(complete3/in_progress3)은 available_at-received_at이32,395.874983~32,399.289004초로 약+9시간 차이다. operation marker는6건 모두 존재하며 당시 processed_at도 보존했다. operation_hash의 원입력 전체 재계산은 미수행이다. 이6건의 source는 현재 metadata source가 아니며 현재 query projection과 숫자가 달랐다. 이는 출처/확정 단계가 다른 저장 이력을 현재 projection과 구분해야 한다는 근거이며 숫자 손상 확정이 아니다. 현재 정상 query projection·가용시각을 이 과거 이력과 묶어 일괄 -9h 하지 않는다. 역사 이력의 수정·별도 정정/manifest·재생·가용시각 cut·재실행 no-op 검증은 아직 수행하지 않았다.

초봉9개 PK 중9/14 08:00:01의3개와10/15 19:58의3개는 available-effective가0.028763~1.086773초,10/13 14:20의3개는32,400.107480~32,400.224962초다. 후자는+9h 후보지만 초봉 테이블에 독립 received_at/revision 근거가 없어 자동 보정을 승인하지 않는다. 전 기간 분류나 전체 초봉 오류 건수로 확대하지 않으며 approved_correction_rows=0/historical_clock_corrections_applied=false다.

운영 DB 조회/재덤프/보정/재시작·전체 복원 재시도는 없다. 현재 status도 정확한 통합v5·health ok·diagnostics idle·trace OFF다. 후속은 전 기간의 필드별 분류와 PK/현재 값/hash 보정 manifest 및 격리 보정/replay/no-op 검증이다. 사용자 지적에 따라 기존 무암호 `kiwoom-nas test`의 에이전트 직접 실행 우선 규칙을 AGENTS/DEVELOPMENT_GUARDRAILS/NAS_OPERATOR_COMMANDS에 명시했다. 실제 설치 도구 `--help`의test 지원을 확인했으며, 일반 테스트 worker의 현재 mount/임시 DB 경계는 관리자 전용 archive/보존 PGDATA/원문 로그 접근과 구분한다. 이번에 별도 일반 operator test job을 실행했다고 보고하지 않는다. 제품 소스·스키마·운영 설정/권한·commit/push·새 CI 변경은 없다.

### 표본 v1 실패와 시각 파서 원문 대조 v2

lineage v1은 KST2026-10-16 03:01:06.160483~03:01:28.748310에 ValueError/reason=unclassified로 실패했다. 임시 PG의 기동·CPU0/768MiB 확인과 read-only ON/autovacuum OFF·내구성 ON profile까지 통과했다. 날짜별 분봉 window3개와2025-09-01 14:20 000660 KRX의 봉/metadata PK조회2개, 총5개 조회가 완료됐고 이후 첫 표본의 Python 처리 중 실패했다. SQL timeout/Docker 거절로 기록된 실패는 없다. 마지막 metadata 자료의 정확한 시각/traceback은 관리자 전용 파일이라 현재 일반 사용자 읽기로는 확인하지 않았다. 결과 파일 SHA256 `63a74cd84db2f16719c7ac2c69325b497b94fb0577a23713e45182016df59e26`, canonical `bc34bc78b8e595dff25b4ada3812ad2eeb451617bfc79d895c4152050465ed2e`에 고정했다. 원본 archive는 시작 전 hash 일치를 확인했고 운영 DB 조회/보정/재시작·전체 복원은 없다. cleanup_errors=[]/temporary_containers_removed=true다. post_fence_verified는false로 후검사 미완료를 숨기지 않는다. 별도 current status는 정확한 통합v5·health ok·diagnostics idle·trace OFF였다. 근거는 clock-lineage/v1/result.json·post-failure-status.json이다.

별도 읽기 전용 합성 입력 실험에서 NAS Python3.8.15의 datetime.fromisoformat은 초 미만 자릿수가1/2/4/5이면 ValueError,0/3/6이면 정상임을 확인했다. 로컬 최신 Python에서는 해당 가변 길이가 수용돼 앞선 로컬 검사가 이 호환 결함을 잡지 못했다. 이는 진단 파서의 재현된 결함이며 아직 실제 v1 실패의 원문 대조 완료를 뜻하지 않는다. PostgreSQL 원본 자료를 잘못된 시각으로 판정하거나 자료를 수정하지 않는다. 근거는 clock-lineage/v1/nas-python-reproduction.json이다.

v2는 기존 v1을 덮어쓰지 않는 새 `clock-lineage-20261016-v2-samples`다. 격리 PG 기동 전에 root 전용 v1 결과·traceback·sample-005를 읽는다. 고정된5개 조회의 마지막 metadata 경계, minute_sample→timestamp_summary→utc traceback, Invalid isoformat string의 strict 시각 형식/1·2·4·5자리, 해당 metadata의 available_at과 동일 문자열, 실제 NAS 기존 파서의 ValueError 재현이 모두 맞아야 다음 단계로 간다. 원인이 다르면 안전 요약만 남기고 새 PG를 시작하지 않는다. 실제 원문 전체·raw metadata는 공개하지 않으며 traceback hash/길이·허용 형식의 문제 시각·재현 분류만 기록한다. 파서는 소수 초를 오른쪽0으로6자리까지 채워 Python3.8에서 읽고 별도 strptime 결과와 exact datetime/microsecond를 대조한다. 초과 정밀도는 조용히 자르지 않고 거절하며 시각 값/-9h/epoch를 변경하지 않는다. 원본 실패 자료의 동일성을 종료 때 다시 확인한다.

나머지 샘플 범위·원본/소스 pin·PGDATA 보호/겹침 거절·CPU/메모리/network none·read-only·3초 SQL·전체360초/정리·후 fence는 v1과 동일하다. 이번 진단의 local31건은 실패/오류/skip0으로 통과했다. 기존22건에 exact microsecond/offset/Z/공백/초과 정밀도 거절, 실제 파서 재현 필요, 다른 traceback/표본 불일치/미확정 때 PG 기동 금지를 추가했다. NAS Python3.8.15에서도 실제 frozen runner의 파서로0~6자리와UTC/KST offset·Z의 합성15건을 확인했고 기존 파서의4자리 거절을 같은 환경에서 재현했다. 이는 root 전용 실제 사건 파일 읽기 완료가 아니며 해당 검사는 관리자 실행에서 요구한다. NAS 셸 구문·hash도 통과했다. 실행 경로 `/volume1/docker/kiwoom-monitor/artifacts/clock-lineage-20261016-v2-samples/audit.sh`, run.py SHA256 `4363c3674f958c2e676c162306a8e2ae028a1ac03c5f28b6459b5285e27b7864`, 셸 SHA256 `cd676e83ce34aa73abcfd4e4a878da304d6679858e75450947f4896616b86bdc`다. 관리자 인증 실행 대기이며 실제 계보 결과·전 기간 분류/보정 manifest·운영 시각 보정은 미완료다. 제품 소스/운영 설정/commit·push·새 CI 변경은 없다.

### 검증 복원본의 시각·revision 표본 대조 v1 준비

사용자의 다음 진행 지시에 따라 `clock-lineage-20261016-v1-samples`를 준비했다. 단일 DB archive의 검증 복원 수락은 완료 상태를 유지하고 전체 복원을 반복하지 않는다. 고정 v8 결과 canonical SHA256 `1cec52712adfe63b024757968ca2fc0e0097ded8f7e5bd75bab42d7342f22170`·정상 재기동/내용 일치/정리/fence, 원본 archive 전후 bytes/SHA256, 실제 배포본 manifest/관련7개 소스 SHA256을 확인한다. 검증된 v8 격리 PGDATA의 PG_VERSION/pg_control·보호 디렉터리 경계를 검사하고 해당 경로와 상위/하위 경로를 마운트한 실행 컨테이너가 있으면 거절한다. 기존 root 전용 PGDATA만 단일 CPU0/768MiB/network none/공개 포트 없음 조건으로 잠시 재기동한다. SQL은 read-only이며 autovacuum OFF, fsync/full_page_writes/synchronous_commit ON을 확인한다. 이 격리 클러스터에는 복구·체크포인트 등의 물리 쓰기가 생길 수 있다. 원본 archive와 운영 DB·기존 실패 결과는 보존한다.

분봉은 기존005930/475150·10/13/10/15 14:20의4개 고정 키와2025-09-01 14:20 이후/10/01 14:20 이후/10/15 19:58 이후의각2개 저장 키로 최대10개를 읽는다. 정상·오류 표본의 존재를 미리 단정하지 않는다. PK 조회의 현재 봉·metadata와 출처별 revision을 최대21행씩 확인하고 한도 도달/표본 밖 parent는 미완전으로 표시한다. 배포본 `ObservationRevisionSource.from_observation`과 동일한 canonical fingerprint를 재계산해 저장 hash와 대조하며 available_at/received_at은 hash 밖에 있음을 유지한다. 실제 수신 시각 대비 약9시간 차이·-9시간 후보와 수신 시각의 차이, effective/key·payload/key, 현재 봉 숫자·현재 metadata source/최신 revision 가용시각·parent 순서를 기록한다. operation_id가 payload에 있으면 PK로 marker의 hash/processed_at만 읽으며 원래 operation 입력 전체가 없으므로 operation_hash 재계산 완료로 보고하지 않는다. raw payload/source_ref/operation identity는 관리자 전용 파일에만 남긴다.

초봉은2026-09-14 08:00 이후/10/13 14:20 이후/10/15 19:58 이후 각3개 키로 최대9개만 읽는다. available_at epoch·키 기준 시각·행 hash를 대조하지만 초봉 테이블에는 별도 received_at/revision이 없으므로 시간 차이만으로 보정을 승인하지 않는다. 분봉의 정상적인 늦은 query backfill도 effective 시각과 멀다는 이유로 오류로 분류하지 않는다. 이 진단은 전 기간 census·보정 manifest 확정·격리/운영 보정이 아니며 approved_correction_rows는0이다.

모든 자료 조회는 실행 전 EXPLAIN JSON으로 중앙 테이블 Seq Scan을 거절하고 LIMIT/인덱스 경로를 요구한다. 각 SQL3초/lock1초·진단 작업300초와 정리 여유를 포함한 전체360초 제한이다. 실패 시 부분 요약·raw 표본·traceback을 보존하고 label/image/ID가 일치하는 소유 임시 컨테이너만 정지·제거한다. v8 수락 결과·원본 hash·소스·운영 identities/idle/fence 후검사까지 통과해야 diagnostic_complete다. 완료여도 전체 역사 복구나 보정 승인을 뜻하지 않는다.

로컬22건은 실패/오류/skip0으로 통과했다. 배포본 실제 함수와 hash 재계산 동등성, availability/receipt가 hash를 바꾸지 않는 조건, payload 변경/UTC 정규화/naive 거절, 늦은 정상 query와 수신 없는 초봉 후보 구분, bounded key/SQL/plan·read-only 세션, 원본 수락/소스 변경 차단, PGDATA 겹침/격리 자원·기동 mount, 표본 raw 비노출, 실패/정리 거절 시 미수락을 확인했다. 첫 실행의2건 실패는 Windows Path fixture와 NAS POSIX 경로 비교 차이였으며 최초 원장은 local-verification-before-platform-fixture.json에 보존하고 테스트에만 PurePosixPath를 적용했다. NAS Python/셸 구문·hash도 통과했다. 실행 경로 `/volume1/docker/kiwoom-monitor/artifacts/clock-lineage-20261016-v1-samples/audit.sh`, run.py SHA256 `b2f5a1b28497097fcbbf6ee71a3de120ee98a6cf6b95527fa899717208da1581`, 셸 SHA256 `6e1017da241d0d7078fe15b07a04f4917f17d105cf6b180ab6df5e547ca5d5a1`이다. 현재 운영은 정확한 통합v5·health ok·diagnostics idle·trace OFF다. 관리자 인증 실행 대기이며 제품 소스/스키마/운영 설정·commit/push·새 CI 변경은 없다.

### v8 실측 — 전체 복원과 내용 대조 완료

recovery v8은 KST2026-10-16 01:07:46.832058~02:42:34.112942에 state/stage=complete, valid_restored_backup=true로 완료됐다. NAS 공개 결과를 다시 읽어 수락 조건을 확인했으며 근거는 `data/diagnostics/nas-integrated-validation-20261015/clock-recovery/v8/result.json`과 `acceptance-summary.json`이다. 결과 파일 SHA256은 `0a64f02575b9e4b5037eba979c8fe284e8bed4df72556dcaf3d4d3d09f90b413`, canonical SHA256은 `1cec52712adfe63b024757968ca2fc0e0097ded8f7e5bd75bab42d7342f22170`이다. 기존 v1/v2/v4 실패 결과는 성공으로 바꾸지 않고 그대로 보존한다.

전체 archive의 pg_restore는3253.747초(54분14초), 반환코드0으로 완료해 transaction COMMIT을 확인했다. 전체 검증은5687.281초(94분47초)였다. verbose stderr의 PostgreSQL 오류/timeout/OOM 표식과 pg_restore 경고는0이다. 정상 종료·같은 격리 PG 재기동,45개 public table 집합,32개 명시 index·73개 필수 constraint/FK identity, 전체103개 index의valid/ready가 일치했다. 복원 진행151개 표본을 보존했으며 이것으로 v4 당시의 미계측 wait까지 소급 확정하지 않는다. fsync/full_page_writes/synchronous_commit ON·minimal WAL·CPU0/768MiB/jobs1 조건이고 PID hard limit은 NAS 미지원으로 명시했다.

archive는5,648,562,856바이트/SHA256 `2b3f2502178953e6517c4b7a5835d16dc332e2b623c45ac893799b3d395e9883`로 원본과 동일하다. v1 snapshot은 **2026-10-15 21:56:05.087584 KST**이며 다음 수치는 그 백업에 들어 있는 실제 행 수다. 운영 DB의 현재 건수 또는 전 기간 시각 오류 수로 해석하지 않는다.

| 테이블 | 백업·복원본 실제 행 수 |
| --- | ---: |
| central_minute_bars | 11,698,784 |
| central_second_trade_bars | 17,337,169 |
| central_market_data_observation_meta | 16,910,571 |
| central_observation_revisions | 18,894,683 |
| central_minute_bar_operations | 7,294,717 |

5개 테이블 모두 raw COPY header SHA256·행 수·행별 SHA256의 sum-mod-2^256/XOR가 archive와 복원본 사이에 일치했다. 중간 rows_fingerprinted_so_far가 아니라 완료 fingerprint의 rows를 사용한다. 원본은 `/volume1/@kiwoom-nas-operator/clock-recovery-20261015-v1/database.dump`, 검증된 격리 PGDATA는 `/volume1/@kiwoom-nas-operator/clock-recovery-20261016-v8-full-restore/postgres/pgdata`에 보존한다. 임시 컨테이너는 제거됐고 cleanup_errors=[]/post_backup_fence_verified=true다. 현재 운영 status도 정확한 통합v5·health ok·diagnostics idle·trace OFF였다. 운영 DB 직접 조회·재덤프·보정·서버 재시작은 실행하지 않았다.

수락 범위는 단일 DB 논리 archive의 전체 복원과 주요5개 시각 테이블의 archive→복원본 내용 대조다. 격리 복원의 ownership/ACL 생략 계약은 유지한다. v1 독립 live source JSONB 지문은 미완료이므로 independent_live_source_manifest_verified=false이며 전역 role/외부 비밀 파일까지 포함한 호스트 복구 묶음으로 확대 해석하지 않는다.

다음 단계의 소스 기준은 dirty 로컬 checkout이 아니라 실제 통합v5 release의7개 관련 파일을 읽기 전용 수집하고 release manifest의 파일 SHA256과 대조했다. 근거는 `clock-lineage/v1/source-receipt.json`·`source-contract-check.json`이다. `domain/research_contract.py::ObservationRevisionSource.from_observation`은 available_at을 별도로 저장하지만 fingerprint에는 포함하지 않으며 `database_observation_writes.py`는 수신 당시 UTC received_at을 별도 생성하고 같은 payload_hash면 revision 추가를 생략한다. `database_market_bars.py::_minute_operation`의 operation_hash는 별도 canonical payload 계약이다. 따라서 hash 일치만으로 가용시각 정합성이나 보정 안전성을 판단하지 않는다. 실제 projection/revision/source payload·수신 시각/operation 계보 대조와 PK/현재 값/hash 보정 manifest, 격리 보정·replay·재실행 무변경 검증은 남으며 승인된 보정 행0·운영 보정 미실행이다. 제품 소스/운영 설정/commit·push/새 CI 변경은 없다.

### v7 비교 통과와 전체 격리 복원 v8

v7은 KST2026-10-16 00:50:13~00:56:50에 diagnostic_complete로 끝났다. 같은 archive의 첫300,000 COPY행 도달 시간은 control_before52.805931~53.871213초, 생성/COPY 단일 transaction9.431940~10.516313초, control_after50.092337~51.160119초다. 보수적 개선4.763298배·대조 경계 drift1.075438·data byte당 WAL 비율0.003145335(약99.685% 감소)로 사전 gate를 통과했다. fsync/full_page_writes/synchronous_commit은 모든 군에서ON이고 CPU0/768MiB 실제 제한·PID 미지원 기록·세 컨테이너 정리/fence를 확인했다. 각 군은90초 표본 한도로 종료했으므로 전체 COPY/COMMIT/백업 수락은 미완료다. 표본 속도는 약5.38/27.51/5.74MB/s였고 좁은 조건의 WAL 경로 비용 감소와 성능 차이가 확인됐다. v4 당시의 실제 wait나 전체 복원 시간으로 일반화하지 않는다. 원본 archive 해시는 동일하다.

v8은 canonical SHA256 `7e5f8e5ea4c672e85eb8eab72a48ee9f98155a4da416db0b9e168dac39edf035`의 v7 결과와 gate/fence/정리에 고정한다. 기존5.65GB archive를 재덤프하지 않고 새 root 전용 job/PGDATA만 사용한다. v7의 minimal WAL·fsync/full_page_writes/synchronous_commit ON 프로필과 CPU0/768MiB/network none/포트 없음/고정 image 조건을 유지한다. probe.list mount와 table/data section 필터를 제거하고 pg_restore --single-transaction --exit-on-error --no-owner --no-acl --verbose로 전체 archive의 schema/data/post-data를 복원한다. ownership/ACL 생략은 기존 격리 검증 계약과 같으며 운영 복구 명령이 아니다. 반환코드0 및 검토할 오류/경고 없음 이후에만 restore_transaction_committed=true다. [공식 pg_restore 동작](https://www.postgresql.org/docs/17/app-pgrestore.html).

복원 중15초 간격으로 activity의 단계·고정5테이블 이름 또는 기타 분류·wait/blocking PID·COPY bytes/tuples·CREATE INDEX phase/blocks/tuples를 기록한다. 미커밋 객체를 pg_class에 join하지 않는다. 원문 SQL/자료 행은 공개하지 않고 verbose stderr/예외 traceback은 관리자 전용 파일로 보존한다. 복원 최대5400초, 전체7200초이며 제한 시 부분 상태로 남긴다. 이는 시간 한도만 늘린 동일 재시도가 아니라 v7에서 같은 자료로 비교한 transaction 프로필을 적용한 전체 검증이다. 전체 성공/시간은 아직 미확인이다.

전체 복원 commit 후 소유 label/image/ID를 다시 확인해 임시 PG만 정상 종료한다. ExitCode0/비OOM을 확인한 뒤 같은 격리 PG를 재기동하고 readiness/resource/내구성 프로필을 재검사한다. 원본 TOC hash와45개 table/32개 명시 index/73개 constraint·FK identity를 파싱해 재기동 뒤 카탈로그에 존재하는지 대조하며 public table 집합은 정확히 일치해야 하고 모든 public index는valid/ready여야 한다. TOC 밖의 embedded CHECK 등의 제약이 추가로 있다고 실패시키지는 않는다. 각 원본 archive와 복원본의 주요5테이블은 기존 raw COPY header/row SHA256 sum-mod-2^256/XOR/행 수를 순서 독립적으로 전수 대조한다. NULL/escape/UTF8/컬럼 순서 의미는 기존 parser를 유지한다. v1 독립 live source JSONB 지문은 완료되지 않았으므로 independent_live_source_manifest_verified=false를 유지한다.

전체 복원/정상 재기동/카탈로그/5테이블 내용 일치/원본 전후 hash/소유 임시 컨테이너 정리와 post fence가 모두 통과해야 valid_restored_backup=true다. 중간 실패를 완료로 바꾸지 않으며 실패 단계·부분 manifest·PGDATA·원본은 보존한다. 운영 DB 직접 조회/재덤프/보정/재시작·기존 부분 클러스터 사용은 없다. COPY 지문 진행 건수도10초 간격으로 기록하되 rows_fingerprinted_so_far를 최종 검증 건수와 구분한다.

로컬23건은 실패/오류/skip0이다. 필터 없는 전체 복원/프로필 OFF·SQL 실패·시간 한도·경고 거절, 정상/강제 종료·foreign 컨테이너 구분, 카탈로그 누락/invalid index, raw COPY escaping/순서/header/불완전 블록, 각 단계 실패 시 정리/미수락, 비교 gate·원본 변경 차단과 내용 mismatch/테이블 누락 거절을 확인했다. NAS Python/셸 구문·hash도 통과했다. 실행 경로 `/volume1/docker/kiwoom-monitor/artifacts/clock-recovery-20261016-v8-full-restore/restore.sh`, Python SHA256 `4348b692dc03072d506d2a1d07641552f5a474c58ef20a819936132a40e48970`, 셸 `60ceb28953a261b2b2339f6f9279eb28beb2d2eee959a4aa19b661d46e0c408a`다. 관리자 인증 실행 대기다. 제품 소스/운영 설정/commit·push/새 CI 변경은 없다. 실제 전체 검증 수락과 이후 역사 시각 보정은 아직 미완료다.

### v6 실측과 durable COPY 비교 v7

v6는 KST2026-10-16 00:35:24~00:38:21에 diagnostic_complete로 끝났다. 실제 COPY는 마지막 유효 표본101.301초에503,959,345바이트/509,697행까지 진행했다. 최초0.002초 표본과의 구간 평균은4,974,968.61바이트/s·5,031.61행/s다.9개의 유효 PG 표본은 ClientRead1/대기 없음4/WalSync3/WALWrite1이었고 blocking_pids는 모두 비었다. 이는 해당 구간의 진행과 WAL 관련 대기 관찰을 뜻하며 대기 시간 점유율·전체 주원인·v4 당시 실행 단계를 증명하지 않는다. CPU 표본은 시작78.08%, 이후9.98~28.61%였고 관측 메모리 최대396.4MiB/768MiB다. Docker BlockIO0B/PIDs0은 실제 I/O/프로세스 부재를 뜻하는 유효 카운터로 확인되지 않았으므로 사용하지 않는다.

120.003초에 sample_time_budget으로 중단했고 target_copy_complete/full_restore_performed/valid_restored_backup은false다. 소유 임시 PG까지 정리했고 cleanup_errors=[]/post_fence_verified=true, archive SHA256 동일·운영 조회/보정/재시작 없음이다. 새 PG log에는ERROR/FATAL/OOM 표식이 없었지만 이전 v5 FATAL1개의 정확한 사유는 추가한 role/database missing 표식에도 해당하지 않아 미확정으로 남는다. 근거는 clock-recovery/v6/result.json·analysis.json이며 v6 결과 canonical SHA256은 `5f4be8eb3cea59c554079d8c65bb3bc26dc3491d1eb9e94c5e46e07d2151b4a3`다.

후속 v7은 [PostgreSQL17의 공식 대량 적재 안내](https://www.postgresql.org/docs/17/populate.html)의 CREATE/TRUNCATE와 COPY를 동일 transaction에서 수행하고 wal_level=minimal을 쓰는 조건을 비교한다. v6 기본 wal_level과 비교하는 단일 인과 실험으로 가장하지 않는다. v7의 모든 군에 wal_level=minimal/max_wal_senders0/archive_modeoff와 track_io_timing/track_wal_io_timingON을 동일 적용하고, fsync/full_page_writes/synchronous_commit은 명시ON으로 확인한다. 각 군은 새 PGDATA/CPU0/768MiB/network none/포트 없음 조건이다. 첫 군과 마지막 군은 pre-data schema를 먼저 commit한 뒤 data COPY를 별도 transaction에서 실행한다. 가운데 군은 pre-data와 data를 하나의 pg_restore --single-transaction에서 실행한다. 각90초씩 순차 실행하며 소유 컨테이너를 정리한 후 다음 군을 시작한다. 운영 또는 이전 부분 클러스터는 마운트하지 않는다.

고정 v5 archive TOC의 SHA256과45개 유일 public TABLE DATA 항목을 검사하고 revisions DATA 하나만 선택한다. 다른 TABLE DATA는 제거하며 large object/materialized view DATA/다른 schema/잘못된 membership은 거절한다. pre-data/data section만 선택하므로 post-data index/constraint는 실행하지 않는다. TOC의 sequence 설정은 같은 목록에 유지된다. observer는 미커밋 CREATE로 pg_class에 보이지 않는 relation도 pid/application identity로 관찰한다. COPY 대상 query 일치/단일 backend·blocking 없음과 pg_stat_progress_copy 실제 행 수를 요구한다. 비교는 같은 archive stream의 첫300,000행에 도달한 전후 표본의 시간 경계(폭5초 이하)를 사용하며 정확한 crossing 시각을 보간으로 꾸미지 않는다. WAL은 pg_current_wal_insert_lsn 기반 실제 증가량을 data 바이트로 정규화한다. pg_stat_wal 시간/횟수도 함께 보존하지만 활성 backend 통계 반영 지연과 표본 오차를 숨기지 않는다.

사전 판정은 두 대조군 시간 경계의 drift1.5배 이내, 느리게 잡은 B의 상한 시간이 빠르게 잡은 양 대조군 하한 대비2배 이상 개선, 데이터 바이트당 WAL 증가량이90% 이상 감소일 때만 gate_passed다. 순서 A/B/A이며 cold cache나 백그라운드 I/O 통제를 주장하지 않는다. 비교에 필요한 prefix 진행이 없거나 표본 간격이 크면 통과하지 않는다. 이 gate는 복원 방법의 제한된 성능 증거이며 전체 백업 복원/내용 지문 수락·v4 역사 원인 확정을 뜻하지 않는다. 전체600초 제한 후 정리하고 원본 archive의 전후 hash와 이전 결과/fence를 재검사한다.

로컬22건은 실패/오류/skip0이다. prefix 일치/보수 시간 경계, drift/속도/WAL 감소의 독립 거절, fsync off 거절, 미커밋 COPY observer 경계, 고정 TOC/section·fresh mount/resource, 세 군 정리와 두 번째 군 실패 시 첫 군 보존/중단을 검사했다. NAS Python/셸 구문·hash도 통과했다. 실행 경로 `/volume1/docker/kiwoom-monitor/artifacts/clock-recovery-20261016-v7-wal-compare/compare.sh`, Python SHA256 `d5012578b51b219055604d0de2a59e21776e51cc5d169a1d0e57cb1ecbd80cdf`, 셸 `b0c0271ab44202331f1f8793a54f4a207e8338d7d7e69f8263a2852f59a6b362`다. 관리자 인증 실행 대기다. 제품 소스/운영 설정/commit·push/새 CI 변경은 없다. 이후 실제 전체 복원·5테이블 내용 대조와 역사 시각 보정은 미완료다.

### v5 완료와 짧은 COPY 진행 계측 v6

v5는 KST2026-10-16 00:27:49~00:28:20에 diagnostic_complete로 끝났다. archive bytes/SHA256은 동일했고 CPU0/768MiB의 실제 제한·PID 미지원 기록·임시 컨테이너 정리·post fence를 확인했다. 공개45개 테이블은 생성됐지만 인덱스는0개였다. metadata heap2,762,539,008바이트, operations1,030,815,744바이트, minute1,369,178,112바이트가 있고 revisions/second heap은0이었다. operations reltuples7,295,089는 카탈로그 추정치이며 실제 행 수나 COPY 완료를 뜻하지 않는다. 자료는 데이터 적재 중 부분 복원 상태와 일치하지만 당시 정확한 COPY 대상·wait·성능 원인은 확정할 수 없다. v4 stdout/stderr는 모두0바이트다. v5 PG log의 FATAL 표식1개는 상세 분류가 없어 복원 오류나 무해한 readiness 메시지로 단정하지 않는다. 전체 복원/5테이블 대조/유효 백업 수락은 미완료다.

v6는 기존 부분 복원본을 재사용하거나 전체45분 복원을 반복하지 않는다. 새 job의 빈 PGDATA에 동일 CPU0/768MiB/network none/포트 없음 조건의 PG를 만들고, 원본 archive의 pre-data schema만 복원한 뒤 고정 central_observation_revisions의 COPY를120초 관찰한다. 관찰 결과는 pg_stat_progress_copy의 bytes/tuples, 별도 읽기 전용3초 SQL의 active backend wait/blocking PID/query 종류, 컨테이너 CPU/메모리/BlockIO/PIDs다. query 원문·자료 행·원문 오류는 공개하지 않는다. 이 표본은 revisions의 현 조건 처리량을 측정하며 v4 당시 실행 테이블/원인을 소급 증명하지 않는다. v5 FATAL 로그도 허용된 role/database missing 등 표식으로 분류한다.

기존 v5 결과 canonical SHA256 `2e7c634d0f745e85ab696cbcb4e638ef43779ca173b8a25f49826d1e50745096`와 archive 원본 hash에 고정한다. pre-data 실행90초, COPY 관찰120초, 전체360초 제한 후 별도 소유 컨테이너 정리를 수행한다. COPY가 진행 중이어도 관찰 한도면 sample_time_budget으로 기록하고 target_copy_complete=false다. docker exec client 종료만으로 DB backend 취소를 주장하지 않으며 label/image/ID를 확인한 임시 PG 자체를 정지·제거한다. 원본 archive는readonly mount이고 이전 부분 클러스터는 마운트하지 않는다. 전체 운영 DB 조회/재덤프/보정/재시작은 없다. 전체 복원/유효 백업 수락은 항상false다.

로컬14건은 실패/오류/skip0이다. 한도 종료를 COPY/백업 성공으로 바꾸지 않음, 실패/변경 시 기동 차단과 정리, 실제 fresh PGDATA/network/resource 경계, 고정 단일 테이블, 관찰 SQL의 row 조회 부재·비밀 비노출, 남은 관찰 시간이 짧을 때 신규 query/stats 호출 생략을 확인했다. NAS Python/셸 구문·hash도 통과했다. 실행 경로 `/volume1/docker/kiwoom-monitor/artifacts/clock-recovery-20261016-v6-copy-probe/probe.sh`, Python SHA256 `a0f3bc9bc413b372467e1d2a1df3e242f5897e532f9aaa4352d47d75775e2211`, 셸 `db14c152739c5c61c0a371f394460cd0c3b92b3ae2011a358211640680fd9282`다. 관리자 인증 실행을 요청하며 실제 처리량·wait 원인/후속 전체 복원과 내용 대조는 아직 남는다. 제품 소스/운영 설정/commit·push/새 CI 변경은 없다.

### v4 실측과 부분 복원 카탈로그 진단 v5

실제 v4는 KST2026-10-15 23:27:36~10/16 00:13:27에 실행됐다. CPU affinity0·memory805,306,368바이트가 실제 적용됐고 CPU quota는 사용하지 않았다. NAS PidsLimit capability는false로 PID hard limit 미지원이 확인됐다. restore_running 이후 Job.wait의 개별2700초(45분) 제한에서 process_timeout이 발생했다. 전체7200초 제한과 별개다. 오류는 SQL 실패로 확정되지 않았으며 당시 COPY/인덱스 단계·backend wait가 기록되지 않아 세부 지연 원인은 아직 모른다. failure_before_cleanup.docker_action=info는 직전 자원 확인 호출 기록이고 실제 pg_restore의 실패 action을 뜻하지 않는다.

archive5,648,562,856바이트/SHA256 `2b3f2502178953e6517c4b7a5835d16dc332e2b623c45ac893799b3d395e9883`는 동일했다. 임시 컨테이너는 정리됐고 cleanup_errors=[]다. 원본 백업·restore stdout/stderr·부분 PGDATA·실패 결과는 보존됐다. restore_completed/valid_restored_backup은false이며5테이블 내용 대조는 시작하지 않았다. 운영 재덤프/전수 조회/보정/재시작은 없다. 후속 현재 status는 정확한 통합v5·health ok·diagnostics idle·trace off였다.

v5는 시간을 늘린 복원 재시도가 아니다. 고정 v4 결과 canonical SHA256 `678690d6a4e3b662b6e008cc08acf83f46dd9a23d6f6ec8ce5baa70a478a63f4`와 원본 archive 해시를 확인하고, 보존 로그의 bytes/hash/허용 오류 표식만 공개한다. 고정 v4 postgres/pgdata를 no-follow로 열어 root 부모0711·PG_VERSION17·pg_control을 확인/별도 보존한다. 같은 디렉터리를 마운트한 실행 컨테이너가 있으면 중단한다. 외부 연결/공개 포트 없는 임시 PG를 단일CPU/768MiB로 재기동해 archive TOC와 PG 카탈로그만 읽으며 복원 SQL·테이블 본문 전수 조회·ANALYZE를 실행하지 않는다. 이 격리 PGDATA에는 복구/체크포인트 쓰기가 생길 수 있으므로 부분 클러스터 자체가 byte-불변이라고 주장하지 않는다. 원본 archive는readonly mount이며 v4 결과와 로그의 불변성은 후검사한다.

카탈로그 결과는 전체 public table/index 개수와 invalid index 개수, 고정5테이블의 존재·heap/total 크기·reltuples 추정치·인덱스 개수다. 추정치는 실제 행 수가 아니며 물리 크기만으로 COPY 완료를 증명하지 않는다. 이미 종료된 v4 backend의 과거 wait를 새 PG 상태로 복원할 수 없다고 명시한다. 개별 SQL5초, 진단300초 한도 후 별도 정리 시간을 두며 소유 label/image/ID가 확인된 임시 컨테이너만 정리한다. 실패도 전체 안전 요약을 출력하고 원문 로그는 관리자 경로에 유지한다. diagnostic_complete는 복원/유효 백업 수락을 뜻하지 않는다.

로컬12건은 실패/오류/skip0으로 통과했다. 부모/원본 변경 시 기동 차단, 기존 마운트 거절, 실패 시 정리, 카탈로그 읽기 전용/시간 제한, 실제 생성 함수에 복원/초기화 호출 없음, TOC 필수 항목, 비밀 비노출과 유효 백업 판정 금지를 검사했다. 최초1건은 Windows fixture가 Linux 경로 문자열과 달라 실패했고 실패 원장은 local-verification-before-platform-fixture.json에 보존한 뒤 fixture만 플랫폼 Path 표현으로 맞췄다. NAS Python/셸 구문·파일 hash도 통과했다. 실행 경로는 `/volume1/docker/kiwoom-monitor/artifacts/clock-recovery-20261016-v5-diagnostic/diagnose.sh`, Python SHA256 `4f19c1512e6a3cd078e616acb01f286f8ef407854884b017d14cbcc59185626b`, 셸은 `3672455bb27eb92b52eb98034bd0526c88f777264cd393ea7b6b983d57c6e107`이다. 관리자 인증 실행 대기이며 실제 세부 진단·후속 복원과5테이블 검증·역사 시각 보정은 남는다. 제품 소스·운영 설정·commit/push·새 CI 변경은 없다.


## 2026-10-18 R3-002·023 선택 경계 보완

로컬커밋 `c0cf7ce`에서 가격 실패 batch의 원거래일/최초dispatch시각과 최신peer·같은날고가MAX/다른날분리·명시close를 보완했다. high caller는 이미 받은 시각을 전달하고, retry가 저장시각을 새로 만들지 않는다. 가격/시총의 개별tick 정밀시각이라는 새계약은 추가하지 않았다. 원originmetadata가없는 legacy콜은 그 한계를 보존한다.

finalization retry와 체결시점pressure는 `datetime.min`을 결측값으로 비교하지 않고 존재시각만 기존 `as_kst`로 정규화한다. 정확5분/최대2회·60초표본/gap만료의 기존 의미를 유지했다. 실제 controller signal→snapshot enqueue와 native SQLite/종료를 포함한5모듈128건/동일전후5조건·실행ID/구조가드 통과다. 새제품 CI·PG·NAS·장중·실계좌는 미검증이다.

스냅샷 stale cutoff는 이미store provider로 연결돼있음을 현재본문으로 정정했다. app의 Ingestor/AutonomousTop20 생성시계 누락, dataset saved_at, 결측봉 fallback/legacySQL 잔여는 이 작업으로 완료되지 않았다. 다음 범위·F연결·전후수치와 실패기록 정본은 [R3-023 및 후속 원장](../../REFACTOR_BACKLOG.md#r3-023--시계-연결의-남은-host-fallback)을 따른다.

## 2026-10-18 R3-023 생성자·dataset receipt 후속

R3-023의 남은 생성자 시계 주입과 dataset saved_at 선택 경계를 로컬 커밋 `4e97fe1`에서 보완했다. 기존 app.server_now를 MarketDataIngestor/AutonomousTop20Service에 전달하고, SQLite/PG dataset saved_at은 기존 store._now_provider의 epoch를 쓴다. 관련6모듈 **273건/98.049초 통과**, source/계획·실행ID/worker·자식 종료·임시 정리를 확인했다. 구조 검사와 20번째 증분 원장·실행 대조도 통과했다. 새 계층·DB 경로·조회·clock polling·스키마 변경은 없다.

RAM projection의 게시시각과 dataset의 저장 receipt는 서로 다른 샘플시점이며 같도록 강제하지 않는다. 원시 epoch·원본 observation·naive KST snapshot key와 외부 UTC canonical 경계는 보존하고 과거 DB 자료는 변환하지 않았다. 실제 PostgreSQL은 실행하지 않았고 PG SQL/transaction fixture만 검증했다. 새 제품 push/GitHub CI·NAS 배포·실시간 키움·운영 DB/주문·로컬 전수 회귀는 미실행이다. R3-023 전체는 결측봉 host fallback/legacy SQL 잔여 때문에 진행을 유지한다. 다음은 `_bar_available_at`와 local bar의 결측 시각 경계를 좁혀 확인하며, 기존 정상 epoch·원본 시각·명시적 offline fallback을 일괄 변환하지 않는다. 전후 실측·관련 테스트·F03/F08/F09/F10/F13 판정은 [최신 R3-023 원장](../../REFACTOR_BACKLOG.md#r3-023-시계-주입dataset-저장시각--2026-10-18)을 따른다.

## 2026-10-18 R3-023 봉 관측 fallback·품질 시각

요약표83행 중 상세 최신 상태와 다른 R3-002·023·083의3행을 `5c4814a`에서 먼저 동기화했다. 이후 로컬 제품 `4280ca5`에서 서버 봉 관측의 결측/invalid 시각을 기존 provider로 전달하고, PC gap·빈 분 확인·시장지수 metadata의 누락3경로에 기존 writer 시계를 배치당1회 전달했다. 관련12모듈 **370건/188.485초 통과**, 구조·21번째 증분 원장/실행ID·소스 불변·worker/자식 종료·임시 정리를 확인했다.

중앙의 유효updated_at은원래epoch를보존하고missing/invalid일때만기존provider를소비한다. local명시available_at도보존하며gap/empty/index3누락경로만기존batch시계를전달한다. 미주입standalone/offline은공통KSTfallback이고replay고정합성2099/검증된finalclosure의유효epoch는원경로를유지한다. 원본/UTCcanonical·업무시각표현을혼동한9시간이동이나과거DB일괄변환은없다. R3-023 전체는 **진행**이다. 다음은 JournalWindow의 `_backfill_received`·`_daily_chart_received`·`import_live_bars`에서의 기존 시계 전달과 standalone/offline 경계를 좁혀 확인하고, SettingsBackup/일지 legacy SQL 시각 잔여를 이어간다. 이번에 끝낸 두 생성자·dataset saved_at·봉 helper/PC3경로를 재조사하지 않는다. 실제 PG·키움/장중·운영DB/주문·push/GitHub CI·NAS 배포·로컬 전수 회귀는 미실행이다. 상세 전후·검증·F 원장은 [최신 R3-023](../../REFACTOR_BACKLOG.md#r3-023-봉-관측-fallback품질-시각--2026-10-18)을 따른다.

## 2026-10-18 R3-023 JournalWindow·legacy 수락 시각

로컬 제품·검사 `f72a85f`에서 JournalWindow 현재 시각과 봉 receipt를 기존 query-client 시계에 연결하고, legacy 일지 수락 기록과 SettingsBackup 카탈로그 INSERT/UPDATE를 기존 서버 시계/KST로 저장하도록 보완했다. MainWindow→Drive v1/v2 복원에도 같은 동적 시계를 전달한다. 관련14모듈 **265건/167.970초 통과**. 구조·22번째 증분 원장·실행ID·소스 불변·worker/자식 종료·임시 정리를 확인했다. 영향 F01/F08/F10/F18/F22/F23/F24와 책임 계약을 갱신했다.

R3-023 전체는 **진행**이다. 다음은 실제 온라인 `TradeAnalysisPreparationService.prepare`의 `import_monitor_daily_bars(monitor_db, code, episode.ended_at.date(), 250)` 미주입 caller(현재148행 부근)다. 이 서비스는 현재 provider가 없으며 기존4인자 Protocol/호환은 보존했다. 이미 끝낸 Window·legacy·constructor/dataset·봉 helper는 재조사하지 않는다. 실제 키움/장중·운영DB/주문·외부 Drive·PostgreSQL·push/GitHub CI·NAS 배포·로컬 전수 회귀는 미실행이다. startup/offline 공통 KST fallback과 원본/스키마 migration은 유지하며 앱 전체 시계 통일·기능 전체 정상으로 확대하지 않는다.

`data/diagnostics/r3-023-journal-legacy-20261018/`의 `architecture.json`, `related-v1/run.json`·`summary.json`, `review-execution.json`, `INDEPENDENT_REVIEW.md`를 기준으로 한다.

## 2026-10-18 R3-023 사후분석 일봉 복사 시계

로컬 제품·검사 `442a83e`에서 JournalWindow가 기존 query-client의 보정 시계를 TradeAnalysisPreparationService에 지연 전달하도록 연결했다. 서비스는 prepare마다 한 번 받은 KST observed_at을 기존 일봉 복사 API로 전달한다. 수동 사후분석과 자동 AnalysisEnrichmentWorker가 같은 preparer를 소비한다. 관련9모듈 **102건/52.764초 통과**. 구조·24번째 증분 원장/실행ID·소스 불변·worker/자식 종료·임시 정리를 확인하고 영향 F08/F10/F18/F22/F24와 책임 계약을 갱신했다.

R3-023 전체는 **진행**이다. 이번 일봉 복사 caller는 완료했으며 다음은 같은 prepare의 `save_trade_analysis_setup(now 미전달)` 및 `journal_analysis_revision(now 미전달)`이 만드는 판정 updated_at/revision created_at이다. 실제 본문에서 기존 host fallback 소비를 확인했으나 이번 일봉 복사 범위 밖으로 남겼다. 같은 주입 시각을 넘기면서 계좌 범위·원자 저장·revision ID/hash·동일 ID 원래 created_at 보존을 검증한다. 끝낸 Window/legacy/dataset/봉 경계는 재조사하지 않는다. 실제 키움/장중·운영DB/주문·외부 Drive·PostgreSQL·push/GitHub CI·NAS 배포·로컬 전수 회귀는 미실행이다. startup/offline 공통 KST fallback은 동기화 완료가 아니며 앱 전체 시계 통일·기능 전체 정상으로 확대하지 않는다.

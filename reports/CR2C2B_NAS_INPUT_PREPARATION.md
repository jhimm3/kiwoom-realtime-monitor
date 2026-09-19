# CR2c2b — 같은 범위 NAS 연구 입력 자동 준비

2026-09-16. CR2c2a 누적 구현을 유지한다. 기존 NAS DB export API를 PC 연구 작업자에 연결했으며 새 날짜/최종평가 정책은 변경하지 않았다.

## 사용과 책임

지속 연구 일시정지·worker 종료 후 `새 자료 폴더`에서 기준 실험/상위 폴더를 고르고
`자동 등록 켜기`와 `NAS에서 새 자료 자동 준비`를 저장한 뒤 시작/재개한다.
기존 source는 NAS OFF로 유지한다. NAS 준비만 끄면 기존 준비 파일의 등록은 계속 사용할 수 있다.
앱의 기존 `data_source.json` 경로만 연구 DB에 저장한다. 작업자는 매 scan 현재 접속 설정을 읽으며
토큰을 source/job/manifest/결과에 복사하지 않는다. UI는 네트워크를 요청하지 않는다.

기존 `CentralContentClient`와 `scripts/export_research_dataset.py` writer를 사용한다.
기존 `research_data_source.py` reader가 완성 파일을 검증하고 기존 Repository/worker가 등록/복구를 소유한다.
새 Manager/Service/API/추가 worker 프로세스는 없다. 단순 전달 계층은 추가하지 않았다.

## 입력·출력 계약

| 경계 | 동작 |
| --- | --- |
| scope | 기존 기준 실험의 기간/subject/kinds/session/universe/order 계약. 평가/비용/날짜를 임의 이동하지 않음 |
| known backlog full | NAS API 요청/자료 준비를 생략하고 기존 연구부터 실행. source 실패로 세지 않음 |
| probe | 날짜별 관측 API limit=1과 as_of=end 테마 이력을 조회. count/ordinal/next_cursor/범위/hash 응답을 검사 |
| signature | 일별 observation revision_ids_hash와 테마 내용 hash. watermark/dataset ID만 변경되면 signature는 같음 |
| unchanged | 마지막 처리된 signature와 같으면 전체 관측 다운로드/파일 생성 생략. 메타데이터·테마 API는 확인 |
| changed | probe page를 첫 페이지로 재사용하고 해당 watermark/cursor를 이어 읽음. 중간 watermark/manifest/ordinal/hash 불일치는 실패 |
| storage | 승인 경로를 덮어쓰지 않고 root 내부의 임시 디렉터리/payload에 streaming 저장. manifest는 writer가 마지막에 작성 |
| verification | 기존 reader의 파일 hash/ordinal/sidecar/bundle 검증 + 고정 scope + 관련 evidence fingerprint 확인 |
| same evidence | 준비 결과가 기존 acceptance fingerprint와 같으면 임시 파일을 제거하고 새 폴더/job을 만들지 않음 |
| publication | `nas-<source prefix>-<fingerprint>` 새 디렉터리로 rename. 이미 있으면 실제 파일을 재검증하고 충돌 시 덮지 않음. symlink/경계 이탈 거부 |
| registration | 기존 CR2c2a job/budget/acceptance 원자 등록기로 연결. 기존 job·완료 결과는 변경하지 않음 |
| signature ack | 폴더 검사/등록 뒤 활성 source/NAS opt-in + campaign/owner/generation/live lease/RUNNING을 확인하고 저장 |
| failure/backlog | 등록 실패나 backlog 도달 때 signature를 ack하지 않아 다음 scan이 자료를 다시 확인함. 임시 cleanup과 완성 orphan 재사용으로 중복 확정 방지 |
| local/direct | NAS 설정이 없거나 직접 모드면 source 오류. 키움 TR/로컬 failover로 연구 자료를 보완하지 않음 |
| network failure | HTTP status만 source 이유에 남기고 서버 error detail은 복사하지 않음. NAS unavailable은 source backoff. 기존 worker/연구 실패와 별도 |

단일 export는 하루 이하의 명시 범위다. 다기간은 기존 frozen daily bundle의 selected_date/일별 captured range를 재구성한다.
새 날짜를 추가하지 않으며 bundle의 일별 파일/ordinal/세션 계약과 기존 continuous runtime reader를 유지한다.
테마 1000개 제한/잘림은 기존 reader 정책을 그대로 사용하며 완전한 테마 이력으로 단정하지 않는다.

## 저장·자원·복구

로컬 연구 v14 `campaign_nas_input_preparation`은 source에 세 필드만 추가한다.
nas_auto_prepare=0, nas_config_path='', remote_signature='' 기본값이며 기존 source 필드는 보존한다.
기존 DB downgrade는 지원하지 않는다. 비밀은 기존 PC 설정 경계에 남고 연구 DB에는 경로/hash만 추가한다.

요청 socket timeout 10초와 기존 30초 source/checkpoint/RSS/preflight/CPU guard를 사용한다.
전체 준비 JSONL byte 상한은 source memory MiB/16(기본 512MiB이면 32MiB)이며 날짜별 남은 예산을 적용한다.
시간/메모리/byte cap 초과는 완성 파일을 게시하지 않고 source backoff로 처리한다.
timeout은 socket 경계이며 source 시간/취소 검사는 호출 전후 및 페이지/행 단위다. 네트워크 호출 중 hard deadline을 보장하는 별도 스레드는 추가하지 않았다.
TemporaryDirectory는 정상/예외 종료 시 자신이 만든 root 내부 임시 경로만 정리한다.
OS 강제 종료 때 남는 임시 디렉터리와 게시 후 ack 전 완성 orphan의 보관/삭제는 다음 CR2c3다.

## 검증

- 실제 NAS/사용자 DB/접속 토큰/키움 TR/주문을 사용하지 않았다. 가짜 API와 임시 DB/파일/offscreen Qt 범위다.
- 초기 집중 회귀 71개 통과, 백로그/페이지 변경/마이그레이션/UI 보완 집중 38개 통과.
- 준비→자동 등록→같은 signature 다운로드 생략과 Repository 재시작 뒤 cache 유지를 검증했다.
- NAS offline/HTTP401 오류 격리, 동적으로 생성한 토큰의 연구 DB 미저장, 직접 모드에서 API 미호출을 검증했다.
- pagination 중 취소/snapshot 변경, 불완전 probe, quota 초과 때 미게시/임시 cleanup을 검증했다.
- backlog full 때 API 미호출과 완료 뒤 재개, signature ack의 일시정지 fence, v13 source 전체 기존 필드 보존/NAS 기본 OFF를 검증했다.
- 2일 bundle의 기존 날짜/일별 범위와 runtime scope 보존을 검증했다.
- 최종 전체 연구 회귀 **232개 / 117.853초 / OK / 프로세스 exit 0**. `tmp/cr2c2b-regression.log`에 기록했다.
- 변경 Python 11개 AST/공백과 관련 `git diff --check` 통과. 테스트 로그에 skip/오류/자식 출력 읽기 예외 없음.

## 남은 범위

다음 CR2c3는 연구 디스크 cap/완료 보관·참조 보호·임시/orphan 정리다.
새 날짜 확장은 CR3 검증 구간 정책, 자동 가설은 CR4, 일지 피드백/자동 모의운영은 후속이다.
실제 NAS의 대량 export/장시간 DB 부하와 PC 24시간 운용은 V1이다.
서버/API/키움 우선순위를 변경하지 않았으며 이번 PC 변경으로 NAS 동기화/재빌드는 하지 않았다.
모델 에스컬레이션 없음.

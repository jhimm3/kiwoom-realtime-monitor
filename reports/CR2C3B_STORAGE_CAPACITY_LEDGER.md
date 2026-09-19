# CR2c3b — PC 새 자료 폴더 용량 상한과 자료 준비 원장

2026-09-16. CR2c3a의 표시/진단에 이어 cap과 준비 원장을 구현했다. 실제 삭제는 없다.

## 입출력과 저장 계약

- 새 자료 폴더의 storage_cap_bytes: 0 무제한, 양수 byte. UI는 GB(1024^3) 정수로 저장한다.
  기존 source는 migration 후 0이며 cap 변경은 기존 일시정지/worker 종료 경계를 따른다.
- 범위는 해당 상위 폴더의 파일 길이다. 수동 파일/기존 export/임시 파일도 합산한다.
  폴더 밖 연구 DB/보고서나 NAS 원시 자료의 용량은 제어하지 않는다. 디스크 블록 사용량과는 다르다.
- capped source의 초기 용량 목록이 불완전하면 준비를 차단한다. cap=0은 추가 용량 검사를 생략한다.
- 변경 없는 remote signature는 다운로드/예약/원장 추가를 생략한다.
- 변경된 snapshot은 기존 worker 소유권으로 예약하고 DB당 파일 준비 하나만 허용한다.
  다른 준비 작업은 WAITING_STORAGE/60초 대기다. NAS 관측 probe는 예약 전 읽을 수 있다.
- 각 저장 전에 observations/themes JSONL, 일별·bundle manifest, 임시/완성 sidecar byte를 차감한다.
  준비 후 실제 폴더 크기를 다시 검사하고 게시한다. 메모리 encoded byte 제한/CPU/RSS/취소 정책도 유지한다.
- 예약 중 네트워크/대량 파일 검증에서는 DB 쓰기 잠금을 잡지 않는다. 최종 rename에만 짧은
  BEGIN IMMEDIATE로 owner/generation/lease/RUNNING 의도를 확인한다. 경로도 선택한 root 내부인지 재확인한다.
- cap 부족은 BLOCKED 원장과 WAITING_STORAGE/실패 횟수 0이다. signature를 ack하지 않는다.
  상한을 올리고 재개하면 다시 준비한다. 기존 입력과 결과를 삭제하지 않는다.
- 같은 capped root를 쓰는 NAS source는 cap도 같아야 한다. parent/child capped root 또는 같은 root의
  서로 다른 cap은 설정 저장 시 거절한다. 다른 source를 끈 뒤 조정할 수 있다.

## 로컬 연구 v15

새 migration만 추가했다. v1~v14 함수는 변경하지 않았다. source에는 storage_cap_bytes만 추가한다.
research_campaign_storage_operations는 operation_id/source/campaign/root/cap/owner/generation,
PREPARING/PUBLISHED/UNCHANGED/BLOCKED/FAILED/CANCELLED/ABANDONED, 시작·종료/staging_path/input_path를 기록한다.
staging_path는 임시 생성 직후 소유권 검사를 거쳐 기록한다. 임시·완성 sidecar의 operation_id와 대조한다.
정상/예외 종료의 임시 정리는 이번 실행이 만든 root 안 경로만 한다. 강제 종료 잔재의 삭제는 아직 하지 않는다.
오래된 예약은 기존 worker lease/세대와 예약 시작 후 120초 상한을 확인하고 ABANDONED로 남긴다.
기존 source의 30초/checkpoint 예산보다 긴 예약 만료를 별도로 두어 종료 기록 유실이 살아 있는 worker를
영구 대기시키지 않게 했다. 만료한 예약은 게시 fence에서도 거절한다. 오래된 응답은 새 예약을 바꾸지 못한다.
PUBLISHED는 파일 게시 뜻이며 자동 실험 등록 성공을 뜻하지 않는다. 게시 후 등록 공백은 다음 보관 단계에서 보호해야 한다.
ledger 조회는 기본 최근 100개/최대 1000개다. 삭제 원장·외부 매매일지 참조 보호를 완료한 것으로 해석하면 안 된다.
PC 운영 경로/cap/원장은 로컬 연구 DB 경계이며 공유 설정/Google/NAS 콘텐츠 백업에 추가하지 않았다.
비밀키·OAuth 토큰을 복사하지 않는다. DB downgrade는 지원하지 않는다.

## 검증

임시 DB/파일·가짜 NAS API/offscreen Qt만 사용했다. 실제 NAS/사용자 데이터/키움 TR/주문/인증키는 사용하지 않았다.
기본 무제한·cached signature 원장 미증가, cap 부족 반복 대기/격리 미발생, 수동 파일 합산/미삭제,
부분 준비 cleanup, 상한 증가 후 재개, 동시 예약/완료 멱등, 세대 교체/과거 응답 차단,
다운로드 중 일시정지 게시 차단, 겹치는 cap 정책, v14 전체 source 필드 보존/기본값과 UI 저장을 검증했다.
불완전 용량 목록과 redirect root도 미게시를 확인했다.

- 집중 회귀 61개 / 38.708초 / OK / exit 0.
- 경로/불완전 목록 보완 후 전체 회귀 256개 / 135.557초 / OK / exit 0.
- 예약 만료 보완까지 포함한 최종 전체 회귀 **257개 / 136.757초 / OK / exit 0** (`tmp/cr2c3b-regression-final.log`).
- 변경 Python 13개와 새 보고서 AST/공백 검사, 관련 `git diff --check` 통과.

종료 기록 유실/건강한 worker 유지 상황은 임시 DB에서 예약을 오래된 시작 시각으로 바꿔 재현했다.
수정 전 새 예약이 None으로 거절되어 회귀 1개 실패(`tmp/cr2c3b-expiry-before.log`).
확인된 예약 만료 처리 누락에만 120초 회수/게시 차단을 추가했고 최종 전체 회귀에 해당 테스트를 포함해 통과했다.

## 남은 범위

CR2c3c: 보호 보관·외부 매매일지 참조·강제 종료 임시/게시 orphan 정리.
참조 중인 완료 자료는 보존한다. 기존 무표시 파일/원시 시장자료는 자동 삭제 대상으로 바꾸지 않는다.
이 cap은 앱의 파일 예산이며 외부 프로그램의 동시 쓰기를 막는 파일시스템 hard quota가 아니다.
실제 NAS 대량 자료/PC 24시간 운용은 V1이다. 새 날짜/최종평가/가설/주문 정책은 변경하지 않았다.
NAS 재빌드 필요 없음. 모델 에스컬레이션 없음.

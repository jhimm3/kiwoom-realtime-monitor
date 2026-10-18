# O2-Me3 NAS runner 수명·제어 API 구현

## 구현 범위

- `MockAutomationSupervisor`가 계좌별 전환 잠금, 자동 bundle, runner 수명과 재시작 복원을 소유한다.
- 시작은 저장된 명세·계좌/profile을 확인하고 기존 `MockCredentialOwner.switch_execution_mode`로 flat
  수동 bundle을 자동 bundle로 교체한다.
- 기존 admission과 최신 risk 기반 recovery를 통과한 뒤에만 runner를 시작한다. 실패 시 신규 주문은
  닫힌 상태로 유지한다.
- 중지·재개는 저장 control revision CAS를 요구한다. 재개 전에 새 broker 복구와 risk revision을
  기록한다.
- 서버 lifespan과 인증된 상태/시작/중지/재개 API, capability를 연결했다.
- NAS 재시작은 저장된 RUNNING control만 복원한다. 실패는 30초 backoff와 상태 사유를 남기며
  STOPPED control은 자동 재개하지 않는다.

## 의도적으로 남긴 범위

- 연구·Shadow·모의자동을 구분하는 PC 운영 UI
- 게시→입장→가짜 체결→매도→A5 조회 전체 통합 fixture와 V1 장시간 검증

READY 명세의 NAS 게시/조회 API는 후속 `O2ME3_SPEC_PUBLICATION_IMPLEMENTATION_20260921.md`에서
연결했다. PC 화면은 아직 이 API를 호출하지 않는다.

## 검증

- supervisor 전환·멱등 시작·실패 fail-closed·control revision·중지/재개 단위 회귀
- 기존 runner/admission/account bundle 인접 회귀
- 서버 파일 compile과 build ID 일치 검사

이 단계는 Me3의 NAS backend 연결 완료이며 전체 Me3 완료 표시는 운영 UI와 통합 fixture 뒤로 둔다.

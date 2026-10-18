# O2-Ma — 자동 모의운용 동결 명세

기준일: 2026-09-16. 로컬 구현·회귀 완료, NAS 누적 배포 전.

## 목적

자동 모의운용에 필요한 값을 임의 기본값 없이 먼저 고정한다. 연구 결과와 주문 실행의 소유자는
계속 분리하며, 이 단계는 기존 수동 주문 transport나 NAS runtime을 활성화하지 않는다.

## 계약

`mock_automation_operating_spec/v1`은 다음을 내용 주소형 `spec_id`로 묶는다.

- 최종 후보 package hash, 완료 result hash와 final batch/run ID
- 전략과 동결 forward profile
- 현재 `ka00001`로 확인된 mock 계좌 scope/profile/binding revision
- 최대 동시 전략·포지션, 사용 자금, 일일 손실
- 데이터 공백, submission unknown, 재접속, 잔고 불일치 한도
- KRX/session/data path와 후보 교체·중지·broker 대조 후 복구 정책

값이 하나라도 비면 `BLOCKED`다. profile·계좌·SHADOW 단계가 다르거나 명세가 평가 시작 뒤
동결된 경우도 `BLOCKED`다. 현재 O1 지원 범위 때문에 단일 전략·단일 포지션·KRX 정규장만
`READY`다. `READY`는 후속 입장 심사 입력이며 주문 승인 상태가 아니다.

비공개 `execution_mock_automation_specs` 컬렉션은 owner=`mock account_ref`, key=`spec_id`로
저장한다. 저장 시 같은 전략의 forward profile과 credential profile의 최신 mock binding을 다시
대조한다. 새 SQL schema나 공개 API는 없다.

## 검증

- O2-Ma 및 기존 execution activation 테스트 10개 통과.
- 계좌 신원·운영 설정·전진평가·A5 피드백·O1 lifecycle/원장 연관 회귀 85개 통과.
- 미정값 차단, content ID 변화, SHADOW/동시성 차단, real scope 거절, 최신 binding 강제,
  불변 저장과 재조회 확인.

## 다음

O2-Mb에서 `READY` 명세를 최종 후보 원장과 대조해 한 번만 입장시키고 같은 mock 계좌의 기존
O1 lease와 충돌하지 않는 자동운용 run 소유권을 만드는 작업을 완료했다. 다음은 broker 복구와
중지 상태를 확인하는 O2-Mc다.

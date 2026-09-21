> **과거 기록** · 원래 경로: `reports/O2ME3_SPEC_PUBLICATION_IMPLEMENTATION_20260921.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# O2-Me3 READY 운용 명세 게시 경계 구현

## 후속: 후보 선택용 읽기 경계

- `GET /api/v1/research/mock-automation-candidates/{account_ref}`는 현재 검증된 모의
  `credential_profile_id`가 같은 익명 계좌를 가리킬 때만 게시 후보 목록을 반환한다.
- 각 행은 candidate package, 최초 동결 eligibility policy, account-scoped receipt를 완전한 계보로
  반환한다. 현재 binding revision·verified_at도 같은 응답에 포함해 PC가 수동 ID 입력 없이 다음
  READY 사양을 구성할 수 있다.
- 읽기는 runner, lease, 주문 transport를 시작하지 않는다. 후보·정책·receipt 중 하나가 없거나 계보가
  맞지 않으면 불완전 문서를 숨기지 않고 충돌로 반환한다.

## PC 작성·게시 UI

- 모의 자동운용 창은 선택한 계좌의 ELIGIBLE 게시 후보만 보여준다. 후보의 family·정규 parameters·session
  profile로 shadow monitor ID를 다시 계산하고, 같은 monitor에서 실제 발생한 `flat->candidate` 사건만
  증거로 선택한다.
- 별도 작성창은 평가 시작·기간, 14개 Forward 통과 기준, 동시 전략/포지션·자금·일손실·자료 공백·불명
  주문·재연결·잔고 불일치의 8개 운용 한도를 전부 표시한다. 화면 제안값은 수정 가능하고, 게시한 값만
  불변 계약이 된다. NAS는 빠진 값을 채우지 않는다.
- 순수 builder가 후보·policy·receipt·binding·shadow를 검증하고 `ForwardEvaluationSpec`, 세 단계 증거
  revision, `MockAutomationOperatingSpec`을 만든다. 게시 완료는 READY 명세 저장만 뜻하며 시작 API를
  호출하지 않는다.

## 구현 범위

- PC가 명시한 forward profile, `DRAFT→EVALUATED→VALIDATED→SHADOW` revision 3개와
  `MockAutomationOperatingSpec`을 받는 Bearer 인증 POST를 추가했다.
- NAS는 저장된 ELIGIBLE 후보 package/policy/receipt와 final 계보, 등록 family/factor/session,
  현재 mock binding을 다시 대조한다.
- 요청한 `shadow_event_id`가 실제 중앙 후보 원장에 있고, 해당 후보 설정으로 결정되는 기존 monitor ID,
  전략 ID/version, `flat->candidate`, 양수 수량과 시간 순서를 만족하는지 검사한다.
- 모든 검증과 기존 immutable 문서 충돌 검사를 먼저 마친 뒤 profile, 누락 stage, spec을 저장한다.
  같은 문서 재게시는 멱등이며 게시 응답은 주문이 시작되지 않았음을 명시한다.
- 계좌별 저장 명세와 게시 계보 기준 readiness를 읽는 인증 GET 및 capability를 추가했다.
- PC `CentralContentClient`에 명세 게시·목록·계좌 runtime 상태·시작·중지·재개 호출을 추가해 UI가
  URL·Bearer·JSON 전송 세부사항을 직접 소유하지 않게 했다.
- PC 운영 창은 활성 mock credential profile과 계좌 설정 revision을 읽고 READY 명세를 표시한다.
  저장 control/runner 상태가 허용하는 시작·중지·재개 버튼만 열고, 중지·재개 이유를 명시적으로 받는다.
  메인 툴바의 작은 주황색 버튼과 기본설정의 전략 연구 탭에서 같은 창을 연다.
- shadow monitor ID 계산을 공통 순수 함수로 옮기되 기존
  `shadow:krx_bar_close_breakout:v1:<digest>` 형식을 보존했다.

## 안전 경계

- 서버가 자금·손실·장애 한도나 평가 기준을 임의 기본값으로 만들지 않는다.
- stage 이름만 받은 것으로 SHADOW를 인정하지 않고 package hash, receipt ID와 실제 저장 event ID를
  연결한다.
- 게시 성공은 admission, lease, account mode 전환, runner 시작 또는 Kiwoom TR/주문 호출이 아니다.
- 현재 실제 중앙 shadow 후보 생성기는 breakout family만 지원한다. 다른 등록 family는 대응하는 실제
  shadow event producer가 생기기 전에는 READY 게시가 차단된다.

## 검증

- 완전 chain의 최초 저장과 동일 재시도 멱등성
- 다른 monitor가 만든 shadow event 및 잘못된 수량 거절
- 기존 stage history 충돌 시 profile/spec 선저장 방지
- 기존 shadow monitor ID 형식 보존
- PC 클라이언트의 account/profile URL 인코딩, 동결 문서 전달과 control revision 본문
- mock profile 필터, READY 명세 기본 선택, RUNNING/STOPPED별 버튼 허용 정책과 메인 UI 연결
- runner/admission/risk/account bundle/execution repository 인접 회귀
- 실제 저장소와 결정적 fake broker를 사용한 게시→입장→위험 대사→매수 체결→매도 체결→계좌별
  A5 매매일지 투영 전체 경로
- 상세 체결이 누적 체결량을 모두 설명하는 경우 0수량 `BROKER_FILL_AGGREGATE`를 만들지 않고 broker
  누적 수량만 reconciliation 상태에 보존하는 회귀

FastAPI/Pydantic 라우트 실행 시험은 이 워크트리의 Python 3.13 실행기 원본이 없는 환경 문제로
실행하지 못했다. 서버 모듈 compile, route inventory와 capability 계약은 정적 검사 대상으로 남긴다.

## 남은 범위

- V1 누적 배포와 장시간/실제 PostgreSQL/제한 모의계좌 검증

# AI Trading Intelligence 개발 로드맵

2026-10-16 진행: 00종합에 명시된 가격 기준선(prior-window-high·현재 구간 제외·관측 가격 교차)을 실제0B/완료1분봉 판단 연구와 기존 기록/API/화면에 로컬 연결했다. 창 길이·주도주 선정·확인·대금·위험/실행 조건은 미정으로 유지하며 새 프로필은 관측 전용이다. 관련449건 및 최종 표시/미정 항목126건 통과는 완성 매매 규칙·all-local/CI·NAS/장중 수락을 뜻하지 않는다. [현재 구현·검증·남은 범위](docs/workstreams/realtime-entry.md#2026-10-16-00종합에-명시된-가격-기준선-연결-로컬).

## 2026-10-16 우선 정정 — 주도주 돌파 연구와 Shadow

후속 전환 회귀인 동적 비중 RMS와 테마 H2 시작가격의 표본 의미는 기존0B 원문에서 원래 의미를 복구하고 관련 검증을 마쳤다. 새 초봉 집계/저장·전략 조건은 없다. [최종 범위](docs/workstreams/second-trade-dependencies.md#2026-10-16-테마-h2-상승률-시작가격-의미-복구)와 현재 전수/장중/NAS 수락·미정 연구 적용 과제를 구분한다.

입력 기준은 후속 사용자 정정으로 **일봉·5분봉·1분봉 등으로 유효한 규칙을 연구·검증하고 자동매매 적용 방법을 찾으며, 실시간 현재가·체결 변화·실행에는 기존 0B를 사용한다**로 명확히 했다. 기존 봉 계산을 실시간이라는 이유로 0B로 일괄 교체하지 않는다. 과거 사례와 현재의 가격·시각·해상도 일치를 연구 결과 활용의 일반 요건으로 삼지 않는다. 기존 초봉 소비를 다음 기본 경로로 승계하지 않는다. 조사 후 사용자 승인에 따라 [분봉 연결 복구](docs/workstreams/second-trade-dependencies.md#2026-10-16-승인된-분봉-연결-복구와-관련-재검증)를 로컬 구현·검증했다. 과거 비교용 조건은 유지하며 새 해상도 지원이나 미정인 실시간 적용 규칙·배포까지 구현한 것은 아니다. 운영 저장 중단·NAS 수락은 별도다.

사용자는 00 AI 인텔리전스가 미완성 초안임을 전제로, 기존 완료봉 돌파·눌림 중심 연구와 Shadow가 의도와 다르다고 정정했다. 다음 Trading 작업은 기존 Family의 수치 최적화나 초 단위 돌파의 즉시 기본 연결이 아니라, **사용자 주도주 돌파 구상의 판단 정의·입력 대응·미정 조건을 정리하고 같은 정의를 연구와 Shadow에 연결하는 것**이다. 기존 실험·결과·ID는 보존하되 사용자 전략의 성과로 사용하지 않는다. 사용자가 초봉을 사용하지 않을 의향을 밝혔으므로 초봉을 새 전략의 필수 입력으로 확정하지 않는다. [현재 방향과 완료 경계](docs/workstreams/realtime-entry.md#2026-10-16-사용자-정정--연구와-shadow의-방향). 아래 기존 단계의 완료 표시는 도구·경계 구현 기록이며 사용자 전략 완성을 의미하지 않는다.

현재 보완은 [종합 대조 감사](docs/AI_TRADING_INTEGRATION_ALIGNMENT_AUDIT_20261014.md)에 따른다. 사용자 지정에 따라 A01~A03을 우선하고 A04는 뒤로 옮긴다. 현재 R4의 A01 정책·행동별 요구자료와 A02 가격/점수/위험 소속 분리를 연결했고 다음은 A03의 조건부 현재 모델 지원과 native 실행 적용 계약 판단/구현이다([설계 재검토](docs/AI_TRADING_SYSTEM_REDESIGN.md#a03-설계-재검토)). 이후 순자산 분모와 보호 권한 경계를 보완한다. 구현·검증은 [CURRENT_STATUS](docs/CURRENT_STATUS.md), 잔여는 [OPEN_ITEMS](docs/OPEN_ITEMS.md)에 둔다. 사용자 지정 큰 개발 순서와 운용 승인 분리는 유지한다.

## 2026-10-14 실제 연구 입력 접근 복구 확인과 v2 재진입 비교 지원

사용자가 접근 복구를 알려준 뒤 두 실제 연구 패키지의 manifest와 TRAIN/VALIDATION4분할을 기존 load_research_input/partition/readiness 함수로 읽기 전용 검증했다. 접근 문제는 해결 확인했으며 로컬 디렉터리·manifest는 정상이다. package hash, 원본 observations/ordinal/revision hash·partition ID/경계를 확인했고 OOS 입력을 열지 않았다.

| 패키지 | TRAIN 관측 | VALIDATION 관측 | 분봉 준비 상태 |
|---|---:|---:|---|
| monthly-2024-09-to-2026-01-clockfix-v2 | 181,708 | 63,218 | READY |
| multi-period-2024-2026-stocks-pairs-v4 | 14,976 | 16,541 | READY |

총276,443관측(분봉276,423·역사 후보 모집단20)이다. 실제 데이터는1분봉/후보 복원이며 당시 TOP20 관측이 아니고 theme snapshot은 모두0이다. 기존 historical-context 변환으로 첫 원래 bar cut을 확인했으며 D2/D5 context가 없는 상태를 그대로 보존했다. 파일 무결성/분봉 READY를 당시 공통 맥락·통계적 독립성·현재 R4 적용 승인으로 바꾸지 않는다. [실제 입력 감사](data/diagnostics/20261014-real-r4-input-audit/real-input-audit.json).

별도의 통제 연구 시험으로 기존 native v2 reader의 REENTER 지원을 연결했다. 기존 registered family/Paper/ResearchRepository가 실제 계산한 선행 매수·전량 SELL 체결과 pending0·빈 포지션·cooldown 상태를 요구한다. 기존 v1 target 가치 adapter의 prefix 읽기 코드를 같은 파일의 공통 helper로 추출해 재사용하고, 원래 빈 계좌 {}와 원래 goal/TTL을 정확히 재생한다. flat HOLD는 검증된 v2 안에서 같은 현금/수량 가치 adapter를 쓰되 HOLD에 target declaration/주문을 만들지 않는다. 짝 경제 상태의 position=None을 보존하며 기존 평균/표준오차·비용 계산·native writer/게시 경로를 사용한다.

이것은 명시 재진입 가설의 과거 Paper 비교다. 통제 시험의 쿨다운 중 명시 목표 적용을 실제 native 재진입/쿨다운 해제 승인으로 해석하지 않는다. 원래 v1 DTO/공개 비교·held HOLD/ADD/REDUCE 의미와 현재 R4 차단을 유지한다. Native RISK_FILL 선행 청산, partial/pending 초기 상태 및 EXIT의 v2 확장은 승인하지 않았다. 새 engine/collector/table/writer/task/실시간 조회가 없다. 기존 과거 목표 TTL 뒤 완결한2case/4run의 REENTER19주 대 flat HOLD 비교는 COHORT_COMPARABLE·평균 순가치-29원이며 현재는 EV_UNAVAILABLE/usablefalse/orderfalse다. [통제 원본 게시](data/diagnostics/20261014-real-r4-input-audit/controlled-reentry-publication.json).

선행 완전 SELL 증명이 빠지면 HOLD/REENTER4branch 모두 제외하고 전체 cohort를 미확인으로 만든다. 공개 원본 준비는 기존4borrow/SELECT16/COMMIT0·원본 SQLite bytes 동일이며 최초 게시 재생은 DB0이다. 원래 outcome/정책/가용 시각/기한을 덮어쓰지 않는다. 실제276,443행을 이 통제 fixture의 모델 학습 자료였다고 주장하지 않는다. explicit KRX scientific source hash는 코드 변경으로 바뀌며 기존 패키지를 자동 재표지하지 않는다. legacy None hash와 supplied v1 전체 예제/decision ID는 보존한다.

신규3 포함 관련 고유44건 통과, compile·소비 감사delta0·기존 required Windows route/core fingerprint 유지. [검증 기록](data/diagnostics/20261014-real-r4-input-audit/verification.json). 다음은 현재 R4용 공통 맥락·상위 입력 독립 계보와 native 보호/미체결 후속 정책 근거를 실제 생산자에서 연결하는 작업이다. 없는 과거 테마/초 단위 입력이나 운영 승인을 합성하지 않는다. 사용자③ 전체·④ 실제 모의계좌00/04/REST·잔고·청산/운용 승인 및 현재 R4 적용은 미완료다. 원래00 상위 Position·Risk·Capacity/Liquidity 자동 비중을 유지한다. main/HEAD232737d·누적 로컬 미커밋·미배포·build2026.10.14-position-reentry-study-v2. 전체 회귀/HTTP 전체 묶음·CI·원격main·실제PG/NAS/장중 수락 미실행, catalog 무관 archive5개 보류 유지.

이하 설명은 해당 시점의 기록이다.

## 2026-10-14 자동 목표 HOLD/EXIT 변환 오류 수정과 현재 R4 근거 확인

현재 근거 확인 중, 근거 부족과 별개인 자동 목표 변환 오류를 재현해 수정했다. 계좌 운용 한도가 기존 보유 평가액과 같아 HOLD가 되거나 0으로 줄어 EXIT가 되는 경우, 상위 비중 계산은 정상이나 RAM 변환이 ADD/REDUCE/REENTER 전용 automatic sizing DTO에 HOLD/EXIT를 넣어 ValueError를 발생시켰다. 같은2/8 Shadow 입력에서 HOLD/EXIT4개 실패를 재현했고 REDUCE를 대조군으로 유지했다.

기존 market_theme_shadow_inputs의 하위 요청 생성 한 곳에서 ADD/REDUCE/REENTER는 기존 sizing 요청을 그대로 사용하고, HOLD/EXIT는 기존 명시 수량 계약(actual/0)으로 전달한다. 상위 자동 목표/정책/ref·원래 receipt TTL은 유지하며 조회 만료 때 결과/하위 목표를 함께 제거한다. 사용자 종목별 수동 금액 정책을 추가한 것이 아니다. DTO 허용 범위·과거 study 계약·native Risk/보호/주문 경로를 넓히지 않는다. 새 함수/owner/collector/table/writer/task나 호출 단계를 추가하지 않았고 기능 이해 파일/호출 깊이는 기존과 같다.

신규2 포함 관련 고유52건 통과: 2/8 HOLD·EXIT·REDUCE의 목표/수량/공통상위ref·매수0·계좌borrow1·hot SQLite접근0·만료 제거, v2 HOLD/EXIT에 ADD 연구 scope를 잘못 승인하지 않는 경계, 기존 ADD/비중/수량/게시·기록/취소 drain을 검증했다. 신선한 native 위험 우선, 만료 위험으로 새 보호를 만들지 않는 경계, 8개 Shadow 계산 정지 중 실제 기존 보호 처리(가짜 전송)3건도 통과했다. 현재 R4는 EV_UNAVAILABLE/result=None/orderfalse를 유지한다. 소비 감사delta0·compile·기존 supplied v1 전체 예제/decision ID 동일·자체 required Windows CI/core fingerprint 유지. [검증 기록](data/diagnostics/20261014-allocation-hold-exit/verification.json).

기존 controlled native 연구 publication을 같은 decoder로 재검증했다: 원래4run·2context의 COHORT_COMPARABLE과 원래 availability/expiry를 보존한다. D2/D5 맥락에는 초 단위 leadership·breakout·시총·상위 EV/출구/stress 현재 지원 근거가 없고, source spec에는 native 위험 한도/미체결 후속 정책이 없다. 현재 맥락/독립 source와 native 보호·후속 모델의 적합성을 승인할 수 없다. [원본 감사](data/diagnostics/20261014-allocation-hold-exit/source-audit.json).

실제 연구 입력 후보 두 디렉터리(`data/research/historical-development-inputs/monthly-2024-09-to-2026-01-clockfix-v2`, `multi-period-2024-2026-stocks-pairs-v4`)는 기본 샌드박스와 사용자 세션 읽기 전용 재확인 모두 Windows 접근 거부였다. 전체 역사 입력이 없다고 결론내리지 않으며 이 두 폴더 내용/적용 근거 존재 여부는 미검증이다. 읽기 ACL을 바꾸거나 자료를 현재 숫자로 합성하지 않았다. 확인된 DTO 오류와 이 자료 접근/모델 근거 문제를 별도로 유지한다.

다음은 접근 가능한 실제 원본 또는 읽기 권한이 회복된 입력에서 사전 cohort·공통 맥락·상위 입력 계보·native 보호/후속 지원을 확인/생산하는 작업이다. 상위00 Position·Risk·Capacity/Liquidity 자동 비중을 유지하고, 현재 R4 적용 및 사용자③ 전체/④ 실제 모의계좌00/04/REST·잔고·청산/운용 승인은 미완료다. 무관 proof/ML 단계를 자동 추가하지 않는다. main/HEAD232737d·누적 로컬 미커밋·미배포·build2026.10.14-allocation-hold-exit-v1. 전체 회귀·HTTP 전체 묶음·CI·원격main·실제PG/NAS/장중 수락 미실행, catalog의 무관 archive5개 보류 유지.

이하 설명은 해당 시점의 기록이다.

## 2026-10-14 마무리 묶음 3 — 주문 OFF 로컬 통합 검증 종료

고정한 세 묶음의 로컬 검증을 마무리했다. 현재 입력→수량/주문 OFF 계획과 고정 연구 입력→합성 브로커 원장 전체 흐름을 구분한다. 현재 R4는 EV_UNAVAILABLE/result=None/orderfalse이며 양의 현재 R4를 native 주문으로 연결한 성공을 주장하지 않는다. 사용자 순서③ 전체 개발 완료 또는④ 실제 모의계좌 통합/운용 승인 완료는 아니다.

| 로컬 묶음 | 종료 범위 |
|---|---|
| 1. 공통 입력 연결 | 기존 현재 관측과 원래 EV/출구/study 참조·cut·TTL 연결 검증 완료. |
| 2. 모델·보호 경계 | 과거 지원 범위와 현재 미지원 차단, native 보호/목적별 미체결 우선 검증 완료. 현재 모델 적용 근거는 보류. |
| 3. 주문 OFF 통합 | 고정 입력의 선택→상위 자동 비중→수량→단일 원장 미체결/청산, 현재 RAM 계획/차단·입력 소실·만료·종료 검증 완료. |

기존 시험 모듈 두 개에 통합 시험3건만 추가했다. 관련 고유59건 통과, compile·소비 감사delta0·기존 required Windows CI route/core fingerprint 유지. 생산 코드·호출 깊이·owner/collector/task/table/writer·배포 build는 변경하지 않았다. 현재 RAM8개 Shadow는 같은 선정 범위/상위 goal ref/비중 금액/하위 수량으로 주문 OFF 계획을 대사하고 DB 접근0·계좌borrow1을 확인했다. 현재 R4 미지원, account source 예외 후 이전 계획 제거, 원래 sizing TTL 만료/조회 제거 및 종료 후 게시 거절을 확인했다. 기존 게시 취소 drain과 selected 기록 회귀도 실행했다.

고정 입력 시나리오는 계좌 운용 한도10,000원과 EV/시장·테마·종목/유동성·위험 입력으로 목표를 자동 결정한다. 양의 EV 종목은10주, 음의 EV 종목은 신규0주로 선택되고 하위 수량 결과가 같은 시험 원장의 root quantity/decision ref로 전달된다. 종목별 투자금을 사용자가 직접 지정하는 최종 구조로 바꾸지 않았다. 이 경로는 supplied fixed-input 연구이며 현재 native 모델 적용 승인·성과 검증이 아니다.

같은 원장의 BUY3주 부분체결/취소 후1,010원 재평가에서는10,000원 목표가9주가 되어 잔여6주다. 이전7주 WAIT/REPRICE 근거를 거절하고 별도 명시한 고정6주 근거만 계산한다. UNKNOWN 응답과 재시작은 중복 제출을 만들지 않는다. 이후 익절 WAIT와 보호 CANCEL/REPRICE를 구분하고, 보호 SELL4주 부분체결/취소 확정 후 잔여5주를 다시 계산한다. 완전 체결+신선한 flat/reconciled 계좌일 때만 liquidation_confirmed이며 불완전 대사/미체결은 청산 완료로 인정하지 않는다. 수량/현금은 원장 상세 FILL을 읽는 시험 계좌 producer에서 계산하고 기존 Paper fee ROUND_DOWN을 재사용한다. gross-110원/모의 수수료·세금34원/net-144원·최종현금99,856원·보유0을 손계산과 대사했다. 원장4intent·가짜 submit4/cancel2·실제 브로커 전송0이다. 합성 응답/모의 비용으로 실제 체결·수수료·잔고 수락을 대체하지 않는다.

[검증 기록](data/diagnostics/20261014-order-off-closeout/verification.json) · [원장 흐름/목표/최종 계좌](data/diagnostics/20261014-order-off-closeout/offline-ledger-flow.json).

다음 남은 항목은 현재 R4에 필요한 exact 과거 맥락·상위 입력 독립성/source custody·native 보호/후속 모델 지원 근거 확보와 실제 모의계좌00/04/REST·부분체결/잔고/청산 검증 및 별도 운용 승인이다. 없는 과거 사실이나 미지원 모델을 승인 자료로 합성하지 않는다. 원래00 Position·Risk·Capacity/Liquidity 상위 자동 비중 정책을 유지한다. 무관 기능/새 ML 프레임워크/새 proof 단계는 자동 추가하지 않는다. main/HEAD232737d·누적 로컬 미커밋·미배포·build2026.10.14-allocation-input-bundle-v1 유지. 전체 회귀·CI·원격main·실제PG·NAS·장중 수락 미실행, catalog 무관 archive5개 보류 유지.

이하 설명은 해당 시점의 기록이다.

## 2026-10-14 마무리 묶음 2 — 지원·차단/보호 경계 검증, 현재 모델 적용은 보류

고정한 둘째 묶음의 **로컬 지원·차단/보호 경계 검증**을 마쳤다. 현재 R4 모델 적용을 승인한 것은 아니다. 새 생산 코드·엔진·수집·조회·기록·주문 경로 없이 기존 시험 모듈에 동시 실행 회귀 한 건을 추가했다. 8개 Shadow의 실제 공통 allocation 계산을 멈춘 동안 별도 native 계좌 owner의 실제 Collector→Supervisor→Runner 손절 cue와 첫 보호 처리가 진행됐다. 두 시험 owner의 계좌 cut·모델 승인을 혼용하지 않는다.

| 검증 대상 | 현재 판정 |
|---|---|
| 사전 동결한 native Paper HOLD·ADD/REDUCE 짝 | 과거 비교 COHORT_COMPARABLE. 음의 수수료 포함 결과도 유지. 관측된 범위의 경험 통계이며 현재 예측/자동 비중 전체 성능 승인이 아님. |
| 현재 계좌의 R4 | 원래 exact context·상위 입력 독립성/source custody·native 보호/후속 모델 지원 미증명. EV_UNAVAILABLE/result=None/orderfalse 유지. 누락·변형·범위 충돌·만료와 후행 표본 선택 차단 검증. |
| native Risk·손절·미체결 | Shadow/EV/다음 분봉 완료 전에 보호 cue·wake·첫 가짜 전송 확인. 취소 확정/체결 대사 전 재주문과 UNKNOWN 중복 제출 금지. 매수·익절·보호 정책 구분. |

신규1 포함 관련 고유 **90건** 통과. 새 동시 시험은 R4를 풀기 전에 첫 보호 전송이 1회 진행되는지, 최초 stop quote/요청·수량을 보존하는지, 제출 상태를 청산 완료로 오인하지 않는지 확인했다. 즉시 cue의 connection/SELECT/COMMIT/전송0, 기존 status100회·기한/중복cue100회의 DB0 및 반복10회 COMMIT0 회귀도 통과했다. 이는 불필요한 전체 preflight 반복을 차단하는 경계 검증이며 Runner의 기존 체결/운영 control 주기 조회가 사라졌다는 뜻은 아니다. 소비 감사delta0·compile·기존 required Windows CI route/core fingerprint 유지. [검증 산출물](data/diagnostics/20261014-model-protection-closeout/verification.json).

다음은 셋째 **주문 OFF 전체 흐름 통합**이다. 현재 R4 미지원은 명시 차단 시나리오로 포함하며, 이를 지원된 현재 R4의 종목 선정/비중/성과 통합 성공으로 표시하지 않는다. 현재 모델 적용 근거 확보와 실제 모의계좌00/04/REST·잔고·청산/운용 승인은 보류 항목으로 유지한다. 원래00의 Position·Risk·Capacity/Liquidity 상위 자동 비중 정책과 수량 하위 기능 구분은 그대로다. bundle 2의 경계 검증 완료를 사용자 순서③ 전체 구현 완료로 표시하지 않는다.

이번 묶음은 시험/문서만 변경했다. main/HEAD232737d·누적 로컬 미커밋, build2026.10.14-allocation-input-bundle-v1 유지, 미배포. 전체 회귀·CI·원격main·실제계좌·실제PG·NAS·장중 수락 미실행. catalog의 기존 무관 archive5개 보류 유지. 기본 샌드박스 비동기 시작은 중단했고 사용자 세션의 완료된 격리 SQLite/가짜 계좌 시험만 통과로 집계했다.

이하 설명은 해당 시점의 기록이다.

## 2026-10-14 마무리 순서 — 입력 연결 완료 후 두 묶음

사용자가 종료 시점을 요청한 뒤 현재 사용자 순서③의 개발 마무리를 다음 세 묶음으로 고정했다. 입력 연결·원본 근거 참조의 구현과 모델 적합성/독립성 검증, 실제 운용 승인을 구분한다. 범위 밖의 새로운 기능·광범위 source 수집/ML/통계 보정 요구를 이 묶음에 자동 추가하지 않는다. 현재 쓰는 paired empirical EV는 학습 예측기가 아니며, 실제 학습/선택/검증 모델을 사용하는 경우의 계보와 기존 후보 모델 지원 검증을 생략한다는 뜻은 아니다.

| 묶음 | 개발 상태 | 종료 기준 |
|---|---|---|
| 1. 현재 입력·원본 근거 연결 | **구현/관련 검증 완료** | 기존 현재가/시총·대금/변동성·시장/테마/종목·계좌Risk와 EV/출구/study 원래 참조를 동일 cut에 연결. 누락/불일치/만료는 현재 입력으로 인정하지 않고 공통 소비·기록 횟수를 보존. |
| 2. 현재 적용·보호 정책 검증 | **지원·차단/보호 경계 검증 완료 · 현재 모델 적용 보류** | 명시된 지원 범위의 판단 결과, 범위 밖/충돌/만료의 EV_UNAVAILABLE, 원래 sampling·학습/선택/검증 계보 및 native Risk/보호/미체결 후속 우선 정책을 함께 검증. 입력 연결 완료를 모델 승인으로 사용하지 않음. |
| 3. 주문 OFF 전체 흐름 통합 | **다음: 미지원 모델 차단 포함** | 종목 선정→상위 자동 비중→하위 수량→주문 계획→미체결/청산까지 공통 경로를 한 통합 시나리오로 대사하고 입력 부족/실패/종료를 검증. |

실제 모의계좌 활성/전송·00/04/REST/잔고/청산, NAS/장중 수락과 별도 운용 승인은 위 로컬 개발 완료와 구분한다. unsupported 입력/모델이 실제 운용 가능한 자료로 준비됐다고 주장하지 않는다. 전체 종료 시간이나 다음 사용자 메시지 수를 보장하지 않는다.

기존 current RAM allocation의 이미 계산된 facts/account·Risk refs와 typed EV/출구 모델 및 재검증한 native source/study refs를 dynamic-allocation-input-bundle/v1으로 한 번 동결했다. v2만 선택 field를 추가하고 v1 출력/수치/ID는 보존한다. R4 각 goal은 같은 bundle_ref를 공유한다. source refs는 원래 것 그대로이며 원래 account/quote/model availability와 expiry 최솟값을 갱신하지 않는다. 지원되지 않는 source/누락/차단된 cut은 INPUTS_UNAVAILABLE, 갖춰진 현재 cut/원래 보고 근거는 INPUTS_BOUND다. 두 경우 모두 model_application_verifiedFalse·orderfalse이며 R4 현재 적용은 계속 EV_UNAVAILABLE다.

새 engine/collector/DB read/table/writer/task나 모델 재생·시장 계산은 없다. 현재 EV 추정 방법을 paired_empirical_incremental_portfolio_delta/v1/trained_predictor_usedFalse로 기록하고 학습 모델 계보를 만들어내지 않는다. Native raw/custody와 독립성·조건별 모델 지원은 다음 묶음의 미검증으로 유지한다. raw facts/EV outcome arrays를 재복사하지 않고 현재 facts의 hash와 원래 모델/입력 refs hash/count를 사용한다.256KiB budget 초과 시 작은 unavailable 사유만 제공하고 이미 계산한 비중/목표/quality/lease를 바꾸지 않는다. 조회 만료 때 현재 bundle과 goal을 함께 제거한다.

신규6 포함 관련17건·기존 v1 전체 예제/decision ID 동일·소비 감사delta0·compile 및 자체 CI 등록/core fingerprint를 확인했다. 격리2/8 Shadow의 Collector/계좌/입력bundle/상위 계산 각1·현재시장1·DB/COMMIT/주문0, status/중복100회 추가 read/build/replay/evaluation0이다. bundle2236bytes·평균 생성0.1377/0.1795ms, cold frame10.647/9.301ms이며 운영 지연 수치가 아니다. 명시 선택 기록은 기존 upsert 한 번/COMMIT1·다른 입력변화 없는 반복0이며 새 저장 경로가 없다. 기록은 selected_latest_transition의 한 latest 진단이고 전체 역사 capture가 아니다. 전체회귀/새bundle HTTP/실계좌·PG/NAS·장중/CI/원격main은 미검증이다. catalog 기존 archive5 보류 유지. build2026.10.14-allocation-input-bundle-v1·main/HEAD232737d 로컬 미커밋·미배포다. [86단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#86-개발-마무리-범위-고정과-공통-입력원본-근거-묶음)

이하 단계 설명은 해당 시점의 기록이다.

## 2026-10-14 현재 진행 — 과거 관측 맥락에서 현재 조건의 모델 지원으로

기존 FrozenResearchDataset의 D2 순위 관측과 D5 테마 스냅샷을 과거 case의 원래 decision cut에서 동결해 native Paper 연구 명세와 기존 POSITION_STUDY_CASE proof에 선택 연결했다. 현재 테마를 과거에 붙이지 않으며 future availability/effective time·다른 dataset/symbol/case·실제 입력과 다른 내용·변형된 theme hash를 거절한다. 기존 연구 실행·목표·체결·값 adapter·writer를 재사용하고 새 collector/TR/DB query/table/writer/task/Manager는 없다. 기본 None 연구 명세/7-key proof는 유지한다.

같은 case의 HOLD/행동 branch는 같은 historical context를 가져야 완결 짝으로 인정한다. 최초 v2 게시에서 기존 H1 market 판단과 D5 membership 변환을 사용하고 동일 context_ref는 한 번만 재생한다. 이후 공통 RAM goal에는 작은 historical_context_summary(원래 refs/관측 테마/시장 quality/맥락·case coverage)를 공유한다. 기존 기본 v1 및 context 없는 v2 packet/goal 모양은 유지한다. 시장/테마 공통 모듈 두 개를 explicit KRX scientific implementation hash의 dependency에 추가했으며 legacy None hash는 유지한다. 기존 패키지를 새 지문으로 자동 재표지하지 않는다.

이 연결은 기록 당시 관측된 순위·테마의 제한된 provenance다. 초 단위 테마 leadership·성숙 breakout·시총·상위 EV/출구/stress 조건, 학습·선택·검증 계보와 native 보호/실행 후속 정책의 모델 지원은 아직 없다. 현재 조건의 exact statistical support를 승인하지 않는다. current_context_support_verifiedFalse·R4 EV_UNAVAILABLE/result=None/order OFF이며 사용자 순서③ 전체 판단은 미완료다. 원본 DB/capture의 독립 인증과 historical theme/revision 완전성도 not_verified다.

context는 최신100 ranking/100 theme snapshot 및128KiB, 기존 case proof256KiB/전체packet4MiB/event10000/outcome2000/bar2000 예산 안에 둔다. row count로 잘린 이력은 명시 flag를 남기고 bytes 초과는 실행 전 실패한다. 잘린 테마의 이전 seed나 빈 자료를 복원·추정하지 않는다. 새 현재 quote/status 조회가 과거 시각·quality/TTL을 갱신하지 않는다. 보호 Risk/Runner 경로는 이 준비/게시/Shadow 평가를 기다리지 않는다.

신규8 포함 관련 고유53건·소비 감사delta0·기존 supplied v1 전체 예제/decision ID 동일을 확인했다. 과거 case2개의 동결은 DB/판단 계산0·1.439ms, 네 run 준비 전후 borrow4/SELECT16/COMMIT0·원본 bytes 동일이다. 명시 연구 packet163433→262097bytes로 커지고 source read21.380→26.437ms다. 기존 별도 case event 행/COMMIT을 추가하지 않고 기존 명세/metadata payload만 커진다. 격리2/8 최초 게시의 과거시장 계산은 각각2회(case별1회), hot Collector/계좌 각1·현재 시장1·상위1·DB/COMMIT/주문0, status/중복100회 추가read/replay/evaluation0이다. 전체회귀·새 context proof의 HTTP/실제계좌 활성·00/04/REST/잔고/청산·PG/NAS/장중/CI/원격main은 검증하지 않았다. 기존 catalog의 무관 archive 미등록5개는 보류다. build2026.10.14-position-historical-context-v1·main/HEAD232737d 로컬 미커밋·미배포다.

다음은 이번에 없는 종목/타점·EV/Capacity/stress 조건과 모델 학습·선택·검증 계보를 기존 생산자에서 연결하고, 실제 현재 맥락에 대한 모델 지원 및 native 보호/실행 후속 정책의 적합성을 증명하는 것이다. 공통 시장/테마 관측이 붙었다는 이유만으로③ 전체 판단이나 모의계좌 운용 승인을 완료로 표시하지 않는다. 원래00 Position·Risk·Capacity/Liquidity의 상위 자동 비중과②/④ 수락·운용 승인 분리는 유지한다. [85단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#85-기존-연구-입력의-과거-순위테마-맥락-연결)

이하 단계 설명은 해당 시점의 기록이다.

## 2026-10-14 현재 진행 — 보고 출처 중복 확인에서 실제 맥락·정책 증명으로

기존 readonly EV 준비에서 원래 연구 명세/report·이벤트를 같은 transaction/borrow로 함께 전달하는 선택 include_source_records=True 경로를 추가했다. 기본 False 결과/원래 outcome·비교·정책 ID와 availability/TTL은 동일하다. 기존 CLI 입력의 명시 옵션으로 native_source_publication을 준비하고 기존 v2 allocation 게시/공통 RAM 경로에서만 원래 결과와 재생 대조한다. 새 DB 읽기·writer/table/collector/계좌/TR/task/Manager 없음.

같은 기존 native source 검증기를 EV와 R4 study가 재사용한다. run/input manifest hash·개발 role/범위·보고된 final 제외·이벤트 content/time/정수·원래 endpoints·outcome/source receipt 전체를 확인한다. 4MiB·전체 event10000/outcome2000/bar2000 예산은 유지한다. v2에서 원본 proof가 명시됐으면 null/변형/다른 receipt는 거절하고 기존 활성 슬롯/세대는 유지한다. proof가 없는 기존 v1/v2는 지원되지 않은 계보를 합성하지 않는다.

재검증한 보고 출처끼리 run·입력 revision hash·warmup을 포함한 선언 기간·outcome 공유를 확인해 현재 goal에 선택 allocation_lineage를 연결한다. 격리 시험의 서로 다른 run/input hash도 선언 기간16쌍이 겹쳐 REPORTED_OVERLAP으로 표시했다. 기간 끝 경계는 보수적으로 포함한다. REPORTED_DISJOINT도 원시 revision 목록·상위 모델 학습/선택/검증 계보·독립 source custody가 없으므로 independence_verified=False다. 보고된 중복 진단을 실제 통계적 독립성 승인으로 바꾸지 않는다. 과거 exact 시장/테마 context와 native 보호·실행 후속 정책의 모델 지원은 아직 미증명이므로 R4 EV_UNAVAILABLE/result=None/order OFF, 사용자 순서③ 전체 판단은 미완료다.

신규7 포함 관련 고유38건·소비 감사delta0·기존 supplied v1 전체 예제/decision ID 동일을 확인했다. 명시 네 run 준비는 변경 전후 SELECT16/COMMIT0·borrow4·원본DB bytes 동일이다. 격리2/8 Shadow는 Collector/계좌 각1·상위1·DB/COMMIT/주문0, status/중복100회 추가 read/replay/evaluation0이다. 재생은 최초 게시 때만 수행하고 실시간에서는 동결한 작은 계보 진단만 복사한다. 실제 CLI opt-in/default와 기존 v1 게시/해제/drain을 실행했다. 새로운 proof의 인증 HTTP→실제 broker 활성/전송·00/04/REST·잔고/청산·PG/NAS/장중/CI/원격main은 검증하지 않았다. 전수 회귀 없음, catalog의 기존 과거자료 미등록5개는 보류다. build2026.10.14-position-ev-lineage-v1·main/HEAD232737d 로컬 미커밋·미배포다.

다음은 기존 source가 관측 당시 공통 시장/테마/종목 context와 상위 EV 모델의 학습·선택·검증 계보를 실제로 생산할 수 있는 경로를 확인·연결하고 native 보호/실행 정책 지원을 증명하는 것이다. 현재 숫자나 서로 다른 run 이름만으로 과거 상태/독립성을 인정하지 않는다. 원래00의 상위 Position·Risk·Capacity/Liquidity 자동 비중 정책과② 모의계좌 수락/④ 전체 통합·운용 승인 분리는 유지한다. [84단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#84-상위-ev-원본-출처-재생과-r4-연구-중복-확인)

이하 단계 설명은 해당 시점의 기록이다.

## 2026-10-14 현재 진행 — 현재 native 사실에서 과거 모델 지원 증명으로

기존 Runner가 실제 반영 체결로 유지하는 가중 진입가·잔여 수량·최초 보유 시각, 등록 family/parameters/session/implementation identity, 원래 보호 요청·session clock·native Risk 한도를 기존 계좌 RAM 관측 한 번에 연결했다. 별도 원장 조회·TR·DB/COMMIT·저장/계산 엔진이나 task를 추가하지 않았다. 기존 기본 PositionAccountObservation 모양은 보존하며 현재 등록 owner의 정합성이 확인된 경우에만 선택 subtype의 native-position-runner-state/v1을 제공한다. 주문 권한은 false다.

82단계의 '원가·기간/전략 상태가 없다'는 표현을 바로잡는다. Risk의 owned_entry_prices와 Runner의 StrategyState/opened_at는 이미 존재했으며 당시 현재 비교 경로에 전달되지 않았던 것이다. 이번 연결은 그 사실의 관측을 해결한다. 보호·실행 후속 정책이 연구 모델과 동등하다는 증명, 과거 공통 시장 맥락과 상위 EV 독립 lineage는 아직 생산되지 않았다. 등록 family/parameters/session/implementation hash가 맞는지 확인하더라도 그 추가 정책 지원을 인정하지 않는다. 현재 R4는 계속 EV_UNAVAILABLE/result=None/order OFF이고 사용자 순서 ③ 전체 판단은 미완료다.

원장 high-watermark·Risk 원가/수량·계좌 cut·run/revision/path와 owner registry/계좌 epoch를 확인한다. 체결 반영 중·오류/동시 writer·복사 중 상태/보호/session 변경·복구/종료 시에는 추가 사실을 버린다. writer metadata lock은 I/O 밖의 짧은 경계에만 두고 getter는 기다리지 않는 lock 검사로 종료한다. 기존 native Risk 보호 callback은 이 lock이나 Shadow/LLM/다음 봉을 기다리지 않는다. 최초 보호 시각·최초 quote·예산·자식 주문 ID를 다시 만들지 않는다. 관측 조회와 미변경 fill read로 state availability를 갱신하지 않는다.

신규 11건 포함 관련 고유 98건·소비 감사 delta0·기존 supplied v1 전체 예제/decision ID 동일을 확인했다. 격리 native getter100회는 원장/DB/COMMIT/주문0, 평균0.1127ms이며 원래 상태/availability가 같다. 2/8 Shadow 모두 Collector/계좌 RAM 각1·native getter1·상위 계산1·원장/DB/COMMIT/주문0, status100회 추가 계좌 조회/계산0이다. 동일 시장 입력 cache 재사용 frame3.737/3.814ms이고 cold baseline11.244/9.499ms와 cache 조건이 다르므로 실운영 지연 개선으로 일반화하지 않는다. 실제 app 계좌 활성 HTTP→broker 전송, 실제 00/04/REST·부분체결/잔고/청산, PG/NAS/장중·CI/원격 main은 검증하지 않았다. 전체 회귀 없음, catalog의 기존 과거자료 미등록5개는 별도 보류다. build2026.10.14-native-position-state-v1·main/HEAD232737d 로컬 미커밋·미배포다.

다음은 기존 연구 source에서 과거 공통 context/상위 EV 입력·검증 lineage와 native 보호/실행 후속 지원을 실제 증명할 수 있는 경로를 확인·연결하는 것이다. 없는 과거 사실을 현재 숫자로 채우지 않는다. 원래 00의 상위 Position·Risk·Capacity/Liquidity 동적 자동 비중과 ② 모의계좌 수락/④ 전체 통합·운용 승인 분리는 유지한다. [83단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#83-native-runner-체결보호-상태의-동일-계좌-ram-관측)

이하 단계 설명은 해당 시점의 기록이다.

## 2026-10-14 현재 진행 — v2 RAM 연결에서 현재 지원 증명으로

81단계 native cohort의 원래 run 명세/report·이벤트를 동일 readonly transaction에서 함께 준비하고, 기존 PC client·인증 allocation-models endpoint·selection lock/세대 fence/owned drain/한 RAM 슬롯에 명시 v2로 게시한다. 최초 게시에서 같은 native source/case/cohort 검증기로 재생하고 원본 ID/availability/TTL을 보존한다. 기존 v1 EV/출구 receipt는 그대로 두며 혼합/부분 종목·변형 source/통계·다른 revision은 원자적으로 거절한다. 4MiB/전체 10,000 event·2,000 outcome/bar 예산은 늘리지 않았다.

공통 RAM에서 기존 상위 자동 비중→하위 목표와 같은 계좌 관측/ACK Risk를 공유해 현재 goal/Risk binding을 추가했다. 계좌·run/revision/path·원래 신선도/기한과 정확한 수량·가격/비용·sizing 의미를 확인하고 native Risk가 우선한다. 만료한 하위 목표/현재 결합은 조회에서 제거하며 모델 원래 기한은 갱신하지 않는다. 현재 관측에는 원가·보유기간/등록 전략 보호 상태가 없고 81단계 source에는 과거 공통 context·상위 EV 독립 lineage 증명이 없으므로 EV_UNAVAILABLE/result=None/order OFF다. v2 게시 완료를 현재 R4 사용/③ 전체 완료로 보고하지 않는다.

신규 8건 포함 관련 고유 63건·소비 감사 delta 0·기존 supplied v1 예제/ID 동일을 확인했다. 명시 PC 준비 네 run SELECT 16/COMMIT 0·원본 DB 동일로 81단계와 SQL 횟수가 같다. 격리 2/8 Shadow의 Collector/계좌 RAM 각 1회·상위 계산 1회·DB/COMMIT/주문 0, 100회 중복/status의 추가 조회/계산 0이다. 인증 HTTP/ASGI v2 최초/중복/revision/해제와 기존 v1 게시·취소 drain/보호 우선 회귀를 실행했다. 전체 HTTP 72묶음/전수 회귀·실제 계좌 활성/전송·PG/NAS/장중·CI/원격 main 검증은 하지 않았다. 신규 CI 등록과 core fingerprint를 유지했고 catalog의 기존 과거자료 미등록 5개는 별도 보류다. build 2026.10.14-position-study-publication-v2·main/HEAD 232737d 로컬 미커밋·미배포다.

다음은 기존 native owner/연구 source에 필요한 보호·후속 상태 및 과거 공통 시장 맥락·상위 EV 입력/검증 lineage 증명을 연결하는 것이다. 없는 상태를 합성하거나 현재 숫자만 과거에 붙이지 않는다. 원래 00의 상위 Position·Risk·Capacity/Liquidity 자동 비중 정책과 ② actual 00/04/REST/잔고·청산 수락, ④ 전체 통합/운용 승인 분리는 유지한다. [82단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#82-v2-연구-근거의-공통-ram-게시와-현재-목표risk-결합)

이하 단계 설명은 해당 시점의 기록이다.

## 2026-10-14 현재 진행 — v2 자체 모집단에서 현재 목표 결합으로

79단계의 두 번째 구현으로 사전 case/HOLD·행동 목록을 각 native run의 시작 명세에 고정하고, 실제 Paper의 HOLD+ADD/REDUCE 결과를 기존 readonly source guard·가치/수수료 adapter와 공통 짝 산술로 비교한다. 원래 outcome·정책/실행 ID와 당시 목표 TTL은 보존한다. 짝의 실제 포트폴리오/전략 상태·source bar/목표가 같아야 하며, case 창은 MARK 끝점을 공유하지 않는다. 선언한 case가 누락·미완료·미지원이면 성공 표본만으로 평균을 만들지 않고 전체 cohort EV를 미확인으로 반환한다.

지원된 완결 모집단의 연구 상태는 COHORT_COMPARABLE다. 이는 역사적 목표 규칙의 조건부 경험 통계이며 상위 자동 비중 정책 전체의 성과나 현재 타점 지원 증명은 아니다. exact context·상위 EV/검증 lineage 독립성·현재 계좌/Risk binding이 아직 없으므로 current_goal_status=EV_UNAVAILABLE/usable_for_current_goal=false/order_authorized=false다. v2 EXIT/flat REENTER와 partial/보호 개입 창의 확대, 명시 v2 인증 게시/공통 RAM 연결은 후속이다. 최종 비중 정책은 기존 00의 Position·Risk·Capacity/Liquidity를 따르는 상위 자동 결정이며 연구 목표금액은 하위 sizing 기능이다.

신규 12건 포함 관련 고유 81건·소비 감사 delta 0·기존 supplied v1 output/ID 동일을 확인했다. 명시 두 case/네 run 준비 SELECT 16·BEGIN/ROLLBACK 각 4·COMMIT 0·원본 SQLite bytes 동일이고 실시간 반복 호출을 추가하지 않았다. 신규 모듈은 기존 연구 profile/required Windows CI에 등록했으나 전체 catalog의 기존 과거자료 미등록 5개는 별도 보류로 남는다. 전수 회귀·HTTP 묶음·실제 주문/활성·PG/NAS/장중 수락·④ 통합/운용 승인·CI/원격 main 확인은 실행하지 않았다. build 2026.10.14-position-study-cohort-v2·main/HEAD 232737d 로컬 미커밋·미배포다. [81단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#81-v2-native-짝-모집단-동결과-공통-통계)

이하 단계 설명은 해당 시점의 기록이다.

## 2026-10-14 현재 진행 — v2 native 증명에서 자체 짝 비교로

79단계 인계의 첫 구현을 완료했다. 기존 position_target.py의 typed v2 study/case 계약은 sizing 의미·등록 family/설정·실행/cost/session·실제 scientific hash를 안정적인 study ref에 묶고, 원래 순간별 요청/TTL은 별도로 유지한다. 기존 research_run_identity/execute_research의 명시 opt-in에서 같은 run spec에 study/case를 동결하고, 같은 Paper/원장 writer가 실제 source bar·포트폴리오/전략 상태·목표 계산의 POSITION_STUDY_CASE 증명 한 건을 기록한다. native readonly reader는 기존 source guard와 가치/비용 adapter를 재사용해 원래 outcome/정책 ID를 보존한다.

실제 native 연구 run에서 30초 목표의 180초 완결 결과를 원래 TTL 연장 없이 읽고, 독립 HOLD에는 목표 주문이 없는 것을 검증했다. 이것은 NATIVE_CASE_PROOF이며 짝 모집단/현재 exact context 지원 증명이 아직 없으므로 R4는 EV_UNAVAILABLE/주문 OFF다. 현재 공통 RAM에 v2를 게시하지 않았다. 다음은 자체 독립 native cohort/같은 짝 통계, 이어 기존 인증 게시/공통 RAM 연결이다.

관련 고유 149건(신규 10건)·기존 supplied v1 예제/ID 동일·소비 감사 delta 0을 확인했다. 기존 연구 101건/118.869초와 수량/R4/비중 호환 46건/0.110초, 최종 신규 경계 검증을 실행했고 전체 회귀/기존 HTTP 묶음은 반복하지 않았다. 단일 명시 native 준비는 run borrow 1·SELECT 4·BEGIN/ROLLBACK 각 1·COMMIT 0·원본 SQLite 동일이다. 이 비용은 실시간 반복 판단 비용이 아니다. source_durability=not_verified/order_authorized=false·실제 계좌/PG/NAS/장중 수락·④ 전체 모의 통합/운용 승인은 별도다. build 2026.10.14-position-study-native-v2·main/HEAD 232737d 로컬 미커밋·미배포다. [80단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#80-v2-연구-규칙순간별-case-계약과-native-증명-첫-구현)

이하 단계 설명은 해당 시점의 기록이다.

## 2026-10-14 현재 진행 — R4 v2의 연구 규칙/순간별 판단 계약 확정

[79단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#79-동적-목표의-연구-정책과-순간별-판단-유효기간-분리)는 설계 완료·구현 대기다. 짧은 현재 목표 TTL과 연구 horizon을 독립적으로 두고, 안정적 study 의미/원래 case 판단 증명/완결 근거를 연결한다. 기존 v1 계약은 보존하며 날짜만 제거한 정책 재표지나 TTL 연장은 하지 않는다. 같은 목표·수량/가격·비용·후속 정책/지원 상태의 명시 native v2 근거만 새롭고 유효한 현재 목표에 사용한다.

다음 Sol High 작업은 v2 식별·시간/native proof → 독립 cohort와 같은 R4 통계 → 기존 인증 게시/공통 RAM의 순서다. 먼저 구현 결과를 검증한 뒤 ③ 주문 없는 통합 완료를 판정한다. 상위 목표는 원래 Position·Risk·Capacity/Liquidity가 자동 결정하고 하위 수량 기능은 이를 소비한다. ② 실제 모의 수락 대기와 ③ 개발을 분리하고, ④ 전체 모의 통합/별도 운용 승인을 유지한다.

## 2026-10-14 현재 진행 — 공통 RAM 게시와 상위→하위 목표 연결

기존 PC readonly 연구 준비의 배분 EV·전체 출구 Capacity/스트레스 결과를 인증된 POST /api/v1/research/ram-shadow/allocation-models로 기존 공통 MarketThemeShadowRuntime의 RAM 슬롯 하나에 게시한다. 기존 선택 revision/계좌·run/binding/path·정책 내용/종목·가격/현재·검증 수량·동결 cost/horizon·원래 availability/expiry를 결합한다. native 짝 EV 통계와 같은 Paper 출구 시나리오를 최초 게시에서 재생 검증하고, 동일 패킷 재전송/status는 재생·계좌 조회·깨우기·TTL 갱신이 없다. 선택 교체/해제·재시작은 슬롯을 복구하지 않으며 이전 worker가 새 세대 결과를 덮어쓰지 못한다.

기존 Collector/공통 시장·테마/ACK된 계좌 Risk 사실이 유효하면 상위 자동 비중→기존 하위 목표 수량을 공통 frame에서 한 번 계산한다. 가격·계좌/Risk binding·source TTL이 바뀌거나 근거가 부족하면 BLOCKED/미확인으로 남기며 만료한 목표 결과는 조회에 노출하지 않는다. 새 동적 목표 정책의 R4 행동 비교는 자체 완결 모집단이 필요하므로 EV_UNAVAILABLE다. 기존 정책의 EV를 새 정책으로 재표지하지 않는다. PC_READONLY_REPLAY_REPORTED·source_durability=not_verified·order_authorized=false이며 DB 원본/시장 확률·운영 Risk/주문 승인을 뜻하지 않는다.

관련 고유 96건(신규 8건) 통과 후 최종 영향 15건 및 입력/종료 경계 2건을 확인했다. 격리 2/8 Shadow에서 게시 후 Collector/계좌 RAM 각 1회·상위 계산 1회, DB 연결/COMMIT/주문/구독 0이며 중복/status 100회 추가 계산/조회 0이다. 최초 시장 계산은 기존 cache에 공유하고 게시만으로 재계산하지 않는다. 원본 자료 준비의 SELECT 16(EV)/4(출구)·COMMIT 0은 앞 단계의 별도 비용이다. 전체 회귀/기존 HTTP 72시나리오는 반복하지 않았다. 소비 감사 delta 0·기존 supplied v1 예제/ID 동일, 관련 없는 과거자료 catalog 5개 보류 유지다. build 2026.10.14-allocation-model-publication-v1·main/HEAD 232737d 로컬 미커밋·미배포·실제 계좌 활성/주문 없음.

다음 개발은 생성된 동적 목표 정책에 일치하는 독립 native 모집단과 R4 행동 비교의 주문 없는 통합이다. actual 모의 00/04/REST·부분체결/취소·잔고/청산 수락과 ④ 전체 모의 통합·별도 운용 승인은 남는다. ② 운용 수락 대기는 ③ 개발의 선행 조건이 아니다. 아래 단계별 설명은 각 시점의 기록이며 현재 미완료 범위는 이 절을 따른다. [78단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#78-준비된-native-모델의-공통-ram-게시와-상위하위-목표-연결)

## 2026-10-14 현재 진행 — 전체 보유 청산/스트레스 검증

기존 PaperExecutionEngine의 부분 SELL 원가/잔고/수수료 경로로 전체 잠재 보유의 출구 Capacity·스트레스 연구 시나리오를 구현했다. 참여율·volume haircut·가격 충격·추가 슬리피지·체결 불가 구간/기간은 전부 명시 정책이다. 기존 guarded native readonly reader에서 시장 bar proof 창을 한 번 읽어 재사용하며 WAIT/REPRICE 성과를 청산 근거로 재표지하지 않는다. 모든 서로 다른 시나리오에서 전체 수량이 완료된 경우만 검증 수량까지 근거를 만들고, 부분/불가/누락/미래/비용 만료는 미확인이다.

상위 비중은 실제 보유+pending BUY 전체·가격·비용·출구 horizon·스트레스 값·원래 TTL을 묶으며 pending SELL로 위험 여유를 만들지 않는다. 기존 native EV와 두 출구 근거를 함께 소비하는 순수 주문 없는 계산도 검증했다. 이는 명시한 역사/충격 가정의 연구 결과이며 미래 유동성/확률·실제 청산 보장·Risk/주문 승인이 아니다. 세 producer의 공통 RAM 게시/실시간 계좌 상위→하위 통합은 다음 단계이며 현재 native RAM result=None/BLOCKED는 유지한다. actual 00/04/REST/잔고/청산 수락과 운용 승인은 별도다.

관련 고유 125건(신규 16건) 및 기존 supplied v1 봉인 예제/ID·소비 감사 delta 0 pass. 명시 준비 SELECT 4·COMMIT 0/1 run·원본 DB 동일, 준비된 native EV/출구·스트레스의 pure preview 101회 DB/주문 0이다. 전체 회귀/기존 HTTP 72시나리오는 반복하지 않았다. 관련 없는 과거자료 catalog 5개 보류 유지. build 2026.10.14-full-exit-stress-v1·main/HEAD 232737d 로컬 미커밋·미배포·실제 계좌 활성/주문 없음. [77단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#77-전체-보유-출구-capacity와-명시-스트레스-연구-근거)

## 2026-10-14 현재 진행 — 검증 크기에 묶은 native 배분 EV

기존 native 연구 원장 reader/PaperExecutionEngine/R4 paired 비교를 재사용해 ADD·REENTER 대 독립 HOLD의 배분 EV를 명시 readonly 준비하는 경로와 기존 preview CLI를 연결했다. 현재가·보유/목표 수량·비용·horizon·완결 TRAIN/VALIDATION 짝·원래 availability/expiry를 묶는다. 순 차이는 추가 매수 대금 기준 Fraction 평균 내림/표준오차 올림이며 calibrated 확률/신뢰구간이 아니다. 상위 비중 계산은 검증된 정확한 BUY 수량만 허용하고 더 큰 비중·부분 현금/위험 수량에 외삽하지 않는다. 불리한 ADD EV를 기존 보유의 EXIT 근거로 바꾸지 않는다.

이 producer는 실시간 루프 밖의 PC readonly 연구 준비다. 현재 공통 RAM에는 게시하지 않았고 전체 출구 Capacity·스트레스 모델도 미공급이므로 native 비중 결과는 계속 None/BLOCKED다. 순수 preview의 supplied Capacity/스트레스를 native 검증으로 보고하지 않는다. 다음은 기존 공통 실행 모델에 전체 청산/스트레스 연구 시나리오를 명시해 크기·시간·유동성·비용 범위를 검증하고, 세 근거의 RAM 게시와 계좌 상위→하위 통합을 연결하는 것이다. 실제 모의 00/04/REST 수락·운용 승인은 별도다.

관련 고유 118건(신규 12건)·최종 통계 영향 49건·기존 RAM/API 14건 및 봉인된 supplied v1 예제/decision ID 대조·소비 감사 delta 0 pass다. readonly 부하는 76단계 및 data/diagnostics/20261014-allocation-native-ev에 기록한다. SELECT 16·COMMIT 0/4 run을 명시 준비에서만 수행했고 pure preview 101회 DB/주문 0·원본 DB 동일이다. 전수 회귀·실제 주문·계좌 활성·배포·commit/push 없음. build 2026.10.14-allocation-native-ev-v1·main/HEAD 232737d 로컬 미커밋이다. [76단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#76-배분-ev의-native-연구-공급과-검증-크기-제한)

## 2026-10-14 현재 진행 — 동적 비중의 실제 입력 사실 연결

2026-10-14 동적 비중의 실제 사실 입력을 기존 공통 Shadow runtime에 연결했다. 명시 position_allocation 선택에서 승인 KRX RAM 현재가/0B 시총·기존 창 대금/관측 종가 RMS·공통 TOP20/테마/종목 맥락과 ACK된 native 계좌/Risk를 한 번 빌린다. 기존 R4 비교와 같은 계좌 read를 공유하고 변환은 같은 owned drain에서 수행해 native 손절을 기다리게 하지 않는다. 조회/재시작으로 원래 source TTL을 갱신하지 않는다.

현재 배분용 독립 EV·전체 출구 Capacity·스트레스 모델 producer는 없으므로 result=None/BLOCKED다. 미체결 EV/leadership 점수·관측 대금을 필요한 근거로 재표지하지 않으며 목표 0을 합성하거나 새 주문을 승인하지 않는다. stage74의 native 전체 목표 계산이 완료된 것으로 보고하지 않는다. source_durability=not_verified/order_authorized=false 유지다.

기존 119건/최종 신규 14건=고유 133건·기존 HTTP 72시나리오/실제 lifespan·health/build·소비 감사 delta 0 pass. 2/8 Shadow 모두 Collector RAM 1·계좌 RAM 1·시장 계산 1·입력 변환 1, DB/COMMIT/추가 주문·구독 0; 조회 100회 추가 계산 0이다. 기존 전체 회귀는 반복하지 않았으며 과거자료 catalog 5개 보류 유지다. 다음은 세 모델의 크기/시점/비용/horizon 결합과 계좌 전체 상위→하위/R4 주문 없는 통합이다. 실제 모의 수락/운용 승인은 별도다. build dynamic-allocation-ram-v1·HEAD 232737d 미커밋, 이번 작업에서 배포/계좌 활성/실제 주문은 하지 않았다. [75단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#75-동적-비중의-공통-ram-사실과-native-계좌-risk-연결)

## 2026-10-14 현재 진행 — ③ 동적 비중 첫 계산 완료

2026-10-14 실제 모의 수락 준비를 인증 GET/선택 NAS 릴리즈 지문으로 확인했다. NAS recorder-shared-10m-v1은 관련 5파일이 개발 소스와 다르고 mock 계좌는 MANUAL·automatic control/Runner 없음·계좌별 주문 OFF·native 원장 0건이다. 실제 주문/취소·활성/설정·배포 변경은 하지 않았다. ② actual 00/04/REST·부분체결/잔고/청산 수락은 남긴다.

사용자 지시대로 운영 수락 대기와 개발을 분리해 ③ 동적 비중의 첫 주문 없는 계산을 구현했다. 기존 position_target.py에서 계좌 운용 한도/현재 현금·보유·미체결을 바탕으로 시장·테마·종목 강도/순 EV·불확실성·시총·거래대금·출구 수용량·변동성·스트레스 위험으로 자동 목표 비중/금액을 결정하고 기존 하위 수량/비용 정책과 preview CLI를 재사용한다. 미체결 매도에 현금/위험 여유를 주지 않고 겹치는 테마·현재/제안 포트폴리오를 모두 검사한다. 수동 종목 투자금 입력·임의 운용 계수/한도·신용 활성 없음. supplied 실험용 정책으로 native 입력/운영 승인은 아니다.

관련 고유 103건(신규 21건)·기존 HTTP 72시나리오/실제 lifespan·health/build·CLI 동일 결과·소비 감사 delta 0 pass. 200종목 controlled 계산 33.337ms·DB/COMMIT/주문 0, source TTL 갱신 없음. 전체 회귀/직전 보호 묶음은 반복하지 않았다. 다음은 실제 공통 RAM의 시장/테마·EV·Capacity·native Risk 입력 연결과 R4 계좌 전체 목표/행동 비교의 주문 없는 통합이다. ② 수락 준비 대기는 ③ 개발의 선행 조건이 아니다. build dynamic-allocation-shadow-v1·HEAD 232737d 미커밋·미배포다. [74단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#74-실제-모의계좌-수락-준비-확인과-상위-동적-비중-계산-시작)

## 2026-10-14 현재 진행 — 즉시 손절 연결 완료

2026-10-14 기존 동결 unfilled execution 정책을 선택한 자동 모의 Runner에 즉시 종목 손절 producer를 연결했다. 상세 FILL 기반 owned_entry_prices·기존 정수 이동평균과 승인 후보 stop_loss_bps를 사용하고, Collector 승인 KRX RAM→기존 Supervisor→Runner의 가격 비교/한 번 wake로 봉·Shadow·LLM/EV를 기다리지 않는다. 최초 손절 가격·시점은 고정하고 현재 계좌 ACK/상세 체결·owner/control/native QUEUED에서 원가·수량·candidate/hash/정규 한도를 재검사한다. 일반 cue는 review 전용, UNKNOWN/취소 미확정의 중복 제출 금지는 유지한다. 오래된 계좌는 기존 recovery queue로 대사하고 가격 만료는 BLOCKED/운영자 주의다.

관련 280건(신규 18건)/101.808초와 기존 HTTP 72시나리오·실제 ASGI lifespan/health/build pass. 정상/손절 중복 1,000틱 각각 SELECT·COMMIT·전송 0, 최초 RAM cue 0.395ms이다. 현재 계좌 ACK 후 첫 보호 승인/원장/가짜 전송 전체는 SELECT 80·COMMIT 18·13.587ms이며 보호 대기 10회 SELECT 20·COMMIT 0이다. 실제 REST/시장/NAS 지연은 별도다. 전체 회귀는 반복하지 않았고 소비 감사 delta 0, 과거자료 catalog 5개 보류 유지다.

실제 계좌 활성/전송·00/04/REST·PG/NAS/장중 수락과 운용 승인은 남는다. 다음은 실제 모의 부분체결/미체결/잔고 수락, 이어 원래 Position·Risk·Capacity/Liquidity의 상위 동적 비중·전체 통합 순서다. 운용 승인 대기는 동적 비중 개발을 막지 않는다. build 2026.10.14-immediate-native-stop-v1·main/HEAD 232737d 미커밋·미배포다. [73단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#73-승인된-collector-ram-가격의-즉시-종목-손절과-native-보호-exit)

2026-10-14 [72단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#72-pc-native-원장-결과의-인증-게시와-기존-runner-ram-접수): PC CentralContentClient의 명시 prepare_native_unfilled_population으로 기존 readonly 연구 원장/공통 재생을 한 번 검증하고, 같은 클라이언트의 publish_native_unfilled_population이 준비된 패킷을 기존 인증 POST 경계로 보낸다. 기존 mock 라우트·Supervisor·Runner의 계좌 lock/owned drain/단일 RAM 접수를 재사용한다. 서버에 DB 경로를 보내거나 계좌를 활성화하지 않는다. 원래 native 결과의 최대 available_at과 내용 hash를 고정하며 HTTP 재전송/계좌 ACK/재시작으로 갱신하지 않는다. 계좌·profile/spec/run/control·원주문/known child·동결 비용/horizon/표본·같은 native 정책/모델·완결 짝/가치·1MiB/200창/10,000 event budget을 검사한다. 새 패킷에는 현재 native RUNNING control 한 번을 확인하고 같은 패킷의 inert 중복은 새 조회/깨우기/슬롯 교체가 없다. 등록 전환과 RAM 교체는 기존 input lock 안에서 검사한다. 현재 native 단계의 출처 보고/실험 경계와 실제 계좌 승인 분리를 유지한다. **다음 우선 보완은 종목별 즉시 native 손절 producer다.** 다음 우선 보완은 종목별 즉시 native 손절 producer이며 기존 Risk→Runner 보호 경로를 재사용한다. 이후 실제 모의 00/04/REST 수락을 별도 다루고 ③ 원래 Position·Risk·Capacity/Liquidity 상위 동적 비중 → ④ 모의 통합/별도 운용 승인 순서를 유지한다. 운용 승인을 동적 비중 개발의 선행 조건으로 두지 않는다. 실제 부분체결/CANCELLED 연구 모델·취소 지연 보정은 후속이며 입력이 없는 상태를 유리한 EV로 합성하지 않는다. 원래 00 통합 명세의 계좌 한도/시장·테마·종목 특성/시총·거래대금·유동성·변동성·EV·위험으로 상위 목표 비중을 자동 결정하는 목표를 유지하고 현재 금액/수량 계산은 하위 기능이다.

2026-10-14 [71단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#71-기존-연구-실행기의-지정가-분기와-native-미체결-결과-읽기): 기존 PaperExecutionEngine에 명시 unfilled_action_plan의 SUBMIT→WAIT/REPRICE 지정가 연구 분기를 연결하고 같은 연구 원장 writer를 재사용했다. native readonly 개발 구간/최종평가·내용 hash/cut/예산 guard 뒤에 같은 실행기로 가격·현금·예약·비용·취소/재주문 순서를 재생해 UnfilledActionOutcome을 생성한다. 기본 next-open 실행과 기존 HOLD/EXIT/자동 목표 자료는 유지한다. **② 다음은 native 원장 결과의 기존 RAM 접수 게시와 미지원 상태 처리다.** 실시간 원장 입력 게시 caller·CANCELLED/부분체결 모집단 생성은 후속이며, 실제 호가 순서·취소 지연·부분체결 확률·시장 원본 lineage는 미검증이다. source_durability=not_verified와 주문 승인 false를 유지한다. 실제 00/04/REST·종목별 즉시 native 손절 producer/수락·PG/NAS/장중 운용 승인도 남는다. 운영 승인 전까지 실험 자료/모델 가정을 유지한다. ③은 원래 00 통합 명세의 시장·테마·시총·거래대금·유동성·변동성·EV·위험을 계좌 운용 한도에 결합하는 Position·Risk·Capacity/Liquidity 상위 동적 비중이고 현재 목표 금액/수량은 하위 기능이다. 이후 ④ 모의 통합/별도 운용 승인 순서를 유지하며 실제 운용 승인을 ③ 개발의 선행 조건으로 두지 않는다. 아래 과거 다음 단계 표현은 당시 기록이다.

2026-10-14 [70단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#70-waitreprice-짝-입력동결-비용익절-판단의-공통-연결)에서 별도 WAIT/REPRICE 짝 입력의 공통 계산/명시 RAM 접수와 동결 비용·기존 전략의 실제 보유 판단을 연결했다. 새 주문의 native Risk 시각 정렬·보호 취소의 빠른 경로·취소 후 상태별 새 모집단을 검증했다. **②의 다음 보완은 기존 연구 Runtime/원장으로 실제 WAIT/REPRICE 모집단을 생성하고 native 출처·개발 구간을 검증하는 것이다.** 현재 명시 실험 자료의 not_verified를 운용 예측/승인으로 승격하지 않는다. 종목별 즉시 손절 및 실제 계좌 00/04/REST 수락은 별도 남고, ③ 원래 Position·Risk·Capacity/Liquidity 상위 동적 비중 → ④ 모의 전체 통합/별도 운용 승인 순서다. 실제 운용 승인을 동적 비중 개발의 선행 조건으로 두지 않는다.

2026-10-14 [65단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#65-running-runner의-사건기한-소비와-고정-주문의-동일-native-실행)에서 ②의 running 소비자와 고정 주문 실행을 연결했다. 기존 loop는 하나의 current RAM cut을 사건/기한에만 처리하고 단순 신선도 갱신은 재검토하지 않는다. **다음은 production 가격/타점/짝 EV·broker 확인 입력 공급자와 기존 취소 primitive의 목적별 자동 caller 연결**, 실제 00/04/REST 부분체결·잔고 대조다. running 소비만으로 ② 전체 완료/운용 승인으로 보지 않는다. ③ 원래 Position·Risk·Capacity/Liquidity 동적 비중·④ 전체 모의 통합/별도 운용 승인 순서는 유지한다.

2026-10-14 [64단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#64-목적별-고정-대체-주문의-native-승인과-제출)에서 ②의 명시 동결 정책/v3 고정 child 승인·native 제출을 로컬 검증했다. 다음은 같은 running Runner의 실제 미체결 사건/기한과 현재 입력 producer를 연결해 목적별 취소·재주문을 자동 처리하는 것이다. 현재 idle 명시 실행, 기존 명세 OFF이며 실제 계좌/운용 승인은 별도다. ③ 동적 비중·④ 전체 모의 통합/운용 승인 순서는 유지한다.

2026-10-14 [63단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#63-모의계좌-자동-취소의-native-실행-경계)에서 ②의 native 취소 실행 기반을 로컬 검증했다. 다음은 목적별 사건/기한 dispatcher와 고정 child의 실제 승인/제출이며 이후 실제 00/04/REST 수락을 진행한다. 자동 매매 활성화나 ② 전체 완료는 아니다. ③ 동적 비중·④ 모의 전체 통합/별도 운용 승인 순서는 유지한다.

2026-10-14 사용자 지정 순서는 **① 운영 Risk→보호 요청 자동 연결 → ② BUY·익절·보호 매도의 모의계좌 자동 취소/재주문 및 실제 00·04/REST 대사 → ③ 원래 Position·Risk·Capacity/Liquidity의 동적 비중조절 → ④ 선정·비중·주문·미체결·청산 모의 통합 검증과 별도 운용 승인**이다. 이전 동적 비중 우선 권고를 대체한다. [62단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#62-운영-risk의-자동-보호-요청-연결과-개발-순서-개정)에서 ①의 자동 전달/검토 연결부터 구현하며 실제 계좌 활성화·주문·배포와 운용 승인은 구분한다.

R4 후 [목적별 고정 child·보호 요청 OFF 검토](docs/AI_TRADING_SYSTEM_REDESIGN.md#60-목적별-고정-대체-주문-검토와-보호-요청의-우선-처리)의 구현과 61단계 통합 수락으로 첫 OFF 범위를 종료했다. 실제 보호 producer·사건/기한 자동 dispatcher·청산/지연/잔고 수락은 별도 후속이다.

갱신: **2026-10-13 · 제품 2.1.0** · [현재 상태](docs/CURRENT_STATUS.md) · [남은 작업](docs/OPEN_ITEMS.md)

## 방향

시장·테마·주도주 맥락과 타점을 연결하고, 행동별 비용·위험을 판단해 검증된 정책만 실행한다. 기존 돌파 조건은 잠정 정책이며 사례와 사용자 피드백으로 개선한다. 뉴스 수집/분석은 기존 독립 범위를 유지하고, 새 뉴스 엔진 완성을 모든 기능의 선행 조건으로 두지 않는다.

최초 사용자 요청은 전체 설계를 다시 구성하고 **기존 코드 재조립과 전체 신규 개발을 비교해 선택할 근거를 만드는 것**이었다. [전체 설계·재구성 판단](docs/AI_TRADING_SYSTEM_REDESIGN.md)이 목표 구조와 선택 근거의 정본이다. 후속 승인으로 기존 돌파 경로의 첫 판단 소비 연결·고정 비교를 완료했다. 전체 목표 구조 구현·운영 전환은 남는다.

현재 추천은 **기존 앱/자료 보존 + 필요한 핵심 경로의 유지·정리·교체 + 업무 저장/판단/UI 소비 경계의 선택적 재조립**이다. 단순 파일 분리나 기존 기능 100% 복제가 목표가 아니다. 작은 새 구조의 비교안은 참고하며 새 프로젝트 생성으로 결정하지 않았다. 첫 실제 변경은 돌파 경로 하나의 판단 소비 연결로 제한했다. 후속 구조 변경과 새 매매 기능 개발도 분리한다.

## 개발 단계

| 단계 | 기능 납품 | 고정 종료 조건 | 상태 |
|---|---|---|---|
| R0 전체 설계/선택 | 현재 구현 대응표, 목표 구조, 재조립/신규 개발 비교 | 판단 가능한 설계안과 실제 근거 제시 | 설계 완료, 선택적 재조립의 첫 경로 비교 승인 |
| R1 기존 핵심 경로 재구성 | 입력→기존 판단→위험→주문/체결→기록 한 경로의 유지/정리/교체 | 기존 동작/안전 경계 보존, 좁은 소비 계약, 새 전달 계층 없음 | 돌파 판단 소비 연결·고정 비교 완료. 전체 구조 목표/새 소비 계약 수락은 남음([범위](docs/AI_TRADING_SYSTEM_REDESIGN.md#22-r1-첫-돌파-판단-소비-연결)) |
| R2 해당 저장 경로 최적화 | 읽기/쓰기 목적·주기·내구성·중복/no-op·업무 transaction | 관련 실제 부하/ACK/rollback·동시성의 전후 비교, 원인 미확정은 해결 완료로 처리하지 않음 | 불변 근거 read 왕복 감소와 checkpoint retry/no-op/owned drain의 로컬 재현·보완 완료. 실제 PG·다른 writer·전체 성능 수락은 남음([read](docs/AI_TRADING_SYSTEM_REDESIGN.md#23-r2-불변-승인-근거의-좁은-조회), [checkpoint](docs/AI_TRADING_SYSTEM_REDESIGN.md#24-r2-checkpoint-저장-실패noop종료-순서)) |
| R3 새 판단 기능 연결 | 시장→테마/대장→종목 행동·무효화와 공통 계산/Shadow | 구조 정리와 별도 기능 개발, 가용시각/quality·같은 입력의 같은 판단·실제 gate | 기존 진입 가설·Collector RAM 공통 입력/H1 공유·독립 판단·앱 수명·명시 화면 선택/선별 기록·원본 대조 연결을 로컬 검증. 신규 진입 규칙·실행 gate 연결·원본 확정/운영 승격·실제 앱/PG/NAS/장중 수락은 남음. 운용 진단 전체를 R4 주문 없는 개발의 선행 조건으로 묶지 않음([범위](docs/AI_TRADING_SYSTEM_REDESIGN.md#45-ram-출처의-기존-녹화-입력-조회-힌트)) |
| R4 포지션·EV | ADD/REDUCE/REENTER 목표 수량, 비용 차감 행동 비교와 불확실성 | 미체결·부분체결·예약 자금 반영, 미지원 주문 차단 | 명시 목표량과 기존 계좌/주문 DTO의 공통 계산·고정 입력 CLI, 완결된 행동별 자료의 HOLD 대비 짝 비교·비용 차감 평균/표본/변동성을 연결. 고정 입력의 기존 공통 Runtime/관찰 표·명시 선택/선별 기록 소비를 연결. 기존 mock owner RAM/Collector 가격의 명시 입력 전달 완료. 선택 계좌의 전체 run 원장/수동 주문·broker 확인 잔량 대사와 결손 차단을 로컬 검증([대사](docs/AI_TRADING_SYSTEM_REDESIGN.md#50-r4-계좌-전체-대사와-브로커-확인-잔량-계산)). 기존 연구 원장의 완결 HOLD/EXIT reader·명시 CLI 선택을 로컬 검증([자료](docs/AI_TRADING_SYSTEM_REDESIGN.md#51-r4-기존-연구-원장의-완결-행동-자료-reader-연결)). 기존 PC 관찰 worker의 명시 DB/창/학습·검증 선택과 저장/복구·취소/종료를 로컬 검증([PC 선택](docs/AI_TRADING_SYSTEM_REDESIGN.md#52-r4-완결-행동-자료의-기존-pc-관찰-선택-연결)). 명시 bounded 원화/보유 한도·남길 현금·가용 구간의 자동 수량을 기존 계산/공통 RAM/CLI·PC 선택·저장/복구에 연결([자동 목표](docs/AI_TRADING_SYSTEM_REDESIGN.md#53-r4-자동-목표-수량-정책의-공통-shadow-계산-연결)). 기존 runner의 명시 목표 계획·같은 PaperExecutionEngine/writer→native ADD/REDUCE/REENTER proof·공통 비교/CLI·PC 선택을 로컬 검증([native 자료](docs/AI_TRADING_SYSTEM_REDESIGN.md#54-r4-자동-목표의-native-addreducereenter-실행-자료-연결)). 명시 연구 위험 제약·비용/불확실성을 반영한 주문 없는 행동 권고를 로컬 검증([행동 권고](docs/AI_TRADING_SYSTEM_REDESIGN.md#55-r4-비용불확실성위험-제약의-주문-없는-행동-권고)). 기존 타점/native Risk RAM의 공통 입력/제약 연결을 로컬 검증([native 입력](docs/AI_TRADING_SYSTEM_REDESIGN.md#56-r4-기존-타점과-native-risk의-공통-ram-입력-연결)). R4 주문 없는 첫 기능 범위 종료; 다음은 R4 후 미체결 정책의 기존 취소/대사 계약 연결을 주문 없는 모의 검증으로 진행. 최종 상위 동적 비중은 원래 Position·Risk·Capacity/Liquidity 설계를 따라 후속 구현하고 현재 목표 금액/수량은 하위 기능. 계획 생성/자동 예약·손절 기반 sizing·예측 보정/운영 승격·실제 앱/운용 연구 데이터·PG/NAS/계좌 수락은 남음([목표량](docs/AI_TRADING_SYSTEM_REDESIGN.md#46-r4-명시-목표-수량과-기존-계좌주문-입력-연결), [비교](docs/AI_TRADING_SYSTEM_REDESIGN.md#47-r4-행동별-비용-차감-관측-기대값과-불확실성-비교)) |
| R4 후 실행 연결 | 매수·일반 익절·손절/위험 청산의 미체결 관리 | WAIT/CANCEL/REPRICE·REENTER/ABANDON의 목적별 판단, 취소 확정 전 재주문·UNKNOWN 중복 제출 금지, 보호 위험 한도 우선·실제 체결/잔고 확인 | [미체결 정책](docs/AI_TRADING_SYSTEM_REDESIGN.md#76-미체결-주문-관리-정책) 설계 명시. 기존 lifecycle/목표·비용 계산의 목적별 순수 선택/고정 CLI를 로컬 검증([첫 정책](docs/AI_TRADING_SYSTEM_REDESIGN.md#57-r4-후-미체결-목적별-정책의-주문-없는-첫-검증)). 기존 idle runner의 영속 첫 preview 예약/현재 결과·취소/체결 변경 재검증·checkpoint 복구/보호 기한 힌트를 로컬 검증([영속 검토](docs/AI_TRADING_SYSTEM_REDESIGN.md#58-미체결-검토-계획의-기존-checkpoint-복구와-실행-전-검증)). 기존 원장/닫힌 owner/control/native Risk의 다회 claim·전송 proof와 고정 child/예산·상세 보유/Risk cut을 로컬 검증([native 시도](docs/AI_TRADING_SYSTEM_REDESIGN.md#59-native-parent-child-시도-투영과-닫힌-owner-gate-검토)). 목적별 고정 child·원래 승인/현재 Risk와 기존 runner의 OFF 보호 요청 우선 처리를 로컬 검증([목적 gate/요청](docs/AI_TRADING_SYSTEM_REDESIGN.md#60-목적별-고정-대체-주문-검토와-보호-요청의-우선-처리)). 첫 OFF 연결 통합 수락 완료([통합/DB 범위](docs/AI_TRADING_SYSTEM_REDESIGN.md#61-주문-off-첫-연결의-통합-수락과-db-호출-범위)). 사용자 순서 ① native Risk 자동 전달/보호 검토를 로컬 검증([62단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#62-운영-risk의-자동-보호-요청-연결과-개발-순서-개정)). 다음은 ② 목적별 모의계좌 자동 취소/재주문·00/04/REST 대사, 이후 ③ 동적 비중·④ 전체 모의 통합/운용 승인. 운영 보호 producer·자동 취소/재주문·실제 청산/지연 수락은 후속. LLM·다음 1분봉·Shadow 완료를 기다리지 않는 실행 연결 필요 |
| R5 학습·운용 확장 | 사례/RAG·후보 모델 비교·승격과 필요 venue/전략 확장 | 시간/사건 분리 평가, 계좌/venue별 명시 지원·승격 | 미구현 |

위 상태는 **새 설계의 연결 기능** 기준이다. 기존 모듈 전체가 미구현이라는 뜻이 아니다. 재사용 파일과 이미 구현된 기능은 전체 설계 3·13절에서 확인한다. 기존 원장/과거 결과·지원 두 Family를 새 정책으로 덮어쓰지 않는다.

## 단계가 다시 옆으로 새지 않게 하는 기준

- Shadow는 공통 판단 엔진의 주문 없는 검증 실행으로 통일한다. 전략별 규칙·상태·결과를 보존하며 수집·시장 분석·기록·복구는 기존 기능을 재사용한다. R3 후속은 전용 엔진/producer 확장보다 기존 입력·공통 맥락·등록 evaluator의 공통 소비 연결을 먼저 진행한다. [계약과 완료 조건](docs/AI_TRADING_SYSTEM_REDESIGN.md#29-shadow를-공통-판단의-검증-실행으로-통일).

- 기존 CandidateMonitor의 돌파·눌림 공통 실행/저장/복구 연결과 1분봉 계약 보존은 완료했다. 1초 입력을 모든 전략에 강제하지 않는다. 다음 R3 범위는 기존 RAM의 단일 입력 발행·다중 소비와 공통 맥락 연결이며 app 수명/운영 선택·장중 부하는 별도로 검증한다. [완료/남은 경계](docs/AI_TRADING_SYSTEM_REDESIGN.md#30-전략-입력-해상도와-기존-shadow-실행부-재사용).
- 기존 native 관측 page 1회의 RAM 다중 전달·독립 cursor·실패/복구/drain 연결은 완료했다. 다음은 collector의 공통 RAM 입력 발행과 app의 공통 worker 수명 연결이며, native read 제거/운영 활성화는 아직 완료되지 않았다. [납품과 남은 연결](docs/AI_TRADING_SYSTEM_REDESIGN.md#31-공통-관측-페이지의-ram-다중-전달).
- 같은 native 페이지의 frame/H1·H2 계산 재사용과 기존 Collector/Hub의 canonical RAM→공통 입력/명시 앱 수명을 연결했다. 분봉 변환 8→4회·동일 H1/H2 각각 2→1회를 확인했고 원본 집계/저장·COMMIT은 유지한다. 후속 RAM 연결의 source/보존/gap/drop·단일 owner/실패/취소/종료는 관련 183건으로 검증했다. R3 다음은 기존 TOP20/theme owner의 context 자동 갱신·운영 선택과 선별 기록/표시·재시작 근거다. 기본 binding은 비어 있으며 장중/PG/NAS 수락과 저장 포함 지연 개선은 미확인이다. [현재 범위/남은 연결](docs/AI_TRADING_SYSTEM_REDESIGN.md#34-기존-collector-ram-관측과-앱의-공통-수명-연결).

- 단계 시작 때 입력·출력·수정 경계·완료 조건·필요 검증을 고정한다. 이후 실제 실패 또는 주문/자료 보존·인증/수명의 직접 영향이 확인된 경우에만 관련 검증을 추가한다.
- 단계 종료는 기능 납품과 해당 경계의 검증으로 보고한다. 테스트 개수나 진단 도구의 증가를 제품 기능 진척으로 대신하지 않는다.
- 기능 완성과 실제 계좌 운용 승격을 분리한다. 작은 자료와 주문 없는 Shadow로 기능을 개발할 수 있지만, 부적격 후보를 실제 모의 주문에 사용하지 않는다.
- 전체 뉴스 소진, 양수 수익률, 모든 행동/venue 지원, 전체 앱 Replay, 전체 성능 감사는 첫 연결의 선행 조건이 아니다. 커밋/배포의 기존 필수 검사·운영 절차는 유지한다.
- 전체 코드 이름 변경, 전달용 Manager/Service 추가, 새 UI/DB/메시지 큐 구축부터 시작하지 않는다. 실제 책임·상태·수명에 필요한 변경만 한다.
- 시장·테마·뉴스는 독립적으로 결과를 갱신하고 타점 엔진은 유효한 최신 결과를 읽는다. 매 주문마다 분석·LLM을 직렬 대기하지 않는다.
- DB 부하 녹화/Replay는 기존 독립 진단 도구다. 첫 구조 변경을 위해 새 녹화 시스템을 만들지 않는다. 전략 연구와 작은 고정 입력의 계산 검사를 구분한다.

- TOP20·테마 owner의 공통 context 자동 갱신을 연결하고 관련 255건·ACK 유실/시간·삭제/수명 경계를 검증했다. 이후 선별 진단 기록/조회도 연결했으며 실제 화면·재시작 승인 근거와 명시 운영 선택은 후속이다. [owner 갱신 범위/제약](docs/AI_TRADING_SYSTEM_REDESIGN.md#35-top20테마-owner의-공통-context-자동-갱신).

- RAM 결과의 선별 저장/인증 조회를 기본 OFF의 공통 Runtime에 연결했다. 같은 범주 판단의 cut/ref 변화는 저장하지 않고 최신 선택 결과 한 행·공통 context 한 번·독립 정책을 보존한다. 다음은 실제 화면·명시 운영 선택/정책·재시작 승인 근거와 PG/NAS·장중/성능/전체 gate다. [현재 범위](docs/AI_TRADING_SYSTEM_REDESIGN.md#36-ram-shadow-결과의-선별-저장과-인증-조회).

- 기존 Recorder 수락 입력 ID를 활성 epoch의 같은 RAM snapshot에 고정해 공통 출처/선별 기록의 조회 힌트로 전달했다. 신규 5건 포함 관련 108건·실제 격리 capture reader/선택 행의 ID 일치와 추가 payload/DB/COMMIT 없음을 확인했다. 현재 0g 제외·전체 원본 출처/적격성/운영 승인과 실제 앱/PG/NAS/장중 수락은 다음 경계다. [45단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#45-ram-출처의-기존-녹화-입력-조회-힌트).
- 같은 RAM 입력의 승인/연속 구간·보존/gap/전달 누락 근거를 회차 한 벌로 묶어 실제 결과/기존 선별 한 행에 연결했다. 신규 6건 포함 관련 142건·동일 ID paired 전달 비용과 원본 writer/COMMIT·H1/H2 공유를 확인했다. 초봉 raw 이력·capture/native/운영 승인은 추가하지 않았으며 다음은 실제 capture lineage·확정 적격성/운영 승인과 앱/PG/NAS/장중 수락이다. [44단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#44-ram-판단의-공통-출처와-연속성-근거-연결).
- 저장된 가설의 원본 대조 버튼/범위·일치/미확인/비승인·조회 비용을 기존 관찰 화면/worker에 연결했다. 기본 polling 대조 0회·단발 요청/편집/live 보존·stale/승인 범위 검증·종료를 신규 5건 포함 관련 50건·격리 native 응답 픽셀 QA에서 확인했다. 다음은 실제 원본 연속성/이전 revision·lineage/적격성·운영 승인 근거와 앱/PG/NAS/장중 수락·지연 실측이다. [43단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#43-관찰-화면의-수동-원본-대조와-결과-표시).
- 기존 연구 가설을 native 테마/초봉과 명시 대조하는 인증 API/client를 연결했다. 조회 두 번·SQLite COMMIT 0회, live/tick source read 0회·다중 Shadow의 원본 저장/계산 공유를 유지한다. 관련 최초 109/110 통과·Windows staging 오류 한 건만 재검증 통과. 현재 값 일치는 연속성/이전 revision/lineage·운영 승인과 구분하며 다음은 대조 결과 화면 표시와 실제 적격성/PG/NAS/장중 수락이다. [42단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#42-연구-가설의-native-원본을-명시-대조).
- 저장된 연구 가설/정책의 관찰 화면·명시 설정을 기존 PC worker/API/Runtime에 연결했다. 서버 파일 경로 없이 불변 snapshot을 전달하고 한 가설/정책·null 해제/ACK 유실/재시작·새 H2 상태를 신규 10건 포함 관련 116건·offscreen QA에서 확인했다. tick 추가 가설 read/수집/주문은 없다. 다음은 실제 앱/NAS 통합·원본 확정 provenance/적격성·운영 승인과 PG/장중 수락이다. [계약/검증](docs/AI_TRADING_SYSTEM_REDESIGN.md#41-저장된-연구-가설정책의-관찰-화면과-명시-설정-연결).
- 기존 연구 DB의 불변 진입 가설을 명시 ID/정책으로 read-only 복구해 기존 CLI/공통 RAM Shadow에 전달했다. 정확한 SELECT 1회/COMMIT 0회·틱 추가 조회 없음, 신규 10건 포함 관련 103건을 확인했다. 저장 연구 근거와 운영 승인/시장 원본 확정을 구분한다. 다음은 실제 화면/운영 가설·정책 선택, 원본 확정 승인·적격성 근거와 PG/NAS/장중 수락이다. [계약/사용](docs/AI_TRADING_SYSTEM_REDESIGN.md#40-기존-연구-db의-불변-진입-가설을-명시-복구해-shadow에-전달).
- 기존 0g 상한가 참조 원본을 Collector RAM→공통 입력→H2에 연결했다. 신규 9건 포함 관련 152건·build/health·release 별도 12건을 확인했고 원본 저장/COMMIT·8개 테마 H1 공유를 유지한다. 실제 장중 수락/지연 개선은 확인되지 않았다. 다음은 확정 입력/entry thesis 승인·보호 정책 선택/재시작 근거와 실제 PG/NAS/장시간/장중 부하다. [계약/근거](docs/AI_TRADING_SYSTEM_REDESIGN.md#39-기존-0g-상한가-참조정보의-ram-shadow-전달).
- 서로 다른 테마의 동일 H1 부분을 회차 안에서 공유하고 검증된 불변 시장 frame만 재사용했다. 8개 테마 H1 8→1/H2 8개 독립·원본 writer/COMMIT 유지·기록 첫 진단 1회 추가, 신규 12건 포함 관련 184건을 확인했다. 로컬 합성 큰 입력 준비→결과 중앙값 677.394→88.058ms이며 장중 수락은 아니다. 다음은 확정 승인/보호 정책/재시작 근거·상한가 원본과 실제 PG/NAS/장시간/장중 부하다. [현재 계약/측정](docs/AI_TRADING_SYSTEM_REDESIGN.md#38-서로-다른-테마의-h1-공유와-불변-시장-입력-재사용).
- 시장·테마 관찰 탭/명시 테마·거래소·기록 선택, 별도 config revision/재시작·취소/종료를 연결했다. 기본 OFF·주문 없는 공통 Runtime을 사용하며 15건 신규 포함 관련 166건·offscreen QA를 확인했다. 다음은 여러 테마의 H1 부분 공유/종단 부하·확정 승인/보호 정책/상한가 원본 연결·PG/NAS/장중 수락이다. [현재 범위](docs/AI_TRADING_SYSTEM_REDESIGN.md#37-시장테마-관찰-화면과-명시-실행-선택).

## 자료·뉴스·LLM의 독립 진행

기존 주도후보·NAS 관측을 재사용하고 과거 시세·뉴스를 출처·시간·해상도·모집단이 고정된 사례로 연결한다. 수집과 첫 정책 연결은 독립 진행할 수 있다. [과거 자료 확보 계획](docs/HISTORICAL_BACKFILL_PLAN.md)의 대신증권 1분/5분, 네이버 사이트 검색/원문, 재개·coverage 계약을 유지한다. 전체 제공 경계·완전성을 완료로 가정하지 않는다.

과거 사후 후보/거래대금 Top40과 실시간 조회 TOP20은 별도 집합이다. 1초 수신 집계·최근 1분·장기 5분·일봉을 구분하고 과거 5분봉을 1초/1분 입력으로 늘리지 않는다. 기존 원본 수집 DB와 과거 뉴스 archive·현재 뉴스 DB를 보존한다.

LLM은 사실·사건·테마 해석과 설명을 담당하고 기존 테마 프로필·확정 별칭·사용자 병합/분리 결정을 존중한다. 낮은 확신은 제안으로 남긴다. 로컬 LLM 목표와 외부 자동 대체 금지를 유지하되 실제 모델/작업자 배치는 별도 확인한다. 장중 주문/보호가 LLM 응답을 동기 대기하지 않게 한다.

기존 사례/검토/블라인드 입력/방법별 평가·사건 분할 기능을 활용한다. 사람 검토·분할·모델 실행이 아직 없으면 학습 완료로 표시하지 않는다. RAG 갱신·모델 훈련·정책 승격은 각각 다른 단계다.

## 거래 정책과 운용 범위

R4 이후 실행 연결에는 매수 주문, 일반 익절 매도, 손절·위험 청산의 세 목적별 미체결 관리를 포함한다. 매수는 기회가 나빠지면 포기할 수 있고 익절은 추세/위험 여유 안에서 기다릴 수 있다. 보호 경로는 강제 위험 한도/기한을 우선하며 PC LLM·다음 1분봉·Shadow 완료를 동기 대기하지 않는다. 모든 재주문은 기존 취소 확정·체결/잔고 대사·멱등/owner/control gate 뒤에 수행한다. UNKNOWN·CANCEL_PENDING은 중복 제출 금지이며 가격/주문 유형 변경으로 체결을 보장하지 않는다. 세 정책은 같은 실행 owner를 공유하고 자동 선택/재주문 연결은 아직 미구현이다. 즉시 다음 R4 자료 reader 작업은 유지하되 실행 승격 전에 이 계약을 수락한다. [목적별 선택·불변 조건·완료 기준](docs/AI_TRADING_SYSTEM_REDESIGN.md#76-미체결-주문-관리-정책).

타점·보유·청산·재진입 조건은 version별 비교 대상으로 유지한다. 개발 손실 결과를 근거로 기준을 낮춰 후보를 승격하지 않고, 미정 적격 기준 4개와 final 기간을 임의 동결하지 않는다. 후보 조건·학습/평가/실제 실행 계약은 [전진 평가 계약](docs/FORWARD_EVALUATION_CONTRACT.md)을 따른다.

일반 거래일의 사용자 시간 기준은 정규 연속장 09:00~15:20 미만, NXT 프리 08:00~08:50 미만, NXT 애프터 15:40~20:00 미만, KRX 애프터 16:00~20:00 미만이다. 실제 달력·효력일·venue/broker 지원과 동적 상태를 우선한다. 첫 자동 실행은 기존 단일 포지션·KRX 정규장 지정가 지원 범위이며 프리/애프터·실전·레버리지의 자동 활성화를 뜻하지 않는다. 상세 마감/미체결 계약은 전체 설계 9절에 둔다.

## 독립 운영 잔여와 기록

기존 NAS 검사·녹화 예약·실제 PostgreSQL·COMMIT 지연·장시간 수락은 [OPEN_ITEMS](docs/OPEN_ITEMS.md)와 [CURRENT_STATUS](docs/CURRENT_STATUS.md)에 보존한다. 직접 변경되는 실행 경계의 검증은 필요하지만 모든 진단 과제를 새 기능 개발의 직렬 선행 조건으로 묶지 않는다.

RAM 최신값·유한 집계, 확정 봉/의미 사건 batch, 주문/체결의 내구성 저장을 구분한다. 상위 계산은 같은 좁은 계약을 사용하고 NAS 모드의 PostgreSQL 또는 독립 PC worker의 SQLite를 하나의 주 backend로 선택한다. 현재 독립 PC 자동운용의 계좌 identity 잔여는 구현 완료로 표시하지 않는다.

이전 로드맵 전문과 단계별 연구/검사 결과는 [2026-10-13 설계 변경 전 기록](docs/archive/2026-10-13/README.md)에 원문 보존했다. 그 기록의 과거 “다음 작업”은 현재 순서가 아니다. [원래 AI Trading 통합 명세](docs/archive/2026-09-22/references/00_전체통합_AI_Trading_Intelligence.md)는 제품 방향 참고로 유지한다.

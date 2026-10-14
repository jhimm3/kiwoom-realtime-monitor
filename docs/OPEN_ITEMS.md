# 남은 작업 요약

## 2026-10-15 Windows 반복 검사 실패의 원인 확인

검사 fixture의 Documents directory rename 실패는 실제 호출 구간에 겹친 `ChatGPT.exe`의 하위 디렉터리 읽기 핸들과 Windows rename 제약으로 확인했다. 기존 Runner의 fixture만 checkout 밖 AppData worker Temp로 격리하고, 실패를 재시도/통과로 바꾸지 않았다. 관련71건·실제 Runner의 source-runtime200건과 누적 archive 계보4건 통과. 마지막 Windows 전수는 한 번 실행해 5,102건/305 worker 전부 통과했고 동일 LF 지문·required local source gate를 확인했다. 다른 운영 자료의 접근 거부·R4/실제 모의계좌·외부 CI/NAS/운용 승인 잔여와 구분한다. [현재 근거](CURRENT_STATUS.md#2026-10-15-반복-검사-실패-원인과-windows-임시-자료-격리).

## 2026-10-14 실제 연구 입력 접근 복구 확인과 v2 재진입 비교 지원

사용자가 접근 복구를 알려준 뒤 두 실제 연구 패키지의 manifest와 TRAIN/VALIDATION4분할을 기존 load_research_input/partition/readiness 함수로 읽기 전용 검증했다. 접근 문제는 해결 확인했으며 로컬 디렉터리·manifest는 정상이다. package hash, 원본 observations/ordinal/revision hash·partition ID/경계를 확인했고 OOS 입력을 열지 않았다.

| 패키지 | TRAIN 관측 | VALIDATION 관측 | 분봉 준비 상태 |
|---|---:|---:|---|
| monthly-2024-09-to-2026-01-clockfix-v2 | 181,708 | 63,218 | READY |
| multi-period-2024-2026-stocks-pairs-v4 | 14,976 | 16,541 | READY |

총276,443관측(분봉276,423·역사 후보 모집단20)이다. 실제 데이터는1분봉/후보 복원이며 당시 TOP20 관측이 아니고 theme snapshot은 모두0이다. 기존 historical-context 변환으로 첫 원래 bar cut을 확인했으며 D2/D5 context가 없는 상태를 그대로 보존했다. 파일 무결성/분봉 READY를 당시 공통 맥락·통계적 독립성·현재 R4 적용 승인으로 바꾸지 않는다. [실제 입력 감사](../data/diagnostics/20261014-real-r4-input-audit/real-input-audit.json).

별도의 통제 연구 시험으로 기존 native v2 reader의 REENTER 지원을 연결했다. 기존 registered family/Paper/ResearchRepository가 실제 계산한 선행 매수·전량 SELL 체결과 pending0·빈 포지션·cooldown 상태를 요구한다. 기존 v1 target 가치 adapter의 prefix 읽기 코드를 같은 파일의 공통 helper로 추출해 재사용하고, 원래 빈 계좌 {}와 원래 goal/TTL을 정확히 재생한다. flat HOLD는 검증된 v2 안에서 같은 현금/수량 가치 adapter를 쓰되 HOLD에 target declaration/주문을 만들지 않는다. 짝 경제 상태의 position=None을 보존하며 기존 평균/표준오차·비용 계산·native writer/게시 경로를 사용한다.

이것은 명시 재진입 가설의 과거 Paper 비교다. 통제 시험의 쿨다운 중 명시 목표 적용을 실제 native 재진입/쿨다운 해제 승인으로 해석하지 않는다. 원래 v1 DTO/공개 비교·held HOLD/ADD/REDUCE 의미와 현재 R4 차단을 유지한다. Native RISK_FILL 선행 청산, partial/pending 초기 상태 및 EXIT의 v2 확장은 승인하지 않았다. 새 engine/collector/table/writer/task/실시간 조회가 없다. 기존 과거 목표 TTL 뒤 완결한2case/4run의 REENTER19주 대 flat HOLD 비교는 COHORT_COMPARABLE·평균 순가치-29원이며 현재는 EV_UNAVAILABLE/usablefalse/orderfalse다. [통제 원본 게시](../data/diagnostics/20261014-real-r4-input-audit/controlled-reentry-publication.json).

선행 완전 SELL 증명이 빠지면 HOLD/REENTER4branch 모두 제외하고 전체 cohort를 미확인으로 만든다. 공개 원본 준비는 기존4borrow/SELECT16/COMMIT0·원본 SQLite bytes 동일이며 최초 게시 재생은 DB0이다. 원래 outcome/정책/가용 시각/기한을 덮어쓰지 않는다. 실제276,443행을 이 통제 fixture의 모델 학습 자료였다고 주장하지 않는다. explicit KRX scientific source hash는 코드 변경으로 바뀌며 기존 패키지를 자동 재표지하지 않는다. legacy None hash와 supplied v1 전체 예제/decision ID는 보존한다.

신규3 포함 관련 고유44건 통과, compile·소비 감사delta0·기존 required Windows route/core fingerprint 유지. [검증 기록](../data/diagnostics/20261014-real-r4-input-audit/verification.json). 다음은 현재 R4용 공통 맥락·상위 입력 독립 계보와 native 보호/미체결 후속 정책 근거를 실제 생산자에서 연결하는 작업이다. 없는 과거 테마/초 단위 입력이나 운영 승인을 합성하지 않는다. 사용자③ 전체·④ 실제 모의계좌00/04/REST·잔고·청산/운용 승인 및 현재 R4 적용은 미완료다. 원래00 상위 Position·Risk·Capacity/Liquidity 자동 비중을 유지한다. main/HEAD232737d·누적 로컬 미커밋·미배포·build2026.10.14-position-reentry-study-v2. 전체 회귀/HTTP 전체 묶음·CI·원격main·실제PG/NAS/장중 수락 미실행, catalog 무관 archive5개 보류 유지.

이하 설명은 해당 시점의 기록이다.

## 2026-10-14 자동 목표 HOLD/EXIT 변환 오류 수정과 현재 R4 근거 확인

현재 근거 확인 중, 근거 부족과 별개인 자동 목표 변환 오류를 재현해 수정했다. 계좌 운용 한도가 기존 보유 평가액과 같아 HOLD가 되거나 0으로 줄어 EXIT가 되는 경우, 상위 비중 계산은 정상이나 RAM 변환이 ADD/REDUCE/REENTER 전용 automatic sizing DTO에 HOLD/EXIT를 넣어 ValueError를 발생시켰다. 같은2/8 Shadow 입력에서 HOLD/EXIT4개 실패를 재현했고 REDUCE를 대조군으로 유지했다.

기존 market_theme_shadow_inputs의 하위 요청 생성 한 곳에서 ADD/REDUCE/REENTER는 기존 sizing 요청을 그대로 사용하고, HOLD/EXIT는 기존 명시 수량 계약(actual/0)으로 전달한다. 상위 자동 목표/정책/ref·원래 receipt TTL은 유지하며 조회 만료 때 결과/하위 목표를 함께 제거한다. 사용자 종목별 수동 금액 정책을 추가한 것이 아니다. DTO 허용 범위·과거 study 계약·native Risk/보호/주문 경로를 넓히지 않는다. 새 함수/owner/collector/table/writer/task나 호출 단계를 추가하지 않았고 기능 이해 파일/호출 깊이는 기존과 같다.

신규2 포함 관련 고유52건 통과: 2/8 HOLD·EXIT·REDUCE의 목표/수량/공통상위ref·매수0·계좌borrow1·hot SQLite접근0·만료 제거, v2 HOLD/EXIT에 ADD 연구 scope를 잘못 승인하지 않는 경계, 기존 ADD/비중/수량/게시·기록/취소 drain을 검증했다. 신선한 native 위험 우선, 만료 위험으로 새 보호를 만들지 않는 경계, 8개 Shadow 계산 정지 중 실제 기존 보호 처리(가짜 전송)3건도 통과했다. 현재 R4는 EV_UNAVAILABLE/result=None/orderfalse를 유지한다. 소비 감사delta0·compile·기존 supplied v1 전체 예제/decision ID 동일·자체 required Windows CI/core fingerprint 유지. [검증 기록](../data/diagnostics/20261014-allocation-hold-exit/verification.json).

기존 controlled native 연구 publication을 같은 decoder로 재검증했다: 원래4run·2context의 COHORT_COMPARABLE과 원래 availability/expiry를 보존한다. D2/D5 맥락에는 초 단위 leadership·breakout·시총·상위 EV/출구/stress 현재 지원 근거가 없고, source spec에는 native 위험 한도/미체결 후속 정책이 없다. 현재 맥락/독립 source와 native 보호·후속 모델의 적합성을 승인할 수 없다. [원본 감사](../data/diagnostics/20261014-allocation-hold-exit/source-audit.json).

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

[검증 기록](../data/diagnostics/20261014-order-off-closeout/verification.json) · [원장 흐름/목표/최종 계좌](../data/diagnostics/20261014-order-off-closeout/offline-ledger-flow.json).

다음 남은 항목은 현재 R4에 필요한 exact 과거 맥락·상위 입력 독립성/source custody·native 보호/후속 모델 지원 근거 확보와 실제 모의계좌00/04/REST·부분체결/잔고/청산 검증 및 별도 운용 승인이다. 없는 과거 사실이나 미지원 모델을 승인 자료로 합성하지 않는다. 원래00 Position·Risk·Capacity/Liquidity 상위 자동 비중 정책을 유지한다. 무관 기능/새 ML 프레임워크/새 proof 단계는 자동 추가하지 않는다. main/HEAD232737d·누적 로컬 미커밋·미배포·build2026.10.14-allocation-input-bundle-v1 유지. 전체 회귀·CI·원격main·실제PG·NAS·장중 수락 미실행, catalog 무관 archive5개 보류 유지.

이하 설명은 해당 시점의 기록이다.

## 2026-10-14 마무리 묶음 2 — 지원·차단/보호 경계 검증, 현재 모델 적용은 보류

고정한 둘째 묶음의 **로컬 지원·차단/보호 경계 검증**을 마쳤다. 현재 R4 모델 적용을 승인한 것은 아니다. 새 생산 코드·엔진·수집·조회·기록·주문 경로 없이 기존 시험 모듈에 동시 실행 회귀 한 건을 추가했다. 8개 Shadow의 실제 공통 allocation 계산을 멈춘 동안 별도 native 계좌 owner의 실제 Collector→Supervisor→Runner 손절 cue와 첫 보호 처리가 진행됐다. 두 시험 owner의 계좌 cut·모델 승인을 혼용하지 않는다.

| 검증 대상 | 현재 판정 |
|---|---|
| 사전 동결한 native Paper HOLD·ADD/REDUCE 짝 | 과거 비교 COHORT_COMPARABLE. 음의 수수료 포함 결과도 유지. 관측된 범위의 경험 통계이며 현재 예측/자동 비중 전체 성능 승인이 아님. |
| 현재 계좌의 R4 | 원래 exact context·상위 입력 독립성/source custody·native 보호/후속 모델 지원 미증명. EV_UNAVAILABLE/result=None/orderfalse 유지. 누락·변형·범위 충돌·만료와 후행 표본 선택 차단 검증. |
| native Risk·손절·미체결 | Shadow/EV/다음 분봉 완료 전에 보호 cue·wake·첫 가짜 전송 확인. 취소 확정/체결 대사 전 재주문과 UNKNOWN 중복 제출 금지. 매수·익절·보호 정책 구분. |

신규1 포함 관련 고유 **90건** 통과. 새 동시 시험은 R4를 풀기 전에 첫 보호 전송이 1회 진행되는지, 최초 stop quote/요청·수량을 보존하는지, 제출 상태를 청산 완료로 오인하지 않는지 확인했다. 즉시 cue의 connection/SELECT/COMMIT/전송0, 기존 status100회·기한/중복cue100회의 DB0 및 반복10회 COMMIT0 회귀도 통과했다. 이는 불필요한 전체 preflight 반복을 차단하는 경계 검증이며 Runner의 기존 체결/운영 control 주기 조회가 사라졌다는 뜻은 아니다. 소비 감사delta0·compile·기존 required Windows CI route/core fingerprint 유지. [검증 산출물](../data/diagnostics/20261014-model-protection-closeout/verification.json).

다음은 셋째 **주문 OFF 전체 흐름 통합**이다. 현재 R4 미지원은 명시 차단 시나리오로 포함하며, 이를 지원된 현재 R4의 종목 선정/비중/성과 통합 성공으로 표시하지 않는다. 현재 모델 적용 근거 확보와 실제 모의계좌00/04/REST·잔고·청산/운용 승인은 보류 항목으로 유지한다. 원래00의 Position·Risk·Capacity/Liquidity 상위 자동 비중 정책과 수량 하위 기능 구분은 그대로다. bundle 2의 경계 검증 완료를 사용자 순서③ 전체 구현 완료로 표시하지 않는다.

이번 묶음은 시험/문서만 변경했다. main/HEAD232737d·누적 로컬 미커밋, build2026.10.14-allocation-input-bundle-v1 유지, 미배포. 전체 회귀·CI·원격main·실제계좌·실제PG·NAS·장중 수락 미실행. catalog의 기존 무관 archive5개 보류 유지. 기본 샌드박스 비동기 시작은 중단했고 사용자 세션의 완료된 격리 SQLite/가짜 계좌 시험만 통과로 집계했다.

이하 설명은 해당 시점의 기록이다.

## 2026-10-14 종료 범위 고정 — 남은 필수는 적용/보호와 주문 OFF 통합

사용자가 종료 시점을 요청한 뒤 현재 사용자 순서③의 개발 마무리를 다음 세 묶음으로 고정했다. 입력 연결·원본 근거 참조의 구현과 모델 적합성/독립성 검증, 실제 운용 승인을 구분한다. 범위 밖의 새로운 기능·광범위 source 수집/ML/통계 보정 요구를 이 묶음에 자동 추가하지 않는다. 현재 쓰는 paired empirical EV는 학습 예측기가 아니며, 실제 학습/선택/검증 모델을 사용하는 경우의 계보와 기존 후보 모델 지원 검증을 생략한다는 뜻은 아니다.

| 묶음 | 개발 상태 | 종료 기준 |
|---|---|---|
| 1. 현재 입력·원본 근거 연결 | **구현/관련 검증 완료** | 기존 현재가/시총·대금/변동성·시장/테마/종목·계좌Risk와 EV/출구/study 원래 참조를 동일 cut에 연결. 누락/불일치/만료는 현재 입력으로 인정하지 않고 공통 소비·기록 횟수를 보존. |
| 2. 현재 적용·보호 정책 검증 | **지원·차단/보호 경계 검증 완료 · 현재 모델 적용 보류** | 명시된 지원 범위의 판단 결과, 범위 밖/충돌/만료의 EV_UNAVAILABLE, 원래 sampling·학습/선택/검증 계보 및 native Risk/보호/미체결 후속 우선 정책을 함께 검증. 입력 연결 완료를 모델 승인으로 사용하지 않음. |
| 3. 주문 OFF 전체 흐름 통합 | **다음: 미지원 모델 차단 포함** | 종목 선정→상위 자동 비중→하위 수량→주문 계획→미체결/청산까지 공통 경로를 한 통합 시나리오로 대사하고 입력 부족/실패/종료를 검증. |

실제 모의계좌 활성/전송·00/04/REST/잔고/청산, NAS/장중 수락과 별도 운용 승인은 위 로컬 개발 완료와 구분한다. unsupported 입력/모델이 실제 운용 가능한 자료로 준비됐다고 주장하지 않는다. 전체 종료 시간이나 다음 사용자 메시지 수를 보장하지 않는다.

기존 current RAM allocation의 이미 계산된 facts/account·Risk refs와 typed EV/출구 모델 및 재검증한 native source/study refs를 dynamic-allocation-input-bundle/v1으로 한 번 동결했다. v2만 선택 field를 추가하고 v1 출력/수치/ID는 보존한다. R4 각 goal은 같은 bundle_ref를 공유한다. source refs는 원래 것 그대로이며 원래 account/quote/model availability와 expiry 최솟값을 갱신하지 않는다. 지원되지 않는 source/누락/차단된 cut은 INPUTS_UNAVAILABLE, 갖춰진 현재 cut/원래 보고 근거는 INPUTS_BOUND다. 두 경우 모두 model_application_verifiedFalse·orderfalse이며 R4 현재 적용은 계속 EV_UNAVAILABLE다.

새 engine/collector/DB read/table/writer/task나 모델 재생·시장 계산은 없다. 현재 EV 추정 방법을 paired_empirical_incremental_portfolio_delta/v1/trained_predictor_usedFalse로 기록하고 학습 모델 계보를 만들어내지 않는다. Native raw/custody와 독립성·조건별 모델 지원은 다음 묶음의 미검증으로 유지한다. raw facts/EV outcome arrays를 재복사하지 않고 현재 facts의 hash와 원래 모델/입력 refs hash/count를 사용한다.256KiB budget 초과 시 작은 unavailable 사유만 제공하고 이미 계산한 비중/목표/quality/lease를 바꾸지 않는다. 조회 만료 때 현재 bundle과 goal을 함께 제거한다.

신규6 포함 관련17건·기존 v1 전체 예제/decision ID 동일·소비 감사delta0·compile 및 자체 CI 등록/core fingerprint를 확인했다. 격리2/8 Shadow의 Collector/계좌/입력bundle/상위 계산 각1·현재시장1·DB/COMMIT/주문0, status/중복100회 추가 read/build/replay/evaluation0이다. bundle2236bytes·평균 생성0.1377/0.1795ms, cold frame10.647/9.301ms이며 운영 지연 수치가 아니다. 명시 선택 기록은 기존 upsert 한 번/COMMIT1·다른 입력변화 없는 반복0이며 새 저장 경로가 없다. 기록은 selected_latest_transition의 한 latest 진단이고 전체 역사 capture가 아니다. 전체회귀/새bundle HTTP/실계좌·PG/NAS·장중/CI/원격main은 미검증이다. catalog 기존 archive5 보류 유지. build2026.10.14-allocation-input-bundle-v1·main/HEAD232737d 로컬 미커밋·미배포다. [86단계](AI_TRADING_SYSTEM_REDESIGN.md#86-개발-마무리-범위-고정과-공통-입력원본-근거-묶음)

이하 단계 설명은 해당 시점의 기록이다.

## 2026-10-14 현재 범위 — D2/D5 과거 관측 연결·조건 지원과 정책 증명 후속

기존 FrozenResearchDataset의 D2 순위 관측과 D5 테마 스냅샷을 과거 case의 원래 decision cut에서 동결해 native Paper 연구 명세와 기존 POSITION_STUDY_CASE proof에 선택 연결했다. 현재 테마를 과거에 붙이지 않으며 future availability/effective time·다른 dataset/symbol/case·실제 입력과 다른 내용·변형된 theme hash를 거절한다. 기존 연구 실행·목표·체결·값 adapter·writer를 재사용하고 새 collector/TR/DB query/table/writer/task/Manager는 없다. 기본 None 연구 명세/7-key proof는 유지한다.

같은 case의 HOLD/행동 branch는 같은 historical context를 가져야 완결 짝으로 인정한다. 최초 v2 게시에서 기존 H1 market 판단과 D5 membership 변환을 사용하고 동일 context_ref는 한 번만 재생한다. 이후 공통 RAM goal에는 작은 historical_context_summary(원래 refs/관측 테마/시장 quality/맥락·case coverage)를 공유한다. 기존 기본 v1 및 context 없는 v2 packet/goal 모양은 유지한다. 시장/테마 공통 모듈 두 개를 explicit KRX scientific implementation hash의 dependency에 추가했으며 legacy None hash는 유지한다. 기존 패키지를 새 지문으로 자동 재표지하지 않는다.

이 연결은 기록 당시 관측된 순위·테마의 제한된 provenance다. 초 단위 테마 leadership·성숙 breakout·시총·상위 EV/출구/stress 조건, 학습·선택·검증 계보와 native 보호/실행 후속 정책의 모델 지원은 아직 없다. 현재 조건의 exact statistical support를 승인하지 않는다. current_context_support_verifiedFalse·R4 EV_UNAVAILABLE/result=None/order OFF이며 사용자 순서③ 전체 판단은 미완료다. 원본 DB/capture의 독립 인증과 historical theme/revision 완전성도 not_verified다.

context는 최신100 ranking/100 theme snapshot 및128KiB, 기존 case proof256KiB/전체packet4MiB/event10000/outcome2000/bar2000 예산 안에 둔다. row count로 잘린 이력은 명시 flag를 남기고 bytes 초과는 실행 전 실패한다. 잘린 테마의 이전 seed나 빈 자료를 복원·추정하지 않는다. 새 현재 quote/status 조회가 과거 시각·quality/TTL을 갱신하지 않는다. 보호 Risk/Runner 경로는 이 준비/게시/Shadow 평가를 기다리지 않는다.

신규8 포함 관련 고유53건·소비 감사delta0·기존 supplied v1 전체 예제/decision ID 동일을 확인했다. 과거 case2개의 동결은 DB/판단 계산0·1.439ms, 네 run 준비 전후 borrow4/SELECT16/COMMIT0·원본 bytes 동일이다. 명시 연구 packet163433→262097bytes로 커지고 source read21.380→26.437ms다. 기존 별도 case event 행/COMMIT을 추가하지 않고 기존 명세/metadata payload만 커진다. 격리2/8 최초 게시의 과거시장 계산은 각각2회(case별1회), hot Collector/계좌 각1·현재 시장1·상위1·DB/COMMIT/주문0, status/중복100회 추가read/replay/evaluation0이다. 전체회귀·새 context proof의 HTTP/실제계좌 활성·00/04/REST/잔고/청산·PG/NAS/장중/CI/원격main은 검증하지 않았다. 기존 catalog의 무관 archive 미등록5개는 보류다. build2026.10.14-position-historical-context-v1·main/HEAD232737d 로컬 미커밋·미배포다.

다음은 이번에 없는 종목/타점·EV/Capacity/stress 조건과 모델 학습·선택·검증 계보를 기존 생산자에서 연결하고, 실제 현재 맥락에 대한 모델 지원 및 native 보호/실행 후속 정책의 적합성을 증명하는 것이다. 공통 시장/테마 관측이 붙었다는 이유만으로③ 전체 판단이나 모의계좌 운용 승인을 완료로 표시하지 않는다. 원래00 Position·Risk·Capacity/Liquidity의 상위 자동 비중과②/④ 수락·운용 승인 분리는 유지한다. [85단계](AI_TRADING_SYSTEM_REDESIGN.md#85-기존-연구-입력의-과거-순위테마-맥락-연결)

이하 단계 설명은 해당 시점의 기록이다.

## 2026-10-14 현재 범위 — EV 출처 재생 완료·맥락과 모델 계보 후속

기존 readonly EV 준비에서 원래 연구 명세/report·이벤트를 같은 transaction/borrow로 함께 전달하는 선택 include_source_records=True 경로를 추가했다. 기본 False 결과/원래 outcome·비교·정책 ID와 availability/TTL은 동일하다. 기존 CLI 입력의 명시 옵션으로 native_source_publication을 준비하고 기존 v2 allocation 게시/공통 RAM 경로에서만 원래 결과와 재생 대조한다. 새 DB 읽기·writer/table/collector/계좌/TR/task/Manager 없음.

같은 기존 native source 검증기를 EV와 R4 study가 재사용한다. run/input manifest hash·개발 role/범위·보고된 final 제외·이벤트 content/time/정수·원래 endpoints·outcome/source receipt 전체를 확인한다. 4MiB·전체 event10000/outcome2000/bar2000 예산은 유지한다. v2에서 원본 proof가 명시됐으면 null/변형/다른 receipt는 거절하고 기존 활성 슬롯/세대는 유지한다. proof가 없는 기존 v1/v2는 지원되지 않은 계보를 합성하지 않는다.

재검증한 보고 출처끼리 run·입력 revision hash·warmup을 포함한 선언 기간·outcome 공유를 확인해 현재 goal에 선택 allocation_lineage를 연결한다. 격리 시험의 서로 다른 run/input hash도 선언 기간16쌍이 겹쳐 REPORTED_OVERLAP으로 표시했다. 기간 끝 경계는 보수적으로 포함한다. REPORTED_DISJOINT도 원시 revision 목록·상위 모델 학습/선택/검증 계보·독립 source custody가 없으므로 independence_verified=False다. 보고된 중복 진단을 실제 통계적 독립성 승인으로 바꾸지 않는다. 과거 exact 시장/테마 context와 native 보호·실행 후속 정책의 모델 지원은 아직 미증명이므로 R4 EV_UNAVAILABLE/result=None/order OFF, 사용자 순서③ 전체 판단은 미완료다.

신규7 포함 관련 고유38건·소비 감사delta0·기존 supplied v1 전체 예제/decision ID 동일을 확인했다. 명시 네 run 준비는 변경 전후 SELECT16/COMMIT0·borrow4·원본DB bytes 동일이다. 격리2/8 Shadow는 Collector/계좌 각1·상위1·DB/COMMIT/주문0, status/중복100회 추가 read/replay/evaluation0이다. 재생은 최초 게시 때만 수행하고 실시간에서는 동결한 작은 계보 진단만 복사한다. 실제 CLI opt-in/default와 기존 v1 게시/해제/drain을 실행했다. 새로운 proof의 인증 HTTP→실제 broker 활성/전송·00/04/REST·잔고/청산·PG/NAS/장중/CI/원격main은 검증하지 않았다. 전수 회귀 없음, catalog의 기존 과거자료 미등록5개는 보류다. build2026.10.14-position-ev-lineage-v1·main/HEAD232737d 로컬 미커밋·미배포다.

다음은 기존 source가 관측 당시 공통 시장/테마/종목 context와 상위 EV 모델의 학습·선택·검증 계보를 실제로 생산할 수 있는 경로를 확인·연결하고 native 보호/실행 정책 지원을 증명하는 것이다. 현재 숫자나 서로 다른 run 이름만으로 과거 상태/독립성을 인정하지 않는다. 원래00의 상위 Position·Risk·Capacity/Liquidity 자동 비중 정책과② 모의계좌 수락/④ 전체 통합·운용 승인 분리는 유지한다. [84단계](AI_TRADING_SYSTEM_REDESIGN.md#84-상위-ev-원본-출처-재생과-r4-연구-중복-확인)

이하 단계 설명은 해당 시점의 기록이다.

## 2026-10-14 현재 범위 — owner 사실 관측 완료·연구 지원 증명 후속

기존 Runner가 실제 반영 체결로 유지하는 가중 진입가·잔여 수량·최초 보유 시각, 등록 family/parameters/session/implementation identity, 원래 보호 요청·session clock·native Risk 한도를 기존 계좌 RAM 관측 한 번에 연결했다. 별도 원장 조회·TR·DB/COMMIT·저장/계산 엔진이나 task를 추가하지 않았다. 기존 기본 PositionAccountObservation 모양은 보존하며 현재 등록 owner의 정합성이 확인된 경우에만 선택 subtype의 native-position-runner-state/v1을 제공한다. 주문 권한은 false다.

82단계의 '원가·기간/전략 상태가 없다'는 표현을 바로잡는다. Risk의 owned_entry_prices와 Runner의 StrategyState/opened_at는 이미 존재했으며 당시 현재 비교 경로에 전달되지 않았던 것이다. 이번 연결은 그 사실의 관측을 해결한다. 보호·실행 후속 정책이 연구 모델과 동등하다는 증명, 과거 공통 시장 맥락과 상위 EV 독립 lineage는 아직 생산되지 않았다. 등록 family/parameters/session/implementation hash가 맞는지 확인하더라도 그 추가 정책 지원을 인정하지 않는다. 현재 R4는 계속 EV_UNAVAILABLE/result=None/order OFF이고 사용자 순서 ③ 전체 판단은 미완료다.

원장 high-watermark·Risk 원가/수량·계좌 cut·run/revision/path와 owner registry/계좌 epoch를 확인한다. 체결 반영 중·오류/동시 writer·복사 중 상태/보호/session 변경·복구/종료 시에는 추가 사실을 버린다. writer metadata lock은 I/O 밖의 짧은 경계에만 두고 getter는 기다리지 않는 lock 검사로 종료한다. 기존 native Risk 보호 callback은 이 lock이나 Shadow/LLM/다음 봉을 기다리지 않는다. 최초 보호 시각·최초 quote·예산·자식 주문 ID를 다시 만들지 않는다. 관측 조회와 미변경 fill read로 state availability를 갱신하지 않는다.

신규 11건 포함 관련 고유 98건·소비 감사 delta0·기존 supplied v1 전체 예제/decision ID 동일을 확인했다. 격리 native getter100회는 원장/DB/COMMIT/주문0, 평균0.1127ms이며 원래 상태/availability가 같다. 2/8 Shadow 모두 Collector/계좌 RAM 각1·native getter1·상위 계산1·원장/DB/COMMIT/주문0, status100회 추가 계좌 조회/계산0이다. 동일 시장 입력 cache 재사용 frame3.737/3.814ms이고 cold baseline11.244/9.499ms와 cache 조건이 다르므로 실운영 지연 개선으로 일반화하지 않는다. 실제 app 계좌 활성 HTTP→broker 전송, 실제 00/04/REST·부분체결/잔고/청산, PG/NAS/장중·CI/원격 main은 검증하지 않았다. 전체 회귀 없음, catalog의 기존 과거자료 미등록5개는 별도 보류다. build2026.10.14-native-position-state-v1·main/HEAD232737d 로컬 미커밋·미배포다.

다음은 기존 연구 source에서 과거 공통 context/상위 EV 입력·검증 lineage와 native 보호/실행 후속 지원을 실제 증명할 수 있는 경로를 확인·연결하는 것이다. 없는 과거 사실을 현재 숫자로 채우지 않는다. 원래 00의 상위 Position·Risk·Capacity/Liquidity 동적 자동 비중과 ② 모의계좌 수락/④ 전체 통합·운용 승인 분리는 유지한다. [83단계](AI_TRADING_SYSTEM_REDESIGN.md#83-native-runner-체결보호-상태의-동일-계좌-ram-관측)

이하 단계 설명은 해당 시점의 기록이다.

## 2026-10-14 현재 범위 — v2 게시 완료·native context/보호 증명 후속

81단계 native cohort의 원래 run 명세/report·이벤트를 동일 readonly transaction에서 함께 준비하고, 기존 PC client·인증 allocation-models endpoint·selection lock/세대 fence/owned drain/한 RAM 슬롯에 명시 v2로 게시한다. 최초 게시에서 같은 native source/case/cohort 검증기로 재생하고 원본 ID/availability/TTL을 보존한다. 기존 v1 EV/출구 receipt는 그대로 두며 혼합/부분 종목·변형 source/통계·다른 revision은 원자적으로 거절한다. 4MiB/전체 10,000 event·2,000 outcome/bar 예산은 늘리지 않았다.

공통 RAM에서 기존 상위 자동 비중→하위 목표와 같은 계좌 관측/ACK Risk를 공유해 현재 goal/Risk binding을 추가했다. 계좌·run/revision/path·원래 신선도/기한과 정확한 수량·가격/비용·sizing 의미를 확인하고 native Risk가 우선한다. 만료한 하위 목표/현재 결합은 조회에서 제거하며 모델 원래 기한은 갱신하지 않는다. 현재 관측에는 원가·보유기간/등록 전략 보호 상태가 없고 81단계 source에는 과거 공통 context·상위 EV 독립 lineage 증명이 없으므로 EV_UNAVAILABLE/result=None/order OFF다. v2 게시 완료를 현재 R4 사용/③ 전체 완료로 보고하지 않는다.

신규 8건 포함 관련 고유 63건·소비 감사 delta 0·기존 supplied v1 예제/ID 동일을 확인했다. 명시 PC 준비 네 run SELECT 16/COMMIT 0·원본 DB 동일로 81단계와 SQL 횟수가 같다. 격리 2/8 Shadow의 Collector/계좌 RAM 각 1회·상위 계산 1회·DB/COMMIT/주문 0, 100회 중복/status의 추가 조회/계산 0이다. 인증 HTTP/ASGI v2 최초/중복/revision/해제와 기존 v1 게시·취소 drain/보호 우선 회귀를 실행했다. 전체 HTTP 72묶음/전수 회귀·실제 계좌 활성/전송·PG/NAS/장중·CI/원격 main 검증은 하지 않았다. 신규 CI 등록과 core fingerprint를 유지했고 catalog의 기존 과거자료 미등록 5개는 별도 보류다. build 2026.10.14-position-study-publication-v2·main/HEAD 232737d 로컬 미커밋·미배포다.

다음은 기존 native owner/연구 source에 필요한 보호·후속 상태 및 과거 공통 시장 맥락·상위 EV 입력/검증 lineage 증명을 연결하는 것이다. 없는 상태를 합성하거나 현재 숫자만 과거에 붙이지 않는다. 원래 00의 상위 Position·Risk·Capacity/Liquidity 자동 비중 정책과 ② actual 00/04/REST/잔고·청산 수락, ④ 전체 통합/운용 승인 분리는 유지한다. [82단계](AI_TRADING_SYSTEM_REDESIGN.md#82-v2-연구-근거의-공통-ram-게시와-현재-목표risk-결합)

이하 단계 설명은 해당 시점의 기록이다.

## 2026-10-14 현재 범위 — v2 연구 짝 완료·현재 scope/게시 후속

79단계의 두 번째 구현으로 사전 case/HOLD·행동 목록을 각 native run의 시작 명세에 고정하고, 실제 Paper의 HOLD+ADD/REDUCE 결과를 기존 readonly source guard·가치/수수료 adapter와 공통 짝 산술로 비교한다. 원래 outcome·정책/실행 ID와 당시 목표 TTL은 보존한다. 짝의 실제 포트폴리오/전략 상태·source bar/목표가 같아야 하며, case 창은 MARK 끝점을 공유하지 않는다. 선언한 case가 누락·미완료·미지원이면 성공 표본만으로 평균을 만들지 않고 전체 cohort EV를 미확인으로 반환한다.

지원된 완결 모집단의 연구 상태는 COHORT_COMPARABLE다. 이는 역사적 목표 규칙의 조건부 경험 통계이며 상위 자동 비중 정책 전체의 성과나 현재 타점 지원 증명은 아니다. exact context·상위 EV/검증 lineage 독립성·현재 계좌/Risk binding이 아직 없으므로 current_goal_status=EV_UNAVAILABLE/usable_for_current_goal=false/order_authorized=false다. v2 EXIT/flat REENTER와 partial/보호 개입 창의 확대, 명시 v2 인증 게시/공통 RAM 연결은 후속이다. 최종 비중 정책은 기존 00의 Position·Risk·Capacity/Liquidity를 따르는 상위 자동 결정이며 연구 목표금액은 하위 sizing 기능이다.

신규 12건 포함 관련 고유 81건·소비 감사 delta 0·기존 supplied v1 output/ID 동일을 확인했다. 명시 두 case/네 run 준비 SELECT 16·BEGIN/ROLLBACK 각 4·COMMIT 0·원본 SQLite bytes 동일이고 실시간 반복 호출을 추가하지 않았다. 신규 모듈은 기존 연구 profile/required Windows CI에 등록했으나 전체 catalog의 기존 과거자료 미등록 5개는 별도 보류로 남는다. 전수 회귀·HTTP 묶음·실제 주문/활성·PG/NAS/장중 수락·④ 통합/운용 승인·CI/원격 main 확인은 실행하지 않았다. build 2026.10.14-position-study-cohort-v2·main/HEAD 232737d 로컬 미커밋·미배포다. [81단계](AI_TRADING_SYSTEM_REDESIGN.md#81-v2-native-짝-모집단-동결과-공통-통계)

이하 단계 설명은 해당 시점의 기록이다.

## 2026-10-14 현재 범위 — v2 native case 증명 완료·짝 모집단 후속

79단계 인계의 첫 구현을 완료했다. 기존 position_target.py의 typed v2 study/case 계약은 sizing 의미·등록 family/설정·실행/cost/session·실제 scientific hash를 안정적인 study ref에 묶고, 원래 순간별 요청/TTL은 별도로 유지한다. 기존 research_run_identity/execute_research의 명시 opt-in에서 같은 run spec에 study/case를 동결하고, 같은 Paper/원장 writer가 실제 source bar·포트폴리오/전략 상태·목표 계산의 POSITION_STUDY_CASE 증명 한 건을 기록한다. native readonly reader는 기존 source guard와 가치/비용 adapter를 재사용해 원래 outcome/정책 ID를 보존한다.

실제 native 연구 run에서 30초 목표의 180초 완결 결과를 원래 TTL 연장 없이 읽고, 독립 HOLD에는 목표 주문이 없는 것을 검증했다. 이것은 NATIVE_CASE_PROOF이며 짝 모집단/현재 exact context 지원 증명이 아직 없으므로 R4는 EV_UNAVAILABLE/주문 OFF다. 현재 공통 RAM에 v2를 게시하지 않았다. 다음은 자체 독립 native cohort/같은 짝 통계, 이어 기존 인증 게시/공통 RAM 연결이다.

관련 고유 149건(신규 10건)·기존 supplied v1 예제/ID 동일·소비 감사 delta 0을 확인했다. 기존 연구 101건/118.869초와 수량/R4/비중 호환 46건/0.110초, 최종 신규 경계 검증을 실행했고 전체 회귀/기존 HTTP 묶음은 반복하지 않았다. 단일 명시 native 준비는 run borrow 1·SELECT 4·BEGIN/ROLLBACK 각 1·COMMIT 0·원본 SQLite 동일이다. 이 비용은 실시간 반복 판단 비용이 아니다. source_durability=not_verified/order_authorized=false·실제 계좌/PG/NAS/장중 수락·④ 전체 모의 통합/운용 승인은 별도다. build 2026.10.14-position-study-native-v2·main/HEAD 232737d 로컬 미커밋·미배포다. [80단계](AI_TRADING_SYSTEM_REDESIGN.md#80-v2-연구-규칙순간별-case-계약과-native-증명-첫-구현)

이하 단계 설명은 해당 시점의 기록이다.

## 2026-10-14 현재 범위 — R4 v2 계약 확정·구현 대기

실시간 목표 TTL과 완결 연구 기간을 분리하는 [79단계 설계](AI_TRADING_SYSTEM_REDESIGN.md#79-동적-목표의-연구-정책과-순간별-판단-유효기간-분리)를 Astra High에서 확정했다. 연구 규칙의 안정적 의미 ID, 원래 순간별 판단 증명, 원래 완료/가용 시각에 묶인 근거 ID를 함께 검증한다. 과거/현재 TTL을 늘리거나 v1 표본의 정책 ID를 바꾸지 않는다. 첫 v2의 목표·크기·가격/비용·후속 정책/시장 상태 지원은 명시한 exact 범위다.

다음 구현은 Sol High의 v2 식별·시간/native proof → 독립 native cohort/공통 통계 → 기존 인증 게시/한 RAM 슬롯 연결이다. 기존 case 중 미완료/미지원 항목을 빼고 성공 표본만 사용하지 않으며, 필요한 시장/보호 상태나 독립성이 입증되지 않으면 EV_UNAVAILABLE다. 현재 런타임은 78단계 그대로이고 새 R4 연결은 아직 구현하지 않았다. actual 00/04/REST·부분체결·잔고/청산 수락, 통계 일반화/운용 승인, ④ 전체 모의 통합 및 기존 catalog 5개 보류는 유지한다.

## 2026-10-14 현재 범위 — 공통 게시 완료·동적 목표의 R4 모집단 후속

기존 PC readonly 연구 준비의 배분 EV·전체 출구 Capacity/스트레스 결과를 인증된 POST /api/v1/research/ram-shadow/allocation-models로 기존 공통 MarketThemeShadowRuntime의 RAM 슬롯 하나에 게시한다. 기존 선택 revision/계좌·run/binding/path·정책 내용/종목·가격/현재·검증 수량·동결 cost/horizon·원래 availability/expiry를 결합한다. native 짝 EV 통계와 같은 Paper 출구 시나리오를 최초 게시에서 재생 검증하고, 동일 패킷 재전송/status는 재생·계좌 조회·깨우기·TTL 갱신이 없다. 선택 교체/해제·재시작은 슬롯을 복구하지 않으며 이전 worker가 새 세대 결과를 덮어쓰지 못한다.

기존 Collector/공통 시장·테마/ACK된 계좌 Risk 사실이 유효하면 상위 자동 비중→기존 하위 목표 수량을 공통 frame에서 한 번 계산한다. 가격·계좌/Risk binding·source TTL이 바뀌거나 근거가 부족하면 BLOCKED/미확인으로 남기며 만료한 목표 결과는 조회에 노출하지 않는다. 새 동적 목표 정책의 R4 행동 비교는 자체 완결 모집단이 필요하므로 EV_UNAVAILABLE다. 기존 정책의 EV를 새 정책으로 재표지하지 않는다. PC_READONLY_REPLAY_REPORTED·source_durability=not_verified·order_authorized=false이며 DB 원본/시장 확률·운영 Risk/주문 승인을 뜻하지 않는다.

관련 고유 96건(신규 8건) 통과 후 최종 영향 15건 및 입력/종료 경계 2건을 확인했다. 격리 2/8 Shadow에서 게시 후 Collector/계좌 RAM 각 1회·상위 계산 1회, DB 연결/COMMIT/주문/구독 0이며 중복/status 100회 추가 계산/조회 0이다. 최초 시장 계산은 기존 cache에 공유하고 게시만으로 재계산하지 않는다. 원본 자료 준비의 SELECT 16(EV)/4(출구)·COMMIT 0은 앞 단계의 별도 비용이다. 전체 회귀/기존 HTTP 72시나리오는 반복하지 않았다. 소비 감사 delta 0·기존 supplied v1 예제/ID 동일, 관련 없는 과거자료 catalog 5개 보류 유지다. build 2026.10.14-allocation-model-publication-v1·main/HEAD 232737d 로컬 미커밋·미배포·실제 계좌 활성/주문 없음.

다음 개발은 생성된 동적 목표 정책에 일치하는 독립 native 모집단과 R4 행동 비교의 주문 없는 통합이다. actual 모의 00/04/REST·부분체결/취소·잔고/청산 수락과 ④ 전체 모의 통합·별도 운용 승인은 남는다. ② 운용 수락 대기는 ③ 개발의 선행 조건이 아니다. 아래 단계별 설명은 각 시점의 기록이며 현재 미완료 범위는 이 절을 따른다. [78단계](AI_TRADING_SYSTEM_REDESIGN.md#78-준비된-native-모델의-공통-ram-게시와-상위하위-목표-연결)

## 2026-10-14 현재 범위 — 세 연구 producer 준비·공통 RAM 게시/통합 후속

기존 PaperExecutionEngine의 부분 SELL 원가/잔고/수수료 경로로 전체 잠재 보유의 출구 Capacity·스트레스 연구 시나리오를 구현했다. 참여율·volume haircut·가격 충격·추가 슬리피지·체결 불가 구간/기간은 전부 명시 정책이다. 기존 guarded native readonly reader에서 시장 bar proof 창을 한 번 읽어 재사용하며 WAIT/REPRICE 성과를 청산 근거로 재표지하지 않는다. 모든 서로 다른 시나리오에서 전체 수량이 완료된 경우만 검증 수량까지 근거를 만들고, 부분/불가/누락/미래/비용 만료는 미확인이다.

상위 비중은 실제 보유+pending BUY 전체·가격·비용·출구 horizon·스트레스 값·원래 TTL을 묶으며 pending SELL로 위험 여유를 만들지 않는다. 기존 native EV와 두 출구 근거를 함께 소비하는 순수 주문 없는 계산도 검증했다. 이는 명시한 역사/충격 가정의 연구 결과이며 미래 유동성/확률·실제 청산 보장·Risk/주문 승인이 아니다. 세 producer의 공통 RAM 게시/실시간 계좌 상위→하위 통합은 다음 단계이며 현재 native RAM result=None/BLOCKED는 유지한다. actual 00/04/REST/잔고/청산 수락과 운용 승인은 별도다.

관련 고유 125건(신규 16건) 및 기존 supplied v1 봉인 예제/ID·소비 감사 delta 0 pass. 명시 준비 SELECT 4·COMMIT 0/1 run·원본 DB 동일, 준비된 native EV/출구·스트레스의 pure preview 101회 DB/주문 0이다. 전체 회귀/기존 HTTP 72시나리오는 반복하지 않았다. 관련 없는 과거자료 catalog 5개 보류 유지. build 2026.10.14-full-exit-stress-v1·main/HEAD 232737d 로컬 미커밋·미배포·실제 계좌 활성/주문 없음. [77단계](AI_TRADING_SYSTEM_REDESIGN.md#77-전체-보유-출구-capacity와-명시-스트레스-연구-근거)

## 2026-10-14 현재 범위 — EV 준비 완료·출구/스트레스 및 RAM 게시 후속

기존 native 연구 원장 reader/PaperExecutionEngine/R4 paired 비교를 재사용해 ADD·REENTER 대 독립 HOLD의 배분 EV를 명시 readonly 준비하는 경로와 기존 preview CLI를 연결했다. 현재가·보유/목표 수량·비용·horizon·완결 TRAIN/VALIDATION 짝·원래 availability/expiry를 묶는다. 순 차이는 추가 매수 대금 기준 Fraction 평균 내림/표준오차 올림이며 calibrated 확률/신뢰구간이 아니다. 상위 비중 계산은 검증된 정확한 BUY 수량만 허용하고 더 큰 비중·부분 현금/위험 수량에 외삽하지 않는다. 불리한 ADD EV를 기존 보유의 EXIT 근거로 바꾸지 않는다.

이 producer는 실시간 루프 밖의 PC readonly 연구 준비다. 현재 공통 RAM에는 게시하지 않았고 전체 출구 Capacity·스트레스 모델도 미공급이므로 native 비중 결과는 계속 None/BLOCKED다. 순수 preview의 supplied Capacity/스트레스를 native 검증으로 보고하지 않는다. 다음은 기존 공통 실행 모델에 전체 청산/스트레스 연구 시나리오를 명시해 크기·시간·유동성·비용 범위를 검증하고, 세 근거의 RAM 게시와 계좌 상위→하위 통합을 연결하는 것이다. 실제 모의 00/04/REST 수락·운용 승인은 별도다.

관련 고유 118건(신규 12건)·최종 통계 영향 49건·기존 RAM/API 14건 및 봉인된 supplied v1 예제/decision ID 대조·소비 감사 delta 0 pass다. readonly 부하는 76단계 및 data/diagnostics/20261014-allocation-native-ev에 기록한다. SELECT 16·COMMIT 0/4 run을 명시 준비에서만 수행했고 pure preview 101회 DB/주문 0·원본 DB 동일이다. 전수 회귀·실제 주문·계좌 활성·배포·commit/push 없음. build 2026.10.14-allocation-native-ev-v1·main/HEAD 232737d 로컬 미커밋이다. [76단계](AI_TRADING_SYSTEM_REDESIGN.md#76-배분-ev의-native-연구-공급과-검증-크기-제한)

## 2026-10-14 현재 범위 — 배분 EV·전체 출구/스트레스 producer 남음

2026-10-14 동적 비중의 실제 사실 입력을 기존 공통 Shadow runtime에 연결했다. 명시 position_allocation 선택에서 승인 KRX RAM 현재가/0B 시총·기존 창 대금/관측 종가 RMS·공통 TOP20/테마/종목 맥락과 ACK된 native 계좌/Risk를 한 번 빌린다. 기존 R4 비교와 같은 계좌 read를 공유하고 변환은 같은 owned drain에서 수행해 native 손절을 기다리게 하지 않는다. 조회/재시작으로 원래 source TTL을 갱신하지 않는다.

현재 배분용 독립 EV·전체 출구 Capacity·스트레스 모델 producer는 없으므로 result=None/BLOCKED다. 미체결 EV/leadership 점수·관측 대금을 필요한 근거로 재표지하지 않으며 목표 0을 합성하거나 새 주문을 승인하지 않는다. stage74의 native 전체 목표 계산이 완료된 것으로 보고하지 않는다. source_durability=not_verified/order_authorized=false 유지다.

기존 119건/최종 신규 14건=고유 133건·기존 HTTP 72시나리오/실제 lifespan·health/build·소비 감사 delta 0 pass. 2/8 Shadow 모두 Collector RAM 1·계좌 RAM 1·시장 계산 1·입력 변환 1, DB/COMMIT/추가 주문·구독 0; 조회 100회 추가 계산 0이다. 기존 전체 회귀는 반복하지 않았으며 과거자료 catalog 5개 보류 유지다. 다음은 세 모델의 크기/시점/비용/horizon 결합과 계좌 전체 상위→하위/R4 주문 없는 통합이다. 실제 모의 수락/운용 승인은 별도다. build dynamic-allocation-ram-v1·HEAD 232737d 미커밋, 이번 작업에서 배포/계좌 활성/실제 주문은 하지 않았다. [75단계](AI_TRADING_SYSTEM_REDESIGN.md#75-동적-비중의-공통-ram-사실과-native-계좌-risk-연결)

## 2026-10-14 현재 범위 — ③ 입력 연결·② 운영 수락 별도 보류

2026-10-14 실제 모의 수락 준비를 인증 GET/선택 NAS 릴리즈 지문으로 확인했다. NAS recorder-shared-10m-v1은 관련 5파일이 개발 소스와 다르고 mock 계좌는 MANUAL·automatic control/Runner 없음·계좌별 주문 OFF·native 원장 0건이다. 실제 주문/취소·활성/설정·배포 변경은 하지 않았다. ② actual 00/04/REST·부분체결/잔고/청산 수락은 남긴다.

사용자 지시대로 운영 수락 대기와 개발을 분리해 ③ 동적 비중의 첫 주문 없는 계산을 구현했다. 기존 position_target.py에서 계좌 운용 한도/현재 현금·보유·미체결을 바탕으로 시장·테마·종목 강도/순 EV·불확실성·시총·거래대금·출구 수용량·변동성·스트레스 위험으로 자동 목표 비중/금액을 결정하고 기존 하위 수량/비용 정책과 preview CLI를 재사용한다. 미체결 매도에 현금/위험 여유를 주지 않고 겹치는 테마·현재/제안 포트폴리오를 모두 검사한다. 수동 종목 투자금 입력·임의 운용 계수/한도·신용 활성 없음. supplied 실험용 정책으로 native 입력/운영 승인은 아니다.

관련 고유 103건(신규 21건)·기존 HTTP 72시나리오/실제 lifespan·health/build·CLI 동일 결과·소비 감사 delta 0 pass. 200종목 controlled 계산 33.337ms·DB/COMMIT/주문 0, source TTL 갱신 없음. 전체 회귀/직전 보호 묶음은 반복하지 않았다. 다음은 실제 공통 RAM의 시장/테마·EV·Capacity·native Risk 입력 연결과 R4 계좌 전체 목표/행동 비교의 주문 없는 통합이다. ② 수락 준비 대기는 ③ 개발의 선행 조건이 아니다. build dynamic-allocation-shadow-v1·HEAD 232737d 미커밋·미배포다. [74단계](AI_TRADING_SYSTEM_REDESIGN.md#74-실제-모의계좌-수락-준비-확인과-상위-동적-비중-계산-시작)

## 2026-10-14 즉시 native 종목 손절 — 구현 완료·계좌 수락 남음

2026-10-14 기존 동결 unfilled execution 정책을 선택한 자동 모의 Runner에 즉시 종목 손절 producer를 연결했다. 상세 FILL 기반 owned_entry_prices·기존 정수 이동평균과 승인 후보 stop_loss_bps를 사용하고, Collector 승인 KRX RAM→기존 Supervisor→Runner의 가격 비교/한 번 wake로 봉·Shadow·LLM/EV를 기다리지 않는다. 최초 손절 가격·시점은 고정하고 현재 계좌 ACK/상세 체결·owner/control/native QUEUED에서 원가·수량·candidate/hash/정규 한도를 재검사한다. 일반 cue는 review 전용, UNKNOWN/취소 미확정의 중복 제출 금지는 유지한다. 오래된 계좌는 기존 recovery queue로 대사하고 가격 만료는 BLOCKED/운영자 주의다.

관련 280건(신규 18건)/101.808초와 기존 HTTP 72시나리오·실제 ASGI lifespan/health/build pass. 정상/손절 중복 1,000틱 각각 SELECT·COMMIT·전송 0, 최초 RAM cue 0.395ms이다. 현재 계좌 ACK 후 첫 보호 승인/원장/가짜 전송 전체는 SELECT 80·COMMIT 18·13.587ms이며 보호 대기 10회 SELECT 20·COMMIT 0이다. 실제 REST/시장/NAS 지연은 별도다. 전체 회귀는 반복하지 않았고 소비 감사 delta 0, 과거자료 catalog 5개 보류 유지다.

실제 계좌 활성/전송·00/04/REST·PG/NAS/장중 수락과 운용 승인은 남는다. 다음은 실제 모의 부분체결/미체결/잔고 수락, 이어 원래 Position·Risk·Capacity/Liquidity의 상위 동적 비중·전체 통합 순서다. 운용 승인 대기는 동적 비중 개발을 막지 않는다. build 2026.10.14-immediate-native-stop-v1·main/HEAD 232737d 미커밋·미배포다. [73단계](AI_TRADING_SYSTEM_REDESIGN.md#73-승인된-collector-ram-가격의-즉시-종목-손절과-native-보호-exit)

**2026-10-14 사용자 순서 ② 원장 결과 게시(로컬):** PC CentralContentClient의 명시 prepare_native_unfilled_population으로 기존 readonly 연구 원장/공통 재생을 한 번 검증하고, 같은 클라이언트의 publish_native_unfilled_population이 준비된 패킷을 기존 인증 POST 경계로 보낸다. 기존 mock 라우트·Supervisor·Runner의 계좌 lock/owned drain/단일 RAM 접수를 재사용한다. 서버에 DB 경로를 보내거나 계좌를 활성화하지 않는다. 원래 native 결과의 최대 available_at과 내용 hash를 고정하며 HTTP 재전송/계좌 ACK/재시작으로 갱신하지 않는다. 계좌·profile/spec/run/control·원주문/known child·동결 비용/horizon/표본·같은 native 정책/모델·완결 짝/가치·1MiB/200창/10,000 event budget을 검사한다. 새 패킷에는 현재 native RUNNING control 한 번을 확인하고 같은 패킷의 inert 중복은 새 조회/깨우기/슬롯 교체가 없다. 등록 전환과 RAM 교체는 기존 input lock 안에서 검사한다. 관련 153건/145.093초 통과 후 최종 변경 영향 신규 15건/28.666초를 확인했다(고유 154건). 기존 start/stop/resume/status HTTP 72시나리오를 지원되는 httpx ASGITransport와 실제 lifespan에서 원래 baseline과 대조했고 health/build도 확인했다. 전수 회귀/기존 통과 묶음을 반복하지 않았고 httpx2를 설치하거나 import를 위장하지 않았다. [72단계](AI_TRADING_SYSTEM_REDESIGN.md#72-pc-native-원장-결과의-인증-게시와-기존-runner-ram-접수). PC의 readonly/replay 검증 보고이며 서버의 시장 원본/호가/체결 확률/취소 지연 승인이 아니다. PC_READONLY_REPLAY_REPORTED·source_durability=not_verified·order_authorized=false를 유지한다. native v1은 ACCEPTED 분봉 자료만 지원하고 현재 PARTIALLY_FILLED/CANCELLED/UNKNOWN에서는 EV 미확인(native_unfilled_model_state_unsupported)이며 이전 cohort를 재표지하지 않는다. 실제 00/04/REST·PG/NAS/장중 수락·부분체결/CANCELLED 모집단/취소 지연 보정은 남는다. 종목별 즉시 native 손절 producer도 남는다. 다음 우선 보완은 종목별 즉시 native 손절 producer이며 기존 Risk→Runner 보호 경로를 재사용한다. 이후 실제 모의 00/04/REST 수락을 별도 다루고 ③ 원래 Position·Risk·Capacity/Liquidity 상위 동적 비중 → ④ 모의 통합/별도 운용 승인 순서를 유지한다. 운용 승인을 동적 비중 개발의 선행 조건으로 두지 않는다. 프로그램 경로이며 UI/자동 예약/게시 스케줄러는 별도 미구현이고 새 scope로 확장하지 않았다. 미커밋·미배포/미활성화·기존 catalog 5개 보류다. 아래 publisher 미연결은 당시 기록이다.

**2026-10-14 사용자 순서 ② native 미체결 연구 자료(로컬):** 기존 PaperExecutionEngine에 명시 unfilled_action_plan의 SUBMIT→WAIT/REPRICE 지정가 연구 분기를 연결하고 같은 연구 원장 writer를 재사용했다. native readonly 개발 구간/최종평가·내용 hash/cut/예산 guard 뒤에 같은 실행기로 가격·현금·예약·비용·취소/재주문 순서를 재생해 UnfilledActionOutcome을 생성한다. 기본 next-open 실행과 기존 HOLD/EXIT/자동 목표 자료는 유지한다. 관련 6모듈 고유 266건의 근거를 대조했다. 초기 제한 실행의 성공 99건 뒤 중단하여 잔여 묶음 120건/135.554초와 미실행 64건/24.719초만 완료하고, 최종 변경 영향 신규 19건/43.142초를 확인했다. 중복은 고유 건수에서 제외하며 전체 회귀는 반복하지 않았다. 초기 신규 fixture는 폐쇄 거래/평일 요건을 충족하도록 수정했고 운영 guard를 완화하지 않았다. [71단계](AI_TRADING_SYSTEM_REDESIGN.md#71-기존-연구-실행기의-지정가-분기와-native-미체결-결과-읽기). 실시간 원장 입력 게시 caller·CANCELLED/부분체결 모집단 생성은 후속이며, 실제 호가 순서·취소 지연·부분체결 확률·시장 원본 lineage는 미검증이다. source_durability=not_verified와 주문 승인 false를 유지한다. 실제 00/04/REST·종목별 즉시 native 손절 producer/수락·PG/NAS/장중 운용 승인도 남는다. **다음 개발은 검증한 원장 결과의 기존 RAM 접수 게시와 미지원 상태 처리다.** 제한된 v1 분봉 모델을 실제 부분체결/호가/취소 지연 수락으로 승격하지 않는다. ③ 상위 동적 비중·④ 모의 전체 통합/별도 운용 승인 순서와 개발/운용 승인 분리를 유지한다. 미커밋·미배포/미활성화이며 기존 catalog 5개 보류도 유지한다. 아래 생성기/reader 미연결은 각 단계 당시 기록이다.

**2026-10-14 사용자 순서 ② 현재 입력 연결(로컬):** WAIT/REPRICE의 별도 자료 계약과 기존 공통 짝 평균/비용/표준오차를 재사용해 명시 bounded RAM 접수·사건별 계산/캐시를 연결했다. 기존 동결 후보 비용 모델과 등록 전략의 실제 bar HOLD/EXIT를 사용하며 보호 요청이 우선한다. 새 주문은 실제 Risk ACK cut에 맞추고 보호 취소는 원래 REST 직전 재검증, 취소 후에는 새 CANCELLED 상태 모집단을 요구한다. 관련 458건의 2건 회귀 수정 후 영향 62건 통과(신규 26·고유 459), source/ASGI/소비 감사 pass. [70단계](AI_TRADING_SYSTEM_REDESIGN.md#70-waitreprice-짝-입력동결-비용익절-판단의-공통-연결). **실제 WAIT/REPRICE 모집단 생성기·native 원장/개발 구간 검증 reader가 없으므로 현재 자료는 명시 공급 실험용/not_verified이며 EV/운용 승격은 미완료다.** 다음은 기존 연구 Runtime/원장 기반 생성·출처 검증이다. 종목별 즉시 손절·실제 모의계좌 00/04/REST·부분체결/잔고/지연·PG/NAS/장중/운용 수락도 남는다. 이후 ③ 원래 동적 비중 → ④ 통합/별도 운용 승인 유지. build `2026.10.14-unfilled-paired-inputs-v1` 미커밋·미배포/미활성. 과거자료 catalog 5개는 별도 보류다.

**2026-10-14 사용자 순서 ② running 소비 연결(로컬):** 최신 typed RAM cut 하나를 같은 Runner가 사건/기한에만 소비하고 같은 native 승인/고정 주문 경로로 제출하도록 구현했다. 가격 시각/건강한 REST revision만으로 전체 gate를 반복하지 않으며 동일 cut·UNKNOWN·STOP/기한/ACK·close/drain을 신규 21건 포함 266건으로 검증했다. 같은 입력 전후 idle SELECT 20/COMMIT 0(10회), 알림 101회/건강한 갱신/기한 RAM 처리 DB 0이다. 실제 제출 비용 SELECT 108–110/COMMIT 14는 남는다. [65단계](AI_TRADING_SYSTEM_REDESIGN.md#65-running-runner의-사건기한-소비와-고정-주문의-동일-native-실행). **production 현재 입력 producer·목적별 자동 취소 호출과 취소 거절 후 bounded 보호 대응은 미연결**이다. Collector 가격·기존 타점/EV·계좌/REST confirmation 공급을 먼저 연결하고 실제 00/04/REST·부분체결/잔고를 확인한다. 이후 ③ 동적 비중·④ 전체 모의 통합/별도 운용 승인 순서를 유지한다. 실제 계좌/PG/NAS/장중·Recorder/Replay 수락, 전체 회귀/CI·commit/push·배포/활성화 없음. catalog 별도 과거자료 5모듈 미등록 유지.

**2026-10-14 사용자 순서 ② 고정 child native 실행(로컬):** 명시 선택한 동결 정책/v3 승인→실제 QUEUED/단발 제출/receipt·원래 root/예산·최신 Risk/부모/계좌 전체 원장 transaction fence를 연결했다. BUY ADD·익절 REDUCE·보호 EXIT, 부분체결 7→5주, ACK 유실/UNKNOWN·재시작/close·metadata 보존을 신규 29건 포함 관련 372건으로 검증했다. [64단계](AI_TRADING_SYSTEM_REDESIGN.md#64-목적별-고정-대체-주문의-native-승인과-제출). **다음은 running Runner의 실제 사건/기한 dispatcher·현재 가격/타점/짝 EV 입력 producer와 목적별 취소/거절 후 보호 대응**이다. 현재 idle 명시 실행이며 실제 계좌 자동매매/배포를 활성화하지 않았다. 실제 00/04/REST·부분체결/잔고/지연·PG/NAS·Recorder capture/Replay·운용 승인 수락은 남는다. 전체 gate는 별도 과거자료 신규 5모듈 catalog 미등록 상태다. 이후 ③ 동적 비중·④ 모의 전체 통합/별도 운용 승인 순서는 유지한다.

**2026-10-14 사용자 순서 ② 취소 실행 기반(로컬):** 기존 owned command/계좌 갱신/취소 원장을 재사용해 exact native 주문·CANCEL_STARTED의 owner/control transaction fence와 ACK 유실/거절/UNKNOWN·재시작의 중복 취소 금지를 구현했다. 신규 18건 포함 관련 212건·source/health/build/consumer pass. native 취소 경계의 SELECT 11→14·COMMIT 3→3이며 실제 REST/Risk 생산 비용은 별도다. [63단계](AI_TRADING_SYSTEM_REDESIGN.md#63-모의계좌-자동-취소의-native-실행-경계). **다음은 목적별 사건/기한 dispatcher와 고정 child의 실행 승인/제출이다.** 기존 OFF preview를 승인으로 바꾸거나 ADD/REDUCE를 ENTER/EXIT로 재표지하지 않는다. 취소 거절 후 보호 재시도/우회는 별도 bounded 정책이 필요하며 현재 이 신규 자동 primitive는 CANCEL_STARTED가 있는 주문을 재전송하지 않는다. 실제 모의계좌 00/04/REST·부분체결/잔고 수락·PG/NAS/장중 검증과 운용 승인은 남는다. 미커밋·미배포/미활성화, 후속 순서는 ③ 동적 비중·④ 전체 모의 통합이다.

**2026-10-14 사용자 지정 순서 ① Risk 자동 요청 연결 완료(로컬):** 기존 ACK된 계좌/Risk→단일 RAM listener→같은 Runner의 보호 요청/wake·checkpoint를 연결했다. 신규 13건 포함 관련 234건(108.710초)·FIFO 실제 손실/UNKNOWN/STOP/ACK·동일 위험 및 mismatch 합치기·listener/초기/닫힘/복구·source/ASGI/소비 감사 pass. callback DB/TR/COMMIT/전송 0·revision 6개에 요청 1개다. [62단계](AI_TRADING_SYSTEM_REDESIGN.md#62-운영-risk의-자동-보호-요청-연결과-개발-순서-개정). **다음은 ② 목적별 모의계좌 자동 취소/재주문·실제 00/04/REST 대사, 이후 ③ 동적 비중, ④ 전체 모의 통합/별도 운용 승인이다.** 현재 요청/검토만 자동이며 실제 보호 주문/지연/계좌·PG/NAS 수락·배포/활성화는 없다. 기존 recorder checkpoint 미지원과 별도 과거자료 테스트 2모듈 catalog 미등록은 남는다. 미커밋·미배포다.

**2026-10-14 OFF 첫 실행 연결 종료·다음 개발 전환:** 신규 통합 4건 포함 선택 38건(10.994초)·기존 실행 SHA/테스트 class AST 일치로 첫 OFF 검토 범위를 닫았다. 격리 idle polling 전후 SELECT 4/COMMIT 0·상태/기한/동일 cue 100회 DB 0·보호 첫 checkpoint 1/반복 10회 0 COMMIT·전체 gate/Risk 조회 0이다. 자동 사건/기한 호출은 현재 미구현이고 수동 전체 검토 반복의 25/27 SELECT·3/2 COMMIT는 남는다. [61단계](AI_TRADING_SYSTEM_REDESIGN.md#61-주문-off-첫-연결의-통합-수락과-db-호출-범위). 다음 개발은 원래 Position·Risk·Capacity/Liquidity의 동적 비중조절 입력/결측 계약→주문 없는 상위 정책이며, 실제 모의계좌 전송/취소/재주문·보호 producer/청산·PG/NAS/장중 운용 수락은 독립 후속으로 유지한다. 순자산을 주문가능 현금으로 대체하거나 수동 종목별 금액을 최종 목표로 삼지 않는다. 미커밋·미배포·주문 OFF다.

**2026-10-14 공유 파일·10MiB/s NAS 배포 완료:** 릴리즈 `2026.10.14-recorder-shared-10m-v1-af0f8072f960415b`가 활성화됐고 실제 health/build/path·녹화 코드/helper hash·idle/trace off를 재확인했다. 공유 형식/독립 호출·PG 복원 검증과 기존 helper 설치 절차를 통과했으며 10MiB/s가 적용됐다. 원본·운영 DB·runtime image·sudoers·revoke 백업·과거 예약은 보존했다. 아래 배포 대기 기록은 이번 결과로 갱신한다. recorder-only 미커밋 후보이며 main/원격 전체 일치는 아니다. **미해결:** 다음 실제 녹화/운영 DB 동시 I/O 영향, 기존 녹화의 거부 4건 범위, 실제 장중 10~20초 COMMIT P1, 전체 앱/Replay 및 2~5배 부하 수락. 새 녹화는 시작/예약하지 않았다. [근거](../artifacts/recorder-shared-deployed-result-20261014.json).

**2026-10-14 공유 파일·10MiB/s 배포 준비 완료:** Linux 등록 복사본의 controlled PG Replay 두 번(19 호출/16 COMMIT/3 예상 rollback)과 기존 실제 계좌 첫 60초 Replay 두 번(각 4호출) 통과·운영 복구 확인. 검증 파일만 배포 기준에 적용한 recorder-only 릴리즈 `2026.10.14-recorder-shared-10m-v1-af0f8072f960415b`의 1,072개 hash/11개 변경·표식만 바뀐 runtime 계약·helper의 공유/v4 두 함수 변경 범위를 확인했다. 필요한 NAS helper 61건·배포/rollback 12건·ASGI 포장 검사 통과. 관리자 배포가 남고 현재 운영 1MiB/s·기존 릴리즈다. 과거 예약 12개 파일은 보존하고 새 녹화는 시작하지 않는다. helper는 기존 설치 절차로 갱신하며 10MiB/s 공개값/준비 상태 실패 시 원래 소스로 복구한다. 운영 DB 동시 I/O·COMMIT P1·전체 앱/Replay 수락은 별도 미해결이다. [준비 근거](../artifacts/recorder-shared-deployment-preparation-20261014.json).

**2026-10-14 목적별 고정 child·보호 요청 검토 완료(로컬, OFF):** 기존 native gate의 원래 BUY 승인/자본·취소/체결·고정 child·익절/보호 구분과 기존 runner의 bounded RAM 요청/idle 깨우기·봉/일반 dispatch 전 차단·checkpoint/새 요청 경쟁·flat native 해제/재시작 재검증을 연결했다. 신규 20건 포함 관련 155건·legacy gate 360회 동일·별도 OS 복구/AST/ASGI/build/소비 감사 pass. BUY SELECT +1·COMMIT 3/2 유지·추가 전송/원장 쓰기 0, idle 요청 DB 0·처리 1.27–1.74ms(격리 6표본)다. [60단계](AI_TRADING_SYSTEM_REDESIGN.md#60-목적별-고정-대체-주문-검토와-보호-요청의-우선-처리). 첫 OFF 연결 종료까지 다음은 통합 수락 한 묶음이다. 실제 보호 producer/주문·이미 진행 중 native 작업 지연·모의계좌 자동 취소/재주문·청산/잔고·PG/NAS/장중 수락, 운영 EV·상위 동적 비중은 별도 후속이다. catalog 미등록·recorder checkpoint 미지원 보류 유지. 미커밋·미배포이며 주문 활성화는 없다.

**2026-10-14 공유 PG fixture 보완·v2 실행 대기:** 기존 gate는 공유 입력 두 건을 만들지 않은 fixture 전제로 실패했고 운영 앱은 복구·정리 완료다. 작은 Shadow 저장 2건을 명시해 19 native 호출/16 COMMIT·각 Shadow 독립 COMMIT·작은/큰 최종 내용과 원장/revision/sequence를 검증하도록 수정했다. 운영 소스는 그대로다. 로컬 관련 3건 및 정리 후 진단 2건 통과, NAS SHA/문법/인자 확인. 기존 Linux 8개는 정확한 hash/통과 로그 ID 확인 후 재사용하며 변경 PG fixture와 별도 DB의 기존 실제 계좌 Replay만 실행한다. 관리자 v2 실행·helper 갱신·10MiB/s 배포/예약 적용·동시 DB 영향과 COMMIT P1·전체 앱 수락은 남는다. [근거](../artifacts/recorder-shared-linux-pg-fixture-fix-20261014.json).

**2026-10-14 Native 미체결 시도/예산·닫힌 owner 검토 완료(로컬):** 기존 checkpoint/native 원장과 실제 owner/control/binding/복구/Risk를 연결해 QUEUED claim·SUBMISSION_STARTED를 분리하고 다회 예산/원래 anchor·기한/결정적 ID를 복구했다. 신규 14건 포함 관련 135건(65.245초)·별도 OS 프로세스의 시도 2/보유 6주·ACK/STOP/UNKNOWN/manifest 손실·느린 ACK 경계·추가 전송/원장 쓰기 0·기존 class AST/ASGI/build/소비 감사 pass. [59단계](AI_TRADING_SYSTEM_REDESIGN.md#59-native-parent-child-시도-투영과-닫힌-owner-gate-검토). 기존 ADD/REDUCE gate 차단은 유지하며 raw EXIT 평가도 고정 child 승인으로 쓰지 않는다. 다음은 목적별 대체 주문의 기존 gate 승인 계약/child 결합·보호 우선 wake-up을 전송 OFF로 검증한다. 실제 자동 취소/재주문·운영 EV·계좌/PG/NAS/지연/체결/잔고와 기존 recorder checkpoint 미지원·catalog 미등록 보류는 남는다. build `2026.10.14-unfilled-native-attempts-v1` 미커밋·미배포. 상위 동적 비중은 원래 Position·Risk·Capacity/Liquidity 후속 정책이다.

**2026-10-14 공유 형식 Linux/PG 검사 준비:** 10MiB/s API/예약/지연 회귀 44건과 배포 기준에 녹화 수정만 적용한 선택 8건이 통과했고(중복 합산 없음), NAS 파일 SHA/기준 소스/설치 인자 확인을 마쳤다. 고정 실행기의 두 새 전용 DB에서 Linux 등록 복사본의 controlled 계좌/lease/VI/대형 입력 Replay와 기존 실제 입력 첫 60초 계좌 Replay를 각각 두 번 실행해야 한다. 관리자 실행은 아직 없고 운영 1MiB/s·release/helper/예약은 유지한다. 통과 후 기존 승인 설치 경로의 helper 갱신·10MiB/s 배포/예약 적용과 동시 DB 영향 확인을 진행한다. 장중 COMMIT P1·전체 앱 수락은 남는다. [준비 근거](../artifacts/recorder-shared-linux-pg-preparation-20261014.json).

**2026-10-14 녹화 저장 속도 선택:** 다음 운영 적용값은 사용자 요청의 **10MiB/s(10,485,760 bytes/s)**다. 공유 형식의 Linux 등록·실제 PostgreSQL/account Replay 및 API·예약 계약 검증 후 배포와 저장 예약에 반영한다. 무제한은 격리 시험에만 사용하며 현재 운영 1MiB/s·릴리즈·예약은 아직 변경하지 않았다. [결정 기록](../artifacts/recorder-operating-rate-choice-20261014.json).

**2026-10-14 미체결 영속 검토/실행 전 검증 완료(로컬):** 기존 idle runner/checkpoint/native parent reader·독립 보호 기한 힌트를 연결했다. 첫 preview 예약·원래 anchor/control/기한/예산·동일 ID/ACK 실패 복구를 보존하고 현재 입력 재검증 전 실행 권한을 주지 않는다. 추가 주문/취소·실행 원장 쓰기 0, 변경 COMMIT 1/동일 owner 반복 0, 신규 13건 포함 관련 100건(42.650초)·별도 OS 프로세스 복구·기존 class AST/ASGI/build/소비 감사 pass. catalog의 기존 과거자료 미등록 보류 유지. [58단계](AI_TRADING_SYSTEM_REDESIGN.md#58-미체결-검토-계획의-기존-checkpoint-복구와-실행-전-검증). 다음은 기존 owner/control fence의 native parent-child 전송 시도 투영·다회 예산/guarded adapter를 주문 OFF로 연결 검증한다. 현재 예약은 실제 retry counter가 아니며 native child가 있거나 공급 횟수가 증가하면 차단한다. 실제 자동 재주문·보호 우선 wake-up·계좌/PG/NAS/지연/체결/잔고 수락과 기존 recorder checkpoint capture 지원은 남는다. build `2026.10.14-unfilled-preflight-v1` 미커밋·미배포, 상위 동적 비중은 원래 Position·Risk·Capacity/Liquidity 후속 범위다.

**2026-10-14 작은 본문 공유 — 구현/133건 통과·NAS 판별 대기:** 1MiB 이하 단일 block의 명시 `blocks/shared-v1`로 같은 16MiB 본문 묶음을 공유하고 구형 v1/큰 입력·checksum/독립 native 호출·메모리/실패 prefix·등록 검증을 유지했다. 같은 실제 입력 PC 저장은 2.837→2.370초(16.47%), 본문 sync 45→9·총 51→15이며 원문/bytes/hash는 동일하다. 큰 개별 파일 sync 최대/p95와 도구 검증 포함 시간은 증가해 전체 개선으로 처리하지 않는다. NAS 두 순서 A/B의 지문/인자/보호 조건만 확인했으며 미실행·미배포다. 이후 Linux 등록·실제 PG/account Replay·설치 helper 갱신·동시 운영 I/O·API/예약/속도 적용 계약을 확인해야 한다. 기존 원본/helper/release/1MiB/s는 유지하고 COMMIT P1은 미해결이다. [근거](../artifacts/recorder-shared-blocks-review-20261014.json).

**2026-10-14 본문 동기화 순서 후보 종료·철회:** NAS 4.89% 개선/5.11% 악화, 평균 9.561→9.566초·fsync 51회 그대로여서 성능 가설을 기각했다. 순서 변경과 전용 2개 검사만 철회하고 기존 두 개선 SHA 4db756a1…를 유지했으며 블록·지연 회귀 29건 통과·NAS 복구/health/idle을 확인했다. 아래 후보 대기 기록은 이 결과로 종료한다. 저장 병목 자체와 COMMIT P1은 미해결이며 다음은 1MiB 이하 block 본문을 기존 bounded bundle에 공유하는 명시적인 writer/reader/등록 계약 수정이다. 아직 공유 형식 구현·NAS 적용은 없다. [근거](../artifacts/recorder-group-sync-nas-result-20261014.json).

**2026-10-14 R4 후 미체결 목적별 정책 첫 연결 완료(로컬):** 기존 lifecycle·고정 CLI와 현재 계좌/목표 계산으로 목적별 WAIT/CANCEL/REPRICE/ABANDON을 연결했다. ACK/UNKNOWN 대체 수량 없음, 취소 경쟁·실제 보유/전체 pending·예약·원래 가격/재시도 한도·독립 관리 EV/비용·보호 우선을 검사했다. 신규 24건 포함 최종 191건 통과·기존 class AST 동일·정책 추가 SQL/submit/cancel 0·CLI/ASGI/감사 delta 0 확인. catalog는 별도 `tests.unit.test_prepared_historical_archive_inputs` 미등록으로 보류 유지. 다음은 기존 owner의 영속 결정/parent-child lineage·재시도 예산·취소/체결 대사·보호 시계 연결의 실행 전 검증, 주문 전송 OFF다. 현재 paired 요약/횟수/Risk 입력을 native 실행 승인으로 쓰지 않는다. build `2026.10.14-unfilled-management-shadow-v1` 미커밋·미배포이며 실제 제한 모의계좌의 재주문/부분체결/보호 wake-up·지연/체결/잔고 수락은 남는다. 상위 동적 비중은 원래 Position·Risk·Capacity/Liquidity의 후속 범위다. [57단계](AI_TRADING_SYSTEM_REDESIGN.md#57-r4-후-미체결-목적별-정책의-주문-없는-첫-검증).

**2026-10-14 본문 묶음 동기화 후보 — NAS 효과 판별 대기:** 구형 blocks/v1·파일별 fsync를 유지하고 지연 저장의 본문 쓰기를 모은 뒤 같은 chunk 공개 전에 모두 동기화하는 최소 수정을 구현했다. 기존/신규 31+39=70건 통과·순서/원문/native Replay/동기화 실패 suffix를 확인했다. 동일 실제 입력 PC 비교는 3.215→3.649초(13.49% 악화)여서 개선 완료로 처리하지 않는다. NAS 두 순서 비교의 SHA/인자/보호 조건만 확인했으며 미실행·미배포다. 대상 NAS에서도 효과가 없으면 이 후보를 철회하고 기존 휴식·묶음 확대 개선을 유지한다. 물리적 본문 파일 공유는 reader/관리자 등록 계약의 별도 보완이 필요하다. [근거](../artifacts/recorder-group-sync-review-20261014.json).

**2026-10-14 저장 속도 범위 비교 완료 — 동시 DB 영향·적용 대기:** 3·5·7·10MiB/s·무제한 각 두 번의 NAS 평균 시간은 31.034/24.047/21.487/19.942/10.363초다. 같은 내용/checksum·51회 fsync·pending/메모리 상한과 운영 복구를 확인했다. 이전 범위 시험 대기 기록을 갱신하며 추가 속도 sweep은 반복하지 않는다. 무제한은 격리 파일 저장 최선 후보지만 동기화에 약 7.333초(71%)가 남아 전체 처리속도는 설정과 비례하지 않는다. 운영 앱을 멈춘 시험이므로 실제 native DB/COMMIT 동시 I/O 영향과 기존 API·예약 계약 보존을 확인해 적용한다. 현재 운영은 기존 1MiB/s·미배포이고 두 코드 수정·후보 배포 및 장중 COMMIT P1·전체 앱 수락은 남는다. [근거](../artifacts/recorder-rate-range-nas-result-20261014.json).

**2026-10-14 R4 native Risk·타점 입력 연결 완료/주문 없는 첫 범위 종료(로컬):** 기존 Risk 저장본/계좌 RAM과 명시 가설의 R3 결과를 공통 비교에 한 번 전달하고 원래 ref/시점/내용/quality·미확인 주문 guard를 적용한다. 보호 EXIT 필요와 실제 승인/주문을 구분한다. 신규 15건 포함 최종 관련 173건 통과·8개 공유/추가 조회 0·이전 고정 결과/ID·CLI/ASGI/Qt·DB 소비 delta 0 확인. catalog의 별도 `tests.unit.test_prepared_historical_archive_inputs` 미등록 보류를 유지한다. 다음은 R4 후 목적별 미체결 정책을 기존 취소/체결 대사 계약에 연결하는 주문 없는 모의 검증이다. 상위 동적 비중은 원래 Position·Risk·Capacity/Liquidity의 후속 정책이며 현재 금액/수량은 하위 기능이다. build `2026.10.14-position-native-inputs-v1` 미커밋·미배포, 운영 Risk/주문 허가·실제 미체결/보호 지연·PG/NAS/장중 수락은 남는다. [56단계](AI_TRADING_SYSTEM_REDESIGN.md#56-r4-기존-타점과-native-risk의-공통-ram-입력-연결).

**2026-10-14 저장 속도 — 3MiB/s 효과 확인·요청한 5/7/10/무제한 비교 대기:** 2→3MiB/s NAS 두 순서에서 22.44%/24.71% 단축, 내용/checksum·51회 fsync·pending/메모리 상한과 운영 복구를 확인했다. 아래 3MiB/s 대기 기록을 갱신한다. 운영은 기존 1MiB/s이며 미배포다. 사용자가 추가한 5·7·10·최대속도 후보를 같은 입력의 3·5·7·10MiB/s·byte pacing 없음 각 두 번/역순 비교로 준비했다. 모든 동기화·입력/메모리 상한·최대 15분·기존 격리/복구와 원본 보존은 유지한다. 코드/hash/인자/다섯 속도/보호 조건만 확인했고 실제 범위 시험은 아직 없다. 후보 선택 뒤 운영 DB 동시 I/O·기존 API/예약 계약 보존/배포와 장중 COMMIT P1을 이어간다. [근거](../artifacts/recorder-rate3-nas-result-20261014.json).

**2026-10-14 R4 행동 권고 첫 연결 완료(로컬):** optional 명시 정책으로 비용 차감 짝 평균 차이·표준오차 페널티·연구 위험 제약을 기존 비교/CLI·공통 RAM/PC·선별 기록에 연결했다. 결손/동률·미래/만료·계좌/input 불일치는 WAIT, 보호 EXIT는 EV보다 우선하며 실제 Risk 승인/주문 권한은 없다. 신규 13건 포함 관련 164건 통과·CLI/ASGI/Qt·DB 소비 delta 0 확인. catalog는 별도 `tests.unit.test_prepared_historical_archive_inputs` 미등록으로 실패했고 등록은 해당 작업에서 처리해야 한다. 다음은 기존 타점·native Risk 관측의 좁은 입력/제약 연결 검증이다. 최종 비중은 원래 Position·Risk·Capacity/Liquidity의 시장/테마/종목·시총/대금/유동성/변동성/EV/위험 기반 상위 동적 정책을 후속 구현한다. 현재 목표 금액/수량은 하위 기능이다. build `2026.10.14-position-action-selection-v1` 미커밋·미배포이며 운영 승격/미체결 실행·실제 앱/계좌/PG/NAS/장중 수락은 남는다. [55단계](AI_TRADING_SYSTEM_REDESIGN.md#55-r4-비용불확실성위험-제약의-주문-없는-행동-권고).

**2026-10-14 저장 속도 — NAS 2MiB/s 효과 확인, 3MiB/s/운영 영향 대기:** 같은 Writer/입력/빈 출력 디렉터리의 두 순서에서 1→2MiB/s는 39.50%/39.15% 단축, 내용/checksum·51회 fsync·memory/pending 상한과 운영 복구를 확인했다. PC만 확인했던 아래 기록을 갱신한다. 현재 운영 속도는 1MiB/s로 유지한다. 사용자가 요청한 다음 2→3MiB/s 판별 시험은 격리 session 속도만 바꿔 준비/인자/hash/보호 조건을 확인했으며 아직 미실행이다. 이후 운영 DB 동시 I/O 영향과 기존 1MiB/s 시작/API·예약 계약 보존을 검증하고 두 코드 수정·선택 속도의 배포를 진행해야 한다. 장중 COMMIT P1·전체 녹화/앱 수락은 별도 미해결이다. [근거](../artifacts/recorder-rate-nas-result-20261014.json).

**2026-10-14 지연 저장 묶음 확대 — NAS 성능 검증 완료·배포 대기:** 같은 입력 두 순서에서 1.86%/4.29% 단축, metadata 10→6개·본문/metadata fsync 59→51회, byte/hash/checksum·pending 상한·운영 복구를 확인했다. p95·최대 대기는 감소했지만 평균 대기는 4.60%/1.27% 증가한 교환관계를 기록한다. 지연 파일 저장에서 코드를 유지하고 prefix 범위 검증을 완료하며 아래 묶음 A/B 대기 기록을 갱신한다. 다음은 같은 수정본의 NAS 1/2MiB/s 비교다. 두 수정의 배포, 장시간 fsync 원인·native SQL/COMMIT P1·전체 앱 수락은 남는다. [근거](../artifacts/recorder-batch-nas-result-20261014.json).

**2026-10-14 동기화 후 추가 휴식 제거 — NAS 성능 검증 완료·배포 대기:** 두 순서의 실제 입력 16,384건 저장은 68.179→62.793초(7.90%), 70.120→63.788초(9.03%) 개선했다. 내용·hash·checksum·59회 fsync·pending 상한이 유지되고 CPU/RSS 급증 없이 운영 복구/health/idle을 확인했다. 코드를 유지하며 이 prefix 범위의 개선 검증을 완료한다. 배포·전체 녹화 성능·17초 파일 fsync 원인·native SQL/COMMIT·앱 전체 수락은 별도로 남긴다. 아래 10/13 및 10/14 실행 전 대기 기록을 갱신하는 결과이며 다음은 묶음 확대 NAS 두 순서 비교다. [근거](../artifacts/recorder-sync-wait-nas-result-20261014.json).

**2026-10-14 원본 저장 종료·NAS 성능 검증 대기:** 10/13 수락 입력 2,485,437건은 모두 durable drain됐고 유지 charge/queue 0·NAS health ok다. 입력 거부 4건 때문에 최종 `incomplete`이므로 전체 Replay 수락은 미완료다. 전체 파일 checksum 재검사와 거부된 collection의 정확한 판별도 남는다. 현재 idle 상태에서 첫 동기화 후 추가 휴식 제거 A/B의 소스/인자/보호 조건을 재확인했지만 sudo 비밀번호 요구로 시험은 시작되지 않았다. 고정 관리자 명령 실행 결과를 받은 뒤 효과·fsync p95/최대·큐·CPU/RSS·운영 복구를 판단하고 묶음·1/2MiB/s 비교를 이어간다. 운영 속도는 1MiB/s이고 기존 release/DB/예약은 유지한다. [근거](../artifacts/recorder-storage-finished-20261014.json).

**2026-10-14 R4 native 자동 목표 행동 자료 첫 연결 완료(로컬):** 기존 runner의 명시 typed 계획·같은 PaperExecutionEngine/writer·보고/manifest→readonly native ADD/REDUCE/REENTER/flat HOLD proof→CLI/PC 공통 비교를 연결했다. 실제 KST 원장/UTC 문자열 비교 오류는 진단 후 시각 비교/정밀 cut guard로 수정했다. 기본 연구 event/state와 기존 broker 경로는 유지했다. 신규 13건·관련 214건 뒤 최종 표시 50건/native 29건 각각 통과·source/감사 delta 0/catalog/CLI/Qt 확인. 미커밋 build `2026.10.14-position-action-native-v1`, 미배포다. 다음은 비용/불확실성/위험을 반영하는 주문 없는 행동 선택이다. 계획 생성 폼/자동 예약·예측 보정/운영 승격·미체결 실행과 실제 앱/운용 연구 데이터·broker/PG/NAS/장중 수락은 남는다. [54단계](AI_TRADING_SYSTEM_REDESIGN.md#54-r4-자동-목표의-native-addreducereenter-실행-자료-연결).

**2026-10-13 R4 후 실행 연결 — 미체결 관리 정책 설계 명시, 구현 대기:** 매수·일반 익절·손절/위험 청산의 세 정책으로 WAIT/CANCEL/REPRICE·REENTER/ABANDON을 판단한다. 기존 취소/체결 대사·owner/UNKNOWN 원장을 재사용하고 취소 확정 전 재주문·UNKNOWN 중복 제출을 금지한다. 보호는 일반 EV 추천보다 강제 위험 한도/기한을 우선하며 PC LLM·다음 1분봉·Shadow를 기다리지 않는다. 시장가/공격적 지정가도 체결 보장은 없고 현재 지원 유형/STOP 의미를 자동 확장하지 않는다. 자동 정책 선택/재주문·목적 전환/보호 wake-up·지연/모의계좌 수락은 미구현이다. 이 정책은 R4 후 실행 연결의 필수 수락 조건이다. [설계 정본](AI_TRADING_SYSTEM_REDESIGN.md#76-미체결-주문-관리-정책).

**2026-10-13 녹화 저장 속도 상향 후보 — PC 비교 통과, NAS 미수락:** 동일 수정본/실제 입력 16,384건/빈 출력 디렉터리에서 1→2MiB/s 두 순서 비교는 실제 PC 디스크 시간 52.9→29.4초·52.6→28.8초(44.5~45.2%)다. 출력 byte/hash/checksum·51회 파일 동기화·pending 상한이 동일하고 CPU/RSS 급증은 없으나 파일 동기화 p95/최대가 일부 증가했다. 원본 persisting 중이므로 운영 설정은 1MiB/s로 유지하며 NAS 고정 A/B의 인자·hash·busy 거부만 확인했다. 다음은 원본 durable idle 뒤 NAS 실측/복구와 운영 DB 동시 I/O 영향 판단이다. PC 통과를 NAS 안전성·장중 COMMIT 해결·최적화 완료로 처리하지 않는다. [근거](../artifacts/recorder-rate-review-20261013.json).

**2026-10-13 녹화 저장 묶음 확대 — NAS A/B 대기:** 기존 reader 2,000,000바이트 한도 안에서 지연 저장 묶음만 확대했고 일반 녹화/입력별 1MiB·4,096행/8MiB pending·본문 16MiB·1MiB/s·동기화/공개 조건을 유지했다. 관련 67건+기본 큰 묶음 실패 복구 1건 통과. 같은 실제 입력 16,384건에서 메타데이터 파일 10→6, 실제 파일 fsync 호출 59→51, 바이트·checksum 동일이며 가상 동기화 조건의 시간은 추가 약 9.7% 감소했다. NAS 실측/운영 부하 검증은 아직 없으므로 미완료이며 미커밋·미배포다. 원본 저장의 durable idle 뒤 기존 일시정지/복구 방식으로 같은 입력의 NAS 두 쌍을 비교한다. 속도 1/2MiB/s 비교는 다음 후보이며 현재 속도·운영 앱을 변경하지 않았다. [근거](../artifacts/recorder-batch-review-20261013.json).

**2026-10-13 녹화 저장 추가 휴식 수정 — NAS A/B 대기:** 운영 저장에서 20:23 기준 동기화 344초·추가 휴식 약 239초를 확인했다. 기존 `diagnostic_trace`에서 완료된 payload/metadata fsync 시간을 추가 휴식 산정에서 제외하는 최소 수정을 구현했다. 1MiB/s·원래 fsync 호출·0.25초 휴식 하한·다른 작업의 5초 상한·순서/체크섬/완료 공개 조건은 유지한다. 관련 42건 통과, 실제 10/12 입력 16,384건의 두 번 비교에서 가상 2초 동기화/가상 pacing 조건의 시간은 약 215.5→173.7초(19.4%)이며 바이트·체크섬·동기화 59회·최대 pending 4,096행이 동일하다. **이 수치는 NAS 실측 개선이 아니고 최적화는 미완료다.** 최초 비교기의 가상 지연 연결 누락 결과는 제외했고 연결 실행 검사를 추가했다. 현재 원본 녹화가 `persisting`이므로 `trace_not_durably_idle`을 우회하지 않는다. 기존 운영 일시정지/복구 방식의 고정 NAS A/B 실행기를 비활성 게시했고 소스 결합·인자·저장 중 거부 조건을 확인했다. 다음은 원본 저장 완료 뒤 두 NAS 순서 교차 비교와 큐/fsync p95·최대·CPU/RSS·운영 복구 확인이다. 효과가 없거나 부작용이 있으면 재평가/철회한다. 운영 배포·커밋·P1 장중 COMMIT 해결은 이번 단계에 포함하지 않았다. 근거: `artifacts/recorder-sync-wait-review-20261013.json`.

**2026-10-13 R4 계좌 전체 대사 연결 완료(로컬):** 명시 선택 계좌의 기존 recovery 전후 전체 run 미종결 원장과 broker 확인 잔량/수동 주문을 공통 계산에 연결했다. 결손/QUEUED/전송 미확인/취소 대기·변경/조회 실패는 수량 차단이다. 신규 22건 포함 최종 관련 188건·정적 계약 5건 통과, 새 read/소비 각 1개와 기존 계약 보존 확인. native SQLite 24쌍 비교는 연결/SELECT 각 1→3·COMMIT/broker recovery 유지이고 시간 개선을 주장하지 않는다. 다음은 행동별 실제 완결 자료의 기존 reader 연결이다. 자동 목표·예측/운영 승격·실제 PG/NAS/계좌/장중 수락은 남으며 주문/배포는 미실행이다. [50단계](AI_TRADING_SYSTEM_REDESIGN.md#50-r4-계좌-전체-대사와-브로커-확인-잔량-계산).

**2026-10-13 R4 모의계좌 owner RAM 전달(49단계 기록):** 기존 recovery 자료를 공통 Shadow/관찰에 전달했다. 신규 11건·관련 160건 뒤 최종 경계 18건 각각 통과·최종 SHA/소비 감사 delta 0/catalog·Qt 화면 확인. 8개 관찰 계좌 read/R4 평가 1회·계좌 알림의 H1/H2 추가 계산 0회·관측 ref만 바뀔 때 추가 COMMIT 0회다. 기존 owner를 활성화하거나 새 TR/DB/1초봉 polling을 만들지 않으며 실패/무효화는 고정 입력으로 fallback하지 않는다. 현재 실행별 원장을 계좌 전체로 간주하지 않아 추가 수량/EV는 차단된다. 계좌 전체 대사의 후속 로컬 구현은 위 50단계에서 완료했다. 실제 행동 자료 reader·자동 목표/예측 검증·NAS/PG/실제 모의계좌/장중·운영 승격은 남는다. [49단계](AI_TRADING_SYSTEM_REDESIGN.md#49-기존-모의계좌-owner의-ram-관측을-공통-r4-입력으로-전달).

**2026-10-13 R4 공통 관찰 소비 연결:** 고정 입력 JSON의 명시 선택→기존 계산 한 번→공통 Runtime/선별 한 행/인증 조회→관찰 수량·비용 평균/표본/미확인 표시를 연결했다. 신규 6건 포함 관련 105건(32.300초)·소스 SHA/consumer delta 0/catalog·격리 Qt 픽셀/스크롤을 확인했다. 틱/다중 테마·조회에서 R4 계산·계좌/DB 조회를 반복하지 않으며 과거 입력은 고정 시각/ID·실시간 계좌 아님으로 유지한다. 다음은 기존 계좌 owner가 이미 얻은 계좌·주문 관측 자동 전달이다. 실행별 원장을 계좌 전체 미체결로 간주하지 않는 범위/대사 근거가 필요하다. 실제 행동 자료 reader·자동 목표/예측 검증·실제 NAS/PG/장중 수락·운영 승격은 남는다. [48단계](AI_TRADING_SYSTEM_REDESIGN.md#48-r4-목표행동-비교의-기존-공통-shadow관찰-소비-연결).

**2026-10-13 R4 행동별 관측 EV 비교:** 같은 초기 상태/기간의 완결된 행동 자료를 명시해 HOLD 대비 비용 차감 평균·짝 차이·표본/분산/표준오차를 계산한다. 현재 목표/자금 차단을 유지하고, 미래/불완전/다른 정책·목표/비용/체결 모델·모집단/해상도 자료와 부족한 행동은 EV_UNAVAILABLE다. 기존 전체 매매 평균을 ADD/REDUCE로 재표지하지 않는다. 신규 15건 포함 관련 100건·현재 소스 SHA/AST/catalog/diff 확인. 고정 입력 함수/CLI 연결이며 실제 행동별 자료 준비/reader·실시간 계좌/관찰 소비·예측 검증과 운영 승격은 남는다. [47단계](AI_TRADING_SYSTEM_REDESIGN.md#47-r4-행동별-비용-차감-관측-기대값과-불확실성-비교).

**2026-10-13 R4 시작 — 명시 목표 수량:** 기존 계좌/주문 snapshot의 실제 수량·확인된 잔량·예약 자금과 기존 비용 모델로 목표까지 추가 가능한 수량을 주문 없이 계산한다. 고정 입력 CLI와 같은 함수/ID를 사용하며 목표/비용·대사 미확인/취소 대기는 차단한다. 신규 15건 포함 관련 114건·최종 소스 SHA/catalog/diff를 확인했다. R3 운용 진단은 별도로 유지한다. 다음 R4 기능은 비용 차감 행동 비교/EV_UNAVAILABLE·불확실성 표현이며 자동 목표 정책·실시간 계좌 owner/관찰 화면 연결과 실행 승격은 아직 남는다. [46단계](AI_TRADING_SYSTEM_REDESIGN.md#46-r4-명시-목표-수량과-기존-계좌주문-입력-연결).

**2026-10-13 R3 녹화 입력 힌트:** 활성 Collector epoch의 기존 수락 high-water ID를 snapshot→공통 출처/선별 한 행으로 전달했다. 세션/OFF/재생·거절/복사 경계와 실제 격리 Recorder reader/SQLite 연결을 신규 5건 포함 관련 108건·SHA/감사/catalog에서 확인했다. 추가 payload/DB/COMMIT·수집은 없고 조회 힌트만 제공한다. 현 0g 제외·전체 원본/lineage·적격성/운영 승인·실제 앱/PG/NAS/장중 수락은 남는다. [45단계](AI_TRADING_SYSTEM_REDESIGN.md#45-ram-출처의-기존-녹화-입력-조회-힌트).

**2026-10-13 R3 RAM 출처 근거:** 단일 Collector RAM cut의 source/generation·승인/연속 구간·보존/gap/전달 누락을 실제 결과에 묶어 기존 live/선별 한 행에서 공유한다. 이전/실패/취소 cut·복사 경계/용량 생략을 신규 6건 포함 관련 142건·SHA/감사/catalog에서 확인했다. 수집/원본 writer·COMMIT/H1 공유는 유지하며 근거는 capture/native 승인으로 쓰지 않는다. 실제 capture lineage·원본 확정/적격성/운영 승인·앱/PG/NAS/장중 수락은 남는다. [44단계](AI_TRADING_SYSTEM_REDESIGN.md#44-ram-판단의-공통-출처와-연속성-근거-연결).

**2026-10-13 R3 수동 원본 대조 표시:** 관찰 화면의 저장된 가설/revision·1–60초 창→기존 worker의 단발 대조와 일치/미확인/비승인·조회 비용을 연결했다. 편집/live 보존·선택 초기화·형식/승인 범위/stale 거절·중복/stop join을 신규 5건 포함 관련 50건과 픽셀 QA·SHA/감사/catalog에서 확인했다. 자동 대조/새 수집·저장은 없다. 실제 앱/PG/NAS·원본 연속성/lineage·적격성/운영 승인·장중 지연 측정은 남는다. [43단계](AI_TRADING_SYSTEM_REDESIGN.md#43-관찰-화면의-수동-원본-대조와-결과-표시).

**2026-10-13 R3 native 원본 대조:** 저장 가설을 expected_revision/명시 창으로 native theme·초봉과 수동 대조하고 결과/조회 비용만 반환한다. live/tick 추가 read·새 저장은 없다. 초봉 최신 값의 hash 일치와 수집 연속성/이전 revision/lineage·운영 승인을 구분한다. 신규 10건·관련 최초 109/110 통과 후 Windows staging 접근 거부 한 건만 재검증 통과, 소스 SHA·감사/catalog 일치. 화면 표시·실제 앱/PG/NAS·확정 적격성/운영 승인·장중 지연은 남는다. [42단계](AI_TRADING_SYSTEM_REDESIGN.md#42-연구-가설의-native-원본을-명시-대조).

**2026-10-13 R3 가설/정책 화면 선택:** 기존 관찰 탭의 PC read-only DB/ID·네 정책 명시 선택→검증 snapshot→인증 selection/API→공통 Runtime을 연결했다. 한 가설/정책·다른 테마 독립, null 해제/ACK 유실·재시작/새 H2 상태·UI read/stop·경로 미전달을 신규 10건 포함 관련 116건·소스 SHA·소비 감사/catalog·픽셀 QA에서 확인했다. 새 PC 선택만 2 SELECT·COMMIT 0회, snapshot 재사용/tick read 0회. 연구 저장/문서 검증과 시장 원본 확정/운영 승인을 구분하며 실제 앱/NAS·원본 provenance/적격성·PG/장중 수락은 남는다. 활성화·주문·배포는 없다. [현재 계약/검증](AI_TRADING_SYSTEM_REDESIGN.md#41-저장된-연구-가설정책의-관찰-화면과-명시-설정-연결).

**2026-10-13 R3 불변 연구 진입 가설 복구:** 기존 DB의 명시 run/thesis ID·연결 대장 근거 1회 조회→typed 요청→기존 CLI/공통 RAM Shadow를 연결했다. 정책은 기존 네 값 중 명시하며 read-only/원문·scope·COMPLETE·시각/실패 run/RAM 참조 검사를 신규 10건 포함 관련 103건에서 확인했다. SELECT 1회/COMMIT 0회·틱 추가 조회 0회·기존 결과/ID/주문 차단을 유지한다. 저장 연구 근거는 운영 승인/시장 원본 확정과 구분한다. 다음은 실제 화면/운영 선택과 원본 확정 승인·적격성/PG/NAS/장중 수락이며 활성화·주문·배포는 없다. [현재 계약/사용](AI_TRADING_SYSTEM_REDESIGN.md#40-기존-연구-db의-불변-진입-가설을-명시-복구해-shadow에-전달).

**2026-10-13 R3 상한가 원본 RAM 연결:** 기존 0g 참조정보를 Collector→공통 입력→H2에 전달했다. 당일/거래소/가용시각·승인 source와 미확인 UNKNOWN·참조 단독 갱신/중복/취소/종료를 신규 9건 포함 관련 152건에서 확인했다. 8개 테마 RAM 읽기 1회/H1 1회/H2 8회, 원본 봉/참조 내용·원본 COMMIT 5회·기록 ON 총 6회는 전후 같다. 새 DB 호출/수집은 없고 지연 개선은 확인하지 못했다. 로컬 build 표식 갱신/health·release 계약 별도 12건 통과. 다음은 확정 승인/보호 정책/재시작 근거·실제 PG/NAS/장시간/장중 수락이며 활성화·주문·배포는 없다. [현재 계약/근거](AI_TRADING_SYSTEM_REDESIGN.md#39-기존-0g-상한가-참조정보의-ram-shadow-전달).

**2026-10-13 R3 테마 간 H1 공유/입력 부하:** 공통 H1 8→1·H2 상태/결과 8개 독립, 불변 market frame 참조 재사용/가변 snapshot 복사와 int/float 결과 인코딩을 보존했다. 신규 12건 포함 관련 184건·소스 SHA·소비 감사/catalog 통과. 로컬 합성 큰 입력 준비→결과 중앙값 677.394→88.058ms이며 실제 장중 성능 수락은 아니다. 원본 writer/COMMIT 4회는 유지하고 recording ON 첫 진단만 1회 추가된다. 다음은 확정 승인/보호 정책/재시작 근거·상한가 원본과 실제 PG/NAS/장시간/장중 부하다. 활성화·주문·배포는 없다. [현재 계약/측정](AI_TRADING_SYSTEM_REDESIGN.md#38-서로-다른-테마의-h1-공유와-불변-시장-입력-재사용).

**2026-10-13 R3 관찰 화면/명시 선택:** 기존 Shadow 창·client/worker/router/app에 기본 OFF의 테마/거래소/기록 선택과 live/과거 기록·만료/독립 정책 표시를 연결했다. 저장/적용 revision·ACK 유실·기동 실패/재시작·반복 취소/종료 fence·늦은 owner 알림을 신규 15건 포함 관련 166건·소스 SHA·소비 감사/catalog·offscreen QA로 확인했다. 다음은 여러 테마의 동일 H1 부분 공유/종단 부하와 확정 승인/보호 정책/상한가 원본 연결·PG/NAS/장중 수락이다. 운영 활성화·주문·배포는 없다. [현재 계약/검증](AI_TRADING_SYSTEM_REDESIGN.md#37-시장테마-관찰-화면과-명시-실행-선택).

**2026-10-13 R3 RAM 선별 기록/조회:** 기본 OFF/명시 binding에서 기존 writer에 최근 진단 한 행만 선택 저장한다. 공통 context·독립 정책, 간격/coalescing·느린 DB 중 판단·ACK 유실/종료/오류, 인증 RAM 기본 무조회·명시 저장분 조회/만료를 신규 7건과 기존 관련 156건에서 확인했다. 신규 앱 검사의 404 기대값 수정 후 재검증과 두 소비 identity 검토를 완료했다. 다음은 실제 화면·명시 운영 선택/정책·재시작 승인 근거와 PG/NAS·장중/성능/전체 gate다. 저장분을 replay/주문 승인으로 쓰지 않는다. [범위/근거](AI_TRADING_SYSTEM_REDESIGN.md#36-ram-shadow-결과의-선별-저장과-인증-조회).

**2026-10-13 R3 TOP20/theme 자동 갱신:** 기존 owner의 RAM 공개/테마 쓰기 완료 신호→공통 source 읽기/context 갱신을 연결했다. 8개 소비자도 읽기/변환을 공유하며 동일 입력·과거 테마 lineage/미래 cut·삭제·실패/취소·ACK 유실을 검증했다. 관련 255건·소비 감사 통과, 원본 저장/COMMIT·API 계약 유지. 다음은 선별 기록/표시·재시작 근거와 명시 운영 선택이다. 기본 binding/배포는 유지하며 실제 PG/NAS/장중·pipeline 성능·전체 gate는 남는다. [범위/근거](AI_TRADING_SYSTEM_REDESIGN.md#35-top20테마-owner의-공통-context-자동-갱신).

**2026-10-13 R3 Collector RAM/앱 수명 연결:** 기존 Hub·canonical 초 관측→공통 typed 입력/계산과 명시 app binding/시작·종료를 연결했다. source generation/구독·보존/gap/drop 품질, bounded RAM 보존·단일 소유권·실패 retry/최종 cut/반복 취소를 검증했다. 0/2/8 소비자에서도 원본 네 writer/COMMIT 4회·봉 내용은 같고 8개 입력/계산은 각각 1회·DB 0회다. 관련 183건과 소비 감사 통과. 다음은 기존 TOP20/theme context의 자동 갱신 연결·명시 운영 선택과 선별 기록/표시/재시작 근거다. 운영 기본 binding은 비어 있으며 장중/PG/NAS·전체 gate·배포는 미실행이다. [범위/근거](AI_TRADING_SYSTEM_REDESIGN.md#34-기존-collector-ram-관측과-앱의-공통-수명-연결).


**2026-10-13 R3 공통 계산 연결:** 같은 native 페이지의 분봉 변환 8→4회/순위 2→1회, 동일 H1/H2 두 context의 각각 2→1회/네 정책+context의 각각 5→1회 계산을 구현·측정했다. 전략별 판단/ID·상태·저장/checkpoint는 유지하고 관련 160건·소비 감사 delta 0을 통과했다. RAM 준비 지연은 줄었지만 native SQLite 저장 포함 지연은 개선되지 않았다. 다음은 기존 Collector RAM의 공통 읽기 전용 발행/generation·gap과 app 공통 호출의 수명/선별 기록이다. 운영 PG/NAS·장중 수락·커밋 전 전체 gate는 남는다. [범위/측정](AI_TRADING_SYSTEM_REDESIGN.md#33-같은-관측의-frame-변환과-h1h2-계산-공유).

**2026-10-13 R3 네 항목 점검:** 기존 Collector/Hub의 RAM 다중 전달은 집계/원본 저장을 늘리지 않았다(0/2/8 소비자·40체결·원본 COMMIT 4회). 확정 DB page 공유는 read 2→1/SELECT 4→2/연결 12→11, 독립 평가/저장·COMMIT은 보존한다. 공통 frame 변환 8→8/H1·H2 두 instance의 각각 2회 계산으로 중복 계산 제거는 미완료이며 31쌍 지연의 일관된 개선도 미확인이다. 현재 평가 저장 ACK 순서를 재현·최소 수정해 관련 113건을 통과했다. 다음은 공통 frame/H1·H2 계산과 Collector RAM 발행/app 수명 연결이며 실제 PG/NAS/장중 수락은 남는다. [측정/근거](AI_TRADING_SYSTEM_REDESIGN.md#32-collector-ram-공유입력-권한중복지연의-네-항목-점검).

**2026-10-13 R3 공통 페이지 다중 전달:** native safe-prefix page 1회→RAM의 돌파·눌림 독립 cursor 소비와 실패/복구/동시/취소 drain을 연결했다. 후보 평가 저장 실패/ACK 유실의 선행 RAM 상태 변경도 재현·최소 수정했다. 새 13건 포함 관련 111건 통과, 같은 native SQLite fixture에서 read 2→1/관측 SELECT 4→2/연결 12→11/쓰기 COMMIT 10회·판단 보존을 확인했다. collector RAM 직접 발행·H1/H2 공통 맥락·app 공통 worker/수명·PG/NAS/장중 수락은 남는다. [범위/근거](AI_TRADING_SYSTEM_REDESIGN.md#31-공통-관측-페이지의-ram-다중-전달).

**2026-10-13 R3 공통 Shadow 두 Family 연결:** 돌파·눌림의 등록 evaluator를 동일 CandidateMonitor/기존 1분봉·순위 reader/저장·복구 경로에서 소비하도록 확장했다. 전략별 ID/상태/결과와 기존 기본 돌파를 보존하며 1초 입력은 강제하지 않는다. 새 6건 포함 관련 98건과 소비 감사 delta 0을 확인했다. RAM 단일 발행·다중 소비/H1·H2 공통 맥락·선별 기록/app 수명·운영 선택·PG/NAS·장중 부하는 남는다. [현재 완료 범위](AI_TRADING_SYSTEM_REDESIGN.md#30-전략-입력-해상도와-기존-shadow-실행부-재사용).

**2026-10-13 Shadow 공통 실행 계약:** Shadow를 공통 판단 엔진의 주문 없는 검증 방식으로 고정했다. 전략별 규칙/상태/결과는 격리하고 수집·시장 분석·기록·복구는 기존 기능을 재사용한다. 미완성 전용 producer hooks는 제거했다. 다음 R3 범위는 기존 입력·공통 맥락·등록 evaluator의 공통 소비 연결이며 기록/복구/수명·전략 격리·주문 호출 차단·장중 부하는 아직 미완료다. [현행 계약](AI_TRADING_SYSTEM_REDESIGN.md#29-shadow를-공통-판단의-검증-실행으로-통일).

**2026-10-13 R3 부하 경계 재조정:** 2초 native 재조회/자동 실행 연결은 제거하고 RAM snapshot 도착만 계산하는 수명 실행부를 구현했다. 새 6건 포함 관련 91건·중복 100회 평가 1회/DB 연결 0회를 확인했다. 기존 돌파 Shadow의 I/O는 그대로이며 native bridge는 복원/명시 점검용이다. 실제 producer 발행·app 연결·generation/continuity·장중 CPU/메모리/부하·선별 저장/표시는 남는다. [역할/납품 범위](AI_TRADING_SYSTEM_REDESIGN.md#28-r3-ram-입력-shadow-수명과-부하-경계).

**2026-10-13 R3 native 입력 연결:** 순위/테마 이력·bounded 1초봉 reader→공통 평가 입력을 구현했고 새 6건 포함 관련 200건과 native fixture→CLI 동등성을 확인했다. 1초봉 연속성 미확인은 UNAVAILABLE/H2 PARTIAL이며 live task는 기동하지 않았다. 실제 source cut/continuity·owned 수명/종료·저장/표시·새 reader 녹화/Replay·PG/NAS·신규 진입/수량은 남는다. [납품/제약](AI_TRADING_SYSTEM_REDESIGN.md#27-r3-native-입력-조회-연결).

**2026-10-13 R3 종목 후속 판단 연결:** 기존 진입 가설/명시 정책의 HOLD/WAIT/REDUCE/EXIT 제안·무효화 근거를 같은 cut의 H1/H2와 연결했고 새 8건 포함 관련 64건·실제 CLI 5사례를 통과했다. 신규 진입/목표 수량·시장별 새 규칙·실시간 source reader/수명·저장/표시·운영 수락은 남는다. 주문 승격이나 전체 R3 완료가 아니다. [범위/계약](AI_TRADING_SYSTEM_REDESIGN.md#26-r3-진입-근거의-종목-행동무효화-shadow-연결).

**2026-10-13 R3 첫 입력 연결:** 고정 입력 H1 시장/H2 대장 공통 Shadow 평가·JSON 미리보기와 새 12건 포함 관련 52건을 완료했다. 실제 source cut/내구성·실시간 reader/Shadow 수명·종목 행동/무효화·저장/표시·실제 gate는 남는다. 전체 R3/운용 완료가 아니다. R1/R2 전체 회귀는 소스 반영 전 중단/미완료로 보존했고 커밋·push 전 all-local/지문 gate가 필요하다. [계약/검증](AI_TRADING_SYSTEM_REDESIGN.md#25-r3-시장테마-shadow-입력-연결).

**2026-10-13 R2 checkpoint 경계 보완:** 연속 native COMMIT 실패 후 idle/같은 중지 상태에서 retry가 사라지는 경로, 동일 저장의 반복 COMMIT, 저장 ACK 전 종료를 로컬 재현해 기존 runner의 pending/no-op/owned drain으로 보완했다. bootstrap의 간접 저장과 FILL 상태 읽기 drain을 포함한 새 9건/278건 관련 회귀는 통과했다. 실제 PG/NAS의 실패·취소/부하·broker 전체 재시작·장시간과 다른 writer/전체 R2 성능은 남는다. 이 완료를 P1 장중 COMMIT 원인 제거로 표시하지 않는다. [재현과 보존 범위](AI_TRADING_SYSTEM_REDESIGN.md#24-r2-checkpoint-저장-실패noop종료-순서).

**2026-10-13 R2 불변 승인 근거의 좁은 조회:** 복구/gate 저장 전 불변 근거 read를 묶는 첫 최적화와 같은 native SQLite 결과/왕복/동시성 비교는 완료했다. 실제 PostgreSQL SQL·planner·동시성/부하, checkpoint/no-op·실패 재시도 등 다른 업무 저장 경계와 전체 R2 성능 수락은 남는다. 실제 쓰기 COMMIT/제어/위험/owner 경계는 유지했으며 P1 장중 COMMIT 원인을 해결했다고 표시하지 않는다. [변경·실측·검증 범위](AI_TRADING_SYSTEM_REDESIGN.md#23-r2-불변-승인-근거의-좁은-조회).

**2026-10-13 R1 첫 돌파 판단 소비 연결:** Shadow/연구/모의 runner가 기존 등록 evaluator를 소비하는 연결과 고정 입력 전후 보존 비교는 완료했다. 전체 R1의 종합 판단 소비 계약·업무별 좁은 저장 API, R2의 실제 native SQL/COMMIT·부하 측정/최적화, R3 새 판단 연결과 운영 수락은 남는다. 95건의 관련 로컬 회귀를 전체 재구성/배포 수락으로 사용하지 않는다. [완료 범위와 미실행](AI_TRADING_SYSTEM_REDESIGN.md#22-r1-첫-돌파-판단-소비-연결).

**개발 우선순위 재정리(2026-10-13 사용자 전체 설계 요청):** [전체 설계와 재조립/신규 개발 비교](AI_TRADING_SYSTEM_REDESIGN.md)를 작성했다. 추천은 기존 앱/자료 보존과 핵심 경로의 유지·정리·교체, 저장/판단/UI 소비 경계의 선택적 재조립이다. 작은 새 구조와 모듈형 모놀리스는 참고안이며 새 프로젝트 생성 결정을 뜻하지 않는다. 다음 실제 변경 범위는 R1 기존 입력→판단→위험→주문/체결→기록 경로 하나이며 새 기능 개발과 분리한다. DB/메인 결합·불변 저장의 반복 조회 근거와 목적별 저장·좁은 계약·기능 추가의 국소화·NAS/독립 PC·DB Replay의 독립 진단 도구 분류를 설계에 반영했다. 기존 모듈/검증이 새 구조 구현 완료를 뜻하지 않는다. R2~R5·후보 적격/final·실제 모의/실전 지원은 [현재 로드맵](../FUTURE_DEVELOPMENT_ROADMAP.md)을 따른다. 아래 운영 잔여는 보존하되 관련 없는 기능의 직렬 선행 조건으로 묶지 않는다. 이번 변경은 설계·문서이며 실제 재조립·DB·계좌·NAS 변경은 없다.

**O11 NAS 차단 확인·시계 v4 검사 준비(2026-10-13 05:37 KST):** trace=off·diagnostics_idle=true지만 오늘 녹화 예약 PID 31632가 예약 status 잠금의 WRITE FLOCK을 보유한 것을 fdinfo로 확인했다. 기존 고정 후보의 133건 요청 1회는 `operation_busy`로 실행 전 거부됐고 시험/새 job 0건이다. 예약은 유지하며 실행기 종료·durable idle·잠금 사용 가능을 함께 확인해야 한다. 최신 통합 소스에는 별도 후보/gate가 필요하다. 로컬 시계 초기 STOPPED·미확인 주문·취소 이력·중복 저장/updated_at·두 번 Replay 검증을 보강해 관련 66건 통과했다. 기존 전용 PostgreSQL 검사에도 같은 시계 5호출과 내용·transaction·초기 상태 복원을 추가했고 로딩·문법·catalog 연결을 확인했다. **실제 PostgreSQL 실행·배포·실제 모의계좌 운용은 미완료**다. [현재 근거·검증 범위](CURRENT_STATUS.md).

**녹화 범위 보완 대기(2026-10-13 통합본 감사):** 새 모의운용 시계 문서 `execution_mock_automation_clock_current`는 native 저장이 실행되지만 녹화 경계에서 `unsupported_document_collection`으로 거부되며 계좌 초기 상태에도 포함되지 않는다. 배포 전 읽기·저장·초기 복원·재시도 계약과 같은 초기 DB의 Replay 비교가 필요하다. 사용자 요청에 따라 시계와 TOP20 장후 표시 계산본·통계를 모두 포함하는 설계를 확정했다([계약·검증 조건](NAS_RUNTIME_DIAGNOSTICS.md)). 새 account-context/v4, display 호출·환경 소비값 기록, 기존 baseline 테이블 재사용을 선택했으며 로컬 캡처·초기 복원·native Replay 연결과 같은 초기 SQLite fixture 두 번 Replay를 구현했다. 저장 reader·실패 drain·구형 호환 회귀도 추가했고 Windows 전체 4,241건·지문 대조가 통과했고 소스 커밋 `3180f04`에 포함했다. 새 계약의 실제 PostgreSQL·배포·장중 수락은 남는다. 앞선 통계 캐시 테이블 누락 판단은 정정한다. 캐시는 이미 central_dataset_snapshots의 kind다. 과거뉴스 v2는 외부 읽기 전용 SQLite 파일을 읽어 PostgreSQL 저장 호출 녹화로 복원되지 않는다. 이 범위가 없는 녹화본을 앱 전체 Replay 완료로 판정하지 않는다. 증분 실시간 등록에는 v2 입력·reader·transport 경로가 있지만 실제 장중 완전성은 별도 검증이다. NAS는 기존 `2026.10.12-recorder-account-index-v3-875421f5e5e3fb26`이며 10/13 08:59:50~09:59:50 녹화·20:10 저장·다섯 옵션 예약을 읽기 확인했다. 이번 커밋 준비에서 NAS는 변경하지 않았다. 실제 장중 10~20초 COMMIT 지연은 P1 미해결로 유지한다.

**과거뉴스 전체 이관 v2 구현·진행(2026-10-13, main `941347e` 작업본·미커밋/미배포):** 검색/시황의 기사·본문·여러 대상 판정·사건·compact 목록을 PC에서 완료하고 NAS는 읽기만 하는 경로를 구현했다. 기존 ID/sequence/참조를 보존하며 기존 v1은 별도로 유지한다. 원본 누락·실패 근거·입력/본문 hash·target/참조·seed 전수 비교·full integrity 검사를 통과해야 봉인된다. OS builder 잠금·batch rollback/재개·2GiB 저용량 중단·불변 버전 복사/전체 hash 검사도 포함한다. 관련 40건 묶음 통과 뒤 canonical 손상/입력 snapshot을 보강한 최신 source/추출/snapshot/진도 재개 묶음 27건도 통과했다. 실제 PostgreSQL 전용 검사 2건도 통과했다. 대용량 사본 생성과 겹친 확대 묶음은 60초 제한으로 중단됐고 성공 처리하지 않았다. 현재 코드의 전체 운영 자료 검증/게시가 완료됐다는 뜻은 아니다. 검색/시황 준비본 총 1,024행 표본은 예상 신규 archive 약 160.0GB, 고정 준비본 57.0GB, 가정 여유 48.0GB다. 원본 5테이블 320행 표본(파싱 항목 포함)으로 원본 이력 약 44.5GB 및 인덱스 여유 포함 66.8GB를 추가 추정해 약 332GB에 요청 실패 원장 추가 여유 6.3GB를 더한 약 338GB+최신 NAS seed가 필요하다. 통계 표본 보장은 아니며 실제 peak는 미측정이다. C: 여유 약 359GB를 확인해 검색 고정 사본 생성을 시작했다. 최신 NAS seed 추출 311,488행을 완료하고 PC 사본의 전체 SHA/무결성을 확인했다. frozen builder 536파일로 기존 ID 물질화를 시작했다. 전체 준비 입력 최종화·봉인·NAS 복사/활성화·원본 삭제는 아직 하지 않았다. [구현/실행 경계](PREPARED_NEWS_ARCHIVE_DESIGN_REVIEW.md#v2-구현과-실행-상태-2026-10-13).

**과거뉴스 전체 archive 게시 후속(2026-10-13):** 두 scope·다중 target 최종화, raw 근거/실패 원장, v2 종목/시황 읽기, 감사/봉인/검증 복사 도구는 로컬 구현했다. 최신 NAS readonly seed 대조 → 두 준비본 고정 → 전체 PC 최종화/원본 대조/봉인 → 비활성 NAS 파일 복사·검증 → 고정 reader 후보 검증/읽기 활성화가 남아 있다. 최신 NAS seed 복사/검증을 완료했고 검색 고정 사본 생성과 기존 ID 물질화를 진행 중이다. 기존 NAS 자료 삭제·매일 자동 최신 보완은 별도 후속이며 이번 이관으로 완료 처리하지 않는다. NAS BODY/RULE/사건/job/대량 적재를 다시 실행하지 않는다. 9월 building 표본을 전체 게시본으로 사용하지 않는다. 전체 DB에서 확인될 누락/실패 사유는 해결하거나 실제 근거와 함께 보류하며 표식만으로 봉인하지 않는다.

**O11 새 소유권의 중지 복구 회귀(2026-10-12 21:07 KST):** 격리 파일 DB를 별도 프로세스의 새 native ExecutionRuntime/runner로 복구하는 시험 1건을 추가했고 관련 4모듈 51건 통과다. 새 소유권·새 broker 대사도 STOPPED/revision·보유 2주를 유지하고 경쟁 소유권·오래된 복구 재개·중지 중 EXIT·직접 주문 전송은 차단한다. 명시 재개에서만 RUNNING으로 바뀌고 broker 제출/취소 0건·종료 lease 0개다. 전체 서비스/supervisor·실제 WebSocket/broker·PG 재시작은 남는다. 운영 코드는 수정하지 않았고 새 시험은 로컬에만 있으며 고정 NAS 후보/133건 계획은 유지했다. NAS는 persisting·queued 753,953건, 이번 턴은 status만 확인했으므로 격리 검사는 여전히 미실행이다. [새 검증·남은 범위](CURRENT_STATUS.md#current-summary).

**O11 NAS 격리 검사 대기(2026-10-12 20:44 KST):** 고정 후보의 1,054파일을 NAS 비활성 release에 검증 게시하고 Linux 단위 124건·임시 PostgreSQL 9건의 계획을 준비했다. 발견/로딩 오류 0개는 실행 결과와 구분한다. 실제 두 요청은 운영 잠금 `operation_busy`로 거부돼 시험/새 job 0개이며 NAS Linux·PG 검증은 미실행이다. 별도 status에서 녹화 자료가 persisting·queued 862,111건이고 written은 증가했다. private operator.lock 접근 제한으로 잠금 소유자·대상은 미확정이며 녹화와 잠금의 인과관계는 단정하지 않는다. 녹화 durable idle·잠금 사용 가능 뒤 같은 후보/계획을 재검증한다. 운영 release·서비스·계좌·주문·예약은 유지했고 차단을 우회하지 않았다. [준비·거부·관측 근거](CURRENT_STATUS.md#current-summary). 아래 PC에만 있다는 표현은 이전 단계 기록이다.

**O11 배포 전 로컬 후보 검증(2026-10-12):** NAS 활성 release 1,044파일 hash를 확인하고 자동운용 관련 8소스+빌드 표식만 반영한 PC 후보를 준비했다. 의존성/스키마 계약을 유지했으며 후보 169건·작업본 162건·PC 전략/도구 30건을 각 범위에서 통과했다. 새 별도 프로세스 파일 DB 복구 시험은 부분 청산·체결 원장 재생·잔여 주문/시계 문서·일지 새 체결 2→1→0건을 확인한다. supervisor/브로커 전체 런타임 재시작·NAS Linux/PG·장시간·실제 계좌 운용은 남는다. PC CLI 의존 시험은 후보 로딩 실패와 제외 범위를 기록하고 PC 전체 해당 모듈에서 별도 검증했으며 기존 별도 뉴스 catalog 미등록도 남는다. 후보는 PC에만 있고 NAS·계좌·주문은 변경하지 않았다. 다음은 같은 후보의 NAS 격리 검사다. [현재 결과·후보·범위](CURRENT_STATUS.md#current-summary).

**O11 모의 준비 점검 후속(2026-10-12):** NAS 읽기 전용 조회에서 모의 binding·계좌 scope는 일치하나 주문 허용 꺼짐·후보/명세/READY 0개·MANUAL을 확인했다. 관련 19파일 중 로컬/NAS 차이 8개이며 독립 시계 청산은 미배포다. 준비된 명세만으로 시작 버튼이 켜지는 화면 결함은 계좌 설정 확인·한글 사유·중지 유지로 보완했고 관련 69건 통과, 로컬 미커밋·미배포다. 소스 차이·미배포 기능의 검증 범위와 후보·명세 준비가 남는다. 계좌 설정·주문·게시·배포는 변경하지 않았으며 실제 운용 검증은 별도다. [현재 근거와 범위](CURRENT_STATUS.md#current-summary). 아래 정리 기록의 읽기 전용 준비 점검 예고는 완료했고 25bp 연구 보류는 유지한다.

정리 기준: **2026-10-11**. 소스 SHA·배포 release·통과한 검증·미실행 검증은 [현재 상태의 한 표](CURRENT_STATUS.md#current-summary)를 정본으로 사용한다.

**완료 연구 데이터 정리·모의 기능 검증(2026-10-12, 미커밋·미배포):** 사용자 결정으로 추가 타점 연구/25bp 비교를 보류하고 모의 주문·체결·청산·복구 기능 확인을 우선한다. 완료 연구 상세 DB·중복 입력 124묶음·599파일·51.12GiB를 NAS 아카이브에 5.17GiB로 검증 보관한 뒤 PC 사본을 제거했다. 실패 0개, 확인 시 C: 여유 93.35GiB다. 뉴스·원본 수집·운영 DB, 현재 16일 기준 DB와 월별 화면 비교 DB는 유지했다. 작은 완료 결과/요청/manifest·239일 체결/타점 진단은 PC에 남고, 원본 재생·상세 DB 감사·새 비교 실행은 복원 후 source/hash 재검증이 필요하다. 입력 결합 714개는 PC 238개와 보관 원본 SHA 476개로 대조했고 완료 DB 119개의 hash는 진단 당시 원장과 일치했다. 접근 거부 과거 폴더는 제외했다. 원본 파일 합계와 전체 여유 변화는 구분한다. 기능 개발의 첫 후속으로 기존 실제 gate·위험 조회·주문 수명·시계 청산·checkpoint·일지 경로의 매수 2주→부분 청산→runner 재생성→잔여 체결→재생성/반복 조회·일지 재투영을 연결 검증했다. 주문 매수/매도 각 1개·상세 체결 3개·최종 포지션/활성 주문 0개·일지 재투영 새 체결 0개이며 새 1건 포함 관련 3모듈 47건 통과다. 회귀만 추가했고 운영 코드는 수정하지 않았다. 격리 브로커·같은 프로세스의 native 인메모리 SQLite/runner 재생성 검증이며 실제 프로세스·NAS 재시작·실제 모의계좌·장시간 운용은 미검증이다. 현재 코드/배포와 모의계좌 binding·후보·운영 명세의 읽기 전용 준비 점검이 다음 확인이다. 실제 주문은 별도 명시 범위와 기존 적격 gate를 유지하며 기능 시험 때문에 부적격 후보나 미정 기준을 승격하지 않는다. [완료 근거](../data/storage-cleanup/20261012-192538-completed-research/CLEANUP_REVIEW.md).

**TRAIN 타점 실패 사례·25bp 비교 준비(2026-10-12, 미커밋·미배포):** TRAIN 239일·7,848거래의 실제 보유 경로와 가격 분해를 완료했다. 보유 만료 7,333건·순손익 -1,737,232원, 돌파 강도/개장 후 시간 네 구간 모두 순손실이다. 관측창 완료 7,409건·공백/늦은 가용 시각 400건·전체 봉 미관측 39건, 양수 종가 뒤 최종 손실 3,558건이며 가상 청산 성과로 사용하지 않는다. 진입 다음 봉 시가 +138,995원·진입 모델 슬리피지 -271,096원, 일반 청산 시가 +3,350원·모델 슬리피지 -266,961원을 대조했다. 위험 청산 515건의 미확인 EXIT와 잔여를 보존했다. 기존 원장·입력·기준 보고서·과학적 구현을 유지했다. 새 7건 포함 관련 46건과 첫 2일/전체 239일 실제 진단을 통과했다. 새 회귀는 기존 Windows 경로에 등록했고 전체 catalog는 기존 뉴스 회귀 미등록 1개로 실패한 상태다. [완료 근거](../data/research/historical-development-results/expanded-training-diagnostics-20261012-v1/TRAINING_FAILURE_REVIEW.md). 돌파 여유만 25bp로 바꾸는 전체 TRAIN 239일·119개 요청을 사전 고정했으며 현재 미실행이다. 현재 C: 여유 42.71GiB 대비 새 결과 추정과 여유를 합친 약 59.44GiB가 필요해 공간 요건은 미통과다. 비교 실행 전 저장 공간을 확보·재검증하고 모든 날짜를 기존 native runner로 비교해야 한다. 25bp는 개발 가설이며 운영 타점·후보 기준에 채택하지 않았다. 순수 가격봉 타점/실행 연구이며 실시간 종목 선정·뉴스/테마·시장 상황·비중 배분을 포함한 전체 전략 수익성 검증과 구분한다.

**O11 TRAIN 239일 기준 평가 완료(2026-10-12):** 119요청·239일 전체를 평가했고 7,848거래·닫힌 거래 순손익 -1,801,369원·수익일 16/239일이다. 연속장 밖 체결 0건·종료 미청산 0일, 기존 TRAIN 12일 비교/기존 DB·입력/과학적 hash 보존을 대조했다. 관련 70건 통과다. [전체 결과](../data/research/historical-development-results/expanded-training-baseline-20261012-v1/BASELINE_EVALUATION_REVIEW.md)를 기준으로 다음은 전체 TRAIN 기준의 손익·비용·미청산을 근거로 실패 사례를 좁히고 소수 사전 고정 개선안을 비교하는 단계다. 실제 계좌/매체 비용·종일 완전성·후보 적격 기준 채택·독립 검증/final·같은 설정 Shadow는 남는다. TRAIN만으로 후보 승격이나 미정 기준 동결을 하지 않는다.

**O11 TRAIN 확대 입력·첫 묶음 시험(2026-10-12):** 고정 239일·9,952종목·일의 불변 입력과 119개 native 요청을 전부 준비하고 결합 파일 714개의 hash·기준 설정 보존을 재검증했다. 첫 2일은 67거래·순손익 -15,485원·연속장 밖 체결 0건·종료 미청산 0일이며 cache 2개와 기존 DB 보존을 확인했다. 관련 133건 통과다. [준비/시험 보고서](../data/research/historical-development-results/expanded-training-baseline-20261012-v1/PREPARATION_REVIEW.md)를 기준으로 다음은 전체 239일의 사전 고정 기준 평가다. 종일 완전성·실제 비용·기준 채택·독립 검증/final·Shadow는 남는다. Windows 교체 거부 세 건은 완료 입력 보존·진단 후 재개했지만 원인 해결로 판정하지 않는다. 입력/요청 준비 완료는 전체 성과·후보 적격성 승인과 구분한다.

**O11 TRAIN 확대 준비(2026-10-12):** 12개월 전체 239일·9,952종목·일을 고정하고 현재 원본 최소 연속쌍 제외 대조를 완료했다. 첫 VALIDATION 경계와 겹치는 결과일 한 사례는 제외 원장에 남겼다. 관련 CLI/경계 10건 통과이며 새 연구 실행은 없다. 로컬 실전 정산 131건을 미배정 296건과 분리해 관측 비용 근거를 확보했지만, 후보 계좌/매체·2024~2025년 비용·슬리피지 확인은 남는다. 다음은 불변 가격 입력 생성/바이트·투영 gate와 전체 날짜를 유지하는 제한된 순차 TRAIN 연결이다. 기존 2~20 월별 사례 요청 계약으로 확대 원장을 곧바로 실행할 수 없다. [검토 보고서](../data/research/historical-train-expansion-20261012-v1/EXPANSION_REVIEW.md)의 최소 입력 `READY`는 종일 완전성·final 적격성 승인과 구분한다.

| 범위 | 현재 남은 일 |
|---|---|
| 통합 소스의 검증 | 직전 제품 소스의 로컬 전체 범위·Actions 9개 job·NAS storage gate와 배포/health/source/예약 확인 완료. 미커밋 자동매매 연결 149건·후속 제어 53건·후보 파일 69건·PC 후보 준비/게시 126건은 각 단계의 관련 검사 통과이며 전체·PG·NAS 검증과 구분한다. 앱 수동·실제 장중·장시간 acceptance는 별도 범위다. |
| 자동 병합 gate | main 필수 검사는 Windows 전수·Disposable PostgreSQL·Required regression 3개로 확인했다. 관리자 예외 허용·strict=false·필수 리뷰 없음은 기존 설정이며 그대로 유지한다. 종합 gate는 선택된 외부 작업의 실패와 동일 소스 대조를 포함한다. |
| O12 녹화·Replay | 10/12 실제 60분 녹화·20:10 저장 결과, 등록 v3 baseline의 public operator Replay. 배포·preflight·예약과 고정 저장본 검증은 완료 기록이 있다. |
| O12 성능·보존 | P1 실제 10~20초 COMMIT 대기 개선, 전체 앱 2~5배 검증, NAS/PC 장시간·다른 PC의 연속성·복구 확인. |
| 원인 조사·저장 정책 | Windows 검사 directory rename 경합은 10/15 실제 호출과 관찰 프로세스 핸들 추적·통제 재현으로 원인을 확인하고 fixture Temp를 격리했다. 과거의 Documents 200회 중 4회 실패/AppData 200회 통과만으로 해결을 판정한 것은 아니다. 연구 파일 교체·다른 경로 ACL 오류와 다른 writer의 실패 완료·영속 보존 정책은 별도 잔여다. |
| 누락 품질 연결 | 분봉·저장·NAS 읽기·합계/차트/판단에 품질 전파와 실제 저장 ACK 복구를 구현하고 로컬 관련 90 / 136 / 151건 통과. 실제 NAS/PG·장시간 검증은 남는다. 구형 서버·불충분한 빈 분 근거는 복구 대기를 유지한다. |
| 업무 상태 관측 | health의 수신·저장 ACK/실패·대기/실제 저장 중·후보군 현재 만료 구현과 관련 92건·배포 후 실제 health 응답 확인 완료. 최초 enqueue 대기와 뉴스/외부시장/다른 writer·모든 판단 입력의 종합 관측은 후속 범위다. |
| 과거 자료·연구 | D01/D02 봉·뉴스 확보와 공백 확인, D03 연구 입력 연결·품질, D04 테마 제안 품질, D06 사람 검토·학습 입력, O21/O22 공급 공백·사건 검토. 항목별 범위는 아래 ID 원장을 따른다. |
| 기능별 운영 확인·보류 | 나머지 O/R 항목의 계좌·뉴스·시장 경계 실환경 확인과 연구 제약은 아래 원장에 유지한다. NAS 용량 상한·자동삭제 등 사용자 보류도 유지한다. |

후속 개선으로 빌드 버전 정보를 여러 곳에서 수동 유지하는 부담을 줄일 수 있다. **현재 빌드 확인 문자열·이미지 태그 불일치는 수정 완료**이며 미완료 결함으로 등록하지 않는다.

`/health` 부분 관측은 구현 완료다. 보유 레코드 시각을 최초 queue 대기 시간으로 보거나 마지막 평가 READY를 현재 모든 입력의 최신 상태로 해석하지 않는다. 모의 상태의 `mock_owner._bundles`·`monitor._task` 직접 읽기는 기존 `account_bindings()`와 공개 `is_started`/`has_started_monitor()`로 정리했다. 원래 TestClient HTTP 3건을 포함한 전체 로컬 검증도 통과했다. 나머지 내부 접근은 반복 사용 또는 실제 수정 필요가 생긴 경계에서 검토하며, 운영 검증의 잔여는 CURRENT_STATUS 표를 따른다.

후보 감시 오류 저장의 종료 대기는 로컬 재현·최소 수정·관련 35건 검증까지 완료했다. 실제 운영 장애로 단정하거나 다른 writer의 실패 보존 정책까지 완료로 처리하지 않는다.

**O03/O12 TOP20 후속(2026-10-12):** 10/1·2·6·7 오전 180분 전체가 NAS에 partial로 남아 11시대부터만 그래프에 표시되는 경로와 complete-only 일별 합계를 확인했다. 순위 응답 대기 뒤 과거 요청 시각이 collector를 되감아 가짜 2초 초과 공백을 만드는 결함은 격리 재현·최소 수정·관련 107건 검증까지 완료했지만 미커밋·미배포다. 새 NAS 적용 뒤 같은 날짜/시각의 분 수·값·품질·그래프·일별 합계를 확인해야 한다. program/index 저장 await에 따른 관측 지연과 실제 체결 누락의 구분, 실제 저장 대기 소유자·원인 계측은 남는다. 과거 partial을 complete로 일괄 승격하지 않으며, 근거 기반 소급 복구와 부분/미수집을 구분하는 차트·통계 표시는 별도 후속이다. 상세 현재 결과는 CURRENT_STATUS의 TOP20 조사와 로컬 `data/diagnostics/top20-capture-rootcause.json`을 따른다.

**O03/O12 30초 체결 표시 멈춤 조사(2026-10-12):** PC 순위 적용 1,120회의 중앙값은 42ms, 최대 208ms였고 직접 수신과 NAS 모두 순위 갱신에 따른 전체 그룹 교체 REG를 확인했다. PC coordinator는 동일 종목의 순서만 바뀌어도 UPDATE를 반환한다. NAS 운영과 로컬 collector 소스 해시는 같으며 `_send_subscription`은 4개 그룹을 0.25초 간격으로 전송한 뒤 같은 루프에서 recv를 재개한다. 가짜 소켓 재현은 752ms로 수신 처리 대기를 확인했다. 이 결과는 키움의 체결 원본 누락이나 직접 수신에서의 실제 공백 길이를 증명하지 않는다. 기존 NAS 스트림의 무구독 수동 관찰은 1012 재시작 통지로 중단됐고 재시도는 502였다. 읽기 전용 operator 상태에서 별도 테스트 job `0fc5e879dfb3183b51279af54e87c66d`의 운영 일시 중지와 server_stopped를 확인했다. 순서 변경의 불필요한 재등록 제거·실제 구성 변경 때 유지 종목의 연속 수신·등록 대기와 recv 분리·ACK/누락/재시작 회귀·같은 장중 조건 전후 실측은 남는다. 이 조사에서는 운영 소스·구독 대상·배포·테스트 job을 변경하지 않았다. 근거는 `data/diagnostics/realtime-30s-registration-reproduction.json`, `realtime-30s-passive-observation.json`이다.

**O03/O12 차등 실시간 구독 보완(2026-10-12, 로컬 부분 수정):** 사용자가 기존 구독 유지와 편입·이탈만 추가/해제하는 변경을 승인했다. PC coordinator의 종목 순서 비교를 membership 집합 비교로 바꿔 순서만 변경될 때 UPDATE를 보내지 않는다. 프리장·정규장 새 재현의 실패 두 건을 확인한 뒤 구독·AppController·worker controller 3모듈 **43건 통과**, diff 공백 검사 통과다. 실행 앱 재시작·NAS 배포·커밋은 수행하지 않았다. 차등 REG/REMOVE와 등록 중 recv 병행은 아직 구현하지 않았다. 현재 NAS의 전체 그룹 수 기반 ACK 판정과 `top20-realtime-tape/v1`은 모든 그룹의 subscription_end 이후 전체 ACK를 요구하고 offline transport도 같은 완성 그룹을 재생·대조한다. 차등 등록에서는 실제 전송 수와 전체 희망 상태를 구분해야 하며, 병행 수신에서는 ACK가 subscription_end 전에 도착할 수 있다. 안정적인 item/group 배치·이탈 전후 200개 제한·REMOVE 실패·source 누적 기준점·종료/재시작·과거 v1 호환을 포함한 계약 설계가 남아 **Astra High 설계 검토로 handoff**한다. 현재 런타임은 `gpt-6.1-sol / High`로 확인됐으며 전환했다고 주장하지 않는다. 관련 경로·선택지·완료 조건은 `data/diagnostics/incremental-subscription-handoff.json`, 재현/회귀는 `realtime-order-retention-before.log`, `realtime-order-retention-after.log`에 보존했다. 이를 체감 멈춤 해결 완료로 보고하지 않는다.

**차등 구독 설계 인계 갱신(2026-10-12):** 위 Astra 검토는 [구독·승인·녹화 설계](INCREMENTAL_REALTIME_SUBSCRIPTION_DESIGN.md)로 확정했다. 안정적인 item/type/group 배치, REMOVE 성공 후 refresh=1 추가, 한 명령씩 승인 대기하면서 recv 지속, 실제 재연결만 connection_opened 발행, source 단위 기준점 초기화, 새 녹화 v2와 엄격한 v1 legacy 재생을 선택했다. 공식 응답 예제에는 grp_no가 없어 직렬 ACK 대응이 필요하며, 식별자 없는 임의 중복 ACK의 완전 판별은 보증할 수 없다. NAS 갱신의 connection_opened가 PC 전체 기준점을 초기화하는 연결도 확인했다. 이번에는 설계·문서와 공식 명세 근거만 추가했으며 차등 송수신 코드는 구현/배포하지 않았다. 당시 실행 기록 `gpt-6-astra / High`를 확인했고 구현·동시성 회귀는 **GPT-6 Sol / High**로 인계했다. 이후 후속 모델 선택은 2026-10-13 개정 [현행 모델 정책](MODEL_HANDOFF_POLICY.md)에 따라 **GPT-6.1 Sol / High**를 기본으로 한다. 앞선 43건은 순서 변경 수정의 검증일 뿐 새 설계의 검증이 아니다.

**차등 구독 로컬 구현 갱신(2026-10-12):** [구독 계약](INCREMENTAL_REALTIME_SUBSCRIPTION_DESIGN.md)의 PC/NAS 안정 그룹 배치·정확한 REMOVE→refresh=1 추가·recv/send 병행·직렬 승인과 timeout·새 source 허용·source별 기준점 초기화·v2 기록/native replay·엄격 v1 legacy 재생을 구현했다. NAS 상류 갱신뿐 아니라 같은 PC socket의 client 재구독도 connection_opened를 반복하지 않고, NAS에 이미 활성화된 신규 종목의 옛 PC 기준점은 요청 변경 전에 초기화한다. 관련 source 회귀 **105건**, 최신 희망 재변경·해제 전용·취소/그룹/장애전환 **42건**, 조건검색·클라이언트 ASGI·자격 수명 **74건**이 통과했다. 실행 수량은 서로 겹치므로 합산하지 않는다. 유지 0B는 보류된 REMOVE send 완료 전에 dispatch됐으며 격리 PC 0.190ms/NAS 0.364ms는 운영/UI 지연이나 원본 무누락 증거가 아니다. 최초 테스트 보조 경로 누락·테스트 이름 오타는 수정 후 실제 재실행했다. 전체 API 기준선은 이전 TOP20 API/schema 추가로 전후 동일하게 실패하지만 불변 WS 프레임·snapshot·태스크 정리와 현재 소스 전후 ASGI 결과는 일치했다. 새 두 테스트는 필수 pc-lifecycle에 등록했고 core 순서/fingerprint는 유지했다. 전체 catalog는 다른 작업의 `test_seed_news_jobs_from_nas_rankings` 미등록이 남는다. 현재 NAS는 별도 작업의 `2026.10.12-recorder-account-index-v3-875421f5e5e3fb26`, health=ok/trace=running이며 diagnostics_enabled로 교체가 제한된다. 이 작업은 미커밋·미배포·앱 미재시작이고 NAS PG/sealed gate·신규 PC/NAS의 실제 장중 연속성과 체감은 남는다. 녹화를 종료하거나 오래된 partial을 complete로 승격하지 않았다. 실행 기록 gpt-6.1-sol/High, 모델 전환 없음이다. 상세 실패/통과 로그와 읽기 대조는 계약 문서 끝을 따른다.

## 항목별 완료 조건

아래 37개 ID와 완료 조건은 기존 원장에서 승계했다. 수집 수량·표본·기능별 운영 상태는 해당 기록 시점의 값이며 이번 정리에서 새로 측정하지 않았다. 특히 D01/D02/D06/O19~O22/R의 오래된 표본은 실자료 원장과 재대조한 뒤 갱신한다. 상단 요약과 충돌하는 과거 검증·배포 표현은 상단과 CURRENT_STATUS를 우선한다.

### 자료 확보와 개발

| ID | 상태 | 작업·완료 조건 |
|---|---|---|
| D01 | 진행 중 | 삼성전자 1분 최과거→이전 5분 최과거 연속조회·최신 보강·일 단위 경계·구간 종료시각·NAS 스냅샷 완료. 후보 전체 시세 재개 원장 운영 중. 숫자 전용 필터에서 빠졌던 영문 포함 6자리 후보 91개를 원장에 추가했고, 전체 수집도 1분 최과거일 전날을 5분 종료일로 보내 중첩 다운로드를 제거함. 현재 개별 주식 2,637개 중 분봉 완료 2,617개·실패 20개이며, 이전 수집분 중 비주식 308개는 원본을 보존하고 수집 범위에서 제외했다. 남은 주식 공백과 NAS 최신 스냅샷 반영은 O21에 기록 |
| D02 | 진행 중 | 날짜 지정 네이버 웹 검색, 원응답·기사-종목 연결·원문 URL별 시도·발행시각 상태 저장, 당시 상호+변경일 ±14일 구·신 이름 작업 95,090개와 NAS 스냅샷 완료. 검색 403/429는 성공 페이지를 유지하고 막힌 페이지만 공통 제한 대기 후 재시도함. 작업 실제 소진·2년/5년 도달·이용 범위 확인 |
| D03 | 부분 구현 / 실행 검증 중 | `historical_reconstruction/v1`과 기존 1분 전략 runner용 어댑터 구현. 후보일 장후 선택→다음 거래일 결과, 실제 수집시각/복원 clock, 사후 후보/당시 TOP20을 분리하고 순위 Factor를 차단. 2024~2026 세 날짜의 개별 주식 파생 입력에서 연속 1분봉 쌍이 없는 종목(`000545`, `019175`, OOS의 `012860`)은 사용자 결정에 따라 제외하고 원본 후보·봉과 파생 manifest의 제외 원장은 보존했다. 첫 개발 입력은 TRAIN 41/41, VALIDATION 44/44 최소 연속쌍 조건으로 `READY`이며 OOS는 봉인했다. 2024-09~2025-12 후보일 321일 중 모든 후보가 최소 조건을 충족한 날은 150일이고, 종목·결과일 단위로 불충족 307/13,632개만 제외하면 321일 모두 적어도 한 후보가 남는다. 제외 투영 원장은 원본 감사의 SHA-256과 제외 코드를 고정했다. 기존 150일 6,327종목·일의 종일 봉 개수 감사에서 263종목·일이 300봉 미만이었고 원인은 봉 개수만으로 확정하지 않았다. 이는 종일 완전성·대표성·수익성 검증이 아니다. 512MB RSS 한도 중단 뒤 1GB에서 평가를 완료했으나 정각 봉 종료→다음 봉 시작 검증의 1분 오차를 발견해 기존 결과는 비교에서 제외했다. 경계 계산 수정 후 첫 TRAIN·VALIDATION 돌파·눌림 네 실행이 모두 `ELIGIBLE`·자료 품질 `PASS`로 완료됐고 OOS는 봉인했다. 후속 월별 16개 날짜의 개발 파생 후보 657종목·일과 제외 15건을 사전 원장과 대조했다. 다기간 독립 TRAIN 488/488·VALIDATION 169/169 최소 연속쌍 gate는 `READY`이며 OOS는 입력에서 제외했다. 동결 월별 657종목·결과일의 추가 감사에서 300봉 미만 22건·10분 초과 간격이 있는 종목·일 15건(실제 간격 26구간)을 진단 원장에 기록했다. 8구간은 분봉 합계와 키움 일봉 거래량 일치, 2구간은 키움 분봉에도 사이 봉 없음, 1구간은 공식 VI와 겹쳤다. CREON 원응답과 저장 DB는 15종목·일 모두 시각·거래량이 일치해 26구간은 저장 누락이 아닌 원응답 공백이다. 남은 15구간의 무거래·공급자 누락 여부는 직접 확정하지 못했고 어느 구간도 자동 제외로 처리하지 않았다. 뉴스 백필이 진행 중이고 종일 봉 완전성·사후 후보 대표성은 확인 전이므로 이 구조 스냅샷을 최종 성과 비교에 승인하지 않음 |
| D04 | 부분 구현 | 프로필별 별칭·분리 확장·재병합 금지 원장과 뉴스 AI의 근거 있는 원시 테마 후보, 기사 제목·발행시각·원문을 포함한 활성 프로필 검토, 사용자 승인·거절·이름 수정 원장, 반복 제안 승인 중 최신 별칭 재해석, 파일 백업·NAS 전체 스냅샷 보존을 구현. 실제 기사 제안 품질·의미별 병합 정확도, 과거 사건 일괄 후보 생성과 검토량 운영은 남음. 동일 지역의 다른 사업은 지역명만으로 합치지 않음 |
| D05 | 이후 개선 | 사용자 학습·사례를 반영한 타점 정책 비교. 현재 기준을 최종 전략으로 확정하지 않음 |
| D06 | 부분 구현 | 첫 50사례와 고유 기사 1,422개를 동결하고, 앱의 105행 사람 검토·불변 판정·사건 단위 시간순 분할·OOS 제외 공통 개발 입력, 정답 없는 블라인드 요청, 방법별 완전 응답 결과와 사건 군집·테마 채점 계약까지 구현함. 현재 사람 판정은 0건이라 실제 입력·요청·결과·평가는 없음. 실제 검토와 첫 입력 생성, RAG·미세조정 실행기, 별도 관련성 음성 표본, 비교 결과 검토, 로컬 LLM/Mac 작업자와 행동 EV 범위 결정이 남음 |
| D07 | 지수·VI NAS 게시 완료 / 개별 주식 3코드 공백 | 지수 1분·5분·일봉과 사용자 KRX VI 476,626건을 확보하고 시장 맥락 DB·원응답을 `historical-market-context/v1` 불변 run에 게시해 NAS DB 해시를 검증했다. 원래 후보 2,945코드 중 코스피·코스닥 개별 주식 2,637개를 시총·주식수·수정주가 대상으로 확정했다. 각 작업은 2,634개 완료·3개(`008290`, `046070`, `082660`) CREON 코드 거부이며 ETF 184·ETN 119·리츠 5개는 제외 상태다. 과거 값 단위·후보일 누락과 연구 입력 연결은 남는다. 기존 NAS 실시간 `dstr_stk` 스키마는 변경하지 않는다 |

### 운영·정확성

**O11 사전 고정 TRAIN 가설 비교(2026-10-12):** 12일 기준 native 원장을 재사용하고 5분 보유/10bp 돌파 여유 두 가설의 새 독립 연구 24회를 완료했다. 기준 396건·-82,706원·수익 2/12일, 5분 692건·-188,900원·1/12일, 10bp 392건·-62,750원·0/12일이며 종료 미청산은 모두 0일이다. 5분은 개선 가설을 지지하지 않았고 10bp의 손실 감소도 수익성/후보 적격을 확인한 것은 아니다. 동일 날짜·입력·비용·구현 대조와 기존 기준 파일 SHA 보존을 완료했고 모든 결과를 별도 연구 DB·불변 JSON·한국어 보고서에 남겼다. 실제 비용·더 넓은 고정 개발 표본/입력 완전성·진입 조건 검증·기준 채택·미사용 final/Shadow는 남는다. 조건을 추가 검색하거나 운영 전략·후보·주문·배포를 변경하지 않았다.

**O11 TRAIN 돌파 경로 진단(2026-10-12):** 동결 개발 전체 바이트를 검증하되 TRAIN 돌파 native 원장만 분석하는 CLI와 실제 396거래의 불변 JSON·한국어 보고서를 추가했다. 새 7건 포함 관련 144건 통과. 1/3/5/10분 경로는 실제 보유 중 관측된 완료봉으로 한정하며 위험 청산 봉·종료 뒤 가격·공백은 제외/미확인 상태로 남긴다. 양수 종가 관측 뒤 순손실 179건, 관측창 봉 수 일치 379건이다. 다음 봉 시가 기여와 모델 슬리피지를 분리했지만 실제 비용·입력 완전성·슬리피지 실측은 미승인이다. 짧은 보유시간은 재진입·마감 진입 예산까지 바꾸므로 가상 경로 청산 대신 기존 runner의 전체 TRAIN 가설 비교가 필요하다. 운영 전략·기준·final/OOS·주문·배포는 변경하지 않았다.

**O11 돌파 손실 가격 분해(2026-10-12):** 고정 개발 원장만 읽는 `--inspect-breakout-losses`와 로컬 한국어 진단/불변 JSON을 추가했다. VALIDATION 132건의 체결가 손실 -19,109원·수수료 4,056원·세금 27,806원·순손익 -50,971원, 125건 보유 만료의 판단 사이 가격 이동 -9,195원·청산 판단→체결 -8,177원을 확인했다. 위험 청산 7건에는 EXIT 가격을 만들지 않았다. 가격 분해와 사전 고정 시간·사유 집계는 기술적 진단이며 특정 보유시간/진입조건의 인과·개선 성과가 아니다. 09–10시 VALIDATION의 양수 결과도 TRAIN의 같은 구간 손실과 함께 보존해 시간 선별 후보로 채택하지 않았다. 기존 DB·요청·결과·준비 보고서의 SHA와 과학적 hash를 유지했다. 다음은 TRAIN의 보유 중 경로·돌파 강도·다음 봉 체결을 확인하고 소수 사전 고정 가설을 비교하는 단계다. 실제 비용·입력 완전성·기준 채택·final/Shadow·실환경은 남으며 OOS·주문·배포는 실행하지 않았다.

**O11 동결 준비와 기준 초안(2026-10-12):** 읽기 전용 PC/CLI 준비 점검과 관련 137건·추가 변경 후 45건을 검증했다. 개발의 실제 끝 시각 이전 final 범위·잘못된 기준·고정 요청 설정 불일치는 거절하며, final 자료·미사용 원장·성과·Shadow 승인은 수행하지 않는다. [검토용 초안](MOCK_CANDIDATE_CRITERIA_PROPOSAL.md)의 100건·20운용일·양수 손익·5% 낙폭·연속 미사용 40거래일은 미채택 상태다. 실제 기준 파일/입력에는 적용하지 않았다. 현재 돌파 검증 4일 모두 순손실이며 수수료/세금 이전 체결가 손익도 음수라 개발 손실 원인·후보 개선을 먼저 검토한다. 실제 비용·입력 완전성·초안 채택과 구체 final 범위·자료·미사용 확인·같은 설정 Shadow·실계좌 주문 검증은 남는다.

**D03/O11 비용·공백 근거 대조(2026-10-12):** 고정 자료 바이트에 묶인 비용 참고 파일과 기존 원응답 감사를 PC/CLI에서 선택 대조한다. 2024년 4일 18bp·2025년 12일 15bp의 당시 매도세 참고값은 기존 현재 비용 시나리오 20bp와 16일 모두 다르다. 계좌·매체·할인·슬리피지는 미검증이며 R07의 실제 비용 승인은 남는다. 새 입력의 긴 내부 공백 18구간은 과거 감사와 18/18 일치하지만 해당 9종목·일의 로컬 원응답 파일은 없어 현재 바이트 대조는 0건이다. 과거 감사의 원응답·저장 일치 판정을 현재 입력의 무거래·완전성으로 승계하지 않는다. 관련 5모듈 95건 통과, 기존 64회 결과·요청·DB·정규장 과학적 구현 hash는 보존했다. 입력 완전성 승인·실제 기준 4개·final 범위와 같은 설정 Shadow·실제 broker 마감 체결 검증이 남는다. OOS·주문·NAS 배포는 수행하지 않았다.

| ID | 상태 | 작업·완료 조건 |
|---|---|---|
| O01 | 최신 소스 배포 확인 / 개별 기능 운영 잔여 | CURRENT_STATUS의 제품 SHA·실제 release·health/source 대조 완료. 시총 복원·계좌 이름·구형 계좌 해제의 개별 실환경 확인은 별도다. 과거 market-cap-reference-v1을 최신 목표 빌드로 재배포하지 않는다. |
| O02 | 운영 미확인 | PC 재시작 공유설정 불필요 POST, NAS 복귀 분봉 조회·API 문구·직접 시총 캐시, proxy 재생성 후 HTTPS/WSS 확인 |
| O03 | 저장 경계 실측 / 공급 신선도 관찰 중 | 2026-09-23 09:28:30 회차의 `ka00198`는 전송 73ms·큐 대기 3,461ms였다. 09:28:22.111 `ka10001` 응답 이후 다음 Kiwoom 완료 로그까지 11.5초 공백이 있고, 기존 브로커는 응답 기록·쿼리 캐시 저장을 마칠 때까지 단일 전송 worker를 점유했다. 같은 구간의 정확한 후처리 단계는 당시 로그만으로 특정할 수 없다. 별도 직렬 저장 worker·단계별 계측 배포 후 `/health.server_build=2026.09.23-ranking-broker-persistence-v1`을 확인했다. 10:05~10:07 회차의 `ka00198` 큐 대기는 0~5ms 수준이었고, 동시에 `ka10001` 캐시 저장 9,205ms 경고가 남아 기존 저장 병목의 실재를 확인했다. 10:06:00 순위는 10:06:02.765까지 직전 회차를 반환하고 10:06:03.559에 최신 20행이 관측됐다. 공급 응답이 갱신되는 정확한 시각과 재조회 간격의 효과는 후속 관찰 범위이며, 최신 회차 전에는 직전·부분 순위를 새 순위로 공개하지 않는다 |
| O04 | 운영 미확인 | PostgreSQL 다중 연결에서 lease·STOP·시작·인증 교체·늦은 writer·CAS·프로세스 강제종료 복구. 기본 schema checker와 별도 |
| O05 | 운영 미확인 | NAS vault Linux 권한·보호키/암호문 별도 백업·복원, 재발급 token 영향·만료 계좌 UNKNOWN 처리 |
| O06 | 운영 미확인 | 실전/모의 복수 계좌 REG·00/04·9201 격리, 조회 표본/빈 표/거래소 잔고 의미, 후착 응답·동일 주문번호 분리 |
| O07 | 미구현/운영 미확인 | 서버 역할 API는 구현됐으나 PC 시세 담당 선택 UI는 연결 필요. 장중 인증/역할 교체·첫 frame·실제 gap·순위 기한·표 보존·장애전환 실검증 |
| O08 | 운영 미확인 | 실제 NAVER/DART/AI 키·IP·예산·캐시·기사/job 보존과 실패격리. AI ACTIVE가 유료 분석 성공을 보증하지 않음 |
| O09 | 운영 미확인 | 조건검색 ACK/재등록/장후 지원, 해외시세 실제 cycle 중 ON/OFF·주기·월물 전환 |
| O10 | 운영 미확인 | 뉴스 revision·usage·대상 병합·읽기만 할 때 예산/job 불증가, query-set 7일 품질·저장량, 사람 정답셋에 대한 의미 정확도. 네이버 증권 FLASH/WORLD의 NAS 빌드 반영·실제 cursor 증가·중복/누락·BODY 부하, 과거 날짜 전체 도달범위와 연구 입력 연결은 별도 확인 |
| O11 | 돌파 연결·제어·PC 후보 준비/게시·개발 원장 확인 구현 / 실제 후보·운영 미확인 | 돌파 판단→주문→체결→청산→일지·재시작·미체결·UNKNOWN·영속 중지와 비용 실패 복구를 가짜 브로커 통합으로 확인했고 당시 149건 통과. 후속 제어 화면 53건·후보 파일 준비 69건·PC 기준 동결/후보 검토/게시 조작 126건·개발 비교 64건 통과. 미청산 원장 추적과 현 엔진·실제 runner의 자료 공백 재현, 후속 보유종목 연속 봉의 정상 청산, 진단·화면 회귀 96건 통과. PC는 적격 후보만 게시하며 검토 파일·계좌·구현·최신 binding revision을 재검증하고 게시 완료는 목록 조회만 요청한다. 10/11 NAS 읽기 기록은 모의계좌 1개·binding 있음, 게시 후보/명세/Shadow 사건 0개·Shadow 활성·수동 모드다. 감사한 연구 DB 21개 중 읽힌 20개에는 final 기록이 없고 1개는 미확인이다. 월별 개발 네 실행은 현재 구현 hash와 다르다. 세 미청산 실행은 첫날 마감 청산 요청 뒤 원장에 보유종목 후속 관측·체결 근거가 없고 다음 월 다른 종목은 보유 상태로 HOLD했음을 확인했다. 원본 TRAIN 입력은 사용자 환경에서도 접근 거부이므로 원본 공백 발생 원인·전체 동결 입력 재평가는 미확인이다. 기존 16개 결과일·657종목·일의 독립 일별 계획과 자료 확인 후 요청 생성은 구현했고 관련 58건이 통과했다. 기존 개발 입력은 사용자 환경에서도 접근 거부지만 10/12 접근 가능한 NAS 게시 스냅샷에서 OOS 없는 새 원본·어댑터를 보존했다. 고정 16일·657종목·일·제외 15건과 최소 연속쌍을 대조하고 현재 구현으로 두 Family 총 32회 일별 평가를 완료했다. native 전략·비용·기간·구현 일치를 확인했다. 돌파 530건·종료 미청산 15일·VALIDATION 중앙값 -10,601.5원, 눌림 70건·미청산 1일·중앙값 -104원이다. 독립 계좌를 합치거나 미청산을 보정하지 않았으며 후보 승격은 없다. UNC URI·불변 읽기·봉 날짜 검색 보완의 관련 37건이 통과했다. 로컬 과거 수집 DB의 봉 테이블 0건은 확인했지만 원인은 미확인이므로 별도 조사 대상으로 남긴다. 읽기 전용 진입·비용·청산 손실 분해와 관련 회귀 59건은 완료했다. 돌파 검증의 보유시간 만료 125건 순손실 48,779원·눌림 손절 10건 순손실 9,107원이며 마감 미청산 16건 모두 보유한도 만료가 15:20 이상이었다. 선택 정규장 시간 정책의 공통 진입 예산·진입 거래일 기준 청산·실제 체결 경계는 구현했고 현재 `0d2393656b3b…`의 같은 입력 비교 64회와 관련 회귀 179건을 완료했다. 기존 정책은 직전 원장과 32/32회 일치하며 새 정책은 돌파 완료 거래 528건·종료 미청산 0일, 눌림 70건·0일이다. VALIDATION 닫힌 거래 순손익은 돌파 -50,971원·눌림 -9,160원, 적격 일별 중앙값은 각각 -10601.5원·-104.0원으로 수익성 개선은 확인되지 않았다. 정규 연속장 밖 체결은 0건이며 새 입력의 마감 청산 판단은 0건이므로 늦은 진입 차단 효과와 구분한다. 전체 테스트 등록 검사의 기존 별도 뉴스 모듈 미등록은 별도 보류다. 독립 시계 청산 판단·미체결 단발 취소·영속 중지/명시 복구는 로컬 구현했고 새 19건 포함 관련 10모듈 117건이 통과했다. 신선한 원본 가격만 기존 O1 gate로 제출하며 UNKNOWN·부분/늦은 체결·취소 직전 체결·재시작·두 번째 포지션·종료 drain을 확인했다. 실제 broker 지연·가격 신선도·마감 전 체결 성공은 미확인이다. 후속 PC/CLI 일별 비용·입력 점검과 새 12건 포함 관련 회귀 82건을 완료했다. 실제 657종목·일 중 연속장 미관측 분이 있는 270건·내부 공백 3,277구간·10분 이상 미관측 내부 18구간을 고정 입력에서 확인했고 자동 제외하지 않았다. 기존 최소 연속쌍 16일 READY와 종일 완전성은 구분한다. 합의한 비용 모델과 유효기간 포함은 확인했지만 실제 broker 비용 근거·공백 원인은 미확인이다. 프리/애프터 별도 정책·실제 비용·입력 완전성·실제 기준 4개·후보·final 기간 결정, final 완료·같은 설정 Shadow 근거, 앱 수동·실제 모의계좌·NAS·PG·장시간 검증이 남는다. 구체적인 승인 범위가 있을 때 제한 모의 주문·체결·취소·UNKNOWN·비용·A5 대조를 수행한다. 과거 1주 시험 계획은 새 주문 권한이 아님 |
| O12 | 운영·Replay·성능 검증 진행 | 10/12 실제 60분 녹화·20:10 저장 결과, 등록 v3 baseline의 public operator Replay, P1 실제 COMMIT 대기 원인/개선·전체 앱 성능/자료 보존 비교. NAS/PC 24시간 이상, 연구 ON/OFF, CPU/RSS/디스크/DB/queue와 순위·초/분봉 연속성·복구, 다른 PC API/WS/일지 동기화를 확인한다. 배포/preflight/고정 저장본 검증 완료와 이 잔여를 구분한다. 과거 wait·장치 실측은 보존 기록에 남긴다. |
| O13 | 미구현 범위 포함 | Shadow producer는 현재 breakout 경로. 다른 Family의 READY/자동운용은 producer 지원·평가가 필요 |
| O14 | 미구현 | A4B 직접/fallback 계좌 신원 1~5단계: DPAPI 지문·고정자격·프로세스간 한도, HTTPS resolve/aliases, fresh REST batch, 9201 직접 WS, offline-origin 결합과 두 PC 검증. NAS R6 완료와 별개 |
| O15 | 미구현/운영 미확인 | S2b VI 대상 호가 snapshot/parser/storage, 실제 phase/FID·애프터 적격·누적 reset·조건/지수/비용·TR 범위 |
| O16 | 운영 미확인 | 15:20/15:30/15:40/16:00/20:00/20:05 경계, 정규종가/전체일 coverage·VI/cohort·다음 거래일 만료·0g 전환 표시 |
| O17 | 운영 미확인/미결정 | SOR 원문·source 전환·partial·통합 차트·과거 오표기 범위. 근거 없이 과거 KRX를 일괄 수정하지 않음. 200종목 초과 수집범위와 통합 연구전략은 별도 |
| O18 | 재현 필요 | 과거 장중 점검의 상한가 `effective_at`/`available_at` 약 9시간 차이. naive KST/Linux 시간대 경로 확인 후 원인·수정 범위 결정. 참조된 JSON 원자료가 현재 보고서 폴더에 없음 |
| O19 | 제한 병렬화 운영 시작 / 별도 프로세스 설계·장시간 검증 필요 | NAS 뉴스 수집·BODY·RULE·AI를 서버 API와 독립 프로세스로 분리하는 작업은 미완료다. 기존 단일 작업기는 BODY 분당 8~10건, 대기 최대 약 43시간, RULE 기아를 보였다. 새 서버 빌드에서는 같은 Uvicorn 프로세스 안에 BODY 2·RULE 선호 1 lane의 제한 병렬화와 중복 claim 방지를 적용했고 첫 1분에 BODY 11건·RULE 5건을 확인했다. 장시간 처리량·DB/CPU wait·순위 지연은 미검증이다. 독립 worker는 vault 단일 소유·키 교체 전달·PostgreSQL job claim·설정 즉시 반영·중복 수집 방지를 정한 뒤 구성한다. |
| O20 | 중복 진입 방지 구현 / 장시간 재검증 대기 | PC 과거 종목 뉴스 수집기 두 개가 동시에 실행돼 공용 `.news-job-heartbeat.json.tmp`에서 `WinError 32`가 발생한 기록이 있다. PowerShell 수집기 시작 전에 배타적 파일 잠금을 잡고, 중복 실행은 상태·STOP 파일을 건드리지 않고 종료한다. Python heartbeat 임시 파일은 PID·UUID별 고유 경로를 사용한다. 잠금을 선점한 재실행이 스킵되는 동작과 동시 heartbeat 쓰기의 JSON/임시 파일 정리를 검증했으며 현재 실행 중인 구 프로세스의 재시작 뒤 장시간 운영 검증은 남았다. |
| O21 | 개별 주식 20코드 공백 / NAS 반영 대기 | 원래 실패 139개를 관리자 CREON에 재조회해 122개 코드 거부와 17개 1분봉 0건을 재현했다. 122개 중 ETN 119개는 `A` 접두사 요청 오류였으나 개별 주식 수집 범위 밖이다. 코스피·코스닥 개별 주식 2,637개 기준 분봉 완료 2,617·실패 20이다. 거부 3개(`008290`, `046070`, `082660`)는 현재 CREON 코드 목록에도 없으며 정확한 목록 제외 사유·대체 원천 확인이 남는다. 1분봉 0건 17개 중 16개는 과거 5분봉을 확보했다. 이전 표적 재조회로 17개 종목의 5분봉 661,048행을 로컬에 추가했고, 주식 기준 5분봉 보유는 2,448개다. 비주식 원응답·기존 봉은 삭제하지 않고 `excluded`로 보존한다. 로컬 갱신분의 NAS 불변 스냅샷 게시와 연구 입력의 비주식 필터 확인이 남는다 |
| O22 | 거래소 원문 효력일 대조 완료 / 정정·상충 사례 검토 | 개별 주식 2,637개의 CREON 수정계수 사건, 상장일, 키움 일봉의 장기 거래량 0 전환·재개일, 상폐 검토 대상의 마지막 거래일에서 4,587개 사건 단서를 만들었다. 기존 DART 수정계수 조회 1,993건을 재사용하고 새 조회는 완료 2,531·공식 빈 응답 63건으로 종결했다. 거래량 0은 거래정지 확정이 아닌 조회 단서다. 짧은 거래정지 누락을 줄이기 위한 2,637종목 전체 DART 거래소 유형 I 공시 조회도 완료 2,620·빈 응답 17건, 공시 332,723건으로 종결했고 우선주 2건의 발행회사 매핑을 재검증했다. DB 무결성 `ok`, 새 DART ZIP 4,215개 해시 일치, NAS 최신 run `20260924T130303Z-2b6cc96dd69a`의 DB SHA-256·원응답 18,449개·latest 전환을 확인했다. 기존 시장 맥락 공급 불가 6건은 게시 manifest에 남고, 거래소 제출 관련 공시 원문 4,220건을 추가 확인해 명시 효력일 3,083건(정지·재개·상폐)을 접수일과 분리했고, 일봉 대조에서 접수일과 다른 효력일 1,562건을 확인했다. 원문 5건은 DART 014로 받을 수 없고, 상폐일 뒤 거래가 나타난 1건과 효력일보다 늦게 접수된 1건은 검토 상태로 보존한다. 월별 16개 TRAIN/VALIDATION 동반 자료에 겹치는 사건 6건을 반영했으나 장중 공시 수신시각·정정 계보가 미확정이라 전략 신호/거래 가능 판정에는 자동 사용하지 않는다. 새 NAS 불변 게시본의 검증 결과는 CURRENT_STATUS를 따른다 |

### TOP20 장후 재계산 — O03/O12 후속

2026-10-12 [구현 계약](TOP20_AFTER_CLOSE_RECONCILIATION.md)에 따른 장중/장후 표시 분리·100%/50% 배분·원본 보존·날짜별 원자 게시·v2 분봉 완료 근거·display API/cache·08시부터 미확인 표시·부분 일별 합계 처리를 로컬 구현했다. 최종 기능·종료·녹화·계좌 경계 회귀 **395건 통과**, 마지막 화면/클라이언트 수정 후 **10건 재검증 통과**. PostgreSQL 직접 연결 감사는 통과했다. 과거 10/1·2·6·7·8 저장 HTTP 입력의 읽기 대조는 각각 정규장 302/326/326/322/316분까지 계산 가능했으며 전일 합계는 null이다. 현재 API로 v2 상세 receipt를 얻지 못해 빈 분을 0으로 승격하지 않았다. 독립 PostgreSQL 입력 snapshot·게시 rollback·동시 cache 검사와 실제 NAS 적용·장중/장후 acceptance·과거일 게시가 남는다. PG gate는 실행 전 `operation_busy`; 기존 월요일 녹화 예약이 `armed`이며 잠금을 유지한다. 예약·제어·운영 DB/release는 변경하지 않았다. 별도 동시 개발의 mock runner consumer 4개와 뉴스 테스트 미등록으로 전체 감사/catalog는 통과 전이다. 최초 실패 기록과 검증 범위는 CURRENT_STATUS를 따른다.

### 연구의 제약과 후속

| ID | 상태 | 작업·완료 조건 |
|---|---|---|
| R01 | 일별 평가기간 확장·NAS 새 날짜 자동 export와 빈 날짜 재확인 로컬 구현 / 실제 NAS 단기 검증 완료, 운영 검증 대기 | opt-in source는 새 거래일의 완성된 동결 입력을 발견하면 동일 전략·비용·예산으로 단일 TRAIN/VALIDATION fold를 이동해 별도 실험으로 등록한다. NAS 자동 준비와 함께 켜면 기준 단일 일별 입력 다음 날짜부터 완료 후 24시간이 지난 날짜를 하루씩 조회·동결·검증·등록하고 작업자 소유 cursor를 전진시킨다. 주말은 건너뛰고 NAS 관측 0건 평일은 v27 원장에 남겨 하루 뒤부터 재확인한다. 늦게 들어온 자료도 같은 불변 게시 경로로 등록한다. FINAL/OOS·범위/종목/세션 불일치·비용 유효기간 밖은 제외한다. 2026-09-25 실제 NAS 두 거래일의 90초 표본으로 격리된 동결·자동 등록을 확인했다. 운영 캠페인 source 미설정으로 24시간 전체 구간, 장시간 처리량·지연 자료 운영 검증은 남았다 |
| R02 | 신규 순차·final RUNNING 수동 회수 구현 / 기존 순차 무소유 run 보류 | 순차 개발 검증과 final batch의 마지막 고정 요청을 앱 상태 폴더에 원자 저장한다. 재시작 후 수동으로 이어서 실행하면 기존 연구 원장의 완료 결과를 확인·재사용한다. final의 명시 복구 요청은 저장 snapshot에서 제거해 재시작만으로 복구를 반복하지 않는다. 손상된 요청은 자동 실행하지 않는다. final 후보별 `NOT_STARTED/RUNNING/COMPLETED` 등 진행 snapshot은 원자 게시하고 실행 중 화면에 표시하되 종료 결과 전에는 복구·개발 사용을 허용하지 않는다. 두 화면의 자식 PID·시작 토큰·고정 요청 hash를 작업별 소유 기록으로 남기고 정상 종료 때 정리하며 `scripts/inspect_research_operation_receipts.py`로 생존·종료·확인 불가를 읽기 전용 판별한다. final UI는 고유 owner token을 갖고, `scripts/recover_final_holdout_orphan.py`가 종료된 자식·요청 hash·미완료 결과·산출물 부재를 확인한 뒤 `--execute`에서만 같은 owner/generation의 RUNNING 후보를 CANCELLED 처리한다. 순차 UI도 새 실행부터 v24 run owner 토큰·세대를 원자 claim하고, `scripts/recover_development_validation_orphan.py`가 같은 종료·요청·산출물 검증 후 해당 세대의 RUNNING만 수동 취소한다. 다음 순차 실행은 사용자가 같은 요청을 다시 시작할 때 새 owner·세대로 claim한다. v24 이전 무소유 RUNNING에는 소유 증거가 없어 자동 회수하지 않는다. |
| R03 | 후속 검토 | 자동 final 선택/실행·미등록 계산식 draft·뉴스/초자료/호가 신규 Family. 기존 자동 가설은 등록 Family/허용값 범위 |
| R04 | 운영 미확인 | 대규모 bundle·입력 전체 로드/RAM projection·결과 보관량. 현재 RSS/preflight·CPU 양보는 OS 강제 quota가 아님 |
| R05 | 범위 미확인 | 기업행동·상장/휴장/특별개장 기준정보의 역사 revision, 직접 TOP20 저장 선택과 coverage UI의 실제 제공 범위 |
| R06 | 모델 한계 | 현 연구 export의 T+1초 미지원, VI 호가·부분체결·시장충격 재현 불가, 미사용 검사가 현재 연구 DB footprint 범위에 한정 |
| R07 | 구조 실행 완료 / 실전비용 미검증 | 연구 `fixed_bps/v2`는 소수 bp 문자열을 정확히 받아 수수료 1.5bp를 양쪽에, 매도세 20bp를 매도에 적용한다. 기존 `fixed_bps/v1` 정수 계약과 과거 1bp+18bp 구조 요청은 보존하되 성과 근거로 재사용하지 않는다. D03 TRAIN·VALIDATION 네 구조 실행에서 v2 계약과 추정 슬리피지 5bp를 동일하게 적용했다. 각 분할은 하루이므로 정책 성과는 미확정이고 실제 브로커 비용 검증도 남는다 |
| R08 | 개별 주식 3코드 공백 / 사건 유형 검토 | 주도후보 원본 2,945코드에서 개별 주식 2,637개만 CREON 수정계수 작업 대상으로 유지한다. 2,634개 완료·3개 코드 거부이고 비주식 308개 작업은 `excluded`다. 기존에 발견한 사건 3,097건의 DART 분류 조회는 끝났지만 확정 989·수동 검토 984·미매칭 1,124이며 이 중 개별 주식은 2,106건(확정 987·수동 검토 980·미매칭 139), 비주식은 991건이다. 주도후보 2,637개 키움 수정 일봉의 NAS 재조회·종목별 누락 0 검증은 완료했다. 후보 연구 창과 겹친 CREON/DART 알려진 사건 보고서는 894종목·1,447건이며, 키움 고유 사건 누락 가능성과 수동 검토 667·미매칭 98건이 남는다 |

## 사용자가 보류한 것

**NAS 저장 용량 상한·자동삭제는 2026-09-21 결정에 따라 보류한다.** 현재 읽기 전용 용량 진단과 24시간 이상 미완성 연구 staging의 제한 정리를 완성 자료 자동삭제로 설명하지 않는다. 완성 연구자료의 삭제/압축/이동과 외부 일지 참조 전수 보호도 미구현이다.

재개 조건은 사용자의 재요청 또는 디스크 임계치 근접이며, 기존 결정대로 [보존 설계 기록](archive/2026-09-22/reports/NAS_STORAGE_RETENTION_DESIGN_REVIEW_20260921.md)의 보호 관계·삭제 순서·기간/용량 충돌을 **Astra Ultra 설계 재검토**로 확정한 뒤 구현한다. `0=무제한`과 원본/일지/연구 참조 보호를 유지한다. 이번 문서 이동은 앱 데이터 정리·삭제 정책의 실행이 아니다.

SOR 프리/애프터 시장가 주문 UX, 시장 수급/Open Space/Base/M/W/압축 화면은 별도 후속 제품 범위다. 일부 기반 모듈의 존재를 전체 UI 완료로 보지 않으며 현재 데이터 확보보다 앞세우지 않는다.

## 종합 명세 대조에서 확인한 보완 — 2026-10-15

[대조 감사](AI_TRADING_INTEGRATION_ALIGNMENT_AUDIT_20261014.md)의 A01~A05는 정책별 자료 요구·독립 가격 입력·현재 R4 양의 지원 판정·검증된 순자산 분모·STOPPED와 보호 권한 분리의 미완료/계약 차이다. A06/A07은 원문 단계 대응과 문서 정본 역할 정리다. 상세 근거·해소 조건은 감사 보고서 한 곳에 유지한다. 이번에는 실행 소스·계좌 권한·주문·배포를 변경하지 않았으며, 이후 구현은 현재 사용자가 정한 개발 순서와 관련 계약 안에서 진행한다.

**2026-10-15 후속:** A01의 현재 R4 정책·행동별 자료 요구와 A02의 독립 가격/조건부 점수/사용 구성요소 lease/테마 위험 소속 분리를 구현·검증했다([현재 상태](CURRENT_STATUS.md)). R4용 theme/news/turnover 추가 맥락 생산자와 미구현 전략 registry 확장은 후속이며 요구한 행동의 미생산 자료는 결측으로 남긴다. 사용자 지정에 따라 다음은 A03의 조건부 현재 모델 지원과 native 실행 적용 계약 판단/구현이며 A04는 뒤로 옮긴다. A03은 EV_UNAVAILABLE를 유지하고 [설계 재검토](AI_TRADING_SYSTEM_REDESIGN.md#a03-설계-재검토)가 필요하다. A04 순자산·A05 STOPPED/보호 권한·실제 모의계좌 통합/운용 승인도 미완료다. 감사 당시 기록과 후속 수정을 구분한다.

## 이번 범위 밖의 발견 — 2026-10-11

- 테스트 등록 검사에서 별도 뉴스 작업의 `tests.unit.test_seed_news_jobs_from_nas_rankings`가 미등록으로 확인됐다. 이 파일과 `scripts/seed_news_jobs_from_nas_rankings.py`, 과거 자료 기획의 기존 변경은 자동매매 작업에서 수정하지 않았다. 해당 뉴스 작업 완료 시 CI 실행 목록에 편입·등록 재검증이 필요하다.

## 근거와 종료된 작업

- [기존 항목별 상세 근거·후속 결정](archive/2026-10-11/history/OPEN_ITEMS_through_20261011.md) — 누락 품질 설계·원인 조사·신고가 수동 확인 잔여 포함.
- [리팩터링 종료 상태](../REFACTORING_CLOSEOUT_PLAN.md) — 종료된 단계를 다시 시작하지 않는다.
- [현재 검증 절차](DEPENDENCY_REFACTOR_VERIFICATION_PLAN.md).

새로 발견한 직접 관련 없는 문제는 ID·근거·남은 확인을 이 원장에 추가한다. 완료 근거와 실패 이력은 날짜별 기록으로 보존한다.

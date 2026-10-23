# 현재 구현 아키텍처

**현행/목표 구분(2026-10-23):** 이 문서의 구현 이력과 현재 책임은 보존한다. PC·NAS·공통 전체의 새 재평가와 목표 책임 배치·재사용/부분 교체·전환 계획은 [전체 목표 구조](docs/architecture/app-target-20261023.md)에 있으며 설계 승인 대기다. 현재 클래스 구성은 보존 조건이 아니고 새 owner는 아직 구현하지 않았다. 이번 평가는 오류·복구·기본 관측·보안·UX·개발/배포/복원 등 14개 품질 영역을 포함한다. 병목이 아직 해결되지 않았으므로 NAS 녹화·리플레이·정밀 진단은 현재 유지·사용한다. 이번 목표는 과거 뉴스·대신증권(CREON)의 일회성 수집/적재 도구까지 포함해 언제든 삭제 가능한 독립 경계를 만드는 것이며, 도구·운영 작업·적재 자료를 지금 삭제/중단하지 않는다. 자체 로컬 LLM 구축·학습·서빙 계획은 취소한다.

현재 allocation의 원래 facts/계좌·Risk cut·모델 게시와 EV/출구/study refs를 한 input_bundle에 동결한다. INPUTS_BOUND는 현재 입력·보고된 source refs의 연결이고 모델 독립성/현재 조건 지원/실제 운영 승인이 아니다. R4는 같은 ref를 사용하며 기존 amount/quantity/비중 arithmetic은 바꾸지 않는다. 원래 quote/account/model lease를 유지하고 expired 조회는 current bundle/goals를 함께 제거한다. 기존 selected latest 기록은 같은 transaction으로 운용하며 history를 새로 수집하지 않는다. 개발 종료는 세 고정 묶음/실제 수락·승인은 별도다. [86단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#86-개발-마무리-범위-고정과-공통-입력원본-근거-묶음)

이하 단계 설명은 해당 시점의 기록이다.

FrozenResearchDataset에 이미 있는 순위/테마를 실제 case cut에서 선택하고 native run identity 전에 실제 dataset 값과 대조한다. source header와 원래 case proof를 정확히 결합해 어느 branch만 다른 맥락을 사용했으면 비교 통계를 거절한다. 첫 게시에서 같은 context_ref를 local dict로 공유해 H1을 case별 한 번 재생하고 runtime에는 관측 summary만 공유한다. 기존 dataset raw 저장소/초봉/Collector를 새로 기록하거나 추정하지 않는다. 기존 연구 명세/metadata payload는 커지며 이는 명시 연구 감사의 비용이다. 현재 context conditional support/독립 계보/보호 정책 admission은 여전히 미지원이고 scientific hash 확장은 새로운 공통 계산 dependency를 정확히 나타낸다. [85단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#85-기존-연구-입력의-과거-순위테마-맥락-연결)

이하 단계 설명은 해당 시점의 기록이다.

상위 EV 원본 proof는 이미 같은 readonly transaction에서 읽은 record와 원래 선택 endpoint prefix를 명시 준비에서 동결한다. 첫 v2 게시에서 기존 native guard/값 adapter로 outcome와 요약 source receipt를 전체 재생 대조한다. 이후 runtime은 재생한 source windows로 만든 작은 reported lineage 진단을 공유한다. 선언 기간 중복은 실제 다른 원시 자료의 독립성을 반증하는 확정 증거가 아니라 보수적 겹침 진단이다. 비중 계산의 숫자와 R4 독립성 승인을 분리하며 lineage 두 상태 모두 independence_verifiedFalse다. raw revision/학습·선택·검증/custody와 historical exact context·native 보호/후속 정책 지원은 여전히 별도다. [84단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#84-상위-ev-원본-출처-재생과-r4-연구-중복-확인)

이하 단계 설명은 해당 시점의 기록이다.

Runner의 원가/최초 보유 시각은 기존 체결 원장에서 반영된 StrategyState이고 Risk의 owned_entry_prices와 같은 수량/가중 원가인지 확인한다. 동일 account observation을 선택 subtype으로 보강하며 lookup 시 TR/원장/DB를 호출하지 않는다. 계좌 epoch·등록 owner·state revision·fill writer·protection/session 복사 경계를 확인해 불일치 시 native facts를 버린다. Risk 보호 callback과 기존 실행 수명은 그대로다. 현재 사실의 provenance 확인은 과거 모델의 보호/실행 정책 지원 증명이 아니므로 R4 양의 행동은 여전히 미지원이다. [83단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#83-native-runner-체결보호-상태의-동일-계좌-ram-관측)

이하 단계 설명은 해당 시점의 기록이다.

v2 연구 근거는 원본 명세/report/events와 사전 cohort를 함께 PC readonly 준비해 기존 인증 게시/한 슬롯에 보관한다. NAS 최초 게시에서 native validation/통계를 재생하고 조회/동일 packet에서 반복하지 않는다. 기존 상위 비중/하위 목표와 계좌·ACK Risk는 한 관측을 공유한다. current_account_risk_bound는 신원/신선도 경계의 확인이며 보호 원가/기간·등록 후속 상태나 historical exact context/EV lineage 지원을 증명하지 않는다. 이 네 증명이 없으므로 현재 R4는 미확인이고 만료한 현재 goal binding은 조회에서 제거한다. 원래 모델 기한은 유지한다. [82단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#82-v2-연구-근거의-공통-ram-게시와-현재-목표risk-결합)

이하 단계 설명은 해당 시점의 기록이다.

각 native run 이전에 v2 cohort/case·행동 목록을 같은 spec에 동결한다. 실제 초기 포트폴리오/전략 상태와 source bar·원래 goal의 정확한 짝만 기존 R4 경험 산술로 비교한다. run별 entry_intent_id만 파생 경제 상태 비교에서 제외하며 원래 proof/ID는 보존한다. 하나라도 누락·미지원이면 cohort 전체 통계를 제공하지 않는다. 역사 연구 COHORT_COMPARABLE와 현재 goal EV_UNAVAILABLE를 분리하며 current exact context/EV lineage/계좌 Risk·한 RAM 슬롯 게시가 후속이다. 원래 모델 가용/기한은 재조회로 갱신하지 않는다. [81단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#81-v2-native-짝-모집단-동결과-공통-통계)

이하 단계 설명은 해당 시점의 기록이다.

v2 study 규칙·순간별 목표 증명 분리의 첫 native 경로를 구현했다. 현재 연구 실행기에서 명시한 한 case/branch를 동결하고 같은 Paper의 실제 상태/source bar/목표를 기존 원장에 기록한다. 기존 readonly native guard 뒤에서 목표가 당시 유효했다는 증명과 완결 후 가치/비용을 읽는다. 기존 outcome/정책 ID를 바꾸지 않는다. 자체 짝 cohort/exact context 지원/현재 RAM 연결은 후속이므로 현재 R4 EV_UNAVAILABLE·주문 OFF다. [80단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#80-v2-연구-규칙순간별-case-계약과-native-증명-첫-구현)

이하 단계 설명은 해당 시점의 기록이다.

**후속 설계·미구현:** 동적 목표의 R4 v2는 안정적인 study 규칙과 순간별 목표/TTL 증명을 분리한다. 기존 상위 비중/하위 수량·Paper/native 연구 원장·공통 통계·한 RAM owner 경로 안에 추가하며 기존 v1의 정책 hash/시점 검사와 주문 권한을 유지한다. 아직 새 v2 런타임은 없고 현재 새 동적 목표 비교는 EV_UNAVAILABLE다. [79단계 계약](docs/AI_TRADING_SYSTEM_REDESIGN.md#79-동적-목표의-연구-정책과-순간별-판단-유효기간-분리)

명시 선택한 배분 정책에서 준비된 native EV·전체 출구/스트레스 패킷을 하나의 공통 RAM 슬롯에 게시한다. 원래 source 시점/범위를 결합한 순수 최초 재생 뒤 기존 Collector/시장·테마/계좌 Risk frame에 상위→하위 목표를 한 번 계산한다. 실시간 루프의 source DB 재조회·전략별 수집/계산/저장을 추가하지 않는다. 같은 선택 lock/owned drain·세대 fence·close 수명을 사용하고 즉시 native 손절은 별도 기존 보호 경로를 유지한다. 새 동적 정책의 R4 모집단은 미공급이며 승인/주문은 false다. [78단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#78-준비된-native-모델의-공통-ram-게시와-상위하위-목표-연결)

이하 단계 설명은 해당 시점의 기록이다.

전체 보유 출구/스트레스 모델은 같은 Paper의 부분 매도 원가/비용/잔고 계산과 하나의 native 시장 bar proof 창을 재사용한다. 시나리오별 가상 포지션은 독립이고 자료 읽기/수집은 공유한다. 미래 체결/유동성/최악 손실의 보장이 아니며 Risk·Execution 권한을 바꾸지 않는다. 세 producer의 공통 RAM 게시/실시간 통합은 후속이다. [77단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#77-전체-보유-출구-capacity와-명시-스트레스-연구-근거)

배분 EV의 명시 PC readonly 준비는 기존 native 연구 원장/Paper/R4 비교를 재사용한다. 실시간 수집/분석을 복제하지 않고 준비 결과의 원래 시점·범위만 소비한다. native RAM 게시와 전체 출구/스트레스 producer는 후속이므로 native 자동 목표는 아직 BLOCKED다. [76단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#76-배분-ev의-native-연구-공급과-검증-크기-제한)

동적 비중 입력은 명시 선택의 같은 공통 runtime에서 Collector RAM/공통 맥락·ACK 계좌/Risk를 한 번 빌린다. Native 변환은 기존 owned drain에서 수행해 보호 Runner와 별도이며 scope/시각/내용/원래 TTL을 검사한다. 독립 배분 EV·전체 출구/스트레스 모델이 없으면 목표를 합성하지 않고 BLOCKED로 남긴다. source_durability=not_verified·주문 승인 false다. [75단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#75-동적-비중의-공통-ram-사실과-native-계좌-risk-연결)

③ 상위 동적 목표는 계좌 운용 한도·순자산/현재 및 불리한 미체결 노출을 검사해 비중/원화 목표를 만들고 기존 PositionTargetSizingPolicy→수량/비용 계산으로 전달한다. 시장/테마/EV 등은 현재 명시 연구 입력이다. 실제 공통 RAM binding/R4 통합·보정/승인·운영 수락은 후속이며 보호 Risk/native 주문 권한은 기존 경로가 유지한다. [74단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#74-실제-모의계좌-수락-준비-확인과-상위-동적-비중-계산-시작)

승인된 Collector RAM tick은 기존 Supervisor/Runner에서 상세 FILL 기반 진입 원가와 후보 손절 한도를 즉시 비교한다. 첫 quote/ref/시점은 sticky이며 계좌 관측 정렬은 기존 Monitor coalesced recovery/ACK를 사용한다. 보호 EXIT는 기존 native owner/control/고정 결정/QUEUED 검증을 통과하고 분봉·Shadow·LLM/EV와 독립적으로 진행한다. 만료/불확실 주문은 BLOCKED·대사/운영자 주의이며 체결을 가정하지 않는다. [73단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#73-승인된-collector-ram-가격의-즉시-종목-손절과-native-보호-exit)

2026-10-14 PC CentralContentClient의 명시 prepare_native_unfilled_population으로 기존 readonly 연구 원장/공통 재생을 한 번 검증하고, 같은 클라이언트의 publish_native_unfilled_population이 준비된 패킷을 기존 인증 POST 경계로 보낸다. 기존 mock 라우트·Supervisor·Runner의 계좌 lock/owned drain/단일 RAM 접수를 재사용한다. 서버에 DB 경로를 보내거나 계좌를 활성화하지 않는다. 원래 native 결과의 최대 available_at과 내용 hash를 고정하며 HTTP 재전송/계좌 ACK/재시작으로 갱신하지 않는다. 계좌·profile/spec/run/control·원주문/known child·동결 비용/horizon/표본·같은 native 정책/모델·완결 짝/가치·1MiB/200창/10,000 event budget을 검사한다. 새 패킷에는 현재 native RUNNING control 한 번을 확인하고 같은 패킷의 inert 중복은 새 조회/깨우기/슬롯 교체가 없다. 등록 전환과 RAM 교체는 기존 input lock 안에서 검사한다. PC는 명시 자료를 한 번 읽어 패킷을 보관하고 HTTP retry는 그 패킷만 보낸다. 서버 원장 경로/RawInput 재수집·연구 재실행/새 자동 task/계좌 활성화는 없다. 기존 보호 요청과 주문 claim은 독립 native 경로로 우선하며 연구 패킷은 주문 권한이 아니다. PC의 readonly/replay 검증 보고이며 서버의 시장 원본/호가/체결 확률/취소 지연 승인이 아니다. PC_READONLY_REPLAY_REPORTED·source_durability=not_verified·order_authorized=false를 유지한다. native v1은 ACCEPTED 분봉 자료만 지원하고 현재 PARTIALLY_FILLED/CANCELLED/UNKNOWN에서는 EV 미확인(native_unfilled_model_state_unsupported)이며 이전 cohort를 재표지하지 않는다. 실제 00/04/REST·PG/NAS/장중 수락·부분체결/CANCELLED 모집단/취소 지연 보정은 남는다. [72단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#72-pc-native-원장-결과의-인증-게시와-기존-runner-ram-접수).

2026-10-14 기존 PaperExecutionEngine에 명시 unfilled_action_plan의 SUBMIT→WAIT/REPRICE 지정가 연구 분기를 연결하고 같은 연구 원장 writer를 재사용했다. native readonly 개발 구간/최종평가·내용 hash/cut/예산 guard 뒤에 같은 실행기로 가격·현금·예약·비용·취소/재주문 순서를 재생해 UnfilledActionOutcome을 생성한다. 기본 next-open 실행과 기존 HOLD/EXIT/자동 목표 자료는 유지한다. 모델은 명시 이후 분봉 시가의 전체 수량 조건과 취소 지연 0을 고정하며 호가 접촉/부분 체결을 추정하지 않는다. 순수 결과 DTO는 기존 Runner 접수 계약에 맞지만 자동 source publisher는 후속이다. 오프라인 분기별 proof/현금/비용은 독립 기록하고 기존 Collector RAM·주문 native 경로·연구 원본 저장 책임은 유지한다. 실시간 원장 입력 게시 caller·CANCELLED/부분체결 모집단 생성은 후속이며, 실제 호가 순서·취소 지연·부분체결 확률·시장 원본 lineage는 미검증이다. source_durability=not_verified와 주문 승인 false를 유지한다. 실제 00/04/REST·종목별 즉시 native 손절 producer/수락·PG/NAS/장중 운용 승인도 남는다. [71단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#71-기존-연구-실행기의-지정가-분기와-native-미체결-결과-읽기).

2026-10-14 미체결 WAIT/REPRICE는 별도 typed outcome을 공통 짝 통계에 넣고 기존 비용 차감/표준오차를 재사용한다. R4 HOLD/EXIT와는 구분한다. 동결 후보 비용과 등록 전략의 현재 HOLD/EXIT를 그대로 빌리며 실제 상태는 FILL로만 변한다. 새 주문의 current cut은 실제 Risk ACK에 맞추고 보호 취소는 기존 REST 전 검증을 유지한다. 입력은 단일 bounded RAM/cache이며 재시작 후 재공급이 필요하고 native 생성/출처 검증이 없어 not_verified인 명시 실험 자료다. [70단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#70-waitreprice-짝-입력동결-비용익절-판단의-공통-연결).

2026-10-14 보호 매도의 마지막 확정 취소 거절 이후 최신 account/broker/Risk·현재 가격/슬리피지를 확인해 원래 취소 경로에서 제한 재시도한다. 새 동결 명세의 한도/지연만 사용하며 기본 OFF다. 원래 계보의 CANCEL_STARTED가 예산이고 native transaction에 현재 응답/상태·Risk·control/owner를 결합한다. UNKNOWN/ACK 유실/확인 대기는 기존 대사, 원래 기한·예산 도달은 운영자 확인이다. 취소 확정 전 새 SELL·UNKNOWN 중복과 EV/LLM/분봉 보호 대기는 없다. [69단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#69-취소-거절불확실-이후-bounded-보호-대응).

2026-10-14 계좌 일손실 EXIT_REQUIRED 요청의 첫 보호 EXIT는 신선한 full account/상세 체결·owned 수량과 승인 Collector 가격을 고정해 기존 checkpoint에 먼저 ACK하고 원래 EXIT gate로 제출한다. 기존 QUEUED transaction이 현재 Risk/승인/요청·고정 payload/TTL·owner/control/계좌 전체 미종결을 확인하고 intent JSON proof를 보존한다. ACK 유실/UNKNOWN은 같은 native root의 목적 등록만 복구하고 새 전송을 하지 않는다. 실제 상세 FILL·신선한 잔고 0만 청산 확인이며 보호 latch는 유지하고 재시작 후 확인이 필요하다. caller cue/불확실 Risk는 검토/대사, 종목별 즉시 손절 trigger는 후속이다. [68단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#68-운영-risk-보호-요청의-첫-exit-생성과-native-claim-확인).

2026-10-14 승인 원주문 제출과 관리 등록은 같은 owned 작업이며 원래 목표 수량/가격·시간·기한/BUY·익절·보호 목적을 기존 checkpoint로 복구한다. ENTER는 기존 승인에서 복구 가능하고 목적 없는 과거 EXIT는 추측 등록하지 않는다. Collector 승인 KRX RAM 가격·Monitor ACK Risk/전체 주문/실제 broker 확인을 기존 Runner에 한 번 공급한다. 실제 00 대사와 관리 기한에 기존 coalesced recovery queue를 요청하며 native thread의 요청은 Monitor loop에서 queue한다. 건강한 REST revision/가격 틱은 전면 검토를 반복하지 않고 보호 가격의 준비/슬리피지 허용 여부 변경만 RAM에서 감지한다. protection은 bootstrap/분봉 복구를 기다리지 않는다. 미선택 정책은 OFF/기존 경계이고 WAIT-vs-REPRICE 짝 EV는 아직 없으므로 일반 재주문은 차단한다. [67단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#67-기존-collector계좌-입력의-자동-공급과-원래-주문-관리-등록). 아래 단계의 미연결 표현은 당시 기록이다.

2026-10-14 기존 Runner의 최신 입력 소비가 WAIT/ABANDON을 기록하고 CANCEL은 기존 자동 취소 gateway로 호출한다. 계좌 refresh 이후 runtime 잠금 내부 callback이 ACK된 owner 계좌/Risk·전체 RAM 주문 관측과 실제 broker confirmation을 읽고 같은 목적 정책을 다시 평가한다. 원래 가격/타점/EV 입력 시각은 보존하며 보호 BUY는 취소 후 ABANDON, protective EXIT는 EV/비용 미확인을 기다리지 않는다. 취소 ACK는 확정이 아니므로 새 입력의 CANCELLED/상세 체결 대사 뒤 기존 fixed child gate로만 제출한다. 거절/UNKNOWN은 동일 CANCEL_STARTED를 자동 반복하지 않고 보호 검토/attention을 표시한다. 최초 시장/판단 입력 production 공급·원래 주문 등록과 운용 수락은 후속이다. [66단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#66-목적별-자동-취소와-취소-직전-native-계좌risk-재검증).

2026-10-14 기존 Runner의 `offer_unfilled_execution_inputs`는 현재 typed cut 하나만 RAM에 방어 복사하고 동일 loop를 깨운다. 동일 내용은 no-op, 더 최신 내용은 슬롯 교체이며 가격 시각/건강한 Risk revision은 신선도일 뿐 전체 gate 사건이 아니다. 주문/상세 체결·위험/타점 조건·EV 허용 변화와 원래 관리 기한에서 같은 잠금 core/native 고정 승인·제출을 실행한다. 현재 입력 없는 기한은 보호 요청만 arming하고 independent session/risk clock을 유지한다. 입력은 checkpoint에 저장/재시작 replay하지 않으며 원래 anchor/child/고정 gate는 기존 writer로 복구한다. 실제 입력 producer/자동 취소 연결은 후속이다. [65단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#65-running-runner의-사건기한-소비와-고정-주문의-동일-native-실행).

2026-10-14 명시 선택한 동결 `unfilled_execution_policy`와 v3 고정 child 승인을 기존 native 실행에 연결했다. 첫 fixed 승인/index는 불변으로 유지하고 최신 Risk/짝 입력 검사는 별도 gate 원장에 저장한다. 실제 claim이 검사한 gate/Risk 참조를 QUEUED의 같은 intent JSON에 기록해 ACK 유실 복구를 식별한다. existing event writer가 후속 대사에도 참조를 보존하며 parent/전체 활성 원장·current Risk/owner/control은 같은 claim transaction에서 검사한다. 기존 명세/연구 preview는 OFF, 현재 caller는 idle 명시 dispatcher이며 running 사건/기한·가격/타점/EV producer는 후속이다. [64단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#64-목적별-고정-대체-주문의-native-승인과-제출).

2026-10-14 내부 자동 취소 실행 경계는 기존 owned gateway→runtime→lifecycle→repository/DB를 재사용한다. 계좌 갱신 후 검토한 주문이 바뀌면 차단하고 CANCEL_STARTED의 native transaction에서 owner/control을 검사한다. claim ACK 뒤 응답/체결 대사는 STOP 이후에도 보존하며 native 시작 기록으로 중복 취소를 막는다. 새 계층/독립 task·writer는 없고 실제 목적 dispatcher/대체 주문/모의계좌 수락은 후속이다. [63단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#63-모의계좌-자동-취소의-native-실행-경계).

2026-10-14 기존 자동 계좌 monitor가 저장 ACK한 Risk/계좌 cut을 독립된 Risk listener로 같은 Runner에 RAM 전달한다. 현재 spec의 실제 손실/UNKNOWN·대사/자료 한도를 검토해 보호 요청을 깨우고 일반 판단에 앞서 기존 checkpoint로 처리한다. 반복 revision/누적 mismatch만으로 요청을 늘리지 않으며 STOP/보호 latch는 자동 해제하지 않는다. 기존 수집/REST·Risk writer·Position/Runtime listener는 유지한다. 실제 보호 취소/재주문·제출은 다음 ②, 동적 비중은 ③, 모의 전체 통합/운용 승인은 ④다. [62단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#62-운영-risk의-자동-보호-요청-연결과-개발-순서-개정).

2026-10-14 명시 native preview·고정 child·OFF 보호 요청/checkpoint의 첫 연결 통합 수락을 마쳤다. 현재 실제 사건/기한의 자동 호출자는 없고 due는 순수 힌트다. 격리 idle polling은 기존 FILL/control/observation SELECT 4·COMMIT 0을 유지하며 전체 미체결 gate를 반복하지 않는다. 다음 개발은 원래 Position·Risk·Capacity/Liquidity의 동적 비중조절 입력 계약/주문 없는 상위 정책이다. 실제 미체결 전송·운영 보호/청산·운용 승격은 그 개발과 독립 후속이다. [61단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#61-주문-off-첫-연결의-통합-수락과-db-호출-범위).

2026-10-14 목적별 고정 child OFF 검토는 기존 gate의 안전 검사와 한 native context를 공유한다. 매수의 원래 승인/총 보유 자본·자금, 익절의 일반 수익/위험, 보호 EXIT의 강제 위험 경로를 구분하며 실제 approved-gate/전송은 생성하지 않는다. 기존 runner의 bounded RAM 보호 요청·동일 loop 깨우기·봉/일반 dispatch 전 차단과 동일 checkpoint/실제 flat 재검증을 연결했다. 이미 시작한 native 작업은 drain하고, 운영 Risk producer·실제 청산 실행/지연 수락은 후속이다. 새 수집/초봉/Shadow·DB writer·독립 task는 없다. [60단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#60-목적별-고정-대체-주문-검토와-보호-요청의-우선-처리).

2026-10-14 native 미체결 시도 검토는 기존 owner guard/Risk/복구·상세 체결 reader와 동일 checkpoint를 사용한다. QUEUED claim과 SUBMISSION_STARTED를 분리하고 기대 child 내용/계보·원래 anchor/기한·소모 예산을 복구한다. 일반 polling에는 연결하지 않으며 ADD/REDUCE를 다른 행동으로 바꿔 기존 gate에 통과시키지 않는다. raw EXIT 평가는 고정 child 실행 승인이 아니고 adapter 전송은 OFF다. 실제 목적별 gate/child 결합·보호 wake-up은 후속이다. [59단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#59-native-parent-child-시도-투영과-닫힌-owner-gate-검토).

2026-10-14 기존 idle runner의 명시 미체결 검토를 native parent reader·기존 checkpoint writer와 연결했다. optional 1MiB receipt가 원래 anchor/control/정책·첫 preview 예약·현재 결과를 복구하며 재시작 후 항상 재검증한다. 기존 독립 시계에는 보호 기한/미해결 위험 힌트만 전달한다. 실제 OrderIntent·추가 취소/주문/자동 timer·native owner/Risk 실행 권한은 만들지 않는다. 현재 단일 root의 첫 슬롯이며 다회 전송 시도 투영/보호 wake-up은 후속이다. [58단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#58-미체결-검토-계획의-기존-checkpoint-복구와-실행-전-검증).

소스 대조: 2026-10-11 · 제품 2.1.0 · 실행/배포 기준은 CURRENT_STATUS · [문서 안내](docs/README.md)

이 문서는 현재 책임과 데이터 흐름을 설명한다. 실제 NAS 배포·실환경 검증은 [현재 상태](docs/CURRENT_STATUS.md), 새로운 기능 순서는 [로드맵](FUTURE_DEVELOPMENT_ROADMAP.md)에 둔다. 과거 단계별 진행 기록은 [아카이브](docs/archive/README.md)에 보존했다.

2026-10-13 목표 구조와 재조립/신규 개발 비교는 [전체 설계안](docs/AI_TRADING_SYSTEM_REDESIGN.md)에 있다. 아직 구현된 아키텍처가 아니므로 이 문서의 현행 구조와 구분한다.

## 실행 단위

```text
Windows 메인 앱 ─ monitor.sqlite3
  ├─ 뉴스 프로세스 ─ news.sqlite3
  ├─ 매매일지 프로세스 ─ journal.sqlite3
  └─ 연구 프로세스 ─ research.sqlite3

연결 모드
  ├─ 이 PC 직접 연결 → Kiwoom REST / WebSocket
  └─ NAS로 연결 → 중앙 FastAPI → Kiwoom REST / WebSocket
                         └─ PostgreSQL 또는 개발용 중앙 SQLite
```

메인·뉴스·일지·연구의 무거운 작업은 Qt 메인 이벤트 루프와 분리한다. 로컬 DB는 사용자 편집·캐시·오프라인 상태를 보존하므로 PostgreSQL 하나만이 모든 데이터의 유일 원본인 구조로 설명하지 않는다. NAS 공유 경로의 SQLite를 여러 PC가 직접 여는 방식도 아니다.

내부 `local_server`/`personal_server`는 기존 설정과 장애전환 호환값이다. 화면 명칭을 이유로 일괄 변경하지 않는다. 테스트 실행 소스는 `scripts/run_test_app_with_data.py`의 `SOURCE_ROOT`로 판단한다. 원본 프로젝트의 Python·데이터 폴더 재사용은 소스 버전의 근거가 아니다.

PC 종료는 AppController가 생산자·queued 결과를 기다린 뒤 pending을 flush하고 실제 writer와
queued 저장 결과까지 기다린다. 마지막 분봉/가격 저장 실패로 복원한 pending이 남으면
`cache_failed` 상태에서 창과 보조 자원을 유지하고 저장 타이머·생산자는 재시작하지 않는다.
사용자가 닫기를 다시 요청하면 같은 정지 writer를 다시 실행해 남은 batch를 저장한다.
이미 끝낸 TOP20 partial 저장·백업·화면 저장은 반복하지 않는다. 종료 때 마지막에 보낸 batch만
보관해 최신 값으로 대체된 옛 실패 key를 복원에서 제외한다. 마지막 batch의 실패와 대체되지 않은
peer는 유지하며, 실제 writer 종료와 queued 저장 결과 처리 뒤 종료 준비 신호와 close 수락을 허용한다.
이 보완은 기존 분봉/가격 실패 복원 경로의 종료 정책이며 다른 writer job의 완료 보장을 뜻하지 않는다.

## 시장 자료

중앙 REST는 `CentralRestBroker`가 우선순위·동시 요청·캐시를 관리한다. `ka00198` 순위가 분봉/일봉/백필보다 우선하며 저우선 작업이 순위 경계를 막지 않는다. NAS 순위 수집은 앱 실행과 독립된다. 화면은 저장된 NAS 자료를 우선 조회하며 정상 연결의 시장자료 부족을 PC의 자동 직접 TR 조회로 바꾸지 않는다.

중앙 WebSocket은 체결·시장·계좌 경계를 구분한다. TOP20/관심 후보의 수신 체결로 1초 OHLCV·거래대금·건수를 집계하고 분봉·TOP20 구성/지수·시장 상태를 저장한다. 1초봉은 수신한 체결의 집계이며 모든 종목·기간의 원시 틱/호가 전수 기록이 아니다. 구독 전·끊김·부분 수집을 완전한 기록이나 실제 0으로 바꾸지 않는다.

TOP20의 0W 프로그램수급은 서비스가 수신 값을 정규화하고 기존 주기로 저장을 요청한다.
`ProgramSnapshotWriter`는 종목별 최신 pending, 분리한 batch의 실제 저장 task, 실패한 값의 병합과
종료 drain을 소유하며 `save_dataset_snapshots` 하나만 요구한다. 전체 종료는 TOP20 서비스가
생산자를 멈춘 뒤 writer를 drain하는 순서다. 같은 native DB batch·저장 키·transaction을 사용하고,
COMMIT ACK 유실 재시도와 취소 시 실제 작업 대기를 유지한다. cold seed 문서는 소유 객체 이동과
무관하게 기존 pending/task 의미와 직렬화 키를 보존한다.

확정값·최신 조회값과 불변 observation revision은 목적이 다르다. 순위·분봉·테마·뉴스의 당시 가용시각을 보존한다. 장후 보완은 당시 장중 관측으로 소급하지 않는다. 거래일별 세션 정책, 정규장 종가와 전체일 종가, 거래소, 수정주가·금액 단위를 구분한다. 자세한 규칙은 [과거 데이터 계약](HISTORICAL_DATA_CONTRACT.md)을 따른다.

로컬 쓰기는 `MarketCacheWriter` 등 소유된 작업 경계를 사용한다. NAS 장애전환은 기존 직접 client의 토큰·잠금을 공유하고 실제 구독 승인/조회 성공을 확인한 뒤 상태를 전환한다. 로컬 장애전환 자료로 중앙의 과거 공백을 감추지 않는다.

## 뉴스·AI·테마

현재 뉴스에는 네이버 검색 API와 선택형 DART, 본문 추출·정제, AI 분석, 사건·구성원 이력, 작업/cursor가 있다. 기사·본문·AI·사건은 서로 다른 revision과 가용시각을 가진다. 현재 수집 대상의 coverage이며 전체 시장·과거 5년 뉴스를 보장하지 않는다.

네이버 증권·날짜 지정 검색 수집과 PC BODY/RULE 처리, 봉인 archive reader/API 및 조회 화면 경로가 있다. 전체 수집 소진·자료 봉인/게시·의미 품질·연구 입력 연결의 잔여는 OPEN_ITEMS D02/D03/O10을 따른다. 외부 백필 도구와 앱의 검색 API 경로는 서로 다른 입력이다. 외부 AI 공급자 경로는 존재하지만 첨부에서 언급한 로컬 LLM/Mac 작업자는 확인된 현재 기능이 아니다.

현재 테마 원본은 `theme_profiles/profile_themes/profile_stock_themes`다. 구형 `themes/stock_themes`는 이전·복원 호환을 위한 자료다. 테마의 로컬 수정·pending 재시도·NAS 수락 스냅샷과 실제 가용시각을 보존한다. 프로필별 대표명·별칭·분리/재병합 금지 원장과 뉴스 AI 후보의 사용자 승인·거절·수정 경로를 구현했다. 제안 품질과 의미별 병합 정확도·과거 사건 일괄 검토는 D04의 잔여다.

뉴스 동기화는 문서 identity·내용 hash의 영속 delta를 사용한다. 파일 수정시각만으로 전체 업로드하지 않으며 pull 결과를 다시 echo push하지 않는다. 공유 AI 운영값은 중앙 운영 설정, 창 위치/표시 설정은 각 PC의 책임이다. 새 설정·테마 필드는 파일 백업·Drive·NAS 왕복까지 확인한다.

## 연구·타점·피드백

기존 reader/runner는 동결 export와 당시 가용했던 입력을 소비한다. 돌파와 눌림 재가속 Family, 순위 Factor, Snapshot/Decision/후보 사건이 있으며 정책은 최종 정답이 아닌 버전별 연구 대상이다.

Shadow의 `CandidateMonitor`도 연구/모의 runner와 같은 기존 `ResearchFamilyDefinition.evaluate_bar`를 소비하며 결과는 `StrategyEvaluation`이다. 같은 실행부가 등록된 돌파·눌림 config를 지원하며 두 전략의 기존 확정 KRX 1분봉·순위 계약과 전략별 monitor ID/상태/결과를 유지한다. 현재 운영 선택은 기본 돌파이고 초 단위 입력을 강제하지 않는다. 순위 freshness·cursor/checkpoint는 각 consumer에 유지하며 실제 FILL은 모의 runner의 포지션에만 적용된다. [첫 연결](docs/AI_TRADING_SYSTEM_REDESIGN.md#22-r1-첫-돌파-판단-소비-연결) · [두 Family/입력 해상도 범위](docs/AI_TRADING_SYSTEM_REDESIGN.md#30-전략-입력-해상도와-기존-shadow-실행부-재사용).

복구/gate 저장 전 불변 spec/admission/lease/recovery의 exact-key 조회는 기존 실행 store의 `load_mock_automation_lineage`가 한 statement snapshot으로 제공한다. `ForwardEvaluationRepository`는 근거 파싱/승인 판단을 유지하고 control/binding/현재 risk는 별도로 재검사한다. 기존 writer transaction/UNKNOWN/체결 경계와 backend 조립은 유지한다. [native SQLite 감소와 PG 검증 범위](docs/AI_TRADING_SYSTEM_REDESIGN.md#23-r2-불변-승인-근거의-좁은-조회).

runner checkpoint는 native ACK까지 pending을 유지하고 현재 fills/control 확인 뒤 후속 입력 전에 retry한다. 같은 runner가 ACK 받은 동일 내용은 일반 저장을 생략하지만 최종 close 저장은 강제하며, checkpoint/간접 bootstrap 저장과 그 상태를 바꾸는 FILL 읽기는 기존 `drain_thread_call`로 join한다. 부분 bootstrap/recovery·체결 원장·v1 JSON/attempt generation은 유지한다. [장애/no-op/취소 재현과 검증](docs/AI_TRADING_SYSTEM_REDESIGN.md#24-r2-checkpoint-저장-실패noop종료-순서).

`PaperExecutionEngine`의 역사봉 체결·현금·비용 원장과 증권사 모의계좌 실행은 별개다. 현재 strict 입력·체결 모델이 지원하지 않는 초 단위 체결 순서·VI 호가·부분체결·시장충격을 결과에 만들어 넣지 않는다.

`application/market_theme_shadow.py`가 같은 cut에서 기존 H1 시장/H2 대장을 계산해 범위·품질·근거·유효기간·내용 revision과 다음 H2 상태를 반환한다. `scripts/preview_market_theme_shadow.py`는 고정 JSON을 소비하며 주문 권한은 없다. 실시간 reader/Shadow 수명은 미연결이다. [첫 R3 범위](docs/AI_TRADING_SYSTEM_REDESIGN.md#25-r3-시장테마-shadow-입력-연결).

같은 모듈의 별도 `stock-thesis-shadow/v1` 계산이 기존 불변 진입 가설과 명시 thesis policy를 소비해 후속 HOLD/WAIT/REDUCE/EXIT 제안·무효화와 원본 결정을 반환한다. 맥락 v1 출력은 보존하며 H1 누락을 H2 보호 제안의 veto로 쓰지 않는다. 신규 진입·목표 수량·runtime 주문 연결은 없다. [후속 행동/무효화 범위](docs/AI_TRADING_SYSTEM_REDESIGN.md#26-r3-진입-근거의-종목-행동무효화-shadow-연결).

`central_server/market_theme_shadow_inputs.py`는 같은 backend의 ranking/theme 이력과 bounded native 1초봉 reader를 평가 입력으로 변환한다. mutable 행의 이전 버전과 수집 연속성을 복원/확정하지 않고 UNAVAILABLE/H2 PARTIAL로 유지한다. read별 snapshot은 별도이고 live task/저장/표시는 미연결이다. [native 입력 범위](docs/AI_TRADING_SYSTEM_REDESIGN.md#27-r3-native-입력-조회-연결).

새 `MarketThemeShadowMonitor`는 RAM snapshot 도착만 평가하며 DB polling/결과 저장을 하지 않는다. 중복/한 대기 snapshot·coalescing 표시·실패 재시도·진행/최종 대기 계산 drain을 소유하고 공통 Runtime의 producer/app와 owner 알림은 연결됐으며 native bridge는 복원/명시 점검 용도다. 기존 돌파 CandidateMonitor의 조회/저장 수명은 별도 유지한다. [R3 RAM 수명/부하 경계](docs/AI_TRADING_SYSTEM_REDESIGN.md#28-r3-ram-입력-shadow-수명과-부하-경계).

후속 Shadow는 공통 판단 엔진을 주문 없이 검증하는 실행 방식으로 통일한다. 전략별 규칙·상태·결과를 유지하고 기존 수집·분석·기록·복구를 재사용한다. 공유 계산·Collector RAM·app 수명·owner context 갱신과 선별 진단 저장/인증 조회는 연결했다. 기본 OFF의 시장·테마 관찰 화면/명시 선택·config 재시작은 연결했다. 확정 입력의 승인 복구/보호 정책 선택은 미완료다. [계약과 다음 연결 범위](docs/AI_TRADING_SYSTEM_REDESIGN.md#29-shadow를-공통-판단의-검증-실행으로-통일).

`run_shadow_monitors_once`는 기존 native safe-prefix page 1회를 RAM에서 같은 store의 준비된 CandidateMonitor들에 전달한다. 각 cursor/기록/복구는 독립이며 개별 worker와 혼용하지 않는다. checkpoint 실패를 보고하고 평가 저장 실패/ACK 유실은 해당 행의 이전 전략 상태에서 같은 문서로 재시도한다. 별도 task/수집기/writer는 없고 collector RAM 직접 발행과 app 조립은 미완료다. [현재 범위/검증](docs/AI_TRADING_SYSTEM_REDESIGN.md#31-공통-관측-페이지의-ram-다중-전달).

현재 평가 상태 공개는 native 저장 ACK 이후다. Collector/Hub는 체결/집계를 한 벌로 전달한다. 공통 native 페이지의 불변 frame과 동일 실제 scope/이전 상태의 H1/H2 계산을 재사용한다. 명시 app binding의 공통 RAM Runtime은 Hub 관측자 한 개·canonical 초 snapshot·bounded 보존/단일 수명 owner·실패/취소·최종 cut/close를 맡는다. 기본 binding은 비어 있고 시장/theme context는 기존 owner 알림으로 갱신하며 운영 선택/선별 기록·장중 수락은 후속이다. RAM 품질과 확정 revision의 기록/복구를 구분하며 native 원본 writer/전략별 저장을 유지한다. [현재 연결/검증/제약](docs/AI_TRADING_SYSTEM_REDESIGN.md#34-기존-collector-ram-관측과-앱의-공통-수명-연결).

TOP20·테마 owner 자동 갱신은 기존 RAM 공개/Content native 쓰기 완료 신호를 공통 Runtime으로 연결한다. 순위는 RAM 한 번, 테마는 변경/완료 시 native snapshot 한 번을 공유하며 과거 frame을 재라벨링하지 않는다. 기본 binding은 비어 있으며 선택적 진단 기록/조회는 아래 공통 수명에 연결한다. 관찰 context의 화면/명시 선택·config 재시작은 연결했으며 확정 승인/보호 정책 선택은 후속이다. [현재 납품/검증](docs/AI_TRADING_SYSTEM_REDESIGN.md#35-top20테마-owner의-공통-context-자동-갱신).

지속 campaign은 설정 revision·job/cycle·lease·재시도·예산·완료 cache를 보존한다. 고정 범위의 새 입력 revision 준비, 독립 시간/종목 검증, final 후보 잠금·접근·명시 복구·개발 노출, 자동 후속 가설과 화면 연결이 있다. 새 거래일을 자동 확장하거나 모든 final 작업을 자동 재개하는 기능까지 완성된 것은 아니다.

일지 상세 체결 projection·대조·불변 feedback evidence·기계 복기·개선 가설·명시 채택·전략 버전 재검증이 연결돼 있다. 사용자 수동 기록과 원본 체결을 사후 AI 결과로 덮지 않는다. [연구 요청 계약](docs/RESEARCH_REQUEST_FORMAT.md)과 [전진평가 계약](docs/FORWARD_EVALUATION_CONTRACT.md)을 따른다.

시장·테마 관찰 선택은 기존 Shadow 창의 별도 탭과 인증 selection API로 설정한다. 기본 OFF, 최대 32개 theme/200종목·KRX/NXT·기록 여부이며 기존 Collector의 관측만 소비한다. app은 별도 native config revision·저장/적용/이전 close/새 Runtime 공개·요청 취소/서버 종료·새 session 재시작을 소유한다. 창의 보이는 live 탭만 5초 RAM 조회하고 과거 저장분은 명시 읽기만 한다. 진단/entry thesis/주문 승인은 복원하지 않는다. [현재 범위](docs/AI_TRADING_SYSTEM_REDESIGN.md#37-시장테마-관찰-화면과-명시-실행-선택).

서로 다른 테마도 같은 typed H1 scope의 시장 계산을 owned 회차 안에서 공유하고 검증된 불변 시장 frame만 참조를 재사용한다. H2 관측/이전 상태/품질·정책/결과는 독립이며 가변 DTO 내부는 snapshot 복사로 격리한다. cache는 회차 종료에 비우고 int/float 인코딩 차이를 구분한다. 원본 writer/COMMIT은 유지하며 명시 recording 첫 진단은 별도 1회 쓰기다. 관련 184건과 로컬 합성 부하를 확인했으며 실제 PG/NAS/장중 수락은 남는다. [계약/측정](docs/AI_TRADING_SYSTEM_REDESIGN.md#38-서로-다른-테마의-h1-공유와-불변-시장-입력-재사용).

기존 Collector의 0g 종목 참조정보는 승인 source마다 최신 당일 기준 한 벌을 RAM에 보존하고 snapshot 복사→공통 입력→기존 H2 UpperLimitReference로 전달한다. 거래소·당일·가용시각·source 삭제/reset/gap을 검증하며 참조만 도착해도 기존 owned worker가 갱신한다. 참조 원본 native writer/COMMIT과 MarketEventService의 ACK·재시도는 그대로이고 새 조회/확정 승인/주문을 만들지 않는다. [현재 계약/검증](docs/AI_TRADING_SYSTEM_REDESIGN.md#39-기존-0g-상한가-참조정보의-ram-shadow-전달).

기존 연구 DB의 불변 진입 가설은 명시 run/thesis ID·정책 선택으로만 복구한다. ResearchRepository의 단일 snapshot 조회→기존 입력 bridge의 불변성/scope/COMPLETE/시각 검사→기존 typed Shadow 요청을 사용한다. CLI는 mode=ro로 열고 RAM binding 준비 후 틱마다 DB를 읽지 않는다. 연구 문서 저장은 운영 승인/시장 원본 확정과 구분하며 H2 이전 상태·주문 권한은 복원하지 않는다. 실제 화면/운영 설정의 선택 연결은 후속이다. [현재 계약/검증](docs/AI_TRADING_SYSTEM_REDESIGN.md#40-기존-연구-db의-불변-진입-가설을-명시-복구해-shadow에-전달).

저장된 연구 가설/정책은 관찰 화면에서 명시 선택하고 PC worker의 read-only 조회 결과 snapshot을 인증 selection API로 전달한다. 서버는 기존 입력 bridge의 순수 불변 검증으로 확인해 해당 테마의 기존 Monitor에만 연결한다. optional stock_thesis·null 해제/설정 ACK 유실·새 session/H2 상태 재시작은 기존 app owner/문서 한 행을 사용한다. 서버 PC 경로 조회·시장 원본 확정/운영 승인·주문 권한 복원은 없다. [현재 계약/검증](docs/AI_TRADING_SYSTEM_REDESIGN.md#41-저장된-연구-가설정책의-관찰-화면과-명시-설정-연결).

명시 원본 대조는 기존 app의 설정 revision/owner lock→입력 bridge→native theme/초봉 reader 두 번을 사용한다. 기본 live/tick은 추가 조회 없이 RAM을 사용한다. 대조 중 선택 변경·shutdown은 기다리고 취소는 read를 drain한다. 테마 정확 ID/내용 hash/구성원과 현재 초봉의 전체 값 hash를 비교하지만 초봉 이력/수집 연속성과 factor/hypothesis lineage가 없어 확정 승인·Replay 적격성으로 승격하지 않는다. 새 저장/스키마·Writer·엔진/중계 계층은 없고 각 native transaction은 독립이다. [42단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#42-연구-가설의-native-원본을-명시-대조).

관찰 화면의 ‘저장된 가설 원본 대조’는 기존 Qt 설정 worker/client의 명시 호출로 연결한다. 저장된 선택 revision과 클릭 시 창만 전달하며 PC 연구 DB를 다시 열거나 편집값을 저장하지 않는다. live/기록 polling과 대조 결과는 독립이고 선택 reload/save 시 이전 대조 표시를 초기화한다. 오래된 대상/승인 범위가 다른 응답은 표시하지 않으며 원본 일치·미확인·조회 비용/비승인 상태만 보여준다. [43단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#43-관찰-화면의-수동-원본-대조와-결과-표시).

공통 Runtime은 같은 Collector RAM cut의 승인/연속 구간·보존/gap·전달 누락을 출처 근거 한 벌로 묶고 현재 context_ref에 연결한다. 성공/실패/취소의 실제 결과 객체·cut에 맞는 근거만 유지하며 과거 결과를 새 source 상태로 덮어쓰지 않는다. 소유자가 나중에 갱신된 delivery 상태도 입력 snapshot 시점에 고정한다. live/기존 선별 한 행에 optional map으로 전달하고 판단 ID/상태/선별 signature에는 넣지 않는다. raw 입력/capture lineage·확정 승인은 여전히 미확인이며 새 수집·원본 writer·1초 이력·주문·중계 계층은 없다. [44단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#44-ram-판단의-공통-출처와-연속성-근거-연결).

Collector의 기존 Recorder high-water를 현재 활성 epoch와 대조해 같은 RAM snapshot의 capture_anchor로 전달한다. 공통 Runtime은 이를 복사하여 조회 힌트로 공유·선별 기록하며, 이후 Recorder 상태로 과거 결과의 힌트를 다시 표기하지 않는다. 녹화/payload/manifest·DB 호출을 추가하지 않고 전체 원본 대조나 승인으로 승격하지 않는다. 현 Collector capture의 0g 제외도 미확인 경계로 유지한다. [45단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#45-ram-출처의-기존-녹화-입력-조회-힌트).

기본 OFF의 명시 RAM recording은 기존 `central_documents` 고정 key에 최근 선택 결과 하나를 저장한다. 범주 상태 변경·기본 30초 간격/최신 pending coalescing·owned native writer/종료 drain을 사용하고 공통 context를 한 번만 담는다. 인증 research API는 기본 RAM 무조회와 명시 저장분 조회를 구분한다. 저장분은 과거 session의 진단이며 승인/monitor 복원 입력이 아니다. [현재 계약](docs/AI_TRADING_SYSTEM_REDESIGN.md#36-ram-shadow-결과의-선별-저장과-인증-조회).

## 계좌·실행·인증

모의 주문은 계좌 단일 owner, 멱등 intent, 전송 직전 gate, unknown 응답 대사·복구를 거친다. 자동 모의운용의 후보 게시·admission·복구·위험 근거·runner/supervisor/UI는 구현돼 있다. 준비 완료나 평가 PASSED가 곧 주문 시작·실계좌 승격은 아니다.

자동 O2-M은 현재 지원하는 단일 전략·단일 포지션·KRX 정규장 범위를 따른다. 수동 KRX 애프터 LIMIT probe의 별도 경계와 일반 자동 운용을 혼동하지 않는다. [모의 실행 계약](docs/KIWOOM_MOCK_EXECUTION_CONTRACT.md)을 따른다.

NAS 복수 계좌 프로필·인증 교체·런타임 owner와 서버 시세 담당 선택 API(`GET/PUT /api/v1/settings/market-profile`)는 구현돼 있다. PC에서 시세 담당을 선택하는 UI는 아직 연결되지 않았다. PC 직접/장애전환 계좌 신원 결합은 별도 경계이며 resolve/aliases·fresh credential 검증·직접 WS 결합에 미완료 항목이 남아 있다.

로컬 비밀은 로컬 암호화 저장소, NAS 비밀은 `server-secrets`의 암호화 vault가 소유한다. 키는 일반 DB·문서·백업·로그에 넣지 않는다. runtime revision과 실제 적용을 확인한 뒤 활성 상태를 표시하고 실패를 임의 이전 키 복귀로 숨기지 않는다.

## 포지션 목표 계산 (R4 첫 범위)

R4 후 미체결 첫 정책은 기존 order_lifecycle.py의 독립 상태 없는 목적별 순수 함수와 고정 preview CLI다. 원래 class/취소·체결 정규화와 현재 계좌 전체 pending/예약·PositionTarget/비용 계산을 재사용하고 ACK/UNKNOWN에는 대체 수량을 만들지 않는다. 보호는 일반 EV 대기를 적용하지 않으며 실제 잔고와 pending 소멸 전 위험 해소를 표시하지 않는다. 현재 연구 요약/횟수/기한 입력은 native 실행 승인이 아니며 영속 parent-child 계획/예산·실제 owner/보호 시계 연결은 후속이다. [57단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#57-r4-후-미체결-목적별-정책의-주문-없는-첫-검증).

optional `owner_ram_risk_and_thesis` 입력은 기존 계좌 모니터가 저장한 native Risk를 같은 계좌 RAM에 복사하고 공통 Runtime이 선택된 기존 R3 타점 결과와 함께 한 번 검증한다. Risk 내용/계좌/as_of·가용/만료와 타점 ref/종목/quality·시간을 확인하고 원래 ref를 결과에 묶는다. HOLD는 증액 허가가 아니고 WAIT/모호한 결과는 차단, 유효 EXIT는 EV 결손보다 보호 필요를 우선 표시한다. 이 경로는 기존 운영 Risk 승인·주문 전송이나 동적 비중 정책을 구현하지 않으며 새 Risk 조회/계산·owner/저장을 만들지 않는다. [56단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#56-r4-기존-타점과-native-risk의-공통-ram-입력-연결).

현재 금액·수량 계산은 하위 기능이다. 최종 목표 비중은 원래 통합 명세의 Position·Risk·Capacity/Liquidity에 따라 시장·테마·종목/시총·대금·유동성·변동성·EV·위험을 종합하는 상위 동적 정책이 후속 결정한다. optional 행동 권고는 같은 HOLD 짝의 비용 차감 순차이에서 명시 표준오차 페널티를 빼고 연구 위험 제약을 적용하며 새 owner/수집/DB/주문 경로를 추가하지 않는다. [55단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#55-r4-비용불확실성위험-제약의-주문-없는-행동-권고).

기존 PositionTargetShadowRequest의 optional bounded 원화 정책으로 ADD/REDUCE/REENTER 수량을 생성한다. 원화/주식 보유 한도와 전체 예약 후 남길 현금·가용 구간은 모두 명시하며 기존 비용/부분체결/native 잔량/대사 guard를 재사용한다. CLI/PC 고정 선택과 같은 공통 Runtime의 계좌/가격 cut에서 계산하고 정책 만료를 receipt에 포함한다. 정책 내용 hash와 계산 목표가 같은 자체 자료만 EV 비교하며 기존 성과를 재표지하지 않는다. optional 명시 연구 제약의 주문 없는 행동 권고를 연결했으며 실제 Risk 승인/보호 주문 권한은 없다. 정책 없는 결과/ID와 HOLD/EXIT는 유지했다. [자동 목표 정책 첫 연결](docs/AI_TRADING_SYSTEM_REDESIGN.md#53-r4-자동-목표-수량-정책의-공통-shadow-계산-연결).

기존 연구 원장의 native HOLD/EXIT reader 첫 연결은 [51단계](docs/AI_TRADING_SYSTEM_REDESIGN.md#51-r4-기존-연구-원장의-완결-행동-자료-reader-연결)에 둔다. 기존 readonly ResearchRepository의 한 snapshot/개발·final guard→실제 MARK/FILL의 비용/고정 기간 포트폴리오 변화→기존 R4 CLI 비교를 사용한다. 원래 정책/시각/초기 상태를 보존하고 없는 행동 성과는 만들지 않는다. 기존 PC 관찰 worker의 명시 DB/창/TRAIN·VALIDATION 선택도 같은 reader를 재사용한다. outcomes만 편집하고 저장/복구/GET/tick은 원장을 다시 읽지 않는다. 선택 변경/닫기/종료는 checkpoint·SQL 중단과 실제 worker 완료를 소유한다. [PC 선택](docs/AI_TRADING_SYSTEM_REDESIGN.md#52-r4-완결-행동-자료의-기존-pc-관찰-선택-연결). 새 DB/시뮬레이터/실시간 owner는 없으며 명시 목표 계획의 같은 연구 엔진/writer·native ADD/REDUCE/REENTER/flat HOLD proof도 연결했다. 상위 정책/동적 비중 선택·계획 생성/자동 예약과 운영 수락은 후속이다.

R4 이후 실행 연결의 [미체결 관리 설계](docs/AI_TRADING_SYSTEM_REDESIGN.md#76-미체결-주문-관리-정책)는 매수·일반 익절·손절/위험 청산의 세 목적별 정책을 공통 owner 안에서 구분한다. 기존 gateway/runtime/lifecycle의 취소·UNKNOWN·체결 대사를 재사용하며 취소 확정 전 재주문·UNKNOWN 중복 제출을 금지한다. 강제 보호는 R4 EV 추천보다 먼저 판단하고 PC LLM·다음 1분봉·Shadow를 기다리지 않도록 연결해야 한다. 목적별 순수 선택·native 계획/고정 child와 OFF 보호 요청 깨우기는 로컬 구현이며, 운영 자동 취소/재주문·보호 producer/실제 주문·지연 수락은 후속이다. 현재 주문 지원·STOP/owner/control 의미를 자동 변경하지 않는다.

고정 행동 비교 입력을 기존 관찰 탭에서 명시 선택해 서버 설정/수명에 전달한다. 선택 준비 때 기존 R4 계산 한 번, 여러 테마/틱·조회에는 공통 결과 한 벌을 복사하며 기존 선별 transaction에 포함한다. None/부족한 행동은 미확인이고 원래 cut/ID·실시간 계좌 자료 아님·주문 권한 false를 보존한다. 기존 검증된 모의계좌 owner의 recovery RAM/Collector 가격을 명시 선택으로 전달했다. source 무효화/부재는 미확인이다. 명시 live 선택 계좌만 기존 recovery 전후 전체 run 원장을 공통으로 읽고 native 확인 잔량/수동 주문을 연결한다. 결손/변경/미확인 주문은 차단하고 완전한 조회 근거만 목표를 계산한다. 다른 run 원장/체결은 쓰지 않으며 주문 권한은 false다. [전체 대사](docs/AI_TRADING_SYSTEM_REDESIGN.md#50-r4-계좌-전체-대사와-브로커-확인-잔량-계산). [계좌 RAM 계약](docs/AI_TRADING_SYSTEM_REDESIGN.md#49-기존-모의계좌-owner의-ram-관측을-공통-r4-입력으로-전달). [공통 소비 계약](docs/AI_TRADING_SYSTEM_REDESIGN.md#48-r4-목표행동-비교의-기존-공통-shadow관찰-소비-연결).

`evaluate_position_action_comparison_shadow`가 같은 계좌/cut·비용 조건의 명시 목표들을 기존 계산으로 검증하고, `research_evaluation.py`의 완결 행동 자료 짝 비교를 호출한다. 같은 사례/초기 상태/기간·정책/목표/비용/체결 모델·모집단/입력 해상도만 비교하며 비용 차감 평균과 표본/변동성·미확인을 반환한다. 기존 전체 매매 평균을 다른 행동 성과로 변환하지 않는다. 새 `position-action-comparison-shadow/v1` 고정 CLI 경로를 연결했고 기존 목표/시장·테마 v1 결과를 유지한다. 고정 관찰/기존 계좌 RAM·계좌 전체 대사와 HOLD/EXIT 자료의 CLI/PC 연결은 로컬 검증했다. 자동 목표의 native 행동 자료도 로컬 검증했다. 상위 정책/동적 비중 선택·미래 예측 검증과 주문 승격은 후속이다. [현재 R4 비교 계약](docs/AI_TRADING_SYSTEM_REDESIGN.md#47-r4-행동별-비용-차감-관측-기대값과-불확실성-비교).

R4의 첫 포지션 목표 계산은 `application/position_target.py`의 순수 함수다. 기존 `AccountSnapshot`과 대사한 `ExecutionRecord`를 제공받아 명시 목표와 실제/미체결 수량을 비교하고, 기존 연구 비용 예약 함수를 공유한다. `preview_market_theme_shadow.py`의 새 고정 입력 `position-target-shadow/v1` 경로가 같은 계산을 사용한다. UNKNOWN 주문/취소 대기와 미확인 목표·대사·비용은 새 수량 계산을 차단한다. 새 계좌 원장·조회 task·주문 owner를 만들지 않았으며 자동 목표·실제 행동 자료 reader/live 소비 연결은 후속이다. [첫 R4 계약](docs/AI_TRADING_SYSTEM_REDESIGN.md#46-r4-명시-목표-수량과-기존-계좌주문-입력-연결).

## 과거 자료 입력의 경계

기존 후보 DB → 원천별 과거 봉/뉴스 → 출처·시각·해상도가 명시된 사례 → 기존 연구·LLM·테마 프로필 순서다. 새 자료의 사후 후보 선택과 수집시각을 기록한 채 연구 입력을 연결한다. 기존 TOP20 revision을 위조하거나 원본 DB를 새 공급자 값으로 덮는 방식을 쓰지 않는다.

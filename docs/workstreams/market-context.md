# ① Market Intelligence — TOP20 관측 변화 계산

작성: 2026-10-15 · 기준 커밋 `50a70c9ce6cdf20915fde27f2d1f47becb82eadf`

## 최신 상태 — 1분·5분 중심, 초봉 보조로 전환

사용자가 시장 흐름/주도 후보는 1분·5분 가격·대금의 지속성으로 보고 초봉은 보조로 두도록 지시했다. 앞 단계의 짧은 초봉 창을 시장 판단의 필수 조건으로 사용한 범위를 바로잡았다. 아래 초봉17/20·80 cut 결과는 당시 초봉 방법의 검증이며 **새 분봉 방법의 정확도 판정에 재사용하지 않는다**.

### 현재 판단

- 조회순위 변화는 기존 `lookup_change`로 별도 유지한다. Runtime의 `price_trade_leadership`은 이제 **minute-price-trade-candidates/v1**, 기본1m이다. 초봉 관측은 `second_trade_auxiliary`로 분리한다.
- 같은 현재 TOP20/거래소에서 완료된 직전1분 구간과 그 앞1분을 비교한다. 각 구간 첫 분봉 시가→마지막 종가의 가격 변동, 실제 대금 합계를 사용한다. 기존 공통 후보 정책(양의 가격 반응·코호트 중앙값 초과, 대금 양수·중앙값 초과·동률 보존 상위 K)을 재사용하며 조회순위로 후보를 선정하지 않는다.
- **5분 지속**은 현재1분 후보가 최근5분 누적 가격·대금 우위도 갖고, 그5개 분 각각의 같은 가격·대금 후보 조건을 **3개 이상** 만족할 때 확인한다. 현재5분 자료만으로 지속을 확인할 수 있다. 과거5분과의 교체 비교(timeframes.5m)는10개 분봉이 필요하며 부족하면 그 변화만 UNKNOWN이다. 5분 이력이 부족해도 완전한1분 관측을 막지 않는다.
- 모든 비교 분봉은 당시 available_at/updated_at, 같은 거래일·venue, complete/actual·출처·분 시작시각을 확인한다. 미완성·추정 대금·미래 봉·결측을 완료 봉/0으로 만들지 않는다. 같은 분의 최신 가용 revision을 먼저 선택하므로 늦은 partial 정정이 과거 complete 값을 유지하지 못한다. 불변 행/metadata로 기존 입력 복사·회차 공유의 값/타입/계보를 보존한다.
- 분봉 마감의 기존2초 지연을 적용한 경계를 사용한다. 분봉의 기본 유효기간은 마지막 완료 구간 끝+90초와 순위 소속의 기존 H1 TTL 중 빠른 값이다. H2 초 창/3초 등 설정, 초봉 부족/실패/만료는 이 판단·유효기간에 들어가지 않는다. 기존 H1 v1 분류·H2/타점·보호 정책 자체를 변경한 것은 아니다.

### 기존 연결과 읽기 경계

새 Collector/시세 축적/DB writer/브로커 TR/등록을 만들지 않고 기존 `Collector.load_live_minute_bars`를 사용한다. 이 reader가 기존 저장 봉과 미저장/ACK 대기 delta를 원래 방식으로 겹쳐 읽으며 계산은 완료 봉만 사용한다. 거래대금은 기존 trade_value_million_won 열의 단위를 WON으로 한 번 변환한다.

기존 Runtime에 코호트/분 경계/원래 minute_bars·minute_finalizations 저장 완료 표식으로 제한한 10분 자료 cache와 **단일 소유 비동기 읽기 작업**을 추가했다. 이 작업은 별도 수집기/상시 worker가 아니라 기존 reader 요청의 수명이다. 유효한 소속만 최대200(code,venue) 예산으로 읽고 2/8 소비자는 같은 코호트20번 읽기를 공유한다. 새 분/소속/원래 저장 ACK 변화 때만 갱신하며 상태/화면100회 조회는 추가읽기0이다. 원래 reader의 연결·transaction·겹침/중복 규칙은 유지한다. 읽기 연결 수는 추가될 수 있으며 이를 원래 writer/COMMIT 수와 혼동하지 않는다.

**분봉 I/O를 기존 H1/H2/보호 처리 앞에서 기다리지 않는다.** 준비 중에는 부가 분봉 관측만 미확인이고, 읽기 완료 시 정확히 연결된 core 결과/원래 cut의 별도 관측만 갱신한다. H1/H2를 재판단하거나 청산/수량 결과를 수정하지 않는다. 원래 cut 이후 자료는 제외하고, cut과 실제 반영시각(published_at)을 구분한다. 종료는 수락한 읽기 작업을 join하고 cache/대상을 정리한다. 기존 선별 기록 signature/한 행/1MiB 예산은 유지하며 분봉 부가 변화만으로 새 기록/주문을 만들지 않는다.

PC 기존 시장 열에 `1분 가격·대금 후보`, `5분 지속`, `1분 TOP20 관측: 가격 상승 비율/대금 변화`를 표시한다. tooltip은 분봉 기준/반영시각·범위·금액·부족 사유와 초봉 보조 역할을 설명한다. 구형 저장 관측/직접 초봉 v1 호출은 당시 의미를 유지한다.

### 검증과 실제 자료 한계

관련 **129건/native 종료0**을 최종 확인한다. 새 분봉 순수11·연결5·표시1을 포함한다. 1분 교체와5분 지속 미확인 구분, 현재5분/이전5분 분리, 미래/부분/추정/잘못된venue·정정/동률/불변 입력, 초봉 오류 중 정상 분봉, 2/8 공유·서로 다른 H2 설정, 네 기존 정책/IMMEDIATE_EXIT, 독립 만료·nested 후보 제거를 검사했다. 실제 SQLite 기존 writer/reader로 단위/연결과 파일 불변·20읽기/100회 추가0을 확인했다. 분봉 native 읽기를 막아 둔 동안 다음0B의 기존 판단도 진행되며, close/취소는 작업 종료를 기다림을 확인했다. 이전 원본 writer4·선별 기록1 검사도 통과했다. 로그는 `minute-regression.log`다.

변경 Python10개 구문 검사와 diff 공백 검사를 통과했다. HEAD의 기존 시장 특징 정의26개는 AST가 동일하다. 실행·정적 확인 요약은 기존 진단 디렉터리의 `minute-implementation-verification.json`에 남겼다.

NAS trace의 같은198개 chunk bytes/SHA256와 선택 payload를 읽기 검증하고 원래 분봉 저장7회/마감4회의 입력을 **격리 SQLite fixture**의 기존 writer/reader에 적용했다. 실제운영 DB/시각은 바꾸지 않았다. 10월13일09:02:03/03:03/04:03/05:03의 원래 TOP20에 해당하는87/86/83/80개 fixture 행은 모두 당시 cut 이후 가용시각으로 기록돼, 새1분·5분 방법에서도 네 시점 모두 UNKNOWN/지속 미확인으로 거절했다. 과거9시간 시각 문제를 초봉만의 문제로 감추거나 새 방법의 성공으로 바꾸지 않았다. `native-minute-validation.json`/`verify_native_minutes.py`에 범위와 결과를 남겼다. 이는 선택 원본 입력의 시점 적격성 검사이며 full Replay/주도주 정답/수익성 수락이 아니다.

기준 HEAD50a70c9와 앞 로컬 변경을 보존했다. 새 제품 파일/계층은 없으며 주요 변경은 기존 특징/Shadow/입력/Runtime/UI5개다. 분봉 경로 이해에는 기존 Collector/분봉 reader2개가 추가로 관련되고, core H1/H2/보호 호출 깊이는 유지한다. 순수 후보 정책 공용 helper와 관측 연결 helper는 실제 두 사용 경로의 동일 규칙을 재사용한다. 독립 시세 owner/장기 기억 DB/주문 계층은 없다. explicit scientific source hash는 달라질 수 있고 기존 패키지를 재표지하지 않는다. legacy None hash/기존 시장 타입·함수 AST는 유지한다.

로컬 미배포이며 실제 앱·NAS 재시작/배포·주문·커밋/push는 없다. 정상 시각의 완전한 분봉/소속으로 실제 운용·전진/OOS와 통합 source gate/전수/외부 CI를 확인해야 한다. 현재 모델은 최신 turn_context gpt-6.1-sol/high로 확인했고 전환은 없다.

## 후속 확인 — 10월15일 실제 TOP20 분봉의 시점 적격성

새 분봉 방식의 실제 검증을 위해 NAS 저장 시장자료 GET만 읽었다. 현재 active release는 여전히 `2026.10.14-recorder-shared-10m-v1-af0f8072f960415b`이며 health 정상/trace OFF/diagnostics idle이다. 로컬 시계 수정은 NAS에 반영되지 않았다. 설정·DB·배포·재시작·수집 요청·계좌/주문은 변경하지 않았다.

먼저 대표 종목005930의 10월14일/15일 native 분봉과 두 날짜 TOP20 저장본을 확인했다. 전날 분봉1,326개 중 실제 거래대금13개, 추정1,313개였고 오늘 조회본72개 중 실제16개·추정54개·metadata 미확인2개였다. 대표 종목 표본을 전체 시장 품질로 일반화하지 않는다.

이어 **10월15일09:02:03~09:10:03 KST의 9개 고정 cut**을 선택하고 각 cut 당시 저장된 실제 TOP20의 합집합26종목을 KRX 분봉 GET26회로 읽었다. 요청 실패0, 최신 projection271행(실제217·추정33·value kind 미확인21)을 로컬 진단 자료로 보존하고 rows/membership SHA256를 남겼다. 각 cut의 소속20종목을 그대로 사용하며 코호트를 임의 축소하거나 시각을 보정하지 않았다.

| cut KST | 완료10분 범위의 실제 대금 행 | 당시 가용시각보다 미래인 행 | 1분 후보 | 5분 지속 |
|---|---:|---:|---|---|
| 09:02:03 | 26 | 26 | UNKNOWN | UNAVAILABLE |
| 09:03:03 | 44 | 44 | UNKNOWN | UNAVAILABLE |
| 09:04:03 | 57 | 57 | UNKNOWN | UNAVAILABLE |
| 09:05:03 | 74 | 74 | UNKNOWN | UNAVAILABLE |
| 09:06:03 | 97 | 97 | UNKNOWN | UNAVAILABLE |
| 09:07:03 | 116 | 116 | UNKNOWN | UNAVAILABLE |
| 09:08:03 | 136 | 136 | UNKNOWN | UNAVAILABLE |
| 09:09:03 | 152 | 152 | UNKNOWN | UNAVAILABLE |
| 09:10:03 | 172 | 172 | UNKNOWN | UNAVAILABLE |

이 범위의 실제 봉 가용시각은 분 종료 후32,402.39~32,403.51초로 기록돼 있다. 9개 cut 모두 비교 가능한20종목 중0종목이고 조회 첫 종목005930은 유지됐다. **조회 첫 순위 유지와 주도 후보 미확인은 별도 결과**다. 반복 cut의 행은 겹치므로 합산한 행 수를 독립 표본 수로 쓰지 않는다. 순수 후보 함수를 호출하지 않은 별도 원본 필드 시각/complete·actual 검사에서도9개 cut 모두 적격 실제 분봉0으로 일치했다. 추정값을 실제 대금으로 인정하지 않았다.

결론은 현재 최신 projection에서 당시 판단에 부적격한 자료를 거절했다는 것이다. 이 GET은 불변 revision 이력 export가 아니므로 이전 가용 revision의 존재/부재, 원본 전체 Replay나 주도 후보 정확도·수익성을 확정하지 않는다. 실제 검증 재개에는 정상 시각으로 기록한 완료 분봉과 당시 소속이 필요하다. 로컬 시계 수정의 NAS 반영은 쓰기/배포 경계이며 이번 읽기 전용 권한으로 수행하지 않았다. 같은 시각 오류 자료의 추가 조회로 정확도 통과를 만들지 않는다.

근거는 기존 진단 디렉터리의 `current-native-minute-survey.json`, `current-native-minute-series-validation.json`, `current-native-minute-independent-check.json`, `verify_current_minute_series.py`다. 이번 단계는 제품 코드 변경0이고 이전129건을 재실행하지 않았다. 새 진단 스크립트 구문/문서 공백 검사를 별도로 확인한다. 모델은 이번 최신 turn_context의 gpt-6.1-sol/high를 확인했으며 변경 없다.

## 이전 확인 — TOP20 코호트 부족 사유 분리·표시

사용자의 후속 `다음` 지시로 시계 수정 뒤에도 전체 TOP20 후보를 확정할 수 없는 이유를 종목별로 대조했다. 기존 source 승인/continuous_from/retention/gap·동일 cut 자료만 사용하며 새 구독/수집 요청이나 NAS 쓰기는 없다.

**확인된 자료 경계:** 같은 checksummed trace의 10월13일09:00:40~09:01:59, 80 cut/1,600 종목×시점에서 별도 trace-wall 수신 재구성을 다시 평가했다. 비교 요건을 충족한 종목은 가장 좋은 시점도 **17/20**이었다. 부족 사유는 시세 신선도831, 두 창 표본382(이전220/현재316), 연속성 확인123이다. 연속성123 중 source 승인 없음30/시작 미확인30, 승인 후20초 비교 이력 부족93을 구분했다. 사유는 중복될 수 있어 합산해 종목 수/거래 유실 건수로 해석하지 않는다.

09:01:55의17/20에서 부족한 세 코드를 원문과 대조했다.

| 코드 | 해당 cut 전 원문 근거 | 부족 요건 |
| --- | --- | --- |
| 011080 | KRX 0B1,578개, 마지막 체결초09:01:43/trace 수신09:01:47.074 | 현재 창09:01:45~55에 관측 가격 없음 |
| 014990 | KRX 0B12개, 마지막 체결초09:01:15/trace 수신09:01:16.405 | 이전/현재 창 가격 없음 |
| 079650 | prefix에서 cut 전 파싱한 해당 코드0B 없음 | 두 창 가격 없음 |

이는 제공된 수신 원문/관측 집합의 부족이며 **거래소 체결 유실·미거래·구독 장애의 원인 확정이 아니다**. 저장 가용시각이 잘못된 원본의80 cut UNKNOWN, 재구성 TOP20의80 cut UNKNOWN, 제한 두 종목의 COMPLETE27/독립 계산 일치는 그대로다. 원본 시각을 보정하거나 전체 Replay 승격을 하지 않았다.

**구현:** 기존 `MarketPriceTradeInputs`에 선택 continuity_reasons를 추가했다. 기존 RAM adapter가 승인 없음·연속 시작 미확인/신규 이력 부족·보존 이력 부족·구간 gap·소속 품질/거래일·시세 변환 일부 미확인을 구분한다. 같은 순수 계산에서 이전/현재 표본 부족과 현재 관측 부재를 별도 기록하고 quality.coverage에 기대/평가/확인 종목 수와 경계별 수를 제공한다. 사유 분해는 기존 연속성·표본·TTL 조건과 동일하며 문턱·후보 규칙을 완화하지 않는다. 부분 종목만으로 상대 순위를 확정하거나 부분 후보를 COMPLETE로 승격하지 않는다.

PC 기존 시장 열에 `판단시점 시세자료 17/20종목` 같은 cut 당시 상태와 종목별 이전/현재 표본 수·한국어 사유를 표시한다. 완료 관측의 만료 투영에서는 이 수를 현재 자료로 보여주지 않고 `시세자료 확인 만료`로 표시한다. 원래 cut의 진단과 현재 후보/TTL을 구분하며 기존 후보 판단·보호/알림·주문·선별 기록 계기는 유지한다.

**관련112건 통과·native 종료0:** 두 신규 결측/표시 검사와 source 승인/시작/신규 이력/retention/gap/future coverage, 독립 TTL, 2/8 가격/H1 공유1, 네 보호 정책, 기존 writer/COMMIT4·선별 recording1·시계/실제 RAM SQLite 연결을 검증했다. 로그 `coverage-regression.log`, 종목별 근거 `coverage-diagnosis.json`/`missing-code-receipts.json`은 기존 ignored 진단 디렉터리에 있다. 이전202건은 앞 시계 수정 단계 결과이며 이번 소스의 전수/CI 수락으로 합산하지 않는다. NAS는 미배포이고 운영 자료·환경·trace를 변경하지 않았다. 모델은 최신 turn_context gpt-6.1-sol/high로 확인했고 변경은 없다.

이 담당 구현의 관측 가능 경계와 부족 사유를 구체화했다. 정상 시각·전원 시세·완전 녹화의 실제 장중/전진/OOS 수락은 여전히 운영 후속이다.

## 이전 확인 — 시장 시계 연결 원인 수정·검증

사용자의 `다음` 지시로 직전 검증을 막은 약9시간 가용시각 차이를 추적했다. 현재 HEAD는 같은 `50a70c9ce6cdf20915fde27f2d1f47becb82eadf`이며 앞 단계 로컬 변경을 보존했다.

**확인한 원인:** `KiwoomRestClient.server_now()`는 기존 계약상 **시간대 없는 KST**를 반환한다. 실제 NAS 활성 release와 워크트리 모두 app에서 이 값을 Collector/MarketEventService에 그대로 주입하고, 이 owner들이 `.timestamp()`로 epoch를 만든다. naive datetime은 호스트 시간대에 따라 해석되므로 UTC에서는 KST09시를 UTC09시로 저장해 +32,400초가 된다. NAS host 자체는 KST임을 별도로 확인했다. 운영 컨테이너 환경값을 직접 조회한 것은 아니며, trace 시각과 epoch의 UTC 해석 일치·현재 실행 소스·별도 UTC 재현으로 코드 원인을 확인했다. 두 분 안의 수신 원문과 대응 가능한 native 초봉2,644행의 epoch는 naive source_time을 UTC로 해석한 값과 모두1초 이내, 최대0.118초 차이로 일치했다.

**최소 수정:** 기존 `app.py`의 두 시장자료 시계 주입점에 이미 있는 `as_kst`를 적용했다. 클라이언트의 기존 naive KST 계약은 유지하고, Collector/시장 관측 내부 시계는 aware KST가 된다. 새 시계 owner/함수·수집·등록·writer·schema·transaction·주문 경로가 없다. 기능 이해 파일 수/호출 깊이는 기존 app→owner와 기존 as_kst 변환 범위다. 계좌 시계는 이미 같은 변환을 사용하고 있어 변경하지 않았다.

**같은 조건의 전후 검증:** 실제 `create_app`→Collector→0B 파싱→RAM→SQLite 초봉 저장/기존 시점 제한 reader를 사용한 격리 시험을 추가했다. 변경 전 naive KST/aware UTC 두 입력 모두 KST 경계 검사에서 실패했다. 변경 후 두 입력 모두 올바른 KST 거래일·초·epoch·RAM cut을 갖고 저장행과 RAM행이 일치했다. 가격100·수량2·대금200원도 유지됐다. token/TR 호출은 시험에서 차단했고 앱 lifespan/실제 브로커 주문을 시작하지 않았다. 시험 중 reader 필수 범위를 빠뜨린 fixture 오류는 기존 계약대로 명시해 수정했다.

동일 app의 현재 시계 식을 AST로 대조하고 **NAS 사용자 Python의 별도 UTC 프로세스**에 메모리로만 전달해 재현했다. 기존 epoch1791882040, 수정 epoch1791849640이며 **오차32,400초→0초**다. 운영 컨테이너·파일·시계 설정을 변경한 것이 아니다.

**관련202건 통과·native 종료0:** 이전 시장/RAM/UI109건, 새 실제 연결1건, Collector·초/분봉·market events·승인 수신·기존 worker/fact delivery·app 수명/인증 시장 읽기를 검증했다. 기존 writer/COMMIT4, 녹화 선택1, 2/8 공통 RAM1·H1 공유1, 보호 정책/취소 drain과 조회 시점 제한을 유지했다. `clock-regression.log`와 `clock-root-cause.json`은 기존 로컬 진단 디렉터리에 남겼다. httpx deprecation 안내가 있었으나 지원되는 기존 경로로 실행했으며 httpx2 설치·의존성/검사 조건 변경은 없다. 전체 Windows/외부 CI 수락은 이202건과 구분한다.

**현재 운영 경계:** NAS 활성 release는 `2026.10.14-recorder-shared-10m-v1-af0f8072f960415b`, health 정상·trace OFF·diagnostics idle을 읽기 확인했다. 이 수정은 **로컬 미배포**다. 과거 저장행/available_at을 보정하지 않았으며 기존80 cut의 UNKNOWN·trace incomplete·TOP20 코호트 결측을 승인 상태로 바꾸지 않았다. 앞으로 정상 시각으로 기록되는 전원 시세와 완전한 녹화의 전진/OOS 수락이 필요하다. NAS 배포·재시작·주문·커밋/push는 수행하지 않았다. 모델은 최신 turn_context `gpt-6.1-sol/high`를 확인했고 변경은 없다.

아래의 발생 원인 미확정 표현은 앞 단계 기록이며 이 절의 확인/수정 상태가 우선한다.

## 이전 확인 — 조회순위와 가격·대금 후보 분리 및 시간순 검증

2026-10-15 사용자가 기존 공통 연결·표시 구현과 **NAS 저장 기록 읽기 전용 검증**을 승인했다. 아래 과거 절의 NAS 미조회 표현은 이전 단계 기록이며 현재 상태는 이 절이 우선한다.

### 구현 범위

- `lookup_change`는 최근 두 완성 TOP20의 첫 조회순위 유지/교체다. 가격·대금·테마 결측과 분리한다. 기존 `pattern`과 v1 시장 분류는 보존한다.
- `price_trade_leadership`은 당시 TOP20 소속의 같은 거래소 0B 관측을 두 인접 창에서 비교한다. 가격 상승률이 양수·비교 집합 중앙값 초과이며, 관측 거래대금도 양수·중앙값 초과·상위 K일 때만 후보다. 대금 동률은 같은 순위를 유지한다. 조회순서·뉴스·테마·합성 점수로 고르지 않는다.
- 기본 창10초·가격 만료3초는 기존 H2 관찰 설정, 소속 만료는 H1 `max_gap_seconds`를 따른다. 동일 현재 코호트로 이전/현재 창을 비교해 구성 변경과 가격 후보 교체를 구분한다. 창별 가격 표본2개·승인 source/retention/gap 연속성·같은 거래일·당시 가용시각·가격 신선도가 모두 필요하다. 한 종목이라도 부족하면 전체 비교 UNKNOWN이다.
- 후보 유지/진입/이탈, 가격 bps·대금 WON·동률 순위, 관측 집합 가격 상승 비율·대금 합계/집중을 제공한다. 전체 시장 폭/대금·대장 확정·수익성·매수 허가가 아니다. 수신 0B 대금은 전체 거래소 체결이나 순매수 금액이 아니다.
- 기존 RAM snapshot 한 번에 이미 수집된 TOP20만 같은 200코드 예산으로 읽는다. 새 REG/Collector/DB 조회·writer·owner가 없다. 회차 cache에서 2/8 소비자별 가격 후보 계산을1회 공유하고 H1 공유·H2/보호 결과를 보존한다. 입력 변환/계산 실패는 부가 관측만 UNAVAILABLE이다.
- PC 시장 열에 `조회순위 변화`와 `가격·대금 주도 후보`를 별도로 표시한다. 시세의 짧은 만료가 조회순위를 만료시키지 않는다. 원본 receipt를 보존하며 조회 투영만 UNKNOWN/STALE로 바꾼다. 선택 기록 계기/1MiB 예산/한 행/주문 OFF는 유지한다.

### 실제 기록 검증

기존 인증 저장 조회 API로 10월14일 TOP20 **2,812개**, 10월13일 **2,839개**를 읽었다. 10월14일을 당시 saved_at 순서로 평가한 첫 조회순위는 **유지2,738 / 교체64 / 미확인10**이다. 64회를 실제 주도주 교체 횟수로 보고하지 않는다. 조회 payload에는 `bigd_rank/past_curr_prc/base_comp_chgr`가 있으나 `trde_prica/flu_rt`는 없다. 빠진 수치를 추정해 채우지 않았다.

NAS trace `20261012T235950Z-fb98b0ddcee9`의 08:59:53~09:05:03 KST **198개 chunk bytes/SHA256**와 선택 시장 payload 해시를 검증했다. prefix 거절0이나 **전체 manifest incomplete·거절4**여서 전체 Replay 승인에 사용할 수 없다. 계좌 payload는 해독하지 않았다. 초봉 저장 호출4개/행6,022개와 09:02 이전 Collector 입력2,851개를 로컬 시장 검증 사본으로만 추출했다. NAS DB/설정/추적 상태/배포는 변경하지 않았다.

10월13일 **09:00:40~09:01:59의 80개 cut**을 평가했다.

| 경로 | 결과 | 해석 |
| --- | --- | --- |
| 저장 초봉의 원래 available_at 그대로 | UNKNOWN80 | 6,022행 전부 당시 cut 이후 가용으로 기록돼 과거 입력 부적격 |
| trace wall 수신시각으로 기존 순수 parser/초 집계를 별도 재구성, 실제 TOP20 비교 | UNKNOWN80 | 코호트 전원의 표본/연속성/신선도가 부족해 전체 후보 확정 차단 |
| 명시한 두 종목 000660/005930의 RECORDED_COHORT 진단 | COMPLETE27 / UNKNOWN53 | 원본 runtime/전체 TOP20 재현과 구분한 제한 관측 |
| COMPLETE27개를 독립 창별 가격·대금 합계/중앙값/순위와 대조 | 일치27 | 유지1·교체4·새 후보6·조건 이탈13·후보 없음3; 조회 첫 순위는80개 모두 유지 |

예: **09:01:34 KST** 조회 첫 순위 유지 중 위 두 종목 코호트의 가격·대금 후보가 000660→005930으로 교체됐다. 이는 제한 코호트의 수신 원문 진단이며 전체 시장 주도주 정답이 아니다.

저장 초봉의 available_at과 체결초 차이는 **32,396.678~32,405.999초(약9시간)**였다. trace wall과 payload source_time(KST 해석)의 차이는0.027~3.378초다. **발생 원인은 미확정**이며 원래 저장 시각을 자동 보정하지 않았다. 장후 조회로 저장된 10월14일 005930 분봉도 origin=query·OHLCV estimated 대금·장후 가용시각이므로 당시 정답에 쓰지 않았다. 역사 TRAIN/VALIDATION의 posthoc 후보 집합도 실제 TOP20로 바꾸지 않았다.

검증 사본/재실행 도구는 `data/diagnostics/market-flow-verification/{verification.json,series-results.json,lookup-validation.json,trace-prefix-integrity.json,extract_trace.py,verify_series.py}`다. ignored 로컬 산출물이며 새 제품 모듈/수집기/시험 catalog가 아니다.

### 완료와 잔여

이 담당 범위의 구분 계산·기존 관찰 연결·자료 부족/미래 입력 차단·시간순 대조를 완료했다. 수익성/전략 효과·전체 시장 주도주 정답 검증 완료를 뜻하지 않는다. 남은 운영 수락은 TOP20 전원의 당시 가용·연속 시세 확보, 기록 시각 차이 원인 확인, 완전한 녹화의 전진/OOS 검증, 통합 source gate/Windows all-local/외부 CI와 운용 승인이다. 실제 앱 실행·주문·NAS 배포/커밋/push/병합은 없다.

최종 관련 **109건 통과**, native 종료0이다(기존 연결91 + 새 후보/소속·RAM 변환14 + 공유/독립 만료/보호3 + UI1). 같은 scope의 2/8 가격 계산1·H1 계산1, 조회100회 추가 RAM/DB/계산0, 순위 교체 중 후보 유지·순위 유지 중 후보 교체, 네 기존 정책/IMMEDIATE_EXIT 결과와 ID 보존, 시세/소속 만료·원본 보존을 확인했다. 기존 0/2/8 writer/COMMIT4와 선별 recording1 유지도 다시 통과했다. 변환 import 누락은 Runtime 실제 공유 검사로 발견해 최소 수정 후 같은 검사와 전체 관련109건을 통과했다. UI fixture의 표시 기대값은 실제 한국어 표시로 바로잡았다. 운영 시각 차이와 이 격리 시험 문제를 구분한다. 로그는 `data/diagnostics/market-flow-verification/unit-tests.log`다.

현재 모델은 최신 turn_context `gpt-6.1-sol/high`를 확인했으며 모델 라우팅 변경은 없다. 과거91건은 이전 연결 단계 결과다. 별도 전체 Windows/외부 CI/배포 수락으로 보고하지 않는다.

## 이전 상태 — 기존 앱 관찰 경로 연결 완료

**왜 필요한가:** 독립 계산을 기존 앱의 읽기 전용 관찰 경로에서 재사용해야 사용자가 시간순 변화를 확인할 수 있다. 사용자가 `계속 진행해`로 범위 확대를 승인한 뒤 기존 공통 연결/표시 파일과 소유 시험까지 수정했다.

**가능해진 판단:** 기존 시장·테마 관찰을 선택해 실행하면 TOP20 첫 순위 집중/교체/유지·분산 순위 회전과 후보 유지/이탈/재등장 관측을 같은 RAM 입력으로 제공한다. PC 관찰 표의 시장 열에서 기존 v1 분류와 별도로 TOP20 변화를 표시하고 tooltip에 관측·판단·만료시각과 후보 수/근거를 제공한다. 테마/뉴스가 없는 시장 관측도 계산하며, 전체 시장이나 매수 승인으로 확대하지 않는다.

**기존 연결:** `market_theme_shadow`의 기존 H1 회차 cache에서 현재 특징을 재사용하고 이전 특징만 한 번 추가 계산한다. `_current_features`는 전체 내용·타입·계보·parameters가 확인된 기존 owner 전용 입력이다. 독립 호출은 종전처럼 현재/이전을 계산한다. 기존 Runtime이 공유 실행에서 관측을 요청하며, 성공한 monitor 결과에 별도 receipt를 묶는다. 기존 v1 context/stock-thesis의 내용·ID·보호 판단에 부가 관측을 넣지 않고 RAM envelope의 optional map/ref로 전달한다. 같은 시장 cut의 2/8개 소비자에서 현재 특징1·이전 특징1·변화 계산1을 확인했다.

**승인 상태:** 격리 fixture의 Runtime→기존 인증 ASGI 조회/선별 기록 경로와 Qt 표시 검증을 완료했다. 관측 계산 실패에도 기존 네 타점 정책의 결과/ID가 동일하고 IMMEDIATE_EXIT의 EXIT가 유지됐다. 실제 앱을 원본 data로 열거나 실제 모의/실계좌·가짜 브로커 주문 전송을 실행하지 않았다. 기본 관찰 선택 OFF/기존 구독 범위·Collector·DB writer는 유지하며 NAS·CI·배포/커밋/push/병합은 미실행이다.

**남은 것:** 실제 장중 입력/앱 수락, 병렬 변경 전체 통합 뒤 Windows all-local 한 번과 동일 LF source gate·외부 CI, 별도 모의계좌/운용 승인이다. 테마 없는 독립 상시 market observer를 새로 만든 것은 아니며 live 관측은 기존 시장·테마 Runtime의 활성 binding을 사용한다. 일봉 Stock Memory·기업/뉴스 맥락·전체 시장 자료와 연구 성과 검증은 후속이다. NAS 빌드 표시 3파일은 이번 미배포 워크스트림에서 바꾸지 않았으며 실제 통합/배포 때 현행 빌드/검증 계약을 적용해야 한다.

최종 관련 고유 **91건 통과**(신규 변화 계산20 + 신규 연결/표시7 포함), Python native 종료0을 별도로 출력해 확인했다. 7개 Python 파일의 구문 검사·기존 시장 특징 타입/함수 AST 동일성·diff 검사를 확인했다. 100회 조회의 추가 계산/DB 접근0, 원래 TTL 만료 투영/원본 receipt 보존, 결과 변이 격리, 실패 회차 재계산, 취소/close drain, 기존 0/2/8 원본 writer/COMMIT 수 유지도 검증했다. 1MiB 기록 예산 초과 시 관측 부가정보/ref만 먼저 생략하며 기존 선택 판단/한 행/기록 signature를 유지한다. 관측 변경 자체로 쓰기를 추가하지 않는다.

기본 샌드박스에서 종료 결과가 없던 실행은 중단했으며 통과로 세지 않았다. 사용자 Python 문맥에서 AppData 전용 TEMP/TMP/TMPDIR를 사용했다. 새 UI 시험의 fixture 외피 참조 오류 1건은 수정한 뒤 최종 91건을 검증했다. 저장 실패/ACK 유실 로그는 기존 의도적 장애 fixture의 검증이며 운영 장애가 아니다. httpx2 설치/위장·의존성 변경·검사 판정 완화는 없다.

수정 범위는 기존 계산/Shadow/Runtime/UI 4개 Python 파일, 기존 소유 시험3개, 본 보고서와 API_CONTRACT/MODULE_MAP/CHANGELOG다. 새 계층·수집기·저장소·worker·주문 경로는 없다. 주요 실행 경로는 기존 H1→공통 RAM→기존 인증 조회→PC 표를 유지하며 관측 선택 계산과 별도 receipt/투영만 추가했다. 상세 단위/자료 요구는 아래 독립 계산 계약과 [API 계약](../../API_CONTRACT.md)의 TOP20 관측 변화 절을 따른다.

아래에는 독립 계산 단계의 계약과 당시 후속 연결 제안을 보존한다. 연결/공통 문서 미수정·44건 등의 표현은 그 단계의 기록이며 현재 상태는 이 절이 우선한다.

## 왜 필요한가

기존 `MarketResearchFrame/Features`는 TOP20의 특징을 계산하며, v1 시장 분류는 테마 소속과 성숙한 돌파 결과까지 요구한다. 이 분류를 완화하면 기존 연구·Shadow의 의미가 바뀐다. 반면 테마·뉴스·성과 자료 없이도 완성된 순위 프레임의 교체, 관측 대금 비중 변화, 종목의 유지·이탈·재등장을 확인할 수 있다. 이 관측을 기존 모듈의 선택 함수 `compute_market_context_changes`로 제공했다.

00 종합의 공통 시장 수치/해석 분리, ①10의 시점·근거·이전 판단 대비 변화, ②7의 TOP20 한계와 조회순위/대금순위 구분을 적용했다. 종목별 관측 이력은 호출자가 제공한 당일 프레임만 사용한다. 일봉 Stock Memory나 사건·기업 맥락을 합성하지 않는다.

## 실제 매매에서 어떤 판단이 가능해지는가

- 조회순위 첫 종목의 유지와 교체, 해당 종목의 관측 TOP20 대금 비중 집중을 구분한다.
- 상위 K 교체, 전체 TOP20 Jaccard 변화, 첫 순위 반복 교체, 현재 첫 종목의 상위 K 체류를 함께 보고 분산 **순위** 회전을 관측한다. 돌파 실패·자금 순유입·수익성을 확정하지 않는다.
- TOP20 후보가 직전에도 보였는지, 최신 완성 프레임에서 빠졌는지, 제공된 이력에서 사라졌다가 다시 보였는지 확인한다. `LEFT/ABSENT`는 시장에서 거래가 사라졌다는 뜻이 아니다. `ENTERED`는 제공된 당일 이력 안에서만 처음 나타났다는 뜻이다.
- 유지된 종목의 당일 등락률 차이와 당일 누적 거래대금 차이를 근거 ID와 함께 확인한다. 자료가 없으면 해당 차이는 `None`이다.

이 결과는 전략 평가의 입력 후보다. 매수 적격·전략 성공·목표 비중·매도 지시를 생성하지 않는다. 조회순위 1위는 대금 1위나 실제 시장 대장으로 확정되지 않는다.

## 기존 기능과 연결

수정 기능 파일은 `src/kiwoom_monitor/application/market_research_features.py` 한 개다. 불변 결과 DTO `MarketContextChanges/MarketCandidateObservation`과 선택 함수만 추가했다. 기존 모든 공개 타입·함수와 v1 상수/분류 규칙은 유지했다. 정상 입력의 특징과 분류는 기존 `compute_market_research_features → classify_market` 결과를 재사용한다. 전략별 판단은 기존 `assess_market_for_strategy`를 그대로 사용하며, 별도 정책을 자동 선택하지 않는다.

현재 소비 경로는 다음과 같다.

| 기존 위치 | 현재 책임 | 이번 결과와의 연결 제안 |
|---|---|---|
| `application/research_factors.py::compute_market_regime` | 기존 H1 특징과 v1 분류 Factor | v1 출력·등록을 유지하고, 독립 관측이 필요한 소비자에 새 함수의 결과를 선택 제공 |
| `application/market_theme_shadow.py::_evaluate_shadow_market` | 동일 cut/venue/window, 성숙 결과 가용시각, H1 TTL | 이미 가진 공통 프레임·판단 시각을 한 번 전달. v1 시장 분류와 관측 변화의 품질은 별도 표시 |
| `central_server/market_theme_shadow_inputs.py::market_frame_from_live_membership` | 검증된 20칸 RAM projection을 기존 frame으로 변환 | 새 adapter/Collector 없이 같은 frame 사용. DB 저장 ACK나 원본 체결 완전성으로 확대하지 않음 |
| `central_server/market_theme_shadow_monitor.py::_apply_ranking` | 기존 공통 RAM의 bounded 프레임 이력(최대 1,000개) | 같은 공통 cut에서 한 번 계산해 공유. 전략별 수집·반복 DB 조회·영속 기억 생성 금지 |

이번에는 위 소비 파일·Runtime·UI를 수정하지 않았다. 새 함수는 현재 Factor/Shadow 출력에 자동 포함되지 않는다. 기능 이해에 필요한 생산 코드 파일은 기존 시장 특징 모듈 한 개로 유지했으며, 기존 호출 깊이는 그대로다. 선택 호출에서만 같은 모듈의 시점/품질 검증과 후보 관측 계산이 추가된다. 현재 특징 한 번과 이전 특징 한 번을 기존 함수로 계산한다. 통합 시 같은 cut의 결과를 공통 경로에서 재사용해야 한다.

### 입력·단위·시각 계약

`frames`와 `MarketFeatureParameters`를 사용하며, `as_of`(timezone-aware datetime), `venue`, `window`는 명시한다. 선택한 범위 밖이나 `available_at > as_of`인 frame은 사용하지 않는다. 동일 revision의 동일 내용은 중복 관측으로 세지 않는다. 동일 revision의 상이한 내용 또는 동일 가용시각의 서로 다른 revision은 모호한 순서로 계산하지 않고 `ValueError`로 거절한다.

| 값 | 의미/단위 | 관측 한계 |
|---|---|---|
| `as_of` / `observed_at` | 판단 cut / 최신 frame의 가용시각 | 거래소 event time·수신 지연을 증명하지 않음 |
| `expires_at` | 최신 가용시각 + 기존 `max_gap_seconds` | 기본 90초. `as_of >= expires_at` 또는 KST 거래일 불일치는 STALE |
| `top1/top5_concentration_delta_ppm` | 직전 대비 조회순위 1위/상위 5행의 TOP20 대금 비중 차이 | 1,000,000 ppm = 100%. 전체 시장 점유율이나 순유입률이 아님 |
| `change_bps_delta` | 같은 종목의 당일 등락률(bps) 차이 | 100 bps = 1%p. 두 관측 사이의 가격 수익률 계산이 아님 |
| `trade_value_delta_million_won` | 같은 당일/venue/누적 창에서 유지된 종목의 대금 차이, 백만원 | 다른 창·결측·누적값 감소는 None. 0B 전 체결 수신·무누락을 뜻하지 않음 |
| `presence_count/observation_count` | 제공된 당일 프레임의 출현 수/전체 관측 수 | 거래 사례 수나 하루 전체 체류율이 아님 |
| `input_refs` | 사용한 순위·선택 테마·선택 성숙 결과의 근거 ID | 데이터 영속성·연속성·feed 완전성 승인과 별개 |

패턴은 `LEADER_CONCENTRATION / LEADER_REPLACEMENT / DISPERSED_ROTATION / LEADER_RETAINED / UNKNOWN`이다. 규칙 버전은 `top20_observed_changes/v1`이며, 기존 `top20_market_types/v1` 시장 분류와 독립된 실험적 관측이다. 집중 400,000 ppm, 체류 667,000 ppm 등 기존 v1에 사용하던 수치 수준을 재사용하되 성과 판정으로 해석하지 않는다. 각 결과에는 실제 `parameters`를 포함한다.

### 자료 요구와 미확인 처리

새 관측의 필수 capability는 완성된 TOP20 membership 이력과 TOP20 대금 snapshot이다. 완성 여부는 `capture_quality == complete`와 정확히 20개 고유 종목으로 확인한다. 최근 이력 부족, 부분/짧은 프레임, 관측 공백, 대금 분모 결측/0이면 패턴은 UNKNOWN이며 concentration 차이는 None이다. 가격 결측은 가격 차이만 미확인으로 남기고, 완성된 membership과 대금 관측까지 막지 않는다.

후보별 판단은 해당 근거만 사용한다. 최신 두 프레임이 정상이라면 유지·이탈은 확인할 수 있다. 이전 이력에 공백/부분 수집이 있으면 새 편입을 재등장/최초 등장으로 확정하지 않는다. `candidate_history_status`는 전체 제공 이력의 불확실성을 별도로 표시한다. `quality.status == COMPLETE`는 최근 순위·대금 관측 요건 충족만 뜻하며, v1 분류·가격·뉴스·역사 자료의 완전성을 뜻하지 않는다.

테마는 `theme_evidence`에서 별도 표시한다. 당시 frame의 선택 revision과 실제 관측 소속이 있을 때만 OBSERVED, 없으면 UNKNOWN, frame이 만료됐으면 STALE다. 기존 첫 대표 테마 배분 정책과 coverage를 명시하며 뉴스의 인과 근거로 사용하지 않는다. 이 타입에는 테마 revision 자체의 가용시각이 없으므로 `revision_available_at=None`을 유지하고 미확인 사유를 표시한다. 테마의 `observed_at`은 해당 소속을 포함한 프레임 시각이며 테마 수정 시각이 아니다. 통합 소비자는 기존 테마 snapshot owner의 가용시각/신선도 검증을 재사용해야 한다.

선택 `breakout_summary`는 `breakout_available_at`과 `breakout_input_ref`를 함께 요구한다. 미래 근거는 v1 분류에 넣지 않고 입력 refs에서도 제외한다. 근거 부재는 성숙 사례 0건으로 기존 UNKNOWN을 보존한다. 실제 증거 생산/완결 여부·별도 TTL 검증은 기존 성숙 결과 owner의 책임이다.

## 연구·Shadow·브로커·배포 승인 상태

| 단계 | 이번 상태 |
|---|---|
| 읽기 전용 계산/격리 연구 fixture | 구현 및 관련 검사 완료 |
| 기존 Factor/Shadow 회귀 | 관련 경로 유지 확인. 새 결과의 자동 실행 연결은 미구현 |
| 연구 성과/전진 검증/시장 해석 품질 | 미검증. 관측 규칙을 검증된 전략으로 표시하지 않음 |
| 가짜 브로커 | 이번 작업에서 실행하지 않음 |
| 실제 모의계좌·실계좌 | 연결/전송/승인 없음 |
| NAS·운영 DB·배포·CI | 접근/쓰기/배포/외부 CI 실행 없음 |
| 커밋/push/병합 | 수행하지 않음. review 가능한 로컬 변경만 유지 |

시작 때 워크트리 HEAD가 공통 기준 커밋과 같고 작업 트리가 깨끗한 detached HEAD임을 확인했다. 원본 main을 변경하지 않았다. 테스트 import가 이 워크트리 `src`를 가리키는 것을 확인했으며 원본 `.venv/Scripts/python.exe`는 인터프리터로만 사용했다. `run_test_app_with_data.py`, 원본 data, .env, NAS X:, 운영 계좌를 실행/연결하지 않았다.

## 아직 안 되는 것과 다음 필수 작업

1. 통합 담당이 기존 공통 RAM/Shadow 경로에서 동일 cut의 새 결과를 선택 계산·공유하고 품질·원래 TTL을 소비자까지 유지해야 한다. 새로운 owner·수집기·writer·기억 DB는 필요하지 않다. 기본 H1/H2와 R4의 의미를 바꾸거나 테마·뉴스를 모든 정책의 요구조건으로 추가하지 않는다.
2. 실제 입력의 관측 cadence, 수집/전달 gap, source 전환, 지연과 데이터 가용시각은 기존 adapter/Runtime에서 검증해야 한다. 가용시각 사이 간격만으로 모든 거래 수신의 연속성을 증명할 수 없다.
3. 일봉 기반 최근 강세·전일 연속성·기업/사건 맥락·뉴스 인과·전체 시장 폭/대금·지수 기여 자료는 이번 입력에 없다. 기존 역사 자료/기업 맥락과 시점 계보가 확보돼야 후속 판단을 연결할 수 있다.
4. 결과의 전략별 기여와 수치 문턱은 기존 연구/전진 검증에서 평가해야 한다. 자동 비중·EV·Risk·주문 권한과 보호 청산 경계는 이번 수정 범위가 아니다.
5. 이 파일은 기존 explicit session-profile scientific hash의 구성 파일이다. 선택 기능 추가여도 source hash는 달라질 수 있다. 기존 연구 패키지를 자동 재표지하지 말고 통합 때 동일 LF source gate와 기존 패키지 계약을 확인해야 한다. legacy None hash 상수는 유지된다.

새 시험 모듈을 만들지 않았고 기존 소유 시험 `test_market_research_features`에 추가했다. 별도 catalog/CI 모듈 등록은 필요 없다. 전체 통합 뒤 관련 회귀와 Windows all-local 한 번, 동일 LF source gate, 외부 CI·실제 모의계좌·운용 승인을 각각 확인해야 한다. 공통 문서/CI manifest 수정은 통합 담당 범위다.

### 후속 연결의 구체적 변경 위치와 수락 조건 — 범위 확대 전 검토 기록

사용자의 `다음` 요청 뒤 기존 연결 지점을 추가로 읽었다. 아래는 범위 확대 승인 전 통합 제안이다. 이후 사용자의 `계속 진행해` 승인으로 이 연결을 구현했으며 실제 구현/검증과 잔여는 맨 위 최신 상태 절에 기록했다.

1. `application/market_theme_shadow.py::_evaluate_shadow_market`: 이미 동일 cut/venue/window로 제한한 frames와 미래를 제외한 `MatureBreakoutEvidence`가 있다. 새 관측에는 이 객체의 `available_at/input_ref`를 그대로 전달한다. 테마 판단이 없어도 순위·대금 관측은 계산할 수 있어야 한다. 기존 v1 분류의 `quality`를 관측 품질로 덮어쓰지 않는다.
2. 같은 파일 `_evaluate_market_theme_shadow`의 `_market_cache`: 현재 key는 cut/venue/window/frame 전체 내용·계보/breakout evidence/시장 parameters이며, `_same_shadow_scope`로 타입까지 대조한다. 새 관측을 공유할 때에도 이 같은 검증을 사용한다. H1의 기존 특징을 새 관측에서 다시 계산하는 중복은 통합 때 제거해야 한다. 현재 독립 함수는 기존 특징을 현재/이전 각각 한 번 계산하므로, 자동 연결만 추가해 기존 H1과 무조건 나란히 호출하는 것은 권장하지 않는다. 공유할 특징의 입력 계약을 같은 application 책임에서 정하고, 상태나 별도 캐시 owner를 추가하지 않는다.
3. `central_server/market_theme_shadow_monitor.py::run_market_theme_shadow_monitors_once`: `calculate()` 안에 이미 회차 한정 market cache가 있고 종료 시 clear한다. 2/8개 소비자가 같은 cut의 관측을 각각 다시 계산하지 않게 이 경로를 재사용한다. 각 결과는 현재처럼 deepcopy로 분리하고 H2의 독립 상태·실패/pending·취소 drain을 유지한다.
4. `MarketThemeShadowRuntime.latest_results`: 기존 복사/공통 맥락 투영과 만료 검사에서 새 관측의 원래 `expires_at`을 지킨다. 같은 이전 프레임을 다시 조회했다고 관측 TTL을 연장하지 않는다. 만료 관측을 현재 비중 점수나 v1 분류의 대체 입력으로 사용하지 않는다.

기본 출력과 revision ID는 유지해야 한다. 선택 출력을 추가할 위치와 ON/OFF 계약은 통합 담당이 기존 envelope에서 결정한다. 독립 관측이 UNKNOWN이어도 기존 H1/H2가 정상이라면 그 판단을 실패로 바꾸지 않고 새 관측의 상태/사유를 별도 표시해야 한다. 반대로 새 관측 COMPLETE를 전체 시장·테마·뉴스·R4 지원 승인으로 승격하지 않는다.

새 연결의 의미 있는 검사는 기존 시험 모듈에서 추가할 수 있다.

- `test_market_research_features`: 선택 출력 OFF의 기존 내용/ID 동일, 단일 계산과 공유 계산 결과 동일, 테마/성숙 근거 결측 중 독립 관측 확인, 미래·다른 venue/window·부분·중복·충돌·만료 입력의 구분.
- `test_candidate_monitor.CrossThemeH1SharingTests`: 같은 시장 cut에서 2/8 소비자의 현재 특징·변화 계산 공유, 전체 내용/타입/계보/parameters가 달라지면 분리, 결과 변이 격리, 회차 종료 후 캐시 소멸, 하나의 관측 실패가 다른 정상 scope를 막지 않음.
- 기존 Runtime 관련 소유 시험: 추가 Collector/DB 조회 0, 원래 TTL 유지와 만료 조회 제거, pending·취소·종료 drain 유지. 실제 계좌/원본 data/NAS 없이 격리 fixture로 확인한다.

이 후속 검사 목록은 **실행 계획**이며 현재 통과 44건에 포함하지 않았다. 자동 연결을 구현한 사실이나 승인으로 기록하지 않는다.

## 수정 파일·근거·검증

- `src/kiwoom_monitor/application/market_research_features.py`: 선택 변화 계산과 두 불변 DTO. 새 전역 가변 상태/Manager/Collector/DB/주문 경로 없음.
- `tests/unit/test_market_research_features.py`: 새 관측 검사 20건. 기존 시장/Shadow 시험을 유지했다.
- `docs/workstreams/market-context.md`: 본 보고서. 공통 상태/설계 문서는 수정하지 않았다.

검증 명령은 워크트리 `src;tests/unit;repo-root`를 PYTHONPATH 맨 앞에 두고 원본 `.venv/Scripts/python.exe -m unittest test_market_research_features test_research_factors -q`로 실행했다. `PYTHONDONTWRITEBYTECODE=1`, TEMP/TMP/TMPDIR는 checkout 밖 `%LOCALAPPDATA%/Temp/kiwoom-regression/market-context-a5af`로 지정했다. 이번 시험은 순수 메모리 fixture라 임시 DB/파일 생성이 없다.

- 변경 전 기존 소유 모듈 19건 통과. 최종 관련 고유 44건 통과(기존 소유 모듈 19 + 신규 20 + 기존 Factor 5), native 프로세스 종료 코드 0.
- 집중 증가/감소·조회순위 교체·반복 회전, 유지/이탈/재등장/최초 관측/관측 부재, 날짜·cut·venue·창·기본 UNKNOWN, 미래 성숙 근거·테마 분리·만료 경계, 가격/대금 결측·0 분모·누적값 감소·다른 창, 중복/충돌 revision·모호한 동시각, 입력 불변·직접 v1/전략 정책 결과 일치, 정규화 코드와 1,000 frame 이력을 확인했다.
- 기존 파일의 모든 기존 클래스·함수 AST가 HEAD와 동일함을 비교했다(`existing definitions changed: []`). 구문 검사와 `git diff --check` 통과. 새 계산 외의 기존 함수 본문을 바꾸지 않았다.
- 추가 합성 관측 1,000 frame/매회 20개 서로 다른 종목: 후보 20,000개·관측 1,000개·DISPERSED_ROTATION을 확인했다. 한 번의 로컬 계산 57.94ms. 종목별 출현 목록을 한 번 구성해 사용했다. 실제 앱의 실행 시간/장중 성능 수락으로 확대하지 않는다.
- `research_implementation_hash(None)`가 기존 legacy 상수와 같음을 확인했다. explicit scientific hash의 변경 자체는 통합 소스 검증 대상으로 보고한다.

Windows all-local·전체 HTTP/API·실제 PostgreSQL·NAS·현재 실행 앱·실제 브로커·외부 CI·통합 source gate는 미실행이다. 관련 단위 검사를 이 수락으로 바꾸어 보고하지 않는다.

모델 설정은 최신 현재 채팅 turn_context에서 `gpt-6.1-sol / high`를 확인했다. 기존 경계 안의 순수 계산·관련 검증으로 충분하므로 모델 라우팅 변경은 없다.

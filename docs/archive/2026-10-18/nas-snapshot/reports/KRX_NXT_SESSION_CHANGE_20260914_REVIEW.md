# KRX/NXT 거래시간 변경 영향 감사와 Sol 구현 계획

기준일: 2026-09-13(KST). 사용자 요청의 “내일”은 **2026-09-14(월)**이다. 이번 작업은 공식 안내·현재 코드·관련 문서의 영향 감사다. **제품 코드/DB/설정/주문/NAS는 변경하지 않았다. 실제 구현은 GPT-5.6 Sol이 수행한다.**

권장 방향: 기존 `application/market_session_schedule.py`를 시행일별 시간 정책의 원본으로 보완하고 NAS와 PC가 함께 사용한다. 수신 연결, 체결 방식, 전략 허용시간, 정규장 종가, 전체 거래일 보완을 각각 구별한다. 모든 15:30을 20:00으로 치환하거나 새 범용 거래소 framework를 만들지 않는다.

**사용자 후속 확인 반영:** KRX 정규장 09:00~15:30 유지, 장후 시간외종가 15:30~16:00 유지, 애프터 16:00~20:00 신설, 시간외단일가 16:00~18:00 폐지다. 장후 시간외종가의 15:30~15:40 주문접수와 15:40~16:00 실제 체결은 구분한다. 이 정정이 아래 KRX 변경 범위의 기준이다.

**사용자 추가 확인 반영:** KRX 정규장 미체결의 애프터 자동 이월 금지, NXT 정적 VI 신설과 VI 중 2분 단일가, 거래정지 후 단일가 재개를 주문·수집·연구 계약과 S2/S5/S6 테스트에 포함했다. 이는 매일 고정된 NXT 개장/마감 동시호가 시간표를 확인한 것은 아니다.

**2026-09-13 추가 화면자료 반영:** NXT 정적 VI는 기준가격 또는 직전 단일가 체결가격 대비 급변 시 2분 동안 호가를 모으는 단일가이며, 프리마켓 전용 GTP 주문은 프리마켓 종료 때 효력이 소멸한다. 제공된 화면은 차트용 일봉이 20:00 기준으로 생성되어 익일부터 반영되고 모든 차트가 20:00까지 생성되며, 화면에는 별도 `정규장만 보기`가 있음을 안내한다. 따라서 `공식 정규장 종가(15:30)`와 `차트용 전체일 일봉(20:00)`을 서로 다른 계약으로 구현한다.

## 1. 공식 확인 내용과 사용자 시간표의 차이

### 확인한 제도 변경

- 2026-09-14부터 KRX 애프터마켓은 **16:00~20:00 접속매매**로 운영되고 기존 16:00~18:00 시간외단일가 시장은 폐지된다. KRX 정규장 미체결 주문은 새 저녁장으로 자동 이전되지 않는다. ETF/ETN 등 제외 종목이 있어 NXT 적격 여부와 별도의 KRX 저녁장 적격 판단이 필요하다. [삼성증권 9/14 제도 변경 공지](https://www.samsungpop.com/ux/kor/customer/notice/notice/noticeViewContent.do?MenuSeqNo=24420)
- NXT는 정적 VI와 거래중단 뒤 단일가 체결 절차를 도입한다. 따라서 “NXT는 항상 접속매매”라는 가정도 점검 대상이다. KRX 저녁장에서도 VI 적용과 종목별 거래 제한을 구별해야 한다. [KB증권 2026-09-04 공지](https://www.kbsec.com/go.able?idt=20260904&linkcd=s060901010000&seq=10010298)
- **정규장 종료 및 당일 공식 종가 기준은 15:30으로 유지**된다는 안내다. 저녁 최종 체결가와 공식 종가를 같은 값으로 변경하면 안 된다. [카카오페이증권 2026-09-03 공지](https://www.kakaopaysec.com/customer/notice/dynamicBoardPageDetail.do?id=7452)

### 미체결과 단일가에 대한 추가 대조

- 미체결 이월 금지는 **실제 KRX 정규장으로 제출된 주문**에 적용한다. NXT 주문은 애프터까지 유지되는 별도 안내가 있으므로 두 거래소의 주문을 15:30에 일괄 만료시키면 안 된다. 통합주문도 실제 전송 venue와 주문 조건으로 판단한다. [삼성증권 미체결 비교표](https://www.samsungpop.com/ux/kor/customer/notice/notice/noticeViewContent.do?MenuSeqNo=24420)
- NXT 동적·정적 VI는 2분 단일가로 안내된다. 모든 재개 단일가가 2분인 것은 아니다. CB 및 중요공시 후 재개는 30초 단일가로 별도 안내되므로 사유별 상태를 구별한다. [KB증권 개편 후 단일가 표](https://www.kbsec.com/go.able?idt=20260904&linkcd=s060901010000&seq=10010298)
- 단일가 호가접수 중에는 연속 체결이 발생하지 않는다. 수집기는 체결 없는 초를 임의 생성하지 않고, 구간 끝의 실제 단일가 체결을 수신하면 보존한다. 호가접수 가능 여부와 연속 체결 가능 여부를 같은 boolean으로 표현하지 않는다. 이는 위 공지를 데이터·시뮬레이션에 적용한 설계 판단이다.

### 시간대별 대조

아래는 일반 주식의 주된 매매구간 비교이며, 주문 접수와 실제 체결을 구별한다. 거래소 전체 중단과 특정 종목의 VI/정지는 별도 상태다.

| 사용자 제시 구간 | 확인된 안내와 구현 시 주의 |
| --- | --- |
| 08:00~08:50 NXT 거래 | NXT 프리마켓 범위와 일치. 다만 KRX에는 08:30부터 시가 호가접수와 별도 장전 시간외종가 시장도 있어 모든 KRX 수신을 닫는 근거로 쓰면 안 됨 |
| 08:50~09:00 KRX/NXT 동시호가 | KRX 시가 단일가 구간. NXT는 공개 안내에서 휴장으로 표시돼 “두 거래소가 모두 동시호가 체결”로 확정할 수 없음 |
| 09:00~15:20 KRX/NXT 거래 | KRX 접속매매 범위. NXT 공식 표기는 **09:00:30~15:20**이므로 30초를 버리면 안 됨 |
| 15:20~15:30 KRX/NXT 동시호가 | KRX 종가 단일가 구간. NXT는 공개 안내에서 휴장으로 표시됨 |
| 15:30~15:40 NXT 동시호가 | NXT 애프터 주문접수(지정가만) 구간은 확인. 매일 고정 시가 단일가인지와 정확한 새 상태 코드는 키움/NXT 개정 안내 대조 후 확정 |
| 15:40~16:00 NXT 거래 | NXT 접속매매 외에 KRX 장후 시간외종가 거래도 존재. “NXT만 존재하는 시간”으로 모델링하면 안 됨 |
| 16:00~20:00 KRX/NXT 거래 | KRX 새 애프터와 NXT 애프터가 겹침. 양 거래소 거래대금의 중복/누락, 거래대상 차이 확인 필요 |

NXT 프리/메인/애프터 시간은 [NXT 공식 메인](https://nextrade.co.kr/main.do)에 08:00~08:50 / 09:00:30~15:20 / 15:40~20:00으로 표기돼 있다. 주문접수·별도 시간외종가 구분은 [한국투자증권 거래 안내](https://file.truefriend.com/Storage/customer/guide/regards/stock_guide.html)를 함께 대조했다. [유안타증권 안내](https://account.myasset.com/myasset/static/trading/TR_1608001_T1.jsp)는 두 NXT 휴장 구간을 명시하지만, 같은 페이지에 새 저녁장 소개와 구 시간외단일가 표기가 섞여 있어 페이지 전체를 새 규정의 확정 원본으로 삼지 않았다.

사용자는 후속 메시지에서 KRX의 정규장/시간외종가 유지와 애프터 신설/시간외단일가 폐지를 확인했다. 원 NXT 상세 시간표의 공지 출처는 아직 확보하지 못했다. **감사와 안전한 공통 구현 준비는 진행할 수 있지만, 불일치하는 NXT 상세 phase를 최초 표만으로 확정해 활성화하지 않는다.** 이는 KRX 저녁장 신설 자체가 불확실하다는 뜻은 아니다.

### 키움 API에서 별도 확인할 항목

키움의 이번 개정 REST/WebSocket 전용 공지는 이번 검색에서 확보하지 못했다. 다른 증권사의 안내로 키움 응답 형식이나 모의환경 지원까지 단정하지 않는다.

| 대상 | 필요한 확인 | 확인 전 처리 |
| --- | --- | --- |
| 실전 0B / 290 장구분 / 종목코드 suffix | KRX 애프터가 기존 코드와 어떤 phase로 수신되는지, 종목별 누적량/대금의 유지·초기화, 20:00 경계 체결 | 원문 출처/시각 보존, venue를 시각만으로 NXT로 바꾸지 않음 |
| 0s 장운영 상태, 1h/ka10054 VI | 거래소·phase·종목정지를 식별하는 필드와 실전/모의 지원 | 0J/0U 지수 메시지를 장운영 상태로 오용하지 않음; 미확인은 UNKNOWN |
| ka10080/ka10081 | KRX/NXT 분봉·일봉에 애프터 포함 여부, 공식 종가/전일기준가, 조회 마지막 봉의 의미 | 정규 일봉을 20시 종가로 재정의하지 않음 |
| 호가 실시간 유형/FID | NXT VI 중 예상체결가·예상체결량·매수/매도 단계별 잔량과 실제 단일가 체결의 제공 범위 | 0B 체결 방향 요약을 호가로 쓰지 않음. 확인 전 parser·저장 schema를 추측하지 않음 |
| KRX 애프터 적격 종목 | 날짜별 제외/정지/상품 유형의 공식 필드나 목록 | NXT 적격 목록을 재사용하지 않음. 관측 시도와 주문 허용을 구분 |
| ka00198, 0w/_AL, 0J/0U | 저녁 순위·프로그램·시장지수의 제공 범위/업데이트/누적 범위 | stale를 현재 자료/0으로 보정하지 않음 |
| CNSRLST/CNSRREQ 조건검색 | KRX 애프터에서 15% 조건 편입·실시간 알림 지원 여부 | 지원 안 되는 시간은 코호트 신규 발견 미관측으로 표시 |
| kt00007/kt00015와 mock 주문/계좌 | 저녁 체결·비용 반영시점, 정규장 주문 종료 통지, 키움 모의 KRX 애프터 지원 | mock 지원/시간/주문유형을 실전 제도만으로 확대하지 않음 |

## 2. 실제 코드 영향 지도

파일 경로는 저장소 루트 기준이다. 행 번호는 이번 감사 시점이며 구현 중 이동할 수 있다.

### A. 수신·수집과 장 종료

| 우선순위 | 대상 | 현재 동작/문제 | 최소 변경 |
| --- | --- | --- | --- |
| P0 | `src/kiwoom_monitor/application/market_session_schedule.py:8,24,32` | 09~15:30만 KRX 전체, 그 외 08~20은 NXT 가능 종목만. 16:00 재구독 경계 없음 | 시행일·거래소·phase·관측 구간을 명시. real/mock 지원을 별도 입력 |
| P0 | `infrastructure/kiwoom_rest/realtime_worker.py:26,139,170` | 직접/fallback WS가 15:30 이후 _NX만 구독. 세션 변경도 recv timeout에서만 확인 | 아래 경로는 모두 src/kiwoom_monitor 기준. 정상 수신 중에도 경계 검사하고 동일 연결의 구독 갱신으로 처리 |
| P0 | `central_server/realtime_collector.py:148,195,384,400` | NAS도 동일 시간 이분법. 'KRX' branch가 실은 KRX+NXT이며 VI/조건검색 수명까지 묶임 | venue 집합/구독 phase와 연결 수명을 분리. 16~20 KRX 추가, 0B·계좌 이벤트 유지 |
| P0 | `application/realtime_subscription.py:58,66`, `presentation/main_window.py:3135,3144` | coordinator가 active_codes만 비교해 거래소 목록만 바뀐 경우 UPDATE 누락 가능 | signature에 KRX/NXT 목록·환경·적용 정책 포함. 시간대별 NXT 사전조회 조건 보완 |
| P1 | `central_server/market_events.py:181,187,191,330,354` | KRX branch 종료가 cohort 거래일 종료와 CLOSED_AT_LIMIT 생성까지 유발. 마지막 틱은 코드만으로 저장 | 정규장 종가 확정과 관측 거래일 종료를 분리. venue/phase 없는 NXT 최종가로 KRX 종가를 판정하지 않음 |
| P1 | `central_server/realtime_collector.py:396`, `infrastructure/kiwoom_rest/realtime.py:25,134` | 1h는 기존 KRX branch에서만 구독. 0B session_type(290)은 parse되지만 봉에 보존 안 됨. 0s 수집 없음 | API 확인 뒤 운영 phase 근거 추가. 기존 market_state=0J/0U 지수 계약 유지 |
| P1 | `central_server/minute_bars.py:58,105,127,156`, `application/minute_trade_value.py:44` | 봉 자체에는 장시간 필터 없음. 누적량 기준·재연결 공백이 새로운 세션과 만남 | venue별 원시 상태와 observed gap 유지. 개장/단일가 체결을 버리지 말고 연구 사용 가능 여부를 별도 판정 |

실제 조립은 `central_server/app.py:110,127,135`와 `bootstrap.py`다. 단순히 UI 시간표만 바꾸면 NAS가 계속 KRX 저녁 자료를 누락한다. 반대로 NAS만 바꾸면 PC direct/fallback과 구독 종목 필터가 예전대로 남는다.

### B. 봉 확정·통계·화면·매매일지

| 우선순위 | 대상 (src/kiwoom_monitor 기준) | 현재 가정 | 변경/유지 기준 |
| --- | --- | --- | --- |
| P0 | `application/market_data_finalization.py:45,64,70` → `presentation/main_window.py:3794,4092` | NXT 아니면 15:35에 보완, 15:29 봉이면 완료 | KRX 애프터 적격/조회 범위별 확정 시점. NXT boolean만으로 전체일 완료 결정 금지 |
| P0 | `central_server/market_ingest.py:274,289` | 당일 KRX 일봉/분봉은 15:30 이후 확정 | official close는 15:30 유지. 시행일 이후 차트용 전체일 일봉·모든 차트는 20:00까지이며 익일부터 반영. 요청 범위와 봉 날짜의 정책 적용 |
| P1 | `central_server/autonomous_top20.py:208,218,262,310,384` | 이미 08~20 수집·20:05 보완. WS ready가 수집 품질 조건 | 외곽 시간 유지 가능. KRX 저녁 누락 보완 및 정규/애프터 구분, phase 전환을 장애로 오인하지 않음 |
| P1 | `infrastructure/persistence/minute_bar_repository.py:280,289,312,320,326` → `presentation/main_window.py:3404,3443` | TOP20 일별 비교는 09:00~15:29와 KOSPI+KOSDAQ 대금 분모 | **정규장 통계는 그대로 유지**. 애프터/전체일 통계는 별도 집계창/같은 분모 확보 뒤 추가 |
| P1 | `application/trade_setup_classification.py:155,180,191` | KRX 15:10 이후 모든 진입이 closing_entry 후보. 09시를 개장기준으로 계속 사용 | 16시/19시 체결이 정규장 종가매매로 오분류되지 않게 venue+phase별 분류. 사용자 수동 유형 보존 |
| P2 | `application/journal_chart_layout.py:87,94` | 세션 표시 경계는 08/09/15:30/20; 일자별 합성 봉은 마지막 close | 새 phase 경계/공백 표시. 정규 종가와 애프터 최종가 명칭 구별; 지수 일봉 합성은 지수 제공 범위 유지 |
| P2 | `journal_process.py:845,849,921`, `presentation/journal_settings_dialogs.py:42` | 자동 일지 가져오기는 20:05, 문구는 'NXT 종료 후' | 시각은 유지 가능하나 '국내 거래 종료 후'로 설명. 비용 지연/0건을 완료로 오인하지 않는지 새 거래구간 fixture로 검증 |
| P2 | `application/minute_chart_service.py:16,17,70,132` | 730/1500개 한도, KRX/NXT 같은 분 대금 합산. 390은 응답 페이지 설명 | 무조건 수치 증가 금지. 같은 시간 두 venue를 합산할 때 _AL과 이중 합산/종가 우선순위 점검; 실제 pagination으로 하루/이틀 범위 검증 |
| P1 | `application/market_data_coverage.py:129`, `central_server/app.py:693,709,1013` | 요청 범위의 고정 슬롯과 explicit 완료 문서 기준 | scheduled pause와 실제 누락을 구별하되 기존 요청의 과거 결과를 최신 시간표로 소급 변경하지 않음 |

`minute_bar_repository.py`의 시장별 분해는 KRX/NXT가 아니라 **KOSPI/KOSDAQ**인 경우가 있다. 이름만 보고 거래소별 애프터 합계로 바꾸면 분자·분모가 달라진다. 공식 시장지수가 저녁까지 같은 의미로 제공되는지도 별도 확인한다.

### C. 시뮬레이션·자동 연구·후보·모의 실행

| 우선순위 | 대상 (src/kiwoom_monitor 기준, scripts 예외) | 확인한 영향 | 최소 변경 |
| --- | --- | --- | --- |
| P0 | `application/research_replay.py:94–126` → `scripts/run_research.py:122,177`, `central_server/candidate_monitor.py:129` | strict reader가 KRX/actual/완료만 검사하므로 새 애프터 봉도 그대로 수락 | 허용 session profile을 명시. 기존 연구/실시간 shadow를 자동으로 저녁까지 확대하지 않음 |
| P0 | `application/research_evaluation.py:172–215,939–947` | 사건→첫 미래봉 공백은 검사하지 않아 휴장 후 한 봉으로 짧은 horizon 완료 가능 | 첫 구간 포함 연속성/phase/기간 검증. T+초와 거래가능시간을 임의 혼용하지 않음 |
| P1 | `application/research_factors.py:296–307,373–384` | '같은 session'이 사실 같은 KST 날짜 | phase별 lookback reset/carry를 전략 정책에 명시 |
| P1 | `application/research_execution.py:172–180,220–228` | pending은 다음 미래 종목 봉이면 체결 가능; 거래세션 만료 없음 | KRX 정규장 주문의 애프터 자동 이월 금지. 새 실행모델 버전의 venue별 만료 정책 |
| 유지 | `application/research_execution.py:246–279`, `scripts/run_research.py:168–178` | 현재 15:30 자동청산은 없음. 종료에서 position/pending CENSORED | 20시 강제청산을 끼워 넣지 않음. 청산은 별도 명시 전략/모델 정책 |
| P1 | `application/market_research_features.py:412–418,475–486` | 같은 HH:MM·venue·cumulative_session끼리 대금 비교 | 제도 변경 전후 집계 범위/phase 불일치 표본 제외 또는 UNKNOWN |
| P1 | `central_server/database.py:3832–3884`, `infrastructure/research_data_source.py:46–123`, `scripts/run_research.py:325–380` | manifest/RunSpec에 시간제도 버전 없음 | 시간제도·사용 phase·세션 연결 정책을 hash/manifest/spec에 고정 |
| P1 | `application/research_search.py:30–55`, `research_process.py:94–108`, `central_server/candidate_monitor.py:47,189,263` | job/monitor/checkpoint가 세션 정책 변경을 설명하지 못함 | spec/evidence/monitor 식별에 연결. 구 완료 run/checkpoint를 덮어쓰지 않음 |
| P2 | `application/research_evaluation.py:865–875`, `application/context_candidates.py:23–38,328–365` | 오전/점심/other 분류, NORMAL_KRX 전일 이월 | 기존 오전 연구 창은 유지. 저녁 평가는 별도 버전·분할로 추가 |
| P1 | `domain/order_contract.py:148`, `infrastructure/kiwoom_rest/mock_execution.py:41`, `central_server/execution_runtime.py:107`, `application/order_lifecycle.py:166` | mock+KRX·수동 LIMIT·인증/계좌/만료/자금 검사는 있지만 **거래시간 gate 없음** | 키움 mock 지원 확인 뒤 session allowlist/주문유형 gate 추가. 이미 시간 gate가 있다고 보고하지 않음 |
| P1 | `domain/execution_activation.py:77`, `application/forward_evaluation.py:84` | forward profile의 기간만으로 세션별 제공/실행 가능성 보장 안 됨 | 관측/연구/주문 capability를 구별하고 새 session profile을 평가 근거에 포함 |

수집시간 확대는 사용자 전략의 허용시간, 주문 권한, mock API 지원을 변경하는 승인이 아니다. 기존 real 5/s와 mock 1/s의 client/queue/WS 분리, 주문 단발 전송·미응답 재전송 금지·실계좌 자동승격 금지는 유지한다.

## 3. 실행으로 확인한 현재 동작

### PC 시간 판단 재현

현재 코드에 2026-09-14 합성 시각/종목을 넣었다. 파일/운영 DB를 변경하지 않았고 종료 코드 0이다.

```text
16:00 active codes             = ('NXT_OK',)       # KRX_ONLY는 제외
16:00 is_nxt_only_session      = True
15:40 다음 재구독 경계          = 20:05             # 16:00 없음
15:35 non-NXT 확정 후보         = ('KRX_ONLY',)
KRX 15:29 한 봉 완료 판정       = True
```

이는 새 KRX 애프터 적격 정보를 표현할 입력조차 없는 현재 정책의 동작을 확인한 것이다. 합성 종목의 실제 거래소 적격을 조회한 결과는 아니다.

### 연구 경계 재현

15:29:02 후보/내부 모의 pending과 16:00 한 개의 완료 KRX 봉을 사용했다. 기존 함수를 메모리에서 실행했고 종료 코드 0이다.

```text
after_hours_replay_accepted = 1
pending → FILL at 16:00, MARK at 16:01
finalize → POSITION_CENSORED
T+60/180/300/600초 outcome → 모두 COMPLETE, matured_at=16:01
```

내부 시뮬레이터의 동작이며 키움 모의주문을 보낸 결과가 아니다. 특히 짧은 horizon이 휴장 구간을 건너 COMPLETE가 되는 것은 재현된 평가 결함이다. 수정 전 회귀로 고정해야 한다.

기준선 테스트 **27개**가 종료 코드 0으로 통과했다: `test_research_replay`, `test_research_execution`, `test_research_evaluation`, `test_research_factors`, `test_market_research_features`, `test_mock_execution`. 기존 테스트 통과가 새 시간표 지원을 뜻하지는 않는다. NAS/키움 실수신·PostgreSQL·GUI 장시간 운전은 이번에 실행하지 않았다.

## 4. 최소 변경 설계 결정

### 기존 모듈의 역할 보완

`application/market_session_schedule.py` 안에 순수 시간 정책과 작은 값 객체를 둔다. NAS 전용 정책/PC 전용 정책을 복사해 만들지 않는다. 구현상 명칭은 다음 정도면 충분하다.

| 입력/출력 | 계약 |
| --- | --- |
| 입력 | KST 관측시각/거래일, venue, real/mock 환경, 상품·종목 적격 상태, 적용 schedule revision, 필요한 경우 검증된 장운영 상태 |
| SessionWindow | venue, trading_date, session/phase, 시작·종료, schedule_version, 근거 상태 |
| 판단 | observe(수신 연결), continuous_trade, order_entry, regular_close, full_day_close를 구별 |
| 불확실성 | UNKNOWN/미지원 사유를 반환. 미수신을 휴장/무체결로 확정하지 않음 |
| 예외일 | 주말·공식 휴장·지연개장·수능일 등. 현재 weekday만 검사하므로 알려진 예외를 명시하고 새 일반 캘린더 서비스는 만들지 않음 |

거래소에는 주문접수·고정가격 매매 등 동시에 존재하는 별도 시장이 있으므로 venue 하나에 단일 OPEN/CLOSED boolean만 두지 않는다. 단계별 구현에는 현재 소비하는 segment만 넣고 알려지지 않은 장구분은 UNKNOWN으로 둔다. 시간표의 초 단위 경계를 보존한다.

**시행일:** 거래일이 2026-09-14 이전이면 이전 정책, 이후면 새 정책이다. 처리한 PC의 오늘 날짜가 아니라 봉/사건의 거래일을 사용한다. 과거 raw와 이미 동결된 manifest/run은 그대로 보존하며, 과거 오류를 재평가한다면 새 정책 버전의 별도 결과로 만든다.

**수신:** 단일 WS는 필요한 준비시간부터 최종 처리시간까지 유지할 수 있다. 동시호가/잠깐의 휴장마다 연결을 끊지 않는다. 0B/00/04/장운영 수신을 유지하는 것과 그 구간을 연속매매 전략에 허용하는 것은 별개다. 늦게 온 체결은 원래 event time/venue로 보존하고 receive time을 따로 둔다.

**종목별 단일가:** 정적 시간표와 VI/정지 이벤트를 분리한다. 동적 상태는 기존 `market_events.py`의 관측 경로에서 venue+종목+발동 사유+event/receive time+근거를 보존하고 소비자가 시간표와 함께 판단한다. 한 종목의 NXT VI가 다른 종목이나 KRX 거래까지 중단시키지 않는다. 확인된 단일가 구간의 무체결은 정상 상태로 설명하되, 체결 미수신만으로 VI를 추정하거나 다른 통신장애 증거까지 숨기지 않는다. 2분 경과만으로 무조건 정상 재개했다고 판정하지 않고 실제 상태/체결 근거를 확인한다. 미확인 상태는 UNKNOWN이다.

동적 상태도 기존 관측/export의 당시 가용시각과 source revision을 따른다. 사후 VI 조회·정정은 새 근거로 추가하되 이미 동결된 연구 cutoff 이전부터 알았던 정보로 소급하지 않는다. 시간표 version만 고정하고 실제 VI 근거를 최신값으로 읽는 재생은 허용하지 않는다.

**관측:** 기존 venue 분리 key와 절대 상태 멱등 저장을 유지한다. 추가 phase/schedule/source 근거는 기존 observation payload의 선택 metadata부터 사용한다. 필터용 DB 컬럼이 실제 필요할 때만 다음 migration을 추가한다. 같은 분에 단일가/접속매매가 섞이면 MIXED 또는 UNKNOWN 품질을 남기고 정밀 연구에 무조건 넣지 않는다. 거래소 필드를 시각만으로 추정 변경하지 않는다.

**VI 호가:** 하루 종일 모든 종목의 원시 호가를 영구 저장하는 범위로 확대하지 않는다. 실제 호가 실시간 유형과 FID를 확인한 뒤, VI가 발생한 추적 종목만 `VI 시작 → 단일가 체결 또는 재개 확인` 구간에 1초 스냅샷으로 저장한다. 최소 필드는 종목·venue·event/receive/available 시각, VI event ref, 기준가·예상체결가·예상체결량, 총 매도/매수 잔량, 제공되는 단계별 가격·잔량, source revision, capture quality다. 값이 변하지 않은 초도 구독 연속성과 실제 응답 근거가 있을 때만 동일 snapshot 또는 heartbeat로 설명하며 임의 0을 만들지 않는다. 단일가 종료 체결은 0B 실제 체결과 연결하되 호가 스냅샷 자체를 체결로 바꾸지 않는다.

**종가:** KRX 정규장 공식 종가와 전체일 마지막 체결가를 별도로 취급한다. `window_closed`(한 분이 지남), `session_finalized`(요청한 세션 자료 확인), 일봉 complete(공식 데이터 범위 확인)를 구분한다. 거래가 없어 19:59 봉이 없는 종목을 무조건 미완료로 보는 기존 마지막 봉 검사도 이 단계에서 실제 조회완료 근거와 맞춰 보완한다.

**cohort:** 사용자 계약의 “그날과 다음날”은 같은 KST 거래일 전체의 관측을 포함하도록 새 정책에서 **다음 실제 관측 거래일의 최종 지원 거래 종료**에 한 번 만료시키는 방향을 권장한다. 정규장 종가 CLOSED_AT_LIMIT 판정은 15:30 기준의 별도 증거로 유지한다. 휴장/조건검색 미지원으로 관측하지 못한 날을 만료일로 추정하지 않는다. 이전 정책 자료의 만료 이력은 수정하지 않는다.

**연구:** 기존 활성 정규장 전략은 정규장 입력으로 고정한다. 새 애프터 데이터는 저장하되 연장 세션 연구는 명시한 새 session profile로만 실행한다. phase/허용시간/단일가 모델/venue별 주문 만료/시간 horizon 정의는 spec hash에 포함한다. wall-clock T+60초의 자료가 없으면 MISSING/CENSORED로 두고 30분 뒤 봉으로 채우지 않는다. 단일가 중 이전 가격으로 연속 체결을 만들어내지 않는다. 현재 1초/분봉만으로 단일가의 호가 우선순위·배정량을 검증할 수 없으면 실제 관측 체결은 보존하고 해당 가상 주문의 체결 평가는 미지원/별도 명시 모델로 처리한다.

**주문 수명:** 내부 시뮬레이션에서는 새 실행모델의 KRX 정규장 pending이 허용기간 이후 애프터 봉에서 체결되지 않게 만료시킨다. 실제 broker 주문은 시각만으로 CANCELLED/EXPIRED 확정, 미체결 삭제 또는 주문가능금액 복구를 하지 않는다. 기존 계좌 대조·주문 조회·체결 통지로 최종 상태를 확인하고, 확인 전에는 미확정 잔량을 별도 유지한다. 15:30 이후 도착한 정규장 체결 통지는 원 체결시각으로 반영한다. NXT 주문 수명은 별도 계약을 따르며 이번 KRX 규칙을 복사하지 않는다. 애프터 재주문은 새로운 의사결정·명시 주문 요청으로만 처리하고 자동 재전송을 추가하지 않는다.

이 만료는 매수·매도 주문의 **남은 미체결 수량**에 적용한다. 부분체결 이력과 이미 보유한 포지션을 삭제하거나 강제 청산하지 않는다. 종료 대조를 재처리해도 수량·자금이 이중 복구되지 않아야 한다.

## 5. Sol 단계별 작업

### S0 — 거래소와 키움 지원 계약 확정

- **목적:** 잘못된 동시호가·거래소 지원을 하드코딩하지 않기.
- **수정 대상:** 먼저 이 감사 문서의 공식 확인 표와 `API_CONTRACT.md`, `HISTORICAL_DATA_CONTRACT.md`의 예정 계약. 실제 계좌/주문 변경 없음.
- **입출력:** 공식 시간표·키움 공지/합성 응답 fixture → 시행일별 phase표와 TR/실시간/모의 지원 매트릭스. NXT 불일치 구간은 근거 확정 전 UNKNOWN.
- **완료 기준:** KRX 애프터 수신 방식, NXT 09:00:30/주문접수/휴장, 일봉 포함 범위, 종목 적격의 확인/미확인 표시. unknown 항목이 있어도 순수 정책/수집 테스트 준비는 진행.
- **테스트:** source와 시행일 기록, 기존 parser의 정상/알 수 없는 phase fixture. 실수신은 읽기 전용으로 하며 원문 계좌를 기록하지 않음.

### S1 — 시행일별 공통 시간 정책과 기존 전략 보호

**상태: 2026-09-13 구현 완료.** 시행일별 구/신 revision, KRX 세션 구분, NXT 미확인 phase `UNKNOWN`, mock 애프터 미지원, 기존 연구·D4 shadow의 `krx-regular/v1` 고정을 반영했다. 집중·인접 회귀 66개와 최종 집중 회귀 18개가 통과했다. S2 구독·수집 배선은 아직 적용하지 않았다.

- **목적:** 새 자료가 유입되기 전에 같은 시간 정책을 사용하고 기존 전략이 자동 확대되지 않게 하기.
- **수정 대상:** `application/market_session_schedule.py`, `application/research_replay.py`, `central_server/candidate_monitor.py`, 필요한 기존 contract/spec.
- **입출력:** 거래일/venue/환경/phase → 관측 구간과 허용 전략 session. old context는 명시 legacy 정책으로 읽고 새로운 profile에 새 revision을 부여.
- **완료 기준:** NAS/PC가 같은 판단, 기존 정규장 shadow는 새 16시 봉을 자동 소비하지 않음. 이전 날짜/완료 run 해석 불변.
- **테스트:** 아래 경계시각 매트릭스, 두 날짜에 같은 시각 비교, mock 미지원, 알 수 없는 장상태, 신규 phase를 구형 소비자가 거부/보류하는 경우.

### S2 — NAS와 PC 실시간 구독·관측 수명

**S2a 상태: 2026-09-13 구현 완료.** NAS 중앙 수집과 PC 직접·fallback worker가 공통 venue별 구독 정책을 사용한다. 실전 KRX는 08:55 준비 후 시행일 이후 20:00까지 현재 요청 종목을 관측 대상으로 유지하고, NXT 적격 목록은 기존 08:00~20:00 `_NX` 구독에만 사용한다. 종목 합집합이 같아도 venue 또는 15:20·15:30·15:40·16:00 phase 정책 서명이 바뀌면 같은 연결에서 refresh한다. 15:30 정규장 종가 확정은 KRX 틱만 사용하며 cohort 만료는 20:00 전체 관측일 종료로 분리했다. 집중·인접 회귀 118개가 통과했다. S2b VI 호가/FID 실응답 확인·parser·storage는 미구현이다.

- **목적:** KRX 저녁 체결 누락을 없애고 단일가/휴장 전환에서도 데이터를 보존.
- **수정 대상:** `realtime_collector.py`, `realtime_worker.py`, `realtime_subscription.py`, `main_window.py`의 재구독 호출자, `market_events.py`, 기존 봉/관측 변환.
- **입출력:** 공통 정책+각 venue 적격 종목 → KRX/NXT 구독 집합·원본 phase/시각 관측. 같은 종목 집합이라도 venue 변경은 UPDATE. phase 전환과 거래일 만료 이벤트는 별도.
- **완료 기준:** KRX 애프터 적격/NXT 불가 종목도 16~20 수신 대상. 15:20/15:30/15:40/16:00에 cohort 조기 만료·중복 구독/집계 없음. 일반 시세/계좌 WS 유지. 종목별 NXT VI와 시장 시간표를 구별하고 실제 단일가 체결을 보존.
- **테스트:** collector/direct worker/subscription/market_events 회귀, 끊임없이 메시지가 오는 상태의 경계 통과, venue만 바뀌는 구독, 재연결·늦은 체결·누적량 reset/유지, 0s/1h 미지원과 UNKNOWN. 원시0B를 synthetic zero로 만들지 않음. NXT VI 중 120초 무체결→종료 체결, 공시/CB 재개 30초 단일가, 상태 통지 누락/중복/지연, 같은 종목 KRX 정상 수신을 각각 검증.

S2는 둘로 나눠 적용한다. **S2a**는 공통 정책에 따른 KRX/NXT 0B 구독과 연결 수명만 먼저 바꾼다. **S2b**는 키움 호가 유형/FID 실응답을 확인한 뒤 VI 발생 종목에만 위 제한 수집을 추가한다. S2b가 준비되지 않았다고 S2a의 KRX 애프터 체결 저장을 늦추지 않으며, 반대로 S2a 완료를 VI 호가 연구 가능으로 표시하지 않는다.

### S3 — 봉 확정·coverage·정규장/전체일 통계

**상태: 2026-09-13 구현 완료.** 시행일부터 KRX 전체일 보완은 NXT 가능 여부와 무관하게 20:00 종료 후 기존 여유를 둔 20:05에 시작한다. 중앙 조회 분봉의 `window_closed`와 전체 요청 범위의 `session_finalized`를 분리했고, 차트용 당일 일봉은 20:00 전까지 `in_progress`로 유지한다. 성공한 전체 조회와 대상일 실제 봉을 완료 근거로 사용하므로 19:59 무체결은 누락으로 만들지 않지만 조회 실패·대상일 빈 응답은 완료로 승격하지 않는다. TOP20 일봉·전체시장 비교는 기존 09:00~15:29 정규장 합계를 유지하고 분·5분·60분 화면은 전체일 범위를 표시한다. 시행 전 확정 정책과 `krx-regular/v1` 연구 profile은 유지했다. 집중·인접 회귀 119개가 통과했다. S2b 호가, S4 일지 분류, S5 연구, 주문, DB migration, NAS 배포/build ID는 변경하지 않았다.

- **목적:** 15:35 조기 완료와 잘못된 종가·거래대금 비교를 방지.
- **수정 대상:** `market_data_finalization.py`, `market_ingest.py`, `autonomous_top20.py`, `market_data_coverage.py`, `minute_bar_repository.py`, `main_window.py` 해당 화면.
- **입출력:** 거래일+venue+정규/애프터/전체일 요청범위+적격/조회완료 근거 → 완료/부분/미지원 상태. 정규장 통계와 애프터 통계는 별도 이름/동일 범위 분모.
- **완료 기준:** 정규장 공식 close 유지, 시행 후 전체일 차트/일봉은 20:00 기준과 익일 반영을 사용하고 애프터 포함 요청을 15:30 자료로 완료하지 않음. `정규장만` 요청은 09:00~15:30으로 별도 제공. 마지막 분 무체결과 원본 수집 공백 구별. 이전 관측 revision 불변.
- **테스트:** 시행 전/후·NXT 불가/KRX after 가능·after 제외·거래 없음·19:59 미체결·조회실패·늦은 정정. 첫/끝 경계 체결과 과거 날짜의 늦은 수입. 기존 정규장 TOP20 합계 golden 결과 유지.

### S4 — 매매일지·차트·자동 분류

**상태: 2026-09-13 구현 완료.** 원 체결의 계좌 scope·venue·event time과 공통 schedule revision을 `trade-analysis/v2` 입력에 고정했다. 시행일 이후 KRX 15:20~15:30 종가 단일가만 정규장 종가베팅 후보로 두고, 15:30~15:40 장후종가 주문접수·15:40~16:00 장후종가 체결·16:00~20:00 애프터를 별도 표시한다. venue 미확인과 NXT 동적 phase는 UNKNOWN/일반 장후로 유지하며 수동 유형·메모·묶음 override를 변경하지 않는다. 차트는 15:30 정규장 종가, 20:00 전체일 최종가, 지수 제공 범위를 구별하고 20:05 자동 조회와 비용 정산 대기 문구를 실제 완료 상태에 맞췄다. 집중 84개, 인접 trade 116개·journal 101개·market session 18개가 통과했다. S2b 호가, S5 연구, 주문, DB migration, NAS 배포/build ID는 변경하지 않았다.

- **목적:** 새 시간대 체결을 정확히 표시하고 기존 수동 기록을 보존.
- **수정 대상:** `trade_setup_classification.py`, `journal_chart_layout.py`, `journal_process.py`, `journal_settings_dialogs.py`, 연결된 자동보완/분석 revision.
- **입출력:** 원래 계좌 scope+체결 venue/phase+당시 가격 근거 → 새 기계 분류 revision·시간대 표시. 저녁 체결도 같은 거래일 계좌 일지에 포함.
- **완료 기준:** 16시 KRX 진입이 시간만으로 정규장 종가매매가 되지 않음. 수동 유형/메모 불변. 20:05 설명과 실제 체결/비용 완결 상태 일치.
- **테스트:** real/mock 동일 종목 다른 계좌, 15:25/16:05/19:45 분류, phase 공백 차트, 당일 비용 반영 지연, 새 뉴스 링크와 A4 계좌 scope 전달 회귀.

### S5 — 재현 가능한 연구와 내부 모의 세션 전환

**상태: 2026-09-13 구현 완료.** `krx-regular/v1`, `krx-after/v1`, `krx-full-day/v1`을 연구 요청·RunSpec·검색 evidence·D4 checkpoint·forward profile 식별에 연결했다. 전체일은 정규장과 16:00 이후 연속 애프터의 합집합이며 15:30~16:00 장후종가 구간을 제외하고 Factor/pending 연속성을 재시작한다. 첫 미래봉 전 공백·세션/거래일 변경은 next-open 체결이나 horizon 완료로 사용하지 않고, 단일가/VI 호가 근거 없는 봉의 가상 체결 경로는 `UNSUPPORTED`로 남긴다. profile 필드가 없던 기존 요청은 기존 `krx-regular/v1` reader·RunSpec·implementation hash를 유지한다. 전체 연구 회귀 91개, S5 집중 93개, 분봉·candidate·forward 인접 48개가 각각 통과했다. S2b 실호가, 실제/모의 broker 주문 gate, DB migration, NAS 배포/build ID는 변경하지 않았다.

- **목적:** 저녁장을 별도 연구할 수 있게 하면서 기존 성과를 오염시키지 않기.
- **수정 대상:** export/manifest/data_source, run_research의 spec/hash, research_factors/evaluation/execution/search, candidate monitor/checkpoint, forward profile.
- **입출력:** dataset+schedule/session profile+거래/시각 정책 → 새 evidence ID와 결과. 기존 run/manifest는 그대로 읽기.
- **완료 기준:** 사건→첫 미래봉 공백까지 검사, T+1분을 30분 뒤 봉으로 완료하지 않음. KRX 정규장 pending은 애프터에 임의 이월되지 않음. 허용하지 않은 phase 입력은 제외/미지원. 단일가 호가접수 중 허위 연속 체결이 없고, 단일가가 섞인 봉으로 체결 순서를 단정하지 않음.
- **테스트:** 이번 15:29:02→16:00 재현 고정, 08:49→09:00:30, 15:19→15:40, 종가 단일가/정지 재개, 거래일 넘어감, 원래 censor 유지, 새 profile별 hash/중복 결과 분리, 신규 monitor의 과거 알림 재발행 방지. VI 발동 직전 pending·단일가 도중 후보·종료 체결을 구별하고, 확장 시 NXT 주문에 KRX 만료를 잘못 적용하지 않는 별도 fixture 유지. 단일가 체결을 next-bar-open이나 가상 손절 체결로 대입하지 않음. 사후 VI 정정으로 이전 cutoff의 동결 결과가 바뀌지 않음.

### S6 — 모의 실행 gate·통합 검증·누적 배포

**S6 로컬 상태: 2026-09-13 구현 완료.** 공통 세션 정책의 KRX 정규장 연속매매 `09:00~15:20` 수동 LIMIT만 broker-backed mock 신규 주문으로 허용한다. 15:20/15:30/15:35/15:40/16:00/19:59과 미검증 order type/venue는 근거를 남겨 `UNSUPPORTED`로 거절한다. 취소·broker 대조·재연결 복구·늦은 체결에는 신규 주문 gate를 적용하지 않으며, 정규장 잔량은 broker 종료 근거 전까지 유지하고 16시 자동 재주문 경로를 만들지 않았다. 로컬 합성·관련 회귀는 통과했으며 실제 모의계좌 제한 주문 왕복, PostgreSQL 왕복, KRX/NXT 장중 관측과 NAS 누적 배포/build 확인은 미실행이다.

- **목적:** 관측 가능 시간과 실제 모의 주문 허용시간을 구별하고 운영 반영을 확인.
- **수정 대상:** 지원 확인 후 기존 mock gateway/order_lifecycle/forward profile의 좁은 gate, 관련 테스트/문서, build 세 파일.
- **입출력:** 동결 mock 지원표+계좌+session+주문유형+기존 자금/만료 정책 → 허용/거절 사유. 자동 신규 주문/재주문은 생성하지 않음.
- **완료 기준:** 검증되지 않은 mock 애프터를 지원한다고 표시하지 않음. 취소/계좌 대조를 신규 주문시간 gate로 막지 않음. 지연 체결 수신 유지. 실제 주문 종료는 broker 근거로 확정하고 시각만으로 잔량·예약자금을 해제하지 않음. 자동 애프터 재주문 없음. 실제 NAS build/capability/수신 로그로 운영 확인.
- **테스트:** mock만 합성 transport로 시간/휴장/주문유형/세션종료/늦은 체결, 실전 한도와 분리. 15:30 잔량 유지→15:35 종료 대조, 부분매수/부분매도 후 잔량만 종료, 종료 통지보다 늦은 부분체결, 조회 실패 시 미확정 유지, 중복 종료/재시작에서 자금 이중 해제 없음, 16시 자동 재주문 없음. 관련 집중/핵심 회귀·SQLite migration 필요분·PostgreSQL 왕복·실제 KRX/NXT 관측. 거래소 공지만으로 주문을 보내 검증하지 않음.
- **배포:** NAS 소스 변경 때 SERVER_BUILD/Compose/server.Dockerfile을 함께 갱신. X: 백업·추가 덮어쓰기, .env/postgres-data/server-data 보존. 사용자 이미지 빌드 후 health/capability/구독/19시대 저장/20시 종료 확인. 현재 누적 A4b 오류도 배포물에 섞이므로 [기존 배포 보류 항목](NAS_DEPLOYMENT_PENDING.md)을 함께 확인한다.

## 6. 반드시 포함할 테스트 매트릭스

- 날짜: 2026-09-11(이전 영업일), 09-13(일요일), 09-14(시행일), 공지된 지연개장/휴장 fixture.
- 시각: 07:55, 08:00, 08:30, 08:50, 09:00, **09:00:30**, 15:20, 15:30, 15:40, **16:00**, 19:59:59, 20:00, 20:05 각각 직전/정각/직후.
- 종목: 양 거래소 가능, KRX 저녁만 가능, NXT만 저녁 가능 조건, 애프터 제외, 적격 미확인, 거래정지/VI.
- 환경: PC direct, NAS, fallback 전환, real/mock. 실제 지원을 별도 가정하는 fake case와 실환경 결과를 구별.
- 이벤트: 지속 수신 중 경계 통과, 늦은 체결, 단일가 체결, 한 분 안 phase 혼합, 누적량 초기화/유지, 무체결, 연결 단절, 재시작.
- 결과: venue/계좌 분리, 같은 초/분 멱등, 공식 종가·애프터 마지막가 분리, 전략 시간 확대 없음, 기존 사용자 기록과 완료 연구 불변.

우선 확장할 기존 테스트(`tests/unit/` 아래 `.py` 파일): `test_market_session_schedule`, `test_market_data_finalization`, `test_realtime_subscription`, `test_central_realtime_collector`, `test_market_events`, `test_central_market_ingest`, `test_autonomous_top20`, `test_central_minute_bars`, `test_second_trade_aggregation`, `test_second_trade_storage`, `test_minute_bar_revisions`, `test_market_data_coverage`, `test_trade_setup_classification`, 위 연구 27개 기반과 `test_candidate_monitor`, `test_order_lifecycle`, `test_execution_activation`.

## 7. 수정할 문서와 유지할 것

### 구현과 함께 갱신할 문서

- `API_CONTRACT.md`: 거래소/phase 근거, capability별 관측·연구·모의 지원 구별. 기존 market_state=지수 의미 유지.
- `HISTORICAL_DATA_CONTRACT.md`, `DB_SCHEMA.md`: 정규장/애프터 자료 범위·완결성·추가 metadata/필요 migration·과거 정책.
- `ARCHITECTURE_CURRENT.md`, `MODULE_MAP.md`: 실제 공통 정책 소유자와 소비 경로.
- `docs/RESEARCH_REQUEST_FORMAT.md`, `docs/FORWARD_EVALUATION_CONTRACT.md`, `docs/KIWOOM_MOCK_EXECUTION_CONTRACT.md`: session profile/결과 동일성/미지원 모의시간.
- `docs/AI_인수인계_개발실행서_v3.3.md:160–176`, `docs/키움_실시간_모니터_기술명세서_v2.0.md`: 구독·후속조회 시간표와 공식 종가 설명.
- `reports/CONTINUOUS_RESEARCH_ACCOUNT_SCOPE_REVIEW.md`, 구현 계획의 A4/CR1~CR4/A5/O2-M: 계좌 원본은 유지하고 시간제도 version 의존성을 연결.
- `CHANGELOG.md`, `reports/NAS_DEPLOYMENT_PENDING.md`: 구현/테스트/배포/장중 실확인 상태를 구별. 과거 릴리스 당시 시간표는 삭제하지 않고 적용 시기를 명시.

### 이번 변경으로 건드릴 필요가 없는 것

- NAS 뉴스 수집 `news_service.py:214`, `news_sources.py:224`는 장시간 gate 없이 주기·KST 일일 예산으로 동작한다. BODY/RULE/선택 AI 실행 방식과 자정 예산 경계는 그대로다.
- TOP20 외곽 08~20, 장후 보완 20:05, 다음날 아침 복구 시각은 최종 시장 종료가 같으면 유지 가능하다. phase별 품질만 점검한다.
- 정규장 09~15:29 TOP20 비교, 공식 KRX 종가, 0J/0U 지수, 전일 NORMAL_KRX 기준은 새 전체일 값으로 덮어쓰지 않는다.
- `SCALPING_ENGINE_DEVELOPMENT_REFERENCE.md`의 09~10시/12시 연구 창은 사용자의 전략 관찰 범위이지 거래소 운영시간이므로 자동 연장하지 않는다. Desktop의 원 설계 3개는 이번에 수정하지 않았다.
- 390 응답 페이지 설명, 730/1500 조회 한도, 강도 임계값 195/390/780, UI 크기 등 숫자는 거래소 종료시각이 아니다. 실제 범위 부족 근거 없이 일괄 변경하지 않는다.
- 계좌 identity/DPAPI·뉴스 본문/AI·DB 보존·실전/모의 한도 분리·주문 활성화 권한은 시간표 변경 자체로 바꾸지 않는다.

## 8. 인수인계 판정

대부분 기존 모듈의 최소 변경으로 구현 가능하다. 설계상 선택은 **시행일별 공통 정책 + 정규장/애프터 구분 + 원본 보존 + 전략 profile별 명시 허용**으로 정리했다. 시간표 숫자만 교체하는 구현은 완료로 보지 않는다.

키움의 새 phase/일봉/모의 지원 확인이 남아 있으나 구조 전체를 다시 설계할 필요는 없다. 확인 가능한 수집·보호 작업부터 Sol이 진행하고, 모의 주문 지원과 미확인 NXT 상세구간만 분리해 보류한다. 이번 감사에서는 코드 수정과 NAS 재빌드를 수행하지 않았다.

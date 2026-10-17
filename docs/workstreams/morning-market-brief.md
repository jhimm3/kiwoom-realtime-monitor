# 직접 검색 아침 시황

2026-10-15 · v4 계약 NAS 통합 v3 활성화 · 실제 검색 출력 검증 실패 후속

NAS 구독 연결·모델 목록 조회와 실제 직접 검색1회는 실행됐다. 그 응답은 원문 게시시각이 없는 datetime 근거 때문에 엄격 검증에 실패했고 원본을 보존했다. 생성 schema/고정 프롬프트와 기존 validator 조건의 정렬, 안전한 거절 원인 표시는 관련 로컬89건 통과·NAS v5 후보 검사 중이며 새 실제 검색 성공은 아직 아니다. 07:55 실제 예약과 C1 소비 연결도 별도다. [실제 실패와 수정 범위](morning-market-brief-alignment.md#실제-실패의-재현과-생성-계약-정렬).

후속 v5는 NAS selected175/storage114 통과 뒤 활성화했다. 새 실제1회는 검색14회·367.544초 뒤 미확인 환율 URL의 출처 불일치에 거절됐고, 진단용으로 그 항목을 제외해도 확정 사실의 원문 검증 조건에 실패했다. 게시 정밀도 모순은 이번0개지만 전체 시황 수락은 미완료다. 원본과 이전 실패·영속 설정은 보존했고 추가 추론/조건 완화는 없다. [실제 영수증과 남은 증거](nas-integrated-validation-20261015.md#최신-후속--시황-생성-근거와-kst-표시-v5-후보).

[00·① 대조와 입력 매핑 보고서](morning-market-brief-alignment.md) · [수정 프롬프트](morning-market-brief-prompt.txt) · [엄격한 JSON Schema](morning-market-brief-output.schema.json)

## 목적·프롬프트·모델

매일 한국 시각 07:55에 AI가 직접 웹 검색·해석을 수행하고 결과를 한 번 저장한다. 앱의 공통 조회 API는 그 결과를 읽고 조회할 때 LLM을 다시 호출하지 않는다. Market Intelligence/C1의 실제 소비 연결은 후속이다. 기존 속보·해외뉴스·Yahoo 지연 선물 DB는 이 프롬프트의 입력이 아니다. 기존 수집기가 이미 중지됐다고 보고하지 않는다.

07:55는 생성 시작 시각이며 완성까지 수분이 걸릴 수 있다. 기본은 ChatGPT 구독 `gpt-6.1-sol`, 추론 `high`. 앱에서 계정별 모델 목록과 지원되는 추론 수준, 프롬프트, 예약 시각, 유효기간, 관심 목록을 수정한다. 별도 OpenAI API 선택도 지원한다. 구독 실패를 결제 API로 바꾸지 않는다. 뉴스 AI의 기존 공급자 선택과 시황 공급자 선택은 구분한다.

원본 뉴스레터의 장기 ETF/분산투자·밸류에이션·가상의 보유 비중·시나리오 확률은 제외했다. 뉴스엔진에 필요한 시장 방향, 필수 지표 9개, 핵심 재료의 사실/해석·새로움·영향 범위·업종 전달 경로·반대 근거·위험·장중 확인 조건을 중심으로 재작성했다. 검색 첫 결과가 최신이라고 가정하지 않으며 기관의 최신 목록과 원문 시각을 확인하도록 한다. 계좌·주문·현금·보유 비중은 전송하거나 추정하지 않는다.

편집 기본 원문은 [morning-market-brief-prompt.txt](morning-market-brief-prompt.txt)이며 실행은 `application/morning_market_brief.py`의 기본값 또는 NAS에 저장한 사용자 편집본을 사용한다. 고정 출력·근거·시간 검증 계약은 편집 프롬프트 뒤에 붙여 유지한다.

## 앱·조회·검증 계약

- 메인 `아침 시황` 메뉴 및 뉴스 AI 설정의 시황 편집 버튼으로 같은 창을 연다. 결과 탭, 프롬프트·모델·예약 설정 탭, 지금 생성, 기본 프롬프트 불러오기를 제공한다. NAS 설정은 revision 비교 후 저장하고 GUI는 background HTTP 요청을 사용한다.
- 기존 NAS 인증을 사용하는 `GET/PUT /api/v1/market/morning-brief/settings`, `GET /api/v1/market/morning-brief`, `POST /api/v1/market/morning-brief/run`을 추가했다. 추가 credential 필드나 토큰 import는 받지 않는다.
- `morning-market-brief-v4`는 ①의 시장 방향/참여 폭/집중도, facts/events, 근거, 관계 후보, 지속성, 장중 확인/반대/무효화 조건, unknowns를 구조화한다. 숫자 신뢰도·매매 점수·확률을 금지하고 초기 UNCONFIRMED만 허용한다. 단위·유한 수치·시차·cutoff·실제 검색 URL·참조·추가 필드를 검증한다. 기존 v2/v3는 보존하되 현재 기계 입력에서 제외한다.
- AP의 짧은 주소와 제목 포함 주소는 검색 결과에 있는 동일한 32자리 기사 ID일 때만 연결한다. 도메인 일치만으로 다른 URL을 승인하지 않는다.
- 공식 확정 종가는 거래일·공식 원문·cutoff 이전 완료 근거가 명확하면 시·분 없이 허용한다. 시간 정밀도/가격 확정 상태/공개 근거를 필드로 저장하고 화면은 거래일 기준으로 표시한다. 실시간/지연 시세는 관측 시각 없으면 `unavailable/null`이다. 확인된 최근 완료 거래일 지수 종가는 휴장 때문에 36시간이 지나도 유지하며, 오래된 다른 자료는 stale로 표시한다. 미래 자료·가짜 시각·출처/단위 오류는 계속 거절한다.
- 조회의 available/TTL을 검사한다. `advisory_only=true`, `auto_trade_eligible=false`, `consumer_binding=not_connected`: 생산·조회 계약은 구현했지만 네이티브 주문/EV/위험 승인에 자동 적용했다고 주장하지 않는다. 원문 응답과 검증 결과를 분리해 보존한다.

## 저장·수명·진단

기존 `central_documents` writer에 `morning_market_brief_settings`와 `morning_market_brief_runs` collection을 추가한다. 기존 connection·transaction·commit을 변경하지 않는다. 각 실행은 시작한 설정 revision·프롬프트/hash·완성 프롬프트/출력 계약 hash·기준 시각·관심 목록·입력 hash·원문·결과·검색 출처·모델/추론·usage·상태를 보존한다. 시황도 `news_request_usage`에 목적을 기록해 중앙 일일 상한을 공유한다. 실패 POST의 token 수가 없으면 미계측으로 남긴다.

예약은 하루 한 번, 시작 시각 이후 15분까지만 catchup한다. claim 저장이 POST보다 앞선다. 재시작한 `running`은 이전 결과 미확인으로 남기고 같은 날짜를 자동 재요청하지 않는다. ‘지금 생성’만 새 실행을 만든다. 동시 요청은 합치고 실행별 설정을 고정하며 종료·계정 변경은 소유한 요청을 drain한다. 일반 뉴스의 180초 stream 상한은 유지하고 직접 웹 조사에만 600초 상한을 적용한다.

진단 master 아래 `morning_market_brief` 자식을 등록했다. 기본 pause 없음; master OFF/TTL/재시작은 기존 계약. pause는 새 시작을 막고 진행 요청과 완료 기록을 보존한다. 재개 때 창 안의 미시도 날짜만 실행한다. 두 collection을 writer registry와 공통 PostgreSQL 문서 단계 계측에 등록한다. runtime은 시도·완료·실패·경과 시간을 표시한다. 일반 문서 writer에서 실제 반영 row 수를 얻지 못하면 입력 시도 수로 가장하지 않는다.

## 현재 검증·운영 경계

- 실제 Windows 구독 Sol High 작은 검색 probe: 검색 2회/22,204 tokens, 공식 한국은행 URL 반환. 금융 사실 자동 승인 증거로 사용하지 않는다.
- 이전 v2 종합 probe(새 v4 수락 증거 아님): 처음에는 기존 180초 제한에서 timeout. 웹 조사 상한을 분리한 뒤 검색 10회/102,040 tokens의 완성 응답을 받았다. 초기 strict 검증은 AP 주소 별칭과 일부 관측 시각 누락을 거절했다. 동일 완성 응답을 추가 LLM 호출 없이 정규화·검증해 시각 미확인 수치를 제외했다. 전체 지표가 완전하게 확보됐다고 보고하지 않는다. 원문/정규화 결과와 실패 기록은 `data/diagnostics/morning-market-brief`에 보존한다.
- Windows 관련 259건: 255 pass/Linux 전용 4 skip/native exit 0. 공식 Windows 프로필과 Linux NAS 필수 검사는 별도로 등록해 skip을 필수 통과로 바꾸지 않는다. 잘못된 테스트 모듈 이름·Qt 보조 함수 호출 실패와 수정 후 결과를 각각 보존했다. sandbox의 async socketpair 초기화 차단은 스택으로 확인하고 사용자 격리 환경에서 재검증했다.
- 새 unit 7개를 catalog에 등록했다. 현재 source의 all-local 전수·원격 CI·commit/push는 수행하지 않았다.
- 이전 v3의 정식 Windows 프로필99건은 과거 결과로 보존한다. 확정 종가 예외·현재 시세의 관측 시각 강제·휴장/미래 거래일·화면 표시를 추가한 v4 최종 소스의 정식 프로필 결과는 `v4-final-platform-profile/run.json`에 둔다. 105건/9 작업자 pass, skip0/native exit0이며 검사 중 소스 변경은 없었다. C1/뉴스 점수/매매·위험·실행 핵심 6파일은 HEAD와 동일하다. all-local·원격 CI·새 실제 웹 검색 v4 수락은 수행하지 않았다.
- 이전 v2 후보의 Windows 정식 프로필 92건, NAS 관련 64건 및 마지막 플랫폼 격리 12건(job `8b03c574ea493180ce4274d7521fbd1a`)은 pass/skip 0/운영 불변이었다. 이 결과를 변경된 v4의 NAS 수락으로 표시하지 않는다. 관리자 설치 시도는 자체 operation_busy 잠금에서 시작 전 거절됐다. 기존 runtime-v3 설치 묶음은 이전 출력 계약을 담은 준비본이다.
- NAS 현재 운영 소스는 `2026.10.14-recorder-shared-10m-v1-af0f8072f960415b`. 구독 runtime 관리자 설치·NAS OAuth 연결·새 source 활성화·07:55 실제 예약 수락은 남는다. 기존 수집기 중지 및 네이티브 자동매매 소비 활성화는 완료로 보고하지 않는다.

[구독 모델/추론](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference), [구독 경로 제한](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations), [검색 source metadata](https://developers.openai.com/api/docs/guides/tools-web-search), [Sol 추론 수준](https://developers.openai.com/api/docs/models/gpt-6.1-sol).

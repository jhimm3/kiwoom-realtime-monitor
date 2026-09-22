# 과거 자료 확보 실행 기획

갱신일: 2026-09-23 · 상태: **표본 도구 구현·첫 검증 진행** · 상위 문서: [개발 로드맵](../FUTURE_DEVELOPMENT_ROADMAP.md)

## 목표와 범위

기존 주도후보를 유지하며 대신증권의 최근 약 2년 1분봉·최근 약 5년 5분봉과 네이버 웹사이트의 과거 뉴스를 확보한다. 지금까지 쌓은 자료와 연결해 LLM 분석·학습 및 기존 연구 엔진에 공급할 사례를 만든다. 표본 수집기는 현재 저장소의 `scripts/probe_historical_backfill.py`에 구현했으며 원본 후보 DB는 수정하지 않는다.

## 확인한 출발 자료

원본: `C:/Users/pc-1/Desktop/kiwoom_history_backfill/data/kiwoom_history.sqlite3`

아래는 이번 읽기 전용 집계 시점의 파일 값이다. 원본 크기·수정시각의 변화가 없음을 확인했다. 총행수와 최소/최대 날짜가 모든 날짜의 완전성을 뜻하지 않는다.

| 항목 | 실제 확인값 |
|---|---|
| `candidate_days` | 2019-01-02~2026-09-18, 1,895일, 하루 50개, **94,750 종목·일**, 2,945종목 |
| `daily_bars` | 2019-01-02~2026-09-18, **5,910,806행**, 4,302종목 |
| `minute_bars` | 2025-09-01~2026-09-18, 257일, **4,562,200행**, 1,895종목 |
| 분봉 작업 `done` | 94,750건 중 **81,901건은 저장 0행**, 12,849건만 1행 이상 |
| 인접 `news_history.sqlite3` | 기사 1,615행, 발행값 2020-01-02, FLASH/일반 웹뉴스 검색, 제목·요약·링크, 본문 컬럼 없음 |

후보는 최종 일봉의 거래대금·상승률·고가 상승·거래량 증가 등을 조합한 사후 수집 모집단이다. 실제 과거 Top20나 단순 Top40로 이름을 바꾸지 않는다. 점수·선정 이유를 보존하고 이 후보를 다시 만드는 작업부터 시작하지 않는다.

## 1. 수집 범위와 원본 보호

| 구간 | 확보·연결할 자료 | 연구 해상도 |
|---|---|---|
| 최근 2년 | 대신 1분·5분, 기존 키움 봉·실제 보유 NAS 관측, 뉴스 | 실제 있는 1분/1초 범위와 5분을 구분 |
| 2년 이전~최근 5년 | 대신 5분, 기존 일봉·후보, 확보 가능한 뉴스 | 5분 또는 일 단위 |
| 5년 이전 | 기존 일봉·후보와 확보 가능한 뉴스 유지 | 확보된 해상도만 사용 |

1분 2년과 5분 5년은 중첩 자료이며 7년의 독립 자료가 아니다. 중첩 1분/5분을 두 개의 독립 학습 사례로 세지 않는다. 5분에서 1분·1초 내부 경로를 합성하지 않는다.

후보일 전의 재료·가격 배경과 이후 결과를 볼 수 있게 선행/후행 구간을 설정으로 둔다. 같은 종목의 겹치는 구간은 합친다. 단순 후보 당일 수집만으로 전일·주말 재료를 놓치지 않게 한다. 제공기간은 수집 시작일·실제 응답으로 고정한다.

기존 `minute_bars`의 키는 `(code, ts)`라 공급자와 1분/5분을 구분하지 못한다. 새 봉을 여기에 바로 덮어쓰지 않는다. 표본 수집용 저장소를 분리하고 실제 앱 입력을 연결할 때 기존 DB 계약과 마이그레이션 경계를 정한다. 공급자·종목·거래소·세션·주기·수정기준·봉 시각을 원본 식별에 포함한다.

## 2. 대신증권 표본과 본 수집

공식 담당자의 2026-09-01 안내는 주식 1분 약 2년·5분 약 5년이다. 종목별 실제 도달 날짜·상장기간·중단 구간은 아직 API 응답으로 확인하지 않았다. [공식 제공기간 안내](https://money2.daishin.com/e5/mboard/ptype_basic/Basic_018/DW_Basic_Read_Page.aspx?boardseq=60&m=9508&p=8827&page=1&searchString=&seq=29008&v=8636)

2026-09-22 현재 이 PC에 CREON/CYBOS Plus가 설치되어 `CpUtil.CpCybos`, `CpSysDib.StockChart`, `CpUtil.CpCodeMgr` COM 등록을 32비트에서 확인했다. CREON은 로그인되어 있었지만 관리자 권한으로 실행되어 일반 권한 조회에서는 `IsConnect=0`이었다. 같은 관리자 권한의 32비트 PowerShell에서는 `connected=true`를 확인했고 `scripts/daishin_stockchart_probe.ps1`로 실제 조회했다. 별도 32비트 Python 설치는 표본의 선행 조건이 아니다.

표본은 정상 거래 종목, 거래정지/상장기간이 짧은 종목, 2년·5년 경계가 필요한 종목을 기존 후보에서 고른다. 설치된 CYBOS/CREON 접속 환경·Python/COM 호환성·로그인을 확인한 뒤 주문 없는 차트 조회만 수행하는 수집 경로를 만든다.

`CpSysDib.StockChart`의 분봉은 기간 요청과 연속조회로 공급자가 제공하는 최과거까지 내려간다. 1분봉의 최초 완전 거래일을 경계로 정하고, 그 이전 거래일만 5분봉으로 저장한다. 5년은 목표 하한일 뿐 고정 중단점이 아니므로 더 오래 제공되면 `Continue=false`가 될 때까지 받는다. 기간 요청이 최신 거래일을 빠뜨리는 실제 응답을 확인했으므로 개수 요청으로 최신 구간을 겹쳐 받아 키 중복 제거로 보강한다. 제공 잔여 요청량·대기시간을 사용하고 키움 NAS 실시간 큐에 대신 백필을 넣지 않는다. [연속조회 안내](https://money2.daishin.com/e5/mboard/ptype_basic/Basic_018/DW_Basic_Read_Page.aspx?boardseq=60&m=9508&p=8827&page=1&searchString=&seq=26051&v=8636), [StockChart 도움말](https://money2.daishin.com/e5/mboard/ptype_basic/HTS_Plus_Helper/DW_Basic_Read_Page.aspx?boardseq=284&m=9508&p=8839&page=1&searchString=StockChart&seq=102&v=8642)

중첩 구간의 키움·대신 값을 비교할 때 단위·수정기준·세션·시간 의미를 먼저 맞춘다. 대신 1분을 5분으로 집계한 결과와 제공 5분을 비교하되 원본 둘은 보존한다. 값 차이를 무조건 어느 한쪽 오류로 정하지 않는다.

본 수집은 가장 오래된 제공구간이 밀려나는 점을 고려해 표본 확인 후 진행한다. 원본 응답/정규화 결과/작업 상태를 구분하고 구간별 체크포인트·재시도·중복 방지·확보 보고를 둔다.

삼성전자 KRX 정규장·무수정 연속조회에서 1분봉은 2024-08-29 09:01부터 2026-09-21 15:30까지 190,102개를 확보했다. 5분봉 원응답은 2021-08-11 09:05까지 도달했고, 정책 경계 전날인 2024-08-28까지 57,710개만 DB에 반영했다. 경계일은 2024-08-28의 5분봉 77개와 2024-08-29의 1분봉 381개로 이어진다. 5분봉 `15:15` 거래량은 1분봉 `15:11~15:15` 합과 정확히 일치하므로 봉 시각은 구간 종료시각인 `interval_end`로 기록한다. 공급자 원시 날짜·시각과 전체 NDJSON 응답도 별도로 보존한다.

## 2-1. 시장 지수·VI·과거 종목 규모 확대 (2026-09-23 기획)

연구의 당시 시장 상태를 복원하기 위해 KOSPI(`U001`)와 KOSDAQ(`U201`) 종합지수를 별도 원장으로 수집한다. 종목 후보 2,945개의 원장과 독립적인 두 지수 작업이며, 실행 중인 종목·뉴스 수집기의 진행 상태를 초기화하거나 우선순위를 빼앗지 않는다. 대신 StockChart는 업종(`U`) 코드를 받으며 날짜·시간·OHLC·거래량·거래대금 필드가 있다. 이 PC의 관리자 권한 32비트 CREON에서 두 코드 모두 1분·5분·일봉 5행씩 실제 응답했고 2019-01-02~10 일봉도 정상 응답했다. 표본 원응답은 `data/historical_collection/daishin-index-probe-20260923-01.ndjson`과 `-02.ndjson`에 있다. 지수 가격은 소수점이고 0거래량·0거래대금 분봉도 있으므로 종목 봉의 정수 가격 파서를 재사용하지 않는다. 거래대금 필드는 원시 수치가 들어오지만 단위·시장 전체 집계 의미는 KRX 대조 전까지 확정하지 않는다. [대신 StockChart 공식 도움말](https://money2.daishin.com/e5/mboard/ptype_basic/HTS_Plus_Helper/DW_Basic_Read_Page.aspx?boardseq=284&m=9508&p=8839&page=1&searchString=StockChart&seq=102&v=8642), [대신 지수 코드 Q&A](https://money2.daishin.com/e5/mboard/ptype_basic/Basic_018/DW_Basic_Read_Page.aspx?boardseq=60&m=9508&p=8827&page=1&searchString=&seq=19891&v=8636)

| 자료 | 목표 구간·저장 의미 | 수집 전 검증 |
|---|---|---|
| KOSPI·KOSDAQ 1분봉 | 최근 2년 또는 공급자 실제 최과거까지, 원본 거래대금과 단위 보존 | 두 코드에서 실제 응답, 지수 가격 소수점, 시간 의미, 첫·마지막 일자 및 거래대금 비영값 확인 |
| KOSPI·KOSDAQ 5분봉 | 1분봉 최과거일 **전날부터** 최근 5년 또는 더 오래 제공되는 최과거까지 | 1분/5분 중복일 제외, 1분 집계와 5분 거래량·거래대금 표본 대조 |
| KOSPI·KOSDAQ 일봉 | 최소 최근 7년 목표, 제공되면 그 이전도 보존. **2019-01-02 실응답 확인** | 원본 거래대금·종가·거래량과 KRX 일별 지수 시세 표본 대조. 분봉 합과 직접 동일하다고 가정하지 않음 |
| 일별 VI 발동 | 후보 종목 중심으로 날짜·종목·유형(동적/정적)·발동/해제시각·거래소를 원본으로 저장 | KRX [변동성완화장치 발동종목 현황](https://data.krx.co.kr/contents/MDC/STAT/issue/MDCSTAT224.jsp)의 과거일 조회 가능 범위·대량 추출 조건·응답 단위 표본 확인 |
| 과거 종목 시총·상장주식수 | 종목·거래일 기준 실제 당시 값. 첫 수집은 기존 후보 종목·일과 인접 배경일 | 대신 일봉 StockChart의 필드 12·13과 KRX [전종목 시세](https://data.krx.co.kr/contents/MDC/MDI/outerLoader/index.cmd?screenId=MDCSTAT015) 비교. 조정주가와 과거 상장주식수를 혼합 산출하지 않음 |
| 과거 유통주식수·비율 | 시총·상장주식수 확보와 독립된 **선택적 후속**. 보고서 기준일과 공개일을 함께 보존한 공시 스냅샷 | 대신 StockChart 필드 12의 상장주식수는 유통주식수가 아님. [OpenDART 주식의 총수 현황](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS002&apiId=2020002)의 `distb_stock_co`(발행주식수−자기주식수)는 확보 가능하지만, 대주주 등 고정 보유분까지 제외한 시장 유동주식 비율과도 정의가 다름 |

지수는 점수(float)를 정수 종목 가격으로 변환하지 않고, `(provider, index_code, venue, interval, bar_time, adjustment)`로 원본 식별한다. 거래대금은 제공 필드의 실제 단위가 확인될 때까지 원시값과 공급자 단위를 함께 저장하고, 시장 전체 거래대금인지 지수 구성종목 거래대금인지도 표본 대조로 확인한다. 1·5분과 일봉은 시간 해상도가 다르므로 일봉 7년을 분봉 완전성으로 해석하지 않는다.

시총은 일봉의 해당 거래일 값이 있으면 높은 신뢰도로 복원할 수 있으나, 그날 장중 각 분의 시총은 별도 관측이 없으면 알 수 없다. 현재 앱의 유통비율·유통주식수는 키움 `ka10001`의 `dstr_rt`·`dstr_stk`를 읽으므로 대신의 과거 상장주식수로 대체할 수 없다. [키움 공식 `ka10001` 예제](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/%EA%B5%AD%EB%82%B4%EC%A3%BC%EC%8B%9D/%EC%A2%85%EB%AA%A9%EC%A0%95%EB%B3%B4/get_domestic_stock_info.py)의 요청에는 `stk_cd`만 있고 과거 기준일이 없다. 현재 중앙 서버는 조회한 날의 `stock_fundamentals` 스냅샷을 별도로 남기므로 그 수집 시작 이후의 관측 이력은 재사용할 수 있지만, `ka10001` 재조회로 수집 이전 날짜의 값을 복원할 수는 없다. 공시 유통주식수는 보고서 결산기준일의 값이며, 그 정보가 시장에 알려진 시점은 제출·공개일이다. 과거 연구 입력에는 `effective_on`, `published_at`, `observed_at`, `source_ref`를 분리해 미래에 공개된 수치를 과거 판단에 넣지 않는다. 일별 유동비율은 검증된 과거 공급원이 없으면 채우지 않고 `unavailable`로 둔다. VI 기록도 KRX 화면의 실제 과거 조회 하한이 확인될 때까지 확보 기간을 약속하지 않는다.

실행 순서는 두 지수의 1분·5분·일봉 및 거래대금 소량 표본 → 별도 지수 원장·재개 수집 → KRX VI 과거일 표본 → 후보 종목의 대신 일봉 시총·상장주식수 표본이다. 이 네 항목에는 DART가 필요 없다. 유통주식수가 실제 연구 입력으로 필요해질 때만 DART 공개시각을 지킨 별도 후속을 연결한다. 현재 실행 중인 종목 봉과 뉴스 수집은 계속 둔다. 각 원본의 범위·누락·단위가 검증된 뒤에만 연구 입력에 연결한다.

## 3. 네이버 증권 사이트 뉴스

목표 경로는 네이버 개발자 뉴스 검색 API가 아닌 **네이버 웹사이트가 쓰는 응답**이다. 이전 대화와 외부 `backfill_news.py`를 다시 확인해 날짜 지정 검색 경로 `s.search.naver.com/p/newssearch/3/api/tab/more`를 복원했다. 2020-01-02 써니전자 한 페이지에서 구조화 기사 10건을 다시 읽어 별도 표본 DB에 저장했다. 검색 날짜는 `published_precision=date`로 보존하며 정확한 시각처럼 자정으로 바꾸지 않는다.

현재 네이버 증권 종목 페이지의 `/api/domestic/detail/news`도 별도로 확인했다. 최신 목록은 기사 ID·매체·분 단위 발행시각·요약·관련기사 묶음을 제공하지만, 삼성전자 기준 20개씩 100페이지까지만 응답하고 101페이지부터 HTTP 400이며 100페이지도 2026-09-16까지만 도달했다. 따라서 이 최신 목록만으로 2년·5년 백필을 구성하지 않는다.

기존 외부 `backfill_news.py`는 사이트 속보·공지·해외뉴스와 일반 네이버 웹뉴스 검색을 함께 사용한다. 종목 웹검색이 곧 증권 종목별 목록은 아니다. 작업 상태·기사 ID 중복 제거·원응답 보존 기반은 재사용 검토한다.

현재 표본 수집기는 날짜 지정 검색 뒤 언론사 원문에서 JSON-LD `datePublished`, `article:published_time`, `<time>`과 화면의 `data-date-time`을 읽어 `published_at`을 보강한다. `published_at_source`, 원문 문자열, 실제 확인 URL, 확인 상태·시각도 함께 저장한다. 날짜만 확인되면 `published_precision=date`를 유지하며 자정으로 만들지 않는다. 2020-01-02 써니전자 10건 표본에서는 5건의 초 단위 원문 시각을 찾았고, 나머지는 차단 2건·시각 없음 2건·기사 없음 1건으로 상태를 남겼다.

현재 앱과 네이버 검색 API의 정규 필드명은 `published_at`이며 API 원응답의 `pubDate`를 여기에 변환한다. 장기 스키마에서는 원문 게시시각인 `publisher_published_at`, 유통시각, 최초 관측시각을 더 명확히 분리할 수 있도록 이번 표본의 출처 필드를 보존한다. 언론사 원문이 403/로봇 확인이면 우회하지 않고, 제목이 검증된 네이버 기사 링크만 대체 경로로 사용한다. HTTP 200이어도 삭제 안내 페이지만 있으면 `article_unavailable`로 구분한다. 원문 BODY 단계에서는 본문과 시각을 한 번에 읽어 기사당 중복 요청을 만들지 않는 방향을 유지한다.

목록에서 찾은 기사와 본문을 확보한 기사를 구분한다. `description`이 없다는 이유만으로 사이트 목록의 기사 메타데이터를 버리지 않는다. 기존 실시간 검색 API의 필터를 다른 소스에 그대로 적용하지 않는다.

기사 ID/URL, 매체, 제목, 발행/수정/수집시각, 시각 정밀도, 본문 상태, 원문 링크를 보존한다. 기사-종목/후보/테마 관계는 별도로 연결해 한 기사가 여러 종목에 나온 횟수를 독립 사건 수로 세지 않는다. 당시 상호는 종목코드·상호변경 근거로 연결한다.

NAS 참조 DB의 `stock_aliases`는 종목코드별 상호와 `valid_from`/`valid_to`, KIND 근거 URL을 가진다. 후보일에는 유효한 당시 상호를 기본 검색어로 사용하고, 이전 대화에서 정한 상호변경일 앞뒤 14일에는 구·신 이름을 모두 작업으로 만든다. 기본 이름과 경계 보완 이름은 `name_source`로 구분하고 KIND `source_ref`를 보존한다. 현재 94,750개 후보 종목·일에서 기본 작업 94,750개와 경계 보완 340개, 합계 95,090개가 생성됐다.

원문 조회는 언론사 원문과 제목이 검증된 네이버 보관 링크의 시도를 각각 기록한다. `published_at_found`만 현재 학습 적격이며 `time_not_found`, `blocked`, `article_unavailable`, `title_mismatch`, `fetch_error`, `not_fetched`는 원자료를 버리지 않고 학습 제외 사유와 함께 남긴다. 원문이 없거나 시각이 없는 건은 별도 집계할 수 있다.

발행시각이 검증된 기사는 기존 인증 API의 `news_article` collection으로 증분 반영한다. NAS는 이를 `collector_id=naver_historical_web`, `collection_scope=historical_backfill` revision으로 저장하고 기존 BODY 작업기로 원문을 읽는다. 따라서 별도 수집 DB는 원응답·실패·재개·감사 원장이고, 앱이 읽는 기사와 원문 상태는 기존 NAS 뉴스 revision DB에 들어간다. 같은 기사·종목 재전송은 기존 content hash 경계에서 중복 revision을 만들지 않는다. 발행시각 미확인 자료는 수집 DB에는 보존하지만 이 운영 반영 대상에서 제외한다.

Npay 공식 도움말에는 서비스 화면 밖 개인 프로그램에서 증권정보를 재가공해 이용하는 것을 제한하는 안내가 있다. 뉴스 목록·본문의 보관과 학습 이용에 해당하는 범위를 확인해 수집 방식에 반영한다. 사이트 접근과 이용 범위는 별도 확인 항목이며 이 문서는 해당 이용 가능성을 확정하지 않는다. [공식 이용 안내](https://help.pay.naver.com/faq/content.help?faqId=17106)

## 표본 실행 위치

수집 결과는 앱 원본 DB와 분리한 `data/historical_intelligence.sqlite3`에 저장한다. 이 파일은 원응답, 기사 메타데이터·종목 연결·원문 확인 시도, 공급자·주기·거래소·세션·수정기준을 가진 봉, 재개 가능한 뉴스 작업을 분리해 보존한다.

```powershell
.\.venv\Scripts\python.exe scripts\probe_historical_backfill.py candidates
.\.venv\Scripts\python.exe scripts\probe_historical_backfill.py news-seed --database <참조DB> --name-transition-days 14 --reset-jobs
.\.venv\Scripts\python.exe scripts\probe_historical_backfill.py naver-history 004770 "써니전자" 2020-01-02 --pages 1
.\.venv\Scripts\python.exe scripts\probe_historical_backfill.py daishin-sample 005930 --interval 1 --count 20
```

마지막 명령은 CREON Plus 로그인과 같은 Windows 권한 수준에서 실행한다. 이 PC에서는 CREON이 관리자 권한이므로 실행 터미널도 관리자 권한이어야 한다. 삼성전자 연속조회와 해상도 경계는 확인했으며, 전체 후보 확대 전에는 시세 작업 재개 원장과 종목별 완전성 보고를 추가한다.

닫힌 SQLite 스냅샷과 원응답은 `scripts/publish_historical_intelligence_to_nas.py`로 `X:\kiwoom-monitor\deploy\synology\server-data\historical-intelligence\v1` 아래에 게시한다. 각 run은 내용 해시를 포함한 불변 디렉터리이고 `latest.json`만 새 run을 가리킨다. SQLite는 NAS 공유에서 직접 갱신하거나 검증하지 않고 로컬 snapshot/검증 복사본을 사용한다.

진행률은 같은 NAS 경로의 `STATUS.md`에서 확인한다. 뉴스 완료/대기/실패·목록 기사·발행시각 상태·운영 뉴스 DB 반영 건수와 대신 종목 작업·주기별 봉 수를 분리해 표시한다. `status.json`은 같은 내용의 기계 판독본이다. 수집기는 작업 하나가 끝날 때마다 진행률을 갱신하고 묶음 종료 시 새 DB 스냅샷을 게시한다.

로컬 프로세스와 NAS 게시 상태는 프로젝트 루트의 `수집기_모니터.cmd`에서 함께 확인한다. 모니터는 관리자 권한으로 열리며 뉴스·대신의 작업 원장 진행률, 현재 작업, heartbeat, 최근 오류와 NAS 상태 갱신 시각을 표시하고 정지한 수집기를 재시작한다. CLI 확인은 일반 PowerShell의 `./scripts/show_historical_collectors.ps1`을 유지한다. 뉴스 수집은 전체 원장을 한 실행에서 소진하도록 `./scripts/run_historical_news_collector.ps1 -Jobs 100000`, 대신 수집은 CREON과 같은 관리자 PowerShell에서 `./scripts/run_daishin_collector.ps1 -Jobs 10000`으로 직접 시작할 수도 있다. `Jobs`는 공급자 조회 건수 제한이 아니라 한 프로세스가 작업 원장에서 처리할 최대 종목 수이며, 실제 대기 작업이 먼저 소진되면 즉시 끝난다.

뉴스 검색의 네트워크 세션 자체가 사용할 수 없으면 해당 작업을 `pending`으로 되돌리고 시도 횟수를 소비하지 않은 채 수집기를 실패 종료한다. 이 경우 프로세스가 살아 있는 것처럼 실패 작업을 계속 넘기지 않는다. 로컬 생존 상태는 작업마다 갱신하고, 운영 뉴스 증분 반영은 기본 10작업마다 수행한다. NAS 진행률은 기본 100작업마다 게시하되 운영 중에는 작업 원장만 빠르게 집계하고 17GB대 기사·원문시도 전체 집계는 최종 스냅샷에서 수행한다. `STOP_NEWS`를 이용한 정상 중지는 빠른 마지막 상태만 게시하고 대형 불변 스냅샷을 만들지 않는다.

검색 페이지는 시작 간격 0.7초를 공유하는 4개 작업자로 응답 대기를 겹치며, 원문 확인은 같은 언론사 도메인에 한 요청만 허용하는 16개 작업자로 수행한다. 0.5초 간격에서는 시간당 반복된 403의 60초 제한 대기가 이론상 추가 요청량보다 커 실제 처리량이 낮아졌다. 원문 대기열은 같은 언론사 기사가 연속돼도 대기 항목이 작업자 스레드를 차지하지 않으며, 서로 다른 언론사는 즉시 병렬 처리한다. 검색과 원문 처리는 한 작업 안에서도 파이프라인으로 진행한다. 검색 또는 원문 결과와 SQLite 저장은 메인 흐름에서 순서대로 확정해 재시작 원장과 중복 제거 계약을 유지한다. 검색 HTTP 403/429가 발생하면 모든 검색 작업자가 60초 제한 대기를 공유하고 막힌 페이지만 최대 10회 재시도한다. 제한 상태와 페이지는 heartbeat에 기록한다. 연속 제한이 그 횟수를 넘을 때만 작업을 `pending`으로 반환하고 래퍼가 다시 시작하므로, 일시 제한 때문에 이미 성공한 수십 페이지를 처음부터 다시 요청하지 않는다.

후보 전체 대신 수집기는 1분봉을 공급자가 주는 최과거까지 먼저 저장한 뒤 그 최과거일의 전날을 5분봉 `ToDate`로 전달한다. 따라서 5분봉은 처음부터 1분봉보다 오래된 구간만 내려받는다. DB import의 `before_date` 경계도 유지해 공급자 응답 이상이나 경계 오차가 있어도 한 거래일에 두 해상도가 섞이지 않게 한다.

대신 작업 원장이 이미 만들어진 재시작에서는 기존 봉 전체를 다시 `GROUP BY`하지 않는다. 새로 원장에 들어온 종목이 있을 때만 해당 코드의 기존 1분·5분봉을 확인해 과거 수집 결과를 승계한다. 중단된 작업의 부분 봉은 양쪽 해상도가 있더라도 기존 원장 상태를 기준으로 다시 실행한다.

일별 검색의 마지막 부분 페이지와 프로세스 시작 비용을 줄이기 위해 밀도 50% 이상인 같은 종목·검색어·월의 대기 일자를 날짜 범위 작업으로 묶는다. 완료된 일별 표본의 평균 페이지 수로 묶음 크기를 산정해 예상 80페이지 이하로 제한하고, 표본이 없으면 일 2페이지를 가정한다. 평균 50페이지 이상인 대량 조합은 범위 재검색 비용이 더 크므로 일별 작업을 유지한다. 실제 응답이 100페이지 상한에 닿으면 해당 범위를 날짜로 이분해 재개한다. 기존 일별 작업은 삭제하지 않고 `grouped` 상태와 범위 구성원을 보존한다. 검색 결과 이미지 경로의 원 게시일 또는 원문에서 확인한 `published_at`이 범위 안에 있을 때만 원래 날짜 작업에 귀속하며, 날짜를 확인하지 못한 기사를 임의로 범위 시작일에 넣지 않는다. 따라서 묶음 검색은 요청 효율만 바꾸고 기사·종목·후보일 관계와 학습 적격 기준은 바꾸지 않는다.

## 4. 수집 상태와 완료 기준

표본 단계에서 필요한 최소 상태 기록은 다음과 같다. 기존 작업 원장이 있으면 확장하고 동일한 기능의 원장을 중복 생성하지 않는다.

| 기록 | 필요한 의미 |
|---|---|
| 요청 | 공급자·대상·구간·주기·세션·수정기준·설정 버전 |
| 실제 확보 | 첫/끝 시각·행수·페이지/연속조회 위치·중복·자료 없음의 근거 |
| 품질 | 부분 구간·오류·정정·시각 정밀도·본문 확보·조회 한도/절단 |
| 재개 | 마지막 확정 체크포인트·재시도 횟수·오류 사유 |
| 결과 | 처리 완료 / 자료 없음 / 부분 확보 / 확보 확인 / 오류 |

HTTP 성공·작업 `done`·구간 최소/최대 날짜만으로 완전 확보를 선언하지 않는다. 거래 없는 구간과 조회 실패는 별도 근거로 구분한다. 재시작해 같은 구간을 다시 읽어도 행수와 학습 표본이 중복되지 않아야 한다.

첫 산출물은 후보별 확보 현황과 소수의 시세·뉴스 연결 사례다. 이를 보고 종목 수·날짜 범위·요청량·저장량·예상 시간을 산정한다. 전체 범위 종료 시 부족한 구간도 목록에 남긴다.

## 5. 기존 연구와 LLM 입력 연결

```text
후보 날짜·종목·선정 이유
  + 원천별 봉·실제 관측
  + 기사·사건·테마 근거
  → 입력 시각·해상도·모집단·결과 기간을 명시한 사례
  → LLM 해석/사례 검색/학습 예제
  → 기존 연구 엔진의 정책 비교·평가
```

실제 수신 기록의 재생과 지금 모은 과거 자료의 복원을 구분한다. `available_at`은 실제 수집·분석 시각으로 보존하고 과거 발행/봉 시각으로 조작하지 않는다. 과거 복원 연구에는 별도의 가정·입력 범위·버전을 둔다.

사후 일봉 후보를 당시 아침 Top20 입력처럼 넣지 않는다. 순위가 필요한 기존 전략은 해당 자료가 없으면 미지원이며 가짜 순위를 만들어 통과시키지 않는다. 5분 사례는 지원하는 정책·관찰 지평으로 제한한다.

후보 안의 실패·무반응·미거래도 보존하고 비후보를 포함한 전체 시장 수익성으로 일반화하지 않는다. 학습 입력과 이후 결과를 분리하고 사건·기간 중복을 고려한 시간순 검증 및 미사용 평가 구간을 유지한다.

사례 연결이 완료돼도 모델 가중치 학습 완료는 아니다. 원문 확보 → 품질 검토 → 사례 구성 → 학습/검색 방식 선택 → 평가 순서로 진행한다. 타점 세부 규칙은 이 자료와 사용자의 학습을 바탕으로 이후 조정한다.

### D06 첫 LLM 학습 준비 사례 계약

`scripts/prepare_historical_learning_cases.py`는 검증된 `historical_reconstruction/v1`을 `historical_learning_cases/v1` 불변 묶음으로 변환한다. 한 사례 안에서도 사후 후보 선정은 `sample_selection`, 발행시각이 검증된 기사 제목·검색 요약은 `model_input`, 아직 실행하지 않은 LLM 해석은 `interpretation.status=not_generated`, 다음 거래일 봉에서 계산한 가격 변화는 `outcome_label`로 분리한다. 결과 라벨은 모델 입력이 아니며, 기사 `published_at`을 실제 수집 `available_at`으로 바꾸지 않는다.

이 계약은 과거 당시의 strict 재생 입력이 아니다. 후보 생성시각을 알 수 없고 뉴스도 사후 수집했으므로 `strict_point_in_time_case=false`, `strict_backtest_input=false`다. 원문 전체가 아니라 제목과 검색 요약만 있는 현재 범위도 `content_scope`에 명시한다. 모델 호출이나 가중치 학습은 수행하지 않으며 `model_weight_training_ready=false`를 유지한다.

최종 첫 불변 표본 `data/research/historical-learning-cases/2026-09-18-v2`는 후보 50사례를 보존한다. 발행시각 검증과 후보일 이전 조건을 통과한 뉴스 근거가 있는 사례는 22개, 다음 거래일 결과 봉이 있는 사례는 2개, 결과 공백은 48개다. 원문 차단 1,295관계, 시각 없음 74관계, 후보일 뒤 발행 21관계 등은 입력에서 제외하고 이유별 개수로 남겼다. 기사 의미 관련성은 아직 사람 검토 전이며 `semantic_relevance_reviewed=false`다. 이는 사례 파이프라인과 누락 처리를 검증한 표본이며 RAG 품질, 미세조정 준비 완료, 전략 성과를 뜻하지 않는다.
### D06 기사 의미 검토 대기열

`scripts/prepare_historical_news_review_queue.py`는 학습 준비 사례의 기사 관계를 `historical_news_review_queue/v1`로 변환한다. 공급자·언론사·기사 식별자가 같은 기사는 한 검토 항목으로 합치되 연결된 사례·종목·검색어·수집 revision은 각 관계에 남긴다. 기존 `assess_stock_news` 결과는 사람이 먼저 볼 순서를 위한 `rule_hint`이며 의미 관련성 정답, LLM 판정, 테마 확정으로 취급하지 않는다.

첫 대기열 `data/research/historical-news-review-queues/2026-09-18-v1`은 2,035개 종목-기사 관계를 1,422개 고유 기사로 묶어 613개의 반복 검토를 줄였다. 종목별 규칙 힌트 중 하나라도 관련으로 나온 기사는 105개다. 전체 항목은 아직 `pending`이고 `rule_hint_is_ground_truth=false`, `llm_used=false`, `human_review_complete=false`, `model_weight_training_ready=false`다. 같은 파일은 NAS `server-data/historical-intelligence/v1/news-review-queues/historical-news-review-queue-578f89c206d709720184aa753649dc3840f949a066819b476fc27ce576311805`에도 불변 게시했다. 사람 판정이 입력된 항목만 별도 불변 결과가 되며, 이후 사건 ID를 기준으로 같은 사건이 학습과 평가에 동시에 들어가지 않게 분할한다.

`scripts/historical_news_review_workflow.py export`는 우선순위 기사 또는 전체 기사를 UTF-8 CSV로 내보내며 기존 파일을 덮어쓰지 않는다. `import`는 대기열 ID·hash·순번·항목 ID를 검증하고 완료된 행만 `historical_news_review_decisions/v1`으로 동결한다. 허용 판정은 `relevant`, `not_relevant`, `uncertain`이다. 관련 판정은 `canonical_event_id`가 필수이고 테마명이 있으면 `theme_profile_name`도 필요하다. 무관·보류에는 사건·테마를 붙이지 않는다. 일부만 검토한 결과는 `partial_review_result=true`이고 모든 결과는 `model_weight_training_ready=false`다.

첫 편집 작업본 `data/research/historical-news-review-work/2026-09-18-priority-v1.csv`에는 규칙상 우선 검토 105행이 들어 있다. 현재 사람 판정은 0건이라 불변 결정 결과는 아직 만들지 않았다. 실제 판정 뒤 사건 ID를 기준으로 같은 사건이 학습과 평가에 동시에 들어가지 않게 분할한다.

앱의 전략 연구 창 `과거 뉴스 검토`는 최신 불변 대기열과 결합된 작업표를 자동 선택한다. 기사 제목·검색 요약·원문 URL·연결 종목과 규칙 점수를 함께 표시하고, 판정 행을 임시 파일 교체 방식으로 저장한다. 관련 판정은 사건 ID를 자동 제안하되 사용자가 기존 사건 ID를 선택하거나 수정할 수 있고, 테마명은 현재 테마 저장소의 프로필 이름과 함께 기록한다. 현재 목록에서 사라진 과거 프로필 이름도 작업표에서 지우지 않는다.

`scripts/plan_historical_news_event_split.py`는 불변 사람 판정 결과에서 관련 기사만 골라 `historical_news_event_split/v1`을 만든다. 같은 `canonical_event_id`의 여러 기사는 최초 발행시각을 기준으로 하나의 사건이 되며 TRAIN·VALIDATION·OOS 사이에서 나뉘지 않는다. 시간대 표기가 달라도 timezone-aware 실제 시각으로 정렬한다. 무관·보류는 입력 정답으로 섞지 않고 제외 수량으로 남긴다. 일부만 검토한 원천으로도 계획 구조는 검증할 수 있지만 `partial_review_source=true`, `model_weight_training_ready=false`를 유지하며 OOS 검토 payload를 계획에 넣지 않는다.

같은 기능은 과거 뉴스 검토 화면의 `사건 분할 계획`에서 실행한다. 최신 불변 판정에 관련 사건이 3개 이상일 때만 TRAIN과 VALIDATION 사건 수를 입력받고, 선택 가능 상한을 조정해 OOS에 최소 1개 사건이 반드시 남게 한다. 계획은 `data/research/historical-news-event-splits/<plan_id>.json`에 불변 저장한다.

`scripts/prepare_historical_news_development_inputs.py`는 결정 dataset ID·파일 hash와 사건 분할 plan ID가 정확히 맞을 때만 `historical_news_development_inputs/v1` 디렉터리를 만든다. `train.jsonl`과 `validation.jsonl`에는 제목·검색 요약·검증 발행시각·연결 사례를 model input으로, 사람 확정 관련성·사건 ID·테마 프로필과 테마명을 target으로 분리한다. OOS 기사와 target은 어떤 출력 파일에도 넣지 않으며 TRAIN과 VALIDATION에 같은 사건 ID가 있으면 거절한다. 이 묶음도 RAG나 미세조정이 실행됐다는 뜻이 아니므로 `llm_used=false`, `model_weight_training_ready=false`를 유지한다.

같은 생성은 과거 뉴스 검토 화면의 `개발 입력 생성`에서 실행할 수 있다. 화면은 최신 불변 사람 판정에 dataset ID와 결정 파일 hash가 일치하는 사건 분할만 후보로 삼고, 일치하는 계획 중 가장 최근 저장본을 사용한다.

`scripts/prepare_historical_news_blind_validation.py`는 개발 입력의 VALIDATION을 모델 실행용 `historical_news_blind_validation/v1` 요청으로 투영한다. `requests.jsonl`에는 `sample_id`와 `model_input`만 있고 `human_target`, 관련성, canonical event ID, 테마 프로필·테마명은 포함할 수 없다. RAG와 미세조정 실행은 이 같은 요청 집합 ID를 사용하고, 채점 단계만 별도로 원본 validation target을 읽는다. 이 요청은 학습 자료가 아니므로 `training_allowed=false`다.

외부 방법의 JSONL 예측은 `scripts/prepare_historical_news_method_results.py`가 `historical_news_method_results/v1`으로 저장한다. 블라인드 요청의 모든 `sample_id`가 정확히 한 번 있어야 하며, 방법 종류·공급자·모델·구현 버전·RAG index 또는 미세조정 artifact ID를 결과 hash에 결합한다. `scripts/evaluate_historical_news_method.py`만 개발 입력의 VALIDATION target을 다시 읽어 사건 pairwise 군집, 정규화 테마 집합, 프로필 정확도, 기권 포함 coverage를 채점한다. 사건 ID 문자열 자체는 방법별 임의 표기이므로 직접 비교하지 않는다. 현재 입력은 관련 기사만 포함하므로 관련성 분류 평가는 별도 음성 표본 계약 전까지 지원하지 않는다.

### D03 첫 입력 계약

`scripts/export_historical_reconstruction.py`는 선택한 후보일의 후보·현재 확보된 대신 봉·뉴스 근거를 `historical_reconstruction/v1` 불변 디렉터리로 만든다. `manifest.json`과 `records.jsonl`의 hash·ordinal·revision ID를 `load_historical_reconstruction`이 다시 검증한다. 이 형식은 기존 `top20_membership` 입력으로 위장하지 않으며 `strict_top20_replay_supported=false`를 필수로 둔다.

종목·일별 해상도는 실제 저장 행을 기준으로 1분 우선, 없으면 5분, 둘 다 없으면 `unavailable`이다. 한 사례에 1분과 5분을 중복 입력하지 않고 5분 내부 경로를 합성하지 않는다. 뉴스는 검증된 발행시각뿐 아니라 원문 차단·시각 미확인 같은 상태도 관계 근거로 보존하되 `publication_time_verified`와 학습 제외 사유를 유지한다.

2026-09-22 첫 최종 로컬 표본 `2026-09-18-initial-v2`는 2026-09-18 후보 50개 중 당시 현재 수집이 끝난 2개 종목의 1분봉 762행, 뉴스 관계 3,497행을 포함했다. 뉴스 관계는 발행시각 확인 2,056개, 원문 차단 1,295개, 시각 없음 74개와 그 밖의 실패 상태를 manifest에 분리했다. 나머지 48개 종목·일은 빈 봉을 만들어 채우지 않고 미확보로 집계했다. 수집이 계속되므로 이 표본은 불변으로 두고, 더 넓은 자료가 필요하면 새 dataset ID의 export를 만든다.

후속 어댑터는 후보일 장 마감 뒤의 날짜만 전략 결과 구간으로 인정한다. `2026-09-18-through-2026-09-21-v1`에서 파생한 `historical-strategy-input/2026-09-18-v1`은 후보 50개와 000150·005930의 2026-09-21 정규장 1분봉 각 381개를 포함한다. 원자료 `available_at`은 payload에 보존하고, 재생 순서에만 명시적인 `historical_bar_close` clock을 사용한다. 5분봉과 진행 중·실패 작업의 봉은 기존 1분 전략 입력에서 제외한다.

```powershell
.\.venv\Scripts\python.exe scripts\export_historical_reconstruction.py `
  --candidate-database <후보DB> --intelligence-database data\historical_intelligence.sqlite3 `
  --dates 2026-09-18 --outcome-end-date 2026-09-21 --output <복원출력>
.\.venv\Scripts\python.exe scripts\prepare_historical_research_input.py `
  --source <복원출력> --date 2026-09-18 --output <연구입력>
```

기존 runner는 `historical_candidate_population`을 명시적인 복원 입력에서만 허용한다. 이 모집단은 20개로 자르지 않으며 `top20_membership`으로 이름을 바꾸지 않는다. 당시 순위 연속성이 없으므로 rank persistence를 켠 설정은 실행 전에 거부한다. 가격 Factor·Paper 실행 계약은 재사용하지만, 실제 정책 비교는 수집 범위와 평가 분할·비용 가정을 고정한 뒤 수행한다.

### D03 다기간 입력

복수 후보일 export는 각 후보일 뒤 후보 DB에서 확인되는 바로 다음 거래일까지만 해당 사례의 결과 구간으로 쓴다. 다음 선택일이 수개월 뒤라는 이유로 그 사이 전체 봉을 한 사례 결과로 넣지 않는다. 어댑터는 사례별 후보군 revision을 다음 거래일 첫 봉 직전에 활성화하고, 완료된 대신 1분봉만 합친다. 결과 봉에는 `historical_case_date`를 남겨 사례 귀속을 검증한다. 완전한 1분 결과 봉이 없는 선택일은 합성하지 않고 파생 manifest의 `excluded_cases`에 남긴다.

첫 다기간 불변 표본은 `multi-period-2024-2026-v1`이다. 선택일과 결과 종료일은 2024-12-24→12-26, 2025-05-29→05-30, 2026-01-14→01-15이며, 후보군 revision 3개와 분봉 7,564개로 총 7,567개 연구 관측을 담았다. 이는 수집 도중 구조를 검증하기 위한 표본이다. 시간순 TRAIN/VALIDATION/OOS 정책과 비용 가정을 고정한 성과 비교는 수집 범위가 더 넓어진 뒤 별도 불변 요청으로 실행한다.

`scripts/plan_historical_research_split.py`는 완성된 사례만 통째로 과거순 TRAIN→VALIDATION→OOS에 배정한다. TRAIN·VALIDATION 사례 수를 명시하고 나머지를 최소 한 사례의 OOS로 남긴다. 산출물 `historical_chronological_split_plan/v1`은 source dataset ID와 revision hash, 사례별 봉 수·시간 경계, 기존 `chronological_holdout/v1` 평가 문서를 함께 고정한다. OOS는 항상 `SEALED`이고 결과를 포함하지 않는다. 첫 계획은 `data/research/historical-evaluation-plans/multi-period-2024-2026-v1.json`이며 사례를 각 1개씩 TRAIN/VALIDATION/OOS에 배정했다.

`scripts/prepare_historical_development_inputs.py`는 계획과 source ID/hash를 대조한 뒤 TRAIN·VALIDATION만 독립 `development_partition/v2` 입력으로 만든다. 역사 후보군 종류와 사후 모집단 제한을 투영 manifest에 유지하며 부분 입력의 ordinal은 새 파일 순서대로 다시 매긴다. 첫 패키지 `data/research/historical-development-inputs/multi-period-2024-2026-v1`은 두 개발 입력의 quality `PASS`와 revision 재현성 `VERIFIED`를 확인했고 `oos_included=false`다.

`scripts/prepare_historical_baseline_requests.py`는 이 OOS 없는 패키지에서 돌파·눌림 재가속의 TRAIN/VALIDATION 고정 요청 네 개만 만든다. 역사 입력에는 당시 TOP20이 없으므로 순위 Factor를 끄고, 잠정 파라미터·브로커 미검증 비용이라는 상태를 요청에 명시한다. `scripts/summarize_historical_baseline_results.py`는 실행 DB의 검열 사유까지 포함해 비교표를 만든다. 첫 구조 실행에서는 대부분의 주문이 다음 연속 1분봉 증거 부족으로 검열됐으므로 수집 확대 전 전략 선택 근거로 사용하지 않는다.

`scripts/assess_historical_development_readiness.py`는 각 분할의 최신 역사 후보군을 기준으로 후보별 분봉 존재와 최소 한 연속 1분쌍을 검사한다. 하나라도 빠지면 요청 생성 기본값은 `BLOCKED`이며, 최초 희소 표본처럼 실행 경로만 확인할 때에만 `--allow-partial`을 명시한다. 이 gate는 수익성이나 대표성을 보증하지 않고 현재 1분 전략이 후보 전체에서 다음 봉 체결을 검토할 최소 입력이 있는지만 판정한다.

후보 코드에는 보통주 숫자 코드뿐 아니라 `00499K` 같은 영문 포함 6자리 단축코드도 있다. 대신 작업 원장과 두 StockChart bridge는 `[0-9A-Z]{6}`을 허용한다. 파생상품 여부나 연구 포함 여부는 후보 원장의 기존 선택을 유지하며, 코드 모양만으로 자동 제외하지 않는다.

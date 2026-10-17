# 통합 시장 관찰·뉴스·장전 시황 NAS 검증

## 2026-10-17 연구 자료 보존·공급 수정 완료 및 NAS 반영

현재 활성 release는 `2026.10.17-research-input-retention-v2-9164688db17d1ddb`다. 출처/원문 열람/게시·관측 시각/모델 분류 미확인을 이유로 연구 값·출처·참조를 지우거나 응답 전체/후속 입력을 막지 않는다. 구독/API의 완료된 텍스트·사용량도 검색 출처 부재로 버리지 않으며, 일부 검색 미완료는 상태로 남긴다. 기본 작성 지시도 자료 보존으로 수정했고 사용자가 저장한 프롬프트·관심목록/공급자/모델·07:55 예약은 유지한다. 화면은 원래 처리 기록과 연구 자료·전체 출처·모델 분류·미확인 메모를 함께 표시한다. 연구 데이터의 시각 미확인 테마와 일부 이력도 묶음·분할에 보존한다. 과거 시점 비교는 시각을 임의 배정하지 않으며 구조/원본 무결성과 기존 실행 경계는 유지한다.

PC 관련218건·후속68건, NAS 관련175건(job `bda7dc166c031778d30651c33a07d79b`)·후속57건(job `efff741a166aed3b6b4f2950d2b42a1e`)이 실패/오류/skip0·정리/후검사를 통과했다. 최초 화면2건 실패와 NAS import2건 오류는 보존했고 원인을 수정했다. NAS의 실제 실패 기록에는 available_at가 누락된 것이 아니라 null로 저장돼 후속 공급이 막혔다. 원래 finished_at를 사용할 때 null을 처리하도록 최소 수정하고 같은 저장 형태로 재검증했다. 이전 연구 묶음/캠페인 소스는 마지막175건 이후 바뀌지 않았음을 파일별 대조했다. 전수 회귀·commit/push/CI는 하지 않았다.

보존 실패 응답5건은 새 추론0으로 연구 재생에 통과했다.04:09 KST 실제 GET에서 직전 run `2026-10-17:0596a2b479d44ea7b8ce046a7575b08c`의 사실6개·출처20개·원래 수치/참조가 보존되고 후속 context.available=true/reason=ok를 확인했다. 원 status=failed/error/history는 그대로이며 result_recovered_from_raw_response로 읽기 결과임을 표시한다. health/KST/인증/613분봉·2일봉/WebSocket ready/pong도 정상이다. 운영 DB 쓰기/복사·추가 유료 호출·DB 컨테이너/런타임/의존성/DB 스키마/권한 변경은 없다.

원래 합의한 통합 확인 경로는 KST 변환, 정상 시각 분봉 원장 재생, 기존 실제 뉴스 추론/저장, 실제07:55 기동/예약, 최종 NAS 기동 및 보존 실제 AI 응답의 연구 공급까지 확인했다. 최종 build에서 새 유료 추론이나 새07:55 시황을 발생시킨 것은 아니다. 장전 자료의 미정 자동 연구 캠페인/매매 연결 규칙을 새로 만들지는 않았다. 이전 non-JSON 뉴스 오류의 내부 원인 미확정은 별도 기록으로 유지하며 현재 자료 사용을 막는 조건으로 추가하지 않는다. 근거는 `data/diagnostics/nas-cumulative-deployment-20261016/integrated-after-kst-20261017/research-input-retention-{closeout,final-acceptance,final-v2-live}.json`이다. 아래 진행 중/미완료 표현은 이 수정 전 단계 기록이다.

## 2026-10-17 연구 자료 사용 제한 정정 구현

사용자는 미확인·불완전·상충 자료도 연구에 사용하도록 앱 표시 밖의 저장·연구 입력·후속 전달까지 수정하도록 요청했다. 장전 처리에서 미확인 출처/게시·관측 시각/모델 분류 때문에 값·참조를 제외하거나 전체 실패시키는 조건을 제거하고 unknowns로 함께 전달한다. 확인된 지표 수량으로 context를 막지 않는다. 기존 실패 raw_response는 읽기 시점에 연구 result로 제공하며 status/error/history는 쓰지 않는다. 공급자도 완료된 응답을 출처 목록 부재 때문에 버리지 않고 일부 검색 실패 상태를 함께 전달한다. 연구 분할의 시각 미확인 테마와 일부 테마 이력을 가진 묶음도 보존한다. 시각을 임의 생성하거나 과거 비교에 소급 적용하지 않는다.

보존 실패 응답5건 모두 추가 추론0으로 연구 재생에 통과했고 원래 수치·출처·참조를 유지했다. 최초 로컬 관련111건의 화면2건 실패는 전체 출처 표시와 모델 표기 구분으로 수정해 관련204건이 통과했다. 이후 공급자 차단을 추가 발견해 그 수정과 관련 검사를 진행 중이다. 첫 NAS 작업 `a3a144be152505c79786da90775936d7`는 worker 검사123건 중 ModuleNotFoundError2건이 있었고 정리는 완료됐다. 패키지 없이 fixture를 import하는 두 검사와 연계 fixture의 경로를 PC의 같은 제한 PYTHONPATH에서 재현해 패키지 import로 수정했다. 실패는 보존했고 필요한 검사를 생략하거나 의존성을 설치하지 않았다. 최종 NAS 검사·배포 결과는 완료 후 기록한다. 기존 운영 release는 이 단계에서 유지한다.

## 2026-10-17 미확인 출처 분리 배포 완료·실제 추가1회 실패

사용자가 미확인 분리와 추가 실제 ChatGPT 구독 호출1회를 승인했다. 모델 unverified + 웹 출처 부재 두 조건을 만족하는 근거와 그 참조만 unknowns로 분리하며 원문 주장·확정 사실/수치·시각/참조 검증을 유지한다. PC 관련113건(화면9건 포함)이 통과했고 실제 이전 실패 응답 재생에서 두 출처만 분리·확정/완료 사실4개·미확인 수치6개 유지가 확인됐다. 원래 run은 실패 그대로 보존한다. NAS 관련72건은 실패/오류/skip0·정리/후검사를 통과했다(job `22f4a60d1c3d683edf8d5637a885f97f`). 동일 후보 `2026.10.17-brief-unknown-separation-v1-d74d7488e41aafef` 배포와02:39 KST health/인증/KST/613분봉·2일봉/WebSocket ready/pong을 확인했다. DB 컨테이너·런타임·설정/스키마는 유지했다. 승인된 추가1회 run `2026-10-17:0596a2b479d44ea7b8ce046a7575b08c`는422.288초·웹 동작14개·출처228개·입력134643/출력16477/총151120token 뒤 `brief_evidence_source_unverified`로 실패했다. 이번 불일치는 `ev_webull_wti` (`https://www.webull.com/quote/nymex-CLX6`) 하나이며 모델이 original_read로 주장했고 WTI에 참조했지만 실제 도구 출처에 없었다. 승인된 unverified 분리 대상이 아니므로 기존 거절 조건을 유지했다. 이전 run/raw_response·설정(revision0/07:55 enabled)·실패 응답/사용량은 보존했다. 추가 재요청은 하지 않았다. **미확인 분리 구현·배포는 완료됐지만 실제 장전 시황 수락과 전체 통합 수락은 미완료**다. 상세 근거는 `unknown-separation-{progress,actual-source-diagnosis}.json`와 `unknown-separation-brief-acceptance.json`이다. 아래 사용자 확인 대기 표현은 이전 단계다.

2026-10-15 · main HEAD `93883dff76171398d858e9c9f5c3cfcf7fd115d2` · 사용자 `하자` 요청에 따른 검증

## 2026-10-17 기존 저장 자료 KST 완료 후 통합 수락 재개

현재 최종 상태: PC 관련101건과 NAS Linux/SSE/장전/뉴스 관련69건이 오류/실패/skip0·정리/후검사를 통과했다(job `fb4682b474a93e79690deee9d56e5702`). `2026.10.17-brief-web-evidence-v1-dfcb5c32623f3638`로 정상 배포했고 DB 컨테이너·의존성·스키마·승인값은 보존했다.02:04 KST health/KST/인증/기존613분봉·2일봉/WebSocket ready/pong을 확인했다. 전수 회귀·commit/push/CI는 실행하지 않았다.

사용자가 승인한 장전1회 `2026-10-17:337085879f094e49bdedd2d59123a4a2`는02:04:33 KST cutoff,518.291초·웹 동작14개(search11/open_page3)·출처248개·입력141241/출력20106/총161347token 뒤 `brief_evidence_source_unverified`로 실패했다. 자동 재요청/설정 변경은 없고 원 run과 raw_response·web_actions·설정 전후를 보존했다. 이번에는 KRX 상세 경로와 CNBC US10Y 주소가 웹 도구 기록에 없었고 모델도 verification=unverified·게시 시각 unknown으로 표시했으나 evidence에 포함했다. 이 실패를 기존 열람 URL 누락의 재발이나 원문을 읽었다는 주장으로 해석하지 않는다.

진단 복사본에서 두 미확인 evidence와 그 참조만 제외하고 unknowns로 남기면 기존 strict validator가 통과한다. 다른 내용과 가격/지수·시각은 바꾸지 않았다. 결과에는 확정/완료 사실4개·미확인 사실1개가 있으며, 국내지수/선물/환율/금리/유가 등 미확인 수치6개는 unavailable로 유지된다. 이 미리보기는 `brief-partial-preview.json`이며 코드/운영/실패 run에는 미적용이다. 현재의 전체 실패 처리를 이런 미확인 분리로 바꿀지와 추가 실제1회 검증을 사용자에게 확인한다. 임의 조건 완화 또는 반복 재추론으로 통과 처리하지 않는다. **장전 수락을 포함한 전체 통합 수락은 아직 미완료다.**

기존10월16일07:55 실제 예약과 정확히 일치하는 장전 사용량 원장은1건, request_count=1이다. provider 측 응답 완료와 시황 검증 실패를 구분했다. `scheduled-run-usage-evidence.json` 참조. 아래 실행 중/게시 대기 표현은 각 시점의 기록이다.

후속 고정 읽기 `integrated-acceptance-20261017-v1-readback`은0B 완료봉 revision254건·SQL 오류0·잘림0·post-fence를 통과했다. 조회 시각의 최신 투영본 대신 해당 시각 이전 available_at/received_at의 원 revision을 골라 운영과 바이트가 같은 `market_research_features`로 재생했다. 15:00/15:01/15:02의 비교 가능 종목은18/19/20이며15:02는 COMPLETE/REPLACED·후보009150이다. 5분 확인은 UNAVAILABLE로 보존한다. `native-revision-{candidates,summary}.json`이 근거다. 당시 뉴스 컨테이너 로그의 marker는0개였으며 과거 non-JSON 원인은 확정하지 않았다.

사용자가 저장 기사1건의 Gemini 전송과 현재 장전 프롬프트/관심 목록의 ChatGPT 구독 검색1회를 명시 승인했다. 승인한 실제 기사 revision `342d734dc17242b198aaea6c32ff1072`는2026-09-04 기사이며 당일 뉴스 범위 수락으로 확대하지 않는다. Gemini 새 호출1회는18.045초·HTTP200·cache_hit=false·본문741자 fulltext·입력995/출력416/총1411token으로 완료했다. 응답과 저장 output, 실제 본문 hash, article/body/analysis revision이 일치한다. 분석 revision은 `14d071542f884e5f844ac223f6479355`다. 자동 재요청·공급자/설정 변경은 없고 `fresh-news-acceptance.json`에 보존한다. 최초 실행은 명시적 Gemini 외부 전송 승인이 없다는 자동 검토 거절로 실행되지 않았으며, 사용자 승인 뒤 정확히 준비한 동일 기사/요청 자료를1회 실행했다.

공식 [웹 검색 출력 문서](https://developers.openai.com/api/docs/guides/tools-web-search)의 search/open_page/find_in_page와 현재 수집 코드를 대조했다. 기존 코드는 sources/citation만 읽어 완료된 open_page의 URL을 버리는 것을 기존 배포 함수로 재현했다. 이 누락만 보완하고 검색어·인증값 없이 동작 종류와 반환 URL을 기존 run에 보존한다. 두 공급자의 전달·SSE·예약 실패 후 저장·기존 cache/usage 경계를 포함한 PC 관련101건이 통과했다. 과거07:55 raw action은 없으므로 해당 누락이 그 실패의 원인이었다고 확정하지 않는다. URL 임의 디코딩/정규화·검증 완화·이전 실패 run 변경은 없다.

첫 NAS 후보 `2026.10.17-brief-web-evidence-v1-9e1a59a74e6a02bf`의85건은 Windows 전용 DPAPI store의 `msvcrt` import 때문에32오류였다(job `a6ba1f74201cb67e8dfb40068e59fd80`). 원 보고서와 실패를 보존하고 통과로 처리하지 않는다. 제품 의존성을 추가하거나 Windows lock을 Linux로 위장하지 않았다. 휴대 가능한 실제 SSE fixture를 기존 장전 검사에 추가한 후보 `2026.10.17-brief-web-evidence-v1-dfcb5c32623f3638`에서 장전·API 공급자·NAS 서비스·실제 Linux store를 다시 검사한다. 제품 소스는 첫 후보와 같고 기반은 현재 누적v2의 불변1133파일이다. 런타임/스키마/승인값은 그대로이며 관련 코드3파일·build marker·테스트3파일만 다르다.

새 근거 디렉터리는 `data/diagnostics/nas-cumulative-deployment-20261016/integrated-after-kst-20261017/`이며 이전 영수증을 덮어쓰지 않았다. 실제01:37 KST API에서 누적v2·health 정상·+09:00·WAITING_MARKET, 기존 뉴스 분석 revision과07:55 설정/실제 예약 이력을 확인했다. 일반 뉴스의 원래 본문 revision/해시·현재 캐시·공급자/모델이 일치함을 먼저 GET으로 확인하고 저장 본문을 명시한 캐시 요청1회만 보냈다. HTTP200·cache_hit=true·저장된 output과 동일·토큰0·사용량 원장 전후 불변이다. 이는 현재 캐시 응답 확인이며 원래 wire 요청 재현 또는 과거 non-JSON 원인 확정은 아니다. 공개 회전 로그에서 해당 시각 오류 marker를 찾지 못했으며, 오류가 없었다고 판단하지 않는다.

10월16일15:00:05/15:01:05/15:02:05 KST의 실제 TOP20 snapshot과 최신 분봉 투영본을 읽어 현재 코드로 재생했다(GET23회, 새 키움 요청/저장0). 결과는 모두 UNKNOWN/PARTIAL이다. 예를 들어 삼성전자14:57~15:00 자료는21:08에 조회한 `kiwoom-ka10080;trade_value=ohlcv_estimate`가 최신이며 당시 장중 원장을 뜻하지 않는다. 이를 시각 오류 또는 보완 분봉의 가격 오류라고 추정하지 않고, 보존된0B 완료봉 revision을 읽어 당시 후보를 확인한다. 고정22종목·12분 키, SQL3초·전체180초의 읽기 전용 묶음을 게시했다. 운영·원본·helper·스키마 변경과 DB 복사는 없고 실제 관리자 실행은 대기다.

10월16일 실제07:55 예약의 raw_response를 같은 validator로 재생해 `brief_evidence_source_unverified`를 재현했다. AP slug는 기존 ID 정규화로 처리되며, 남는 차이는 웹 도구가 반환한 `https://www.bloomberglinea.com/quote/USDKRW%3ACUR/`와 결과의 `https://www.bloomberglinea.com/quote/USDKRW:CUR/`다. 이 URL 한 값만 바꾼 진단 복사본은 나머지 검증 전체를 통과한다. 현재 외부 열람에서도 두 주소는 같은 publisher 페이지로 이동했지만, 이를 당시 tool action 보존이나 원 run 수락으로 대체하지 않는다. 과거 raw action은 미보존이며 기존 출처 검증·원 run·코드·프롬프트·예약 설정을 수정하지 않았다. `scheduled-brief-replay.json`과 `scheduled-brief-url-comparison.json`을 참조한다.

PC28,813셀 및 NAS15,013셀의 기존 UTC 표현 변환을 완료했다. NAS22개 대상의 전체 후검사에서 변환 대상 UTC 잔여0·원본 보존·정리·운영 후검사를 확인했다. 제품 활성 release는 누적v2이며 이 자료 변환에서 제품 재배포·운영 서버 재시작은 없다. 아래v3/v5 후보 배포 기록은 과거 단계다.

다음 실제 수락은 정상 시각 분봉 후보 변화·일반 뉴스 HTTP 응답/저장 일치·장전 시황 결과다. 기존 실제07:55 예약 기동과 실패 결과 저장은 확인했고 출처 검증 실패를 성공으로 바꾸지 않는다. KST 완료만으로 전체 통합 수락을 완료 처리하지 않는다. 이전 영수증을 보존한 새 읽기 근거로 이어간다.

## 이전 후속 — 시황 생성 근거와 KST 표시 v5 후보

기존 실제 실패 응답을 같은 validator로 재생해 FRED 두 근거의 게시시각=null/precision=datetime 모순을 확인했다. 이 두 항목만 바꾼 진단 복사본도 다음 URL 검증에 거절됐다. 원래 실패 run과 raw 응답은 수정하지 않았다. 기존 validator 조건을 생성 schema와 고정 프롬프트에 맞추고 세 가지 출처/게시시각 실패에 안전한 코드와 한국어 설명을 더했다. 근거 조건 완화·시각 합성·자동 재요청·새 저장 owner는 없다. 관련 로컬89건/5worker는 오류·실패·skip0, 프로세스 종료·실행 전후 동일 LF 지문으로 통과했다(`evidence-fix-checks/run.json`).

KST 표시 후속과 함께 새 비활성 후보 `2026.10.15-integrated-market-news-v5-6fafc127b0b95b1f`를 1,126파일로 고정했다. manifest SHA256 `c2594adac535d103e4969ce1d73e7d1274bbabdd0bcc35d64f5abab88d913f02`, archive SHA256 `6aced247d240625e866891df7b7b4a7d4fc8e1cb52b97222786d04abd9b808bd`다. checkout과 제품 차이는 고유 build marker이며, NAS에서 파일별 hash를 확인해 비활성 경로에 게시했다. 새 후보의 NAS selected/storage·배포·새 실제 검색은 각 실행 결과로 구분하며 기존 v3 성공 근거를 재사용하지 않는다. 기존 runtime은 유지한다.

후속 실행에서 설치된 새 runtime `sha256:f57806e98598f2ab96794c0847118eee6ef5131014cd4a82c7c50226b3cb7f46`으로 v5 selected **175건**(job `d794ec057a0b1b65415db7a0b568bc7b`), storage **114건**(job `e5d3f8d15fa3c15bcb5b41ebbb1000fa`)이 실패·오류·skip0으로 통과했다. 각 보고서의 source hash/cleanup/post-fence/운영 컨테이너 불변을 검증한 뒤 고정 operator로 v5를 활성화했다. 배포 state=complete, 운영 DB 컨테이너 보존, 새 build/source path·health 정상과 diagnostics idle을 확인했다. 재시작 구간의 realtime_loss_verified=false를 그대로 보존하며 무손실이라고 주장하지 않는다. 영수증은 `v5-evidence/{selected-report,storage-report,deployment,after-deployment}.json`이다.

새 실제 검색 run `2026-10-15:6283628ad6b54a27b86ad6c9ebb4f93e`를 한 번 요청했다. 이 실행의 결과·엄격 검증·설정 보존은 완료 후 별도로 기록하며 실패 시 자동 재요청/공급자 변경은 없다. 기존 run을 덮어쓰지 않았다. commit/push·새 외부 CI·운영 DB 과거 보정은 수행하지 않았다.

이 run은 KST15:07:26 cutoff, 검색14회·367.544초 뒤 `brief_evidence_source_unverified`로 **실패**했다. input133,924/output15,923/total149,847token이며 저장 결과와 오프라인 재생의 안전 코드가 같다. 저장된 출처239개와 evidence17개 중 불일치는 `ev_fx_attempt`의 `http://www.smbs.biz` 하나다. 모델은 이를 unverified/게시시각 unknown으로 표시하고 원달러를 unavailable로 남겼으나, 실패한 접근 주소를 evidence에 포함해 기존 출처 계약에 거절됐다. 게시시각/정밀도 쌍 모순은 이번 응답에서0개였다. raw tool action은 보존되지 않아 누락된 접근 메타데이터와 모델 출력의 차이를 완전히 추적한 것은 아니다.

진단용 복사본에서 그 evidence와 참조만 제거해도 다음 `확정 사실의 원문 검증이 없습니다.`에 거절됐다. 복사본을 실제 결과로 수락하거나 저장하지 않았다. 편집/예약/모델 등 영속 시황 설정은 runtime의 running/diagnostics를 제외한 전 필드가 같고, 일반 운영 설정도 전 필드가 같다. 이전 실패 run도 history에 보존됐다. 실제 시황 수락은 미완료이며 반복 프롬프트 수정이나 추가 추론 요청 대신 검색·열람 원시 action의 안전한 관측과 사실별 verified 근거 참조를 확인해야 한다. 상세는 `v5-evidence/{brief-acceptance,source-mismatch-analysis}.json`이다.

별도 DB 시각 감사 v2는 세 쿼리 모두 실패해 영향 건수 미확정이며 보정 없이 fence를 통과했다. v3 진단은 빈 매개변수 오류를 수정하고 큰 집계를 재실행하지 않는다. EXPLAIN(ANALYZE 없음), 카탈로그, 최대12개 PK 분봉만 읽는다. [감사 범위와 남은 증거](time-basis-kst.md#운영-db-읽기-감사의-실패와-후속-범위). 아래 내용은 각 이전 단계 기록이다.

기준은 [main 통합 기록](main-integration-20261015.md)이다. 이전 뉴스 전용 설치 후보를 운영 소스로 선택하지 않고, 시장 관찰·aware KST 시계·뉴스 구독·장전 v4가 함께 있는 전체 코드 후보를 고정했다. 이번 작업에서 기존 제품 판단·저장·주문 로직을 변경하지 않았다. 테스트 실행 구성과 정적 DB 호출 감사 원장의 누락을 보완한다.

## 운영 경계와 후보

- 활성 운영 릴리즈는 `2026.10.14-recorder-shared-10m-v1-af0f8072f960415b`다. 읽기 전용 operator status에서 health 정상, trace/capture OFF, diagnostics idle을 확인했다.
- 새 최종 비활성 후보는 `2026.10.15-integrated-market-news-v3-543cbeddae623b53`이며 code/test/script 1,125파일이다. source 마운트 모드의 빌드 식별자만 후보에서 올렸다. 현재 checkout의 제품 코드와 의미 차이는 이 빌드명 한 줄뿐이다. 마지막 공백 검사에서 시장 테스트 파일 끝의 빈 줄 하나를 로컬에서 제거했으며 후보와의 이 추가 byte 차이는 AST 동일성과 정확한 말미 개행 차이로 별도 기록했다. NAS의 고정 후보·검사 결과를 다시 쓰지 않았다.
- pyproject·schema·migration의 실제 파일 hash는 기존 NAS 계약과 같고, Dockerfile 핵심 계약도 기존 runtime을 유지한다. google-auth 2.41.1은 별도 additive runtime 검증 대상이며 기존 기본 이미지의 검증 완료로 간주하지 않는다.
- 게시한 경로는 새 artifacts ZIP·새 비활성 source release·새 관리자 설치 묶음이다. active 포인터·운영 DB·환경 파일·보호 인증 저장소·예약·수집 제어는 변경하지 않았다. commit/push·외부 CI는 실행하지 않았다.
- 읽기 전용 `git ls-remote origin refs/heads/main`의 원격 main은 `941347e30ce9ff55603fced66e90fd75de094672`다. 로컬 main HEAD `93883df…` 및 미커밋 통합 후보와 다르다. 원격의 이전 CI를 현재 통합본의 CI 통과로 재사용하지 않는다.

## 실패 기록과 보완

원래 실패를 통과로 덮어쓰지 않는다. 정확한 JSON·로그·시작/종료 코드 지문은 `data/diagnostics/nas-integrated-validation-20261015`에 보존한다.

1. sandbox의 전체 회귀는 첫 비동기 테스트에서 멈췄다. `asyncio.run(asyncio.sleep(0))`의 별도 최소 재현도 socket.accept → fallback_socketpair → asyncio self-pipe에서 멈췄다. 이번 runner와 그 소유 자식만 종료하고, 같은 기존 runner를 사용자 환경에서 실행했다. sandbox 실행은 incomplete로 보존한다.
2. v1 NAS selected 107건은 import/패키지 오류 3건으로 실패했다. 테스트 간 짧은 import와 코드 allowlist 밖 문서 JSON 의존을 확인했다. 정식 `tests.unit` import를 사용하고, 문서와 바이트가 같은 schema fixture를 tests에 포함했다. 개발 checkout에서는 문서와 fixture의 동일성도 계속 검사한다.
3. v1 storage 114건은 가짜 서비스의 시계와 가짜 후보의 구현 hash 누락으로 실패했다. v2에서 드러난 가짜 명세의 미체결 정책 필드도 실제 constructor·checkpoint 경로를 읽어 보완했다. 거래 판단·전략·주문·writer를 수정하거나 검사 assertion을 완화하지 않았다.
4. v2의 일반 HTTP 테스트 모듈 로딩은 RuntimeError로 끝났다. 그 모듈만 분리한 같은 후보의 197건은 실행됐고 연구 helper의 전이 import 1건만 남았다. 해당 경로는 로컬에서 NAS와 같은 import 경계로 재현했다. 실제 시계 검사를 HTTP 클라이언트가 필요 없는 시장 테스트 모듈로 옮기고 assertion과 앱→0B→RAM/SQLite 검증을 유지했다. `httpx2`를 설치하거나 FastAPI/Starlette를 바꾸지 않는다. 뉴스 인증·잘못된 요청 검사는 기존 ASGI 경로로 실행한다.
5. Windows 전수에서는 새 8개 테스트 모듈의 등록 수 기대값과 장전 DB 호출 감사 원장이 이전 상태였다. 실제 등록 317/137/2와 기존 native store의 새 호출 10개·전달 1개를 대조했다. 새 장전 객체는 create_app의 같은 QueryStore를 받으며 기존 load/upsert의 개별 transaction과 claim-before-inference, 취소 drain을 사용한다. QueryStore/SQLite/PostgreSQL 메서드 서명은 바뀌지 않았다. 보존한 이전 감사 원장과 대조해 삭제 0·서명 변경 0을 확인했다.

## 최종 검사 상태

최종 v3 NAS selected **198/198**, storage **114/114**가 실패·오류·skip 0으로 통과했다. job은 각각 `cd142c28a2e587aa96fe5140cf860f90`, `e7ac64f8c89e94ca94fd4f1232bee8bf`다. 두 작업 모두 cleanup/post-job fence/active release 및 운영 컨테이너 불변을 확인했다. 기존 runtime image `sha256:6a4bc1e915d2c525ca5c2e2b3027afe750159c5a4e7e3b2a7df22bb771e1206b`의 Linux/mock·격리 PG 검사이며, google-auth 추가 이미지나 실제 OAuth/추론·장중 정확도 수락은 아니다. worker 4GiB/CPU0,1·PG CPU0·network 격리를 확인했고 이 NAS의 pids 제한 미지원을 보존했다.

최종 v3 Windows 뉴스 API **106건/9 worker**도 실패·오류·skip 0·동일 지문으로 통과했다. DB 감사 회귀 **4건**이 통과했다. 코드 전용 NAS 패키지에는 Windows lockfile이 없어 후보에서 runner 자체 검사를 포함한 보조 141건 실행이 1오류로 끝났으며, 해당 실패는 보존한다. v2의 Windows 뉴스 API 106건/시장 95건 통과는 이전 후보 결과로 구분한다. 로딩 실패·미실행·skip을 통과 수로 바꾸지 않는다.

Windows 고정 소스의 전체 회귀는 **5,321건/313 worker를 모두 실행**했다. 실패는 등록 수 기대값과 DB 감사 원장의 **2건**, 오류·skip·미실행은 **0건**이다. 모든 프로세스 트리 종료와 전수 실행 전후 LF 지문 동일성을 확인했다. 이 원래 all-local의 status는 `failed`이며 통과로 고치지 않았다.

전수 종료와 원본 테스트 hash를 확인한 뒤 테스트 8파일의 검토한 수정 및 schema fixture를 현재 checkout에 반영했다. 제품 src는 추가 수정하지 않았다. 원래 checkout에서 보완 관련 **145건**, 정식 뉴스 API **106건/9 worker**가 오류·실패·skip0·native exit0·각 실행 전후 동일 지문으로 통과했다. catalog unit461/integration27도 통과했다. 시계 테스트의 기존 실행/검증 본문 AST와 이동 후 본문은 동일하다. 공백 수정 역시 AST 동일성을 확인했다.

보완 후 전수 전체를 다시 실행한 것은 아니다. 원래 전수 실패 2건과 소스, 보완 후 관련 검사 및 테스트 형식 차이를 분리해 `all-local-user/run.json`, `post-correction-regression/run.json`, `post-correction-news-api/run.json`, `local-validation-receipt.json`, `final-formatting-receipt.json`에 보존한다. 최종 후보 1,125파일·ZIP hash·구문·checkout 대응 검증은 `v3-final/candidate-verification.json`을 따른다.

최종 읽기 확인에서 NAS health 정상, active release/기존 runtime image 유지, `extensions=null`을 확인했다. 관리자 추가 runtime은 아직 설치되지 않았고 실제 계정·뉴스 추론·새 source·정상 시각 운영/07:55 예약 수락도 남는다. 원격 main과 현재 후보의 CI 상태는 별도다.

## 관리자 실행 경계

`sudo -n id -u`는 `a password is required`로 거절됐다. 파일 샌드박스의 실행 승인은 NAS 관리자 인증을 대신하지 않는다. 아래는 최종 후보에 고정된 별도 묶음이며, 암호를 채팅이나 파일에 저장하지 않고 사용자의 SSH 터미널에서 입력한다.

```powershell
ssh -t k379@192.168.0.5 "sudo sh /volume1/docker/kiwoom-monitor/artifacts/integrated-market-news-runtime-20261015-v3/install.sh"
```

설치 묶음 descriptor SHA256은 `b7d37e0d71fb7c0309290bf260adb2b19d92a80c14e18197ee1d35a7deba4933`, install.sh SHA256은 `6d85805c9c1237ad84e0152f000f34f3c0b165fcedae99c8b58acf8669f374a6`다. 기존 helper가 fence의 lock_held 인수를 지원하는 것을 실제 설치 파일의 정의로 확인했다.

이 설치는 기존 이미지 layer를 유지하고 google-auth만 추가한 뒤 최종 source의 selected/storage gate를 새 runtime에서 실행한다. 통과 후에만 기존 구성·mount·network·환경을 보존한 server 교체를 수행하고 원래 container와 사설 설정/runtime/회수 원장 백업을 남긴다. 운영 PG와 active source는 유지한다. server 정상 종료·재시작 구간에는 수집 공백이 생길 수 있으며 HTTP ready를 무손실 증거로 확대하지 않는다. `--recover`는 runtime journal이 prepared/needs_admin인 교체 중단에만 사용하며, 그보다 앞선 검증 실패에는 적용하지 않는다.

## 관리자 설치 실패와 네트워크 증거 수집

사용자가 위 명령을 실행했고 installer/descriptor checksum OK 뒤 `RuntimeError: replacement_network_changed`로 종료됐다. 코드의 실제 순서는 격리 selected/storage → 생성 상태 교체 컨테이너 inspect/비교 → 비교 실패 시 정지 상태 확인·해당 ID 삭제 → (비교 통과한 경우에만) 백업/journal 작성·운영 서버 중지다. 따라서 이 오류는 운영 서버 교체 전에 발생했다. 후속 operator status는 기존 10/14 릴리즈·정상 health·진단 idle·helper/journal complete를 확인했다.

실패 직전 새 runtime `sha256:f57806e98598f2ab96794c0847118eee6ef5131014cd4a82c7c50226b3cb7f46`의 storage job `fe0811d28d500135baab1380926a574b` 보고서를 조회했다. **114건**, 실패/오류/skip0, cleanup_complete/post_job_fence_verified/active_release_and_containers_unchanged 모두 true다. selected가 먼저 통과해야 이 지점에 도달하지만 해당 새 이미지 selected의 job ID/건수는 아직 별도 조회하지 않았으며 기존 이미지의 selected198 결과와 합산하지 않는다.

실제 원본/생성 컨테이너의 네트워크 이름·NetworkID 값은 최초 오류에 남지 않았다. 생성 전후 Docker 필드의 수명 차이와 실제 연결 대상 변경을 구분할 증거가 없으므로 원인을 단정하거나 비교 조건을 완화하지 않았다. 일반 SSH 사용자는 Docker socket 읽기 권한이 없음을 실제 확인했다. 관리자 인증이 필요한 한정 진단 묶음을 새 경로에 게시했다:

```powershell
ssh -t k379@192.168.0.5 "sudo sh /volume1/docker/kiwoom-monitor/artifacts/integrated-market-news-network-probe-20261015-v1/diagnose.sh"
```

이 진단은 기존 v3 installer의 SHA256을 검증하고 같은 생성 함수를 재사용한다. 동일 private operator/control lock과 전후 fence 아래에서 새 이미지의 임시 컨테이너 하나만 **시작하지 않고** 생성·inspect·삭제한다. 운영 서버 stop/start/rename, active/runtime/config 변경, DB 접근, 이미지 빌드·재검사는 실행하지 않는다. 컨테이너 환경변수·인증정보는 출력하지 않고 네트워크 이름/ID/alias/IPAM·NetworkMode·Docker 버전·비교 결과·삭제/fence 확인만 출력한다. 생성한 정확한 ID만 제거하며 예상과 달리 실행 중이면 삭제·중지를 거부한다. 기존 동명 컨테이너 충돌 시 생성 실패로 종료하며 기존 객체는 제거하지 않는다.

로컬 진단 검증 4건은 mismatch 원문 보존, 비교 통과/예외 시 생성 ID 삭제, 실행 중 객체 삭제·중지 거부, 운영 start/stop 호출 부재를 확인했다. NAS에서 셸 구문과 업로드 byte hash를 재확인했다. `probe.py` SHA256 `5c302ade01a8211606b2a5dfc7b87a58f489d0c248649f8a6fe983ac1aa48b5d`, `diagnose.sh` SHA256 `f19c22546ae9d9a3ceccdb4406d94d57472cbfb557ffa0dfb8c6bb1206bfb040`. 자료는 `data/diagnostics/nas-integrated-validation-20261015/network-failure/`에 보존한다. **NAS 진단 실행과 원인 확정·최소 수정·실제 배포는 아직 남는다.** 설치를 다시 실행하거나 `--recover`를 실행하지 않았다. 모델 변경은 없다.

## 네트워크 수명 원인 확정과 v4 설치 도구

사용자가 위 진단을 실행한 결과 Docker24.0.2/API1.43에서 probe는 `created`, Running=false이며 NetworkID/EndpointID/IPAddress가 빈 문자열이었다. 반면 NetworkMode와 네트워크 이름 `kiwoom-monitor_default`, 원래 alias3개, IPAMConfig/Links는 같았다. 원본 NetworkID와 별도 `docker network inspect`로 조회한 ID는 모두 `2b7e0e5bced323d116885579a4210b85d4c092733de367c68b6452d744acd352`였다. probe 미시작·정확한 ID 삭제·전후 fence도 확인됐다. 이 증거로 **아직 시작하지 않은 객체의 런타임 연결 필드를 이미 연결된 객체와 비교한 수명 단계 오류**를 확정했다. 실제 네트워크 대상 변경은 관찰되지 않았다.

`scripts/install_nas_chatgpt_runtime.py`의 생성 단계 검사는 `created`/미실행 상태, 네트워크 이름 집합, 독립적으로 조회한 원본 네트워크 ID 일치, 기존 Config/HostConfig/mount/IPAM/Links/별칭 보존을 요구한다. 이 조건 아래에서만 빈 NetworkID를 허용한다. 시작한 다음에는 실제 비어 있지 않은 원본 NetworkID를 엄격히 검사하며, fingerprint/config 승인 전에 실행한다. Docker가 생성하는 새 컨테이너 자신의 short-ID alias만 추가 허용하고 원래 별칭 삭제나 무관한 별칭 추가는 거부한다. 실패는 기존 rollback 경로로 전달한다. 따라서 실제 연결 검사를 생략하거나 네트워크 이름만으로 연결 완료를 승인하지 않는다.

실제 진단의 네트워크 필드를 기존 v3 검사에 재생해 동일 실패를 재현했고, 수정본의 생성 단계 통과와 실행 상태에서 빈/다른 ID 거부를 확인했다. Config/mount는 인증정보 없는 동일 placeholder이며 원래 live 비교가 이미 그 보존을 통과한 것과 구분한다. 자료는 `network-failure/reproduction.json`이다. 관련 Windows 회귀 **81건**(transfer/operator/source-runtime)은 실패·오류·skip0/native exit0·전 프로세스 종료·실행 전후 LF 지문 동일로 통과했다(`network-failure/regression/run.json`). 동일 테스트에서 네트워크/보존 관련 6개를 AST로 추출해 실제 NAS Python 표준 라이브러리만으로 실행했고 **6건 통과**했다. 6건은 81건의 부분집합이며 합산하지 않는다. catalog unit461/integration27도 통과했다. 전체 all-local의 과거 2실패와 이후 관련 보완 결과를 이번 제한 회귀로 덮어쓰지 않는다.

수정된 호스트 설치 도구만 새 immutable v4 묶음에 게시했다. 기존 v3 묶음과 앱 후보 `2026.10.15-integrated-market-news-v3-543cbeddae623b53`는 그대로 유지한다. 이 후보 내부의 과거 installer/test 파일을 현재 수정본이라고 표시하지 않으며, 새 host bundle은 별도 hash·회귀 근거를 갖는다. 앱 제품 src/DB schema/collector/주문 경로는 바꾸지 않았다. v4는 기존 설치 순서의 image/base 검사와 동일 selected/storage gate를 유지하고 최종 서버 교체의 네트워크 승인 단계만 보완한다.

```powershell
ssh -t k379@192.168.0.5 "sudo sh /volume1/docker/kiwoom-monitor/artifacts/integrated-market-news-runtime-20261015-v4/install.sh"
```

NAS에서 셸 구문 및 파일 hash를 재확인했다. descriptor SHA256 `3c5808a21203846c1ebe5efef6308c5dd4e6a23f0f927715acbb18ae7d25ec07`, installer `fa0e6f0f48071248b974cb36477dba27840dede6c6c391a454bb0273435da39e`, shell `1875ffb0e6d6b5ed3c26b2df673f971ee6d12419ee29a845f746c0279b038334`다. 상세 provenance는 `network-failure/admin-bundle-v4/preparation.json`이다. 관리자 인증 때문에 실제 v4 설치는 아직 실행하지 않았다. 운영 status는 기존10/14 release/health정상/diagnostics idle/helper·journal complete다. **실제 새 서버 시작 후 네트워크 검사와 배포 수락은 아직 미검증**이며, synthetic 시작 상태 검사를 실제 연결 증거로 사용하지 않는다. 서버 교체 시 정상 종료/재시작 사이 수집 공백 가능성이 있고 DB/active source는 보존한다. 모델 변경은 없다.

## v4 실제 설치와 통합 소스 활성화

사용자가 v4를 실행해 installer/descriptor/network tests checksum 및 독립6건 통과 후 `state=complete`를 반환했다. 새 서버 ID는 `9397fd73ee0fe2ebc9fb5727412cc2e8beea018df49755a962af584cf8e3712c`, image는 `sha256:f57806e98598f2ab96794c0847118eee6ef5131014cd4a82c7c50226b3cb7f46`, HTTP ready는31.933초였다. active source를 바꾸지 않고 PG 컨테이너를 보존했다. 원래 서버는 rollback용으로 유지했다. installer의 실제 시작 후 엄격 네트워크 검사와 outer fence가 통과한 상태이며 무손실 수락은 false다.

후속 실제 `kiwoom-nas status`/runtime descriptor와 새 storage report `c7f6fdd82b61be138d0e5a416f667407`를 확인했다. storage114건 실패/오류/skip0, cleanup/post-job fence/운영 불변이며 동일 image/source hash다. 같은 후보에 대한 operator의 exact-source gate를 사용해 `deploy 2026.10.15-integrated-market-news-v3-543cbeddae623b53`를 실행했다. complete/PG 보존이며 health=ok, build=`2026.10.15-integrated-market-news-v3`, source path=`/app/source-runtime/releases/2026.10.15-integrated-market-news-v3-543cbeddae623b53/src`, trace OFF/idle을 실제 확인했다. `.env`/PG/server-data/server-secrets/예약을 덮어쓰지 않았다. 이 활성화도 정상 종료·재시작을 수반했으며 무손실은 검증하지 않았다. 근거는 `live-acceptance/before-status.json`, `runtime-storage-report.json`, `runtime-summary.json`, `deployment.json`, `after-status.json`이다.

NAS/PC 모두 미연결 상태임을 확인한 뒤 고정 SSH prepare와 NAS host ID를 사용하는 로컬 OAuth를 시작했다. 사용자가 브라우저에서 동의했고 importer의 `imported=true`/`pc_session_retired=true`와 NAS connected/permitted 계정1개를 확인했다. 토큰·암호·이메일은 로그/문서에 남기지 않았다. 실제 구독 models GET은7모델을 반환했고 gpt-6.1-sol이 포함됐다. 이후 refresh는 NAS 소유다. 사용자는 일반 뉴스 공급자를 앱에서 설정할 수 있음을 확인했으므로 운영 Gemini 설정은 바꾸지 않았다.

### 실제 뉴스와 시황의 수락 범위

일반 뉴스는 기존 Gemini/gemini-3.5-flash-lite 그대로, 저장된 2026-09-04 삼성전자 기사1건으로 기존 `/api/v1/news/analyze`를 한 번 호출했다. 클라이언트는 non-JSON HTTP 오류 응답을 받았고 최초 진단 parser가 상태코드를 보존하지 못했다. 이 실패를 숨기지 않았고 같은 POST를 재시도하지 않았다. 이후 새 AI revision `1c2edd8ba17e49f79935ce62bbc9b01a`가 저장된 것을 확인했다. 기사 ID·본문 revision·`analysis_body_hash`와 저장 input_hash가 일치하며 요청 후 computed_at, 실제 usage(input701/output464/total1165)를 확인했다. 사용 본문은 summary_only238자이고 fulltext라고 승격하지 않았다. **분석/저장 lineage는 확인됐지만 HTTP 응답과 readback의 일치 및 non-JSON 응답 원인은 미확정**이다. 프록시 timeout/실행 대기를 원인으로 단정하지 않았다. `news-ai-acceptance.json` 및 `news-body-used.json`에 범위를 남긴다. 오늘 전체 기사 수집·자동 처리 수락으로 확대하지 않는다.

ChatGPT 장전 시황의 수동 생성1회는 run `2026-10-15:cd0fee16e548477ba60376afbe529f9d`로 실행됐다. 실제 web search13회, input131185/output15410 tokens가 기록됐으나314.021초 뒤 엄격 출력 검증에서 failed다. 저장 raw_response와 원래 sources/cutoff를 기존 validator에 오프라인 재생해 **ValueError: 원문 게시 시각이 없습니다.**를 재현했다. `market_brief_failed`를 연결 실패로 오인하지 않으며, 실제 구독 추론/검색과 검증 통과를 구분한다. 근거 조건 완화/유료 fallback/재생성은 하지 않았다. 결과는 `brief-current.json`, `brief-offline-validation.json`에 보존했다. 실제07:55 예약의 claim/완료/재시작 중복 방지 수락은 여전히 남는다.

### 배포 후 시장 시각과 후보

KST14:27:38의 실제 TOP20 20종목·KRX 저장자료21 GET에서1분 비교20/20 COMPLETE/NO_CANDIDATE를 확인했다. 배포 전9시간 미래 가용행을 원본 그대로 제외했고5분 비교는 배포 후 이력 부족으로 UNAVAILABLE이었다. KST14:35:24의 후속 실제 소속은402340 진입으로19/20 PARTIAL/UNKNOWN이며 해당 종목의 previous minute history/closed actual 부족을 거절했다. 최근12분252행 중 실제 완료211행의 가용 지연은 분 종료2.195~3.156초, 미래 가용행0이다. 1분/5분 모두 코호트 전체 요구를 낮추지 않았다. 봉의 effective_at·수신/가용시각·분 키를 분리했고 과거 필드를 수정하지 않았다. 저장 GET은 추가 키움 TR/수집 요청을 하지 않는다.

KST14:44:15 최종 표본은 실제 TOP20 전원20/20의1분 비교가 COMPLETE/REPLACED(현재 후보009150)였다. 최근5분의 지속 평가는 NOT_CONFIRMED(3/5분 필요, 해당 후보2/5분)이다. 그 앞5분과의10분 비교는176750의 이전 이력 부족 때문에19/20 PARTIAL/UNKNOWN이며, 최근5분 지속 계산 가능과 별개다. 처음 summary에서 지속 NOT_CONFIRMED를 보고5분 비교까지 COMPLETE라고 표현한 보고는 전체 `timeframes.5m.quality` 대조로 정정했다. 최근12분257행 중 실제 완료237행, 가용 지연2.195~3.405초, 미래행0이다. 이전 partial 표본은 `market-captures/20261015T053524Z/`에 보존하고 최종 원본·계산·hash는 `market-summary.json`, `market-minute-rows.json`, `market-candidates.json`에 둔다.

이 자료는 최신 projection의 관측이며 immutable revision Replay·전체 시장 주도주 정답·수익성/OOS 승인이 아니다. 새 회원 진입 등으로 자료가 부분이면 UNKNOWN을 유지한다. 후속 PC KST 표시 수정은 이 NAS 고정 후보보다 나중 로컬 작업이고 아직 NAS release에 포함되지 않았다([시간 기준](time-basis-kst.md)). 로컬 branch/HEAD 및 원격 main/CI와 배포 source identity는 계속 분리하며 commit/push는 수행하지 않았다.

## 설치 뒤 실제 수락

새 runtime 식별·gate 확인 → 최종 source 선택과 health/실제 source path·DB container 보존 확인 → 기존 SSH importer로 구독 세션 연결과 PC 토큰 소유권 정리 → 실제 저장 뉴스의 소량 분석·기존 writer readback → 정상 시각의 완료 분봉과 실제 TOP20 소속으로 1분 후보/5분 지속·결측 사유 확인 순서다.

실제 07:55 예약은 다음 해당 시각의 claim·단일 요청·완료 저장·재시작 중복 방지 근거가 필요하다. 수동 생성이나 시계 합성 테스트만으로 실제 예약 완료로 표시하지 않는다. 과거 9시간 오류 기록·불완전 녹화를 보정하거나 정확도 수락으로 승격하지 않는다. 장전 JSON의 native C1 소비는 기존 미연결 범위로 남는다.

구독 세션의 NAS host ID 유지·SSH 이전·NAS 단독 refresh 소유권은 [OpenAI 공식 self-hosted VM 절차](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms)와 대조했다. 실제 OAuth·모델 조회·추론 결과는 설치 뒤 별도 검증 대상이다.

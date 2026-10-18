# 런타임 인증 R5b — DART 키 교체

2026-09-15. 로컬 구현/가짜 API 검증. NAS 소스 동기화·배포 전.
최신 누적 build: `2026.09.15-runtime-credentials-r5-dart-rotation-v1`.

## 목적과 변경 대상

NAS 재시작 없이 DART 키 교체·비활성화와 keyless 설치의 첫 활성화를 지원한다.
기존 `news_credentials.py`에 DART owner를 추가하고 `news_service.py`, `app.py`,
`infrastructure/dart_disclosures.py`와 기존 Runtime 검증 실패 코드 허용 목록만 보완했다.
서버/Compose/Dockerfile의 누적 build를 같은 버전으로 갱신했다.
새 manager/service/wrapper·DB table/migration·키움 TR 경로는 없다.
원문/요약/분류 품질, 뉴스 주기, 순위 우선순위와 실전·모의 한도도 변경하지 않았다.

## 계약과 실제 수명

- provider/profile: `dart/nas-dart-default` 한 개. 키 없어도 DB metadata를 등록하며 임의 profile은 미지원이다.
- prepare: request_id/expected_revision/replacement `{api_key}` 또는 disable+빈 객체. 계좌 대상은 null이다.
- 검증: 당일 공시 검색 `list.json`, page_no=1/page_count=1. 회사코드를 지정하지 않아 회사코드 캐시를 읽거나 쓰지 않는다.
- 정상 status 000+list 또는 013 조회 없음은 VERIFIED다. 키 오류 010/011/901은 INVALID_CREDENTIAL,
  한도/점검 020/800/900·통신 timeout·HTTP 429/5xx는 CREDENTIAL_VALIDATION_RETRYABLE,
  그 밖의 접근 제한/잘못된 응답은 CREDENTIAL_VALIDATION_FAILED로 실패한다.
- 실패에는 원래 query URL/키/공급자 메시지를 담지 않는다. 재시도 가능 실패도 자동 저장/적용하지 않는다.
- disable은 검증 HTTP 없이 tombstone을 저장하고 기사·cache·운영 토글은 유지한다.

응답 코드와 공시 검색 인자는 [금융감독원 공식 공시검색 가이드](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001)를 확인했다.
통신/HTTP 오류의 재시도 가능 분류는 앱의 구현 정책이다. 실제 API 검증을 실행한 것은 아니다.

종목 수집 task는 접수 시 NAVER/DART client와 당시 DART 사용 여부를 고정한다.
느린 NAVER 호출 뒤에도 같은 DART client를 쓰며 운영 토글 변경은 다음 접수부터 적용한다.
DART pause는 새 종목 수집을 막고 기존 task의 실제 HTTP thread/최종 DB 저장을 끝까지 기다린다.
요청자가 취소됐어도 기존 owned task는 진행·정리되고 늦은 이전 결과/실패가 새 인증 revision을 덮지 않는다.
pause 중 신규 종목 조회는 저장 목록만 읽고 fresh 완료 시각을 기록하지 않는다.
NAVER query-set는 DART pause에 종속되지 않는다. NAVER/DART flag를 각각 관리하고 둘 다
풀릴 때만 새 종목 task를 만든다. 동시 교체 시 한쪽 resume가 다른 쪽 pause를 해제하지 않는다.

deadline 초과는 BUSY 후 실제 종료 뒤 commit 없이 FAILED/기존 연결 재개다.
commit 시작 시 이전 DART client를 제거한다. vault 쓰기 결과가 불확실하거나 공개가 실패하면
DART None/RECOVERY_REQUIRED/활성 revision 없음으로 유지하고 이전 키를 복원하지 않는다.
gate만 DART 부재 상태로 재개해 NAVER·캐시·후속 독립 작업이 계속 돌아가게 한다.
명시적 다음 prepare/apply로 복구하며 정상 서버 시작은 기존 vault 복구 계약을 따른다.

## 보존과 완료 기준

새 DART client는 같은 cache Path를 사용한다. 검증/키 교체는 파일 내용·mtime을 바꾸거나
삭제하지 않는다. 실제 공시 조회에서 캐시가 30일 지나 갱신되는 기존 정책은 유지한다.
기존 기사·종목 news_sync·source cursor/next schedule·BODY/RULE/AI 작업·NAVER 사용량을 초기화하지 않는다.
서비스·collector·job runner도 재생성하지 않는다. 검증 요청으로 유료 AI 분석/회사코드 전체 다운로드를 하지 않는다.
`dart_enabled`는 별도 운영 설정이다. 키를 적용/비활성화해도 이를 자동 켜거나 끄지 않는다.
OFF 설치는 키 적용 후에도 공시 수집 OFF, ON keyless 설치는 키 적용 후 정상 공시 수집이 가능하다.
disable ACTIVE는 비활성화 적용 완료이며 startup tombstone은 예전 env 키보다 우선한다.

## 검증

신규 `test_dart_credential_owner.py`: owner 12개·검증 client 4개·HTTPS route 2개, 총 18개.
가짜 DART/NAVER와 임시 SQLite/vault/cache만 사용하며 실제 API·키·사용자 DB·NAS 접속·주문은 없다.
확인: 캐시 내용/mtime·기사/sync/cursor·NAVER 예산 보존, 새 키 공시 조회, 취소된 요청 실제 drain,
느린 NAVER 뒤 DART snapshot, provider 동시 교체와 독립 pause, query-set 지속,
인증/한도/점검/HTTP/timeout/잘못된 JSON 분류와 비밀 제거, stale revision/후보 취소,
disable+env 부활 방지+운영 ON 보존, drain deadline, commit/공개 실패와 명시 복구,
취소된 검증 probe 실제 종료와 늦은 활성화 차단, 서버 종료 시 DART 최종 저장 drain,
keyless ON/OFF 설치의 기본 프로필 적용/공시 수집을 검증한다.
미연결 provider 회귀 fixture는 DART에서 아직 미연결인 Gemini로 옮겼다.
해당 API fixture는 별도 가짜 OpenAI hook을 등록하므로 그 공급자를 미연결 대상으로 쓰지 않는다.
관련 회귀 334개: 333개 통과, Windows POSIX 권한 검사 1개 생략, 종료 코드 0.
최종 DART 18개 단독 회귀도 모두 통과했다. 로그는 `tmp/r5b-regression.log`, `tmp/r5b-final-focused.log`다.
최초 전체 실행에서 미지원 검사 대상이 fixture의 fake OpenAI hook과 충돌했으며 Gemini로 바로잡은 뒤
전체 회귀를 재실행했다. 제품/테스트 미해결 실패 없음.
Python 문법 7개 파일·관련 diff/신규 파일 공백 검사 통과, 서버/Compose/Dockerfile build 세 곳 일치.
실제 NAS TLS/PostgreSQL/Linux·DART 서버 응답을 검증한 것은 아니다.

## 다음 한 단계

R5c AI 공급자 인증 snapshot/키 교체다. 논리 요청의 provider/model/revision을 고정하고
기존 성공 분석 캐시·대기 작업·사용량을 보존하며 저장 검증 때문에 유료 분석을 자동 실행하지 않는다.
일반 공급자 PC 입력 UI·조건식/해외시세 운영 확대, R6 실전 owner, R7 누적 배포/실환경은 남았다.
현재는 서버 API만 연결했고 PC 관리 화면의 DART 입력 완료를 뜻하지 않는다.
NAS 소스 동기화는 하지 않았으며 R7까지 중간 재빌드를 요청하지 않는다.
실제 DART key/IP/응답·NAS TLS/PostgreSQL/Linux·장중 검증은 미완료다.
모델 에스컬레이션 없음.

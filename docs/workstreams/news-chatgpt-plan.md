# 뉴스 AI ChatGPT 구독 연결 — Windows 검증 경계

갱신: 2026-10-15 KST. 작업 소유: ed9d 뉴스 인증·요청·설정.
기준 HEAD: `50a70c9ce6cdf20915fde27f2d1f47becb82eadf`. 시작 시 작업 트리 깨끗함.
현재 변경은 로컬 미커밋이다. 원본 main·다른 워크트리·NAS·운영 DB·운영 자격증명에 쓰지 않았다.

## 1. 왜 필요한가

기존 OpenAI/Gemini/Claude API 키에 더해 `chatgpt_plan`을 선택해 뉴스 분석을 시험하기 위한 변경이다. 공식 문서의 로컬 앱 구독 경로를 사용했고, 사용자 승인 뒤 이 PC의 별도 프로필에서 실제 로그인·권한·모델·소량 추론·뉴스 저장을 확인했다. 다른 계정·워크스페이스, 긴 원문·지속 처리량과 NAS는 검증하지 않았다. 이번 결과로 로컬 LLM의 완전 대체를 판단하지 않는다.

## 2. 뉴스·실제 매매에서 달라지는 것

Windows 직접 뉴스 분석에서 ChatGPT 연결, 활성 계정·워크스페이스 등록 선택, 계정별 모델 조회를 사용할 수 있다. 성공 결과는 기존 요약·종목별 영향·테마 후보와 요청 토큰 기록으로 돌아온다. 중간 스트림·불완전 JSON·권한 부족·사용 한도는 완료 결과가 아니다.

실시간 매매·Collector·시장·타점·EV·Position·Risk·주문 경로에는 변경이 없다. 뉴스 AI 오류가 매매 중단이나 유료 API 전환을 만들지 않는다. 기존 로컬 LLM·유료 공급자를 삭제하지 않았다. NAS UI는 구독 실행을 제공하지 않으며 CentralAIClient는 구독 분석을 네트워크 전송 전에 거절한다.

## 3. 기존 판단·저장·설정과의 연결

기존 호출은 뉴스창 → AINewsWorker → analyze_articles → 공급자 요청 → 공통 JSON parser → 기존 완료 신호 → NewsAIRepository다. NAS 호출은 CentralAIClient → CentralAIService의 별도 경로다.

- 기존 OpenAI도 Responses API를 사용한다. 기존 키 요청·재시도와 OpenAI/Gemini/Claude 모델 목록은 그대로다. 구독은 별도 인증과 SSE 요청을 사용한다.
- `target-company-theme-v3`, `_prompt`/`_batch_prompt`, AINewsAnalysis/AIRequestUsage, company_impacts/theme_candidates, 기존 writer/schema/뉴스 큐를 재사용한다. 구독 JSON의 필수 항목과 묶음 ID/개수를 저장 전에 검사한다.
- 구독은 DEFAULT_MODELS에 추가하지 않았다. 이 사전은 NAS API 키·credential owner 초기화에서도 사용한다. 계정 catalog의 `models`/`visibility=list`/`display_name`/`slug` 순서를 사용하고 요청 직전에도 허용 모델을 확인한다.
- 인증의 active/client_id/subject/revision은 보호 기록에만 존재한다. NAS 유료 credential revision과 합치지 않는다. 로컬 뉴스 DB의 provider/model/body_hash는 분석 이력이며 현재 OAuth 계정 승인 증거가 아니다. 계정별 새로운 DB 캐시나 인증 테이블을 만들지 않았다.
- 계정 전환·해제는 같은 요청 소유권 잠금 뒤에 실행되고 모델 목록을 다시 조회한다. 다른 계정의 토큰과 client ID를 섞지 않는다.
- 일반 뉴스 설정에는 기존 provider/model 등 공개 선택값만 저장한다. runtime 전용 `chatgpt_storage_path`는 직렬화하지 않는다. 구독 선택 중 기존 유료 API 키를 지우지 않는다.
- SettingsBackupService의 허용 공급자에 `chatgpt_plan`만 추가했다. v4 파일 형식/키·뉴스 schema는 그대로다. 설정·뉴스 AI 백업의 구독 선택값 복원과 비밀 제외를 검사했다.
- Google Drive는 기존 설정·뉴스 AI 백업 경로를 사용한다. NAS app_settings는 별도 일반 settings DB를 공유하고, NAS 뉴스 공급자는 기존 운영 설정이 소유한다. OAuth 파일은 이 경로에 포함되지 않는다. 실제 Drive 업로드·NAS 동기화는 실행하지 않았다.

요청·parser·writer를 이해하는 기존 호출 깊이는 그대로다. 구독 분기에서 필요한 인증 소유권과 스트림 수명만 2개 모듈로 추가했다. 일반 Manager/Controller/새 writer는 추가하지 않았다.

## 4. 인증·요청 책임

`chatgpt_auth.py`:

- 설치별 안정적인 UUID host ID, 첫 dynamic 등록과 issued client ID 구분. invalid_grant 뒤에는 미검증 pending 등록 ID만 따로 보존하고 활성 계정으로 쓰지 않는다.
- IPv4 loopback listener를 먼저 열고 매번 state/nonce/PKCE S256을 생성한다. exact redirect URI와 issued ID로 교환한다. callback state·중복 query·client mismatch·거절·timeout·취소를 검사한다. listener/연결 읽기는 시간 제한을 둔다. callback 로그·에러 traceback에 query를 쓰지 않고 응답은 URL query를 제거한다.
- 공식 JWKS의 RS256 키와 유지보수되는 google-auth 검증기로 서명·issuer·audience·nonce·iat/expiry·subject를 확인한다. 다른 알고리즘은 안전하게 거절한다. 현재 PC의 google-auth/cryptography를 사용하며 새 의존성을 설치하지 않았다.
- direct scope와 로그인 상태를 구분한다. identity-only grant도 보존하되 추론을 금지한다. 재인증 identity/client mismatch는 기존 계정을 교체하지 않는다.
- 기존 Windows DPAPI 함수만 재사용한다. 일반 뉴스 설정 파일이나 NAS API key vault를 OAuth 저장소로 재사용하지 않는다. 보호 기록은 atomic replace하고 토큰/만료/scopes를 함께 교체한다. 기록 객체 repr와 UI status에 토큰을 표시하지 않는다.
- 프로세스 간 Windows 파일 잠금 + 스레드 잠금이 refresh와 추론·전환·해제를 직렬화한다. refresh 전 영속 pending 표식을 저장하고 성공 시 replacement token과 함께 해제한다. 모호한 연결 실패·교체 실패·중단 뒤에는 재사용을 차단한다. terminal refresh error는 토큰을 지우고 등록/host mapping은 남긴다. 확인된 일시 HTTP 오류는 인증을 보존한다.
- 공식 discovery의 revocation endpoint로 해제한다. 원격 해제를 확인하지 못하면 로컬 토큰을 지우고 미확인 상태를 표시한다. 성공을 꾸미지 않는다.

`chatgpt_plan.py`:

- 공개 `/v1/models`, `/v1/responses`와 OAuth Bearer만 사용한다. 쿠키·Codex 인증 파일·backend-api를 읽거나 사용하지 않는다. credential 요청 redirect는 거절한다.
- Responses body는 model, input 배열, store:false, stream:true만 사용한다. unsupported 필드·HTTP previous_response_id·system item·유료 fallback이 없다.
- SSE는 terminal completed/status/output/usage를 확인한다. delta만으로 완료하지 않는다. failed/incomplete/HTTP 401·429/중단/잘린 JSON/빈 출력/취소를 구분한다. 암호를 포함할 수 있는 원문 메시지는 UI에 전달하지 않고 allowlist code·status·body shape·검증한 request ID를 보존한다.
- 자동 재요청이 없다. 모델 조회/권한 실패는 추론 요청으로 집계하지 않고, 실제 POST 실패·완료 후 JSON 실패는 기존 실패 요청 기록 경계로 전달한다.

설정창은 기존 SettingsRequestWorker로 보호 저장·OAuth·catalog를 실행한다. UI는 로그인 취소, 부분 권한, 모델 실패, 활성 계정, 사용량 링크를 표시한다. 구독 분석 실패 뒤에는 뉴스창의 자동 요청을 보류하고 대기 기사는 남긴다. 이후 도착한 기사도 자동 재요청하지 않는다. 직접 분석·명시 큐 재개·자동 분석 켜기·설정 저장은 사용자 재개 동작이다. 이 보류는 현재 뉴스창 수명의 상태이며 재시작을 넘는 구독 reset 추정으로 사용하지 않는다. 유료 공급자의 기존 다음 기사 처리 정책은 유지한다. 취소는 협조적이며 네트워크 읽기는 최대 30초 timeout 경계에서 종료된다. 24시간 실행과 전체 앱 종료 수락을 이 검사로 대신하지 않는다.

기본 보호 경로는 LOCALAPPDATA/KiwoomMonitor/ChatGPTPlan/<config 경로 hash>/credentials.dpapi다. 검증 전용 경로는 아래의 별도 ChatGPTPlanValidation이며 운영 프로필과 다르다.

## 5. 현재 검증 범위

첫 완료 경계인 **Windows 구현·모의 응답 검증·격리 로그인 준비**를 마쳤고, 이후 사용자가 실제 로그인·소량 요청을 승인해 아래 검증까지 진행했다.

| 단계 | 결과 |
|---|---|
| 기준 HEAD/실행 import | ed9d의 50a70c9, 자신의 src import 확인 |
| 실제 모델 설정 | 현재 turn_context의 gpt-6.1-sol / high 확인. 모델 변경 없음 |
| 인접 회귀 | 12개 module, 117 tests OK, 종료 코드 0 |
| 마지막 repr 보호 보완 후 | OAuth·SSE·설정 35 tests OK, 종료 코드 0 |
| 마지막 자동 큐 보완 후 | OAuth·설정·뉴스창·큐·기존 AI 68 tests OK, 종료 코드 0 |
| 실제 응답 호환 보완 후 | 같은 12개 module, 125 tests OK, 종료 코드 0 |
| 변경 실행 파일 compile | 11개 성공 |
| 로그인 준비 preflight | Windows·검증 의존성·격리 경로 확인. 인증 읽기/브라우저/외부 요청 0 |
| 실제 OAuth 승인·모델 catalog | 승인 후 통과. direct scope·보호 저장·계정 catalog 7개 확인 |
| 실제 Responses·뉴스 저장 | 짧은 요청1·뉴스1·뉴스2건 묶음 성공, 격리 writer/readback 통과 |
| 품질·처리량 | 공개 발표의 짧은 요약 입력 2사례만 정성 대조. 지속 처리량·일반 정확도 미검증 |
| NAS 적합성·운영 연결·배포 | 미실행·미승인 |

모의 검사는 실행 중 생성한 RSA 키와 가짜 토큰, 실제 DPAPI 격리 파일, 로컬 loopback callback, mock HTTP/SSE, 기존 writer의 임시 SQLite를 사용했다. state/client/identity/signature/nonce/expiry 오류, partial grant, invalid_grant·pending 등록, timeout/취소, refresh thread·process 경합, 회전 저장 실패·재시작 fence, catalog 형식, terminal/usage/빈 출력/중단, HTTP 오류 비밀 제외, 작은 묶음, 백업·복원과 기존 공급자/UI/동기화를 검사했다.

초기 제한 계정 실행은 sandbox 임시 경로 WinError 5 때문에 기존 검사까지 실패했다. 이 실행을 통과로 집계하지 않았다. 지침에 따라 사용자 세션의 checkout 밖 임시 경로에서 새 검사 실행을 수행했다. 그때 발견한 시험 함수 인자 누락, 잠금 바이트의 선행 읽기, 이미 파괴된 Qt 객체의 시험 접근을 구분해 수정했다. 환경 접근 거부가 제품 코드 수정으로 해결됐다고 보고하지 않는다.

검사 TEMP/TMP/TMPDIR: `C:/Users/pc-1/AppData/Local/Temp/kiwoom-news-chatgpt-ed9d-20261015-user`.
근거: `data/diagnostics/news-chatgpt-plan/focused-tests-final.log`, `protected-record-final.log`, `queue-final.log`, `preflight.json`, `verification.json`.
초기 신규 검사는 37개였으며 실제 응답의 audience/헤더/완료 항목 처리와 비밀 제외 진단 검사를 추가해 현재 43개다. 현재 고유 관련 검사는 125개이며 최종 변경에서 같은 12개 module을 모두 실행했다. 실행 합계와 고유 검사 수를 혼동하지 않는다. 최종 큐 시험의 중복 Qt 정리 오류는 시험 코드에서 수정했고 native 종료 코드까지 확인했다.

## 6. 실제 로그인·소량 검증 준비

`scripts/validate_news_chatgpt_plan.py`는 자신의 src를 먼저 사용한다. 원본 test-app launcher/운영 data/.env/계좌/NAS를 사용하지 않는다.

- profile: `news-ed9d-01`.
- OAuth: 사용자 LOCALAPPDATA/KiwoomMonitor/ChatGPTPlanValidation/news-ed9d-01/credentials.dpapi.
- DB·공개 검증 기록: 이 워크트리 data/diagnostics/chatgpt-plan-validation/news-ed9d-01/news.sqlite3.
- preflight → login → models → probe → news → batch의 명시 단계다. 각 단계는 자동으로 다음 추론을 시작하지 않는다. model은 catalog에서 선택한 slug를 명시한다.
- probe는 짧은 요청 1회다. news는 1기사, batch는 같은 종목의 2~3기사와 기존 JSON/parser/writer/readback을 사용한다. 입력 JSON은 stock_code/stock_name/articles이며 각 기사는 title/body/url과 선택 published_at을 가진다.
- 설정창을 쓰려면 `--stage settings`로 기존 설정창을 별도 config에 연다. 시작만으로 로그인하지 않고 사용자 버튼과 브라우저 권한 승인이 필요하다.
- 보고서는 토큰/code/nonce/원문 인증 응답을 기록하지 않는다. 승인 전에는 preflight만 실행했고, 사용자 `응` 승인 뒤 login/models/probe/news/batch를 실행했다. 실제 settings UI/disconnect는 실행하지 않았다.

**이 승인 범위 완료**: 사용자 직접 로그인·권한 승인을 받았고, 짧은 요청1건→뉴스1건→묶음2건의 성공 검증을 마쳤다. NAS 운영 이전·설치·배포나 더 큰 부하는 승인에 포함되지 않는다.

## 6-1. 실제 검증 결과 — 2026-10-15 06:58~07:13 KST

현재 검증 프로필에 실제 토큰은 Windows DPAPI로 보존되어 있다. 토큰/code/nonce·계정 식별값·이메일을 증거 파일에 넣지 않았다. 원래 운영 profile/DB에는 쓰지 않았다.

실제 응답으로 확인한 세 호환 문제를 분리해서 재현·수정했다.

1. JWT의 `aud`가 배열이었다. google-auth의 scalar audience 비교가 이를 거절했다. 서명·시간 검증 뒤 string/array의 issued client ID를 확인하고, 여러 audience 또는 azp가 있는 경우 authorized party도 정확히 검사한다. 잘못된 audience·nonce·서명·만료는 계속 거절한다. 초기 2개 실패 뒤 동일 pending issued ID로 로그인·direct 권한·보호 저장이 성공했다.
2. 이 환경의 실제 HTTP 200 Responses 응답은 Content-Type이 없었지만 본문은 SSE였다. 명시적인 다른 content type은 계속 거절하고, 헤더가 없을 때에도 SSE JSON/type/terminal/status/output/usage 검사 전체를 수행한다. 헤더 없이 일반 JSON이나 delta만 보내는 응답은 실패다.
3. 실제 `response.completed`는 완료 상태와 usage를 전달했지만 `output`이 비어 있었고 앞선 `response.output_item.done`이 완결한 메시지를 전달했다. 공식 완료 항목 계약을 사용해 added/done의 item ID·순서·누락·created/final response ID를 검사한 뒤 최종 응답에 조립한다. delta로 조립하지 않는다. 최종 실패·중단·불완전 메시지·ID 불일치는 저장하지 않는다.

진단은 고정된 실패 범주, 이벤트 건수와 필드 존재 여부만 기록했다. raw JWT/응답 본문을 증거로 저장하지 않았다. 진단 계측의 한 들여쓰기 오류는 실제 요청 전에 compile에서 발견해 수정하고 회귀를 실행했다.

| 실제 성공 단계 | 사용 모델 | 전체 단계 시간 | 입력/출력/합계 토큰 | 저장 |
|---|---|---:|---:|---|
| 짧은 OK 요청 | gpt-6.1-sol | 3.059초 | 11/5/16 | 검증 기록 |
| 뉴스1건 | gpt-6.1-sol | 15.993초 | 799/696/1495 | 기존 격리 writer 1건·readback |
| 뉴스2건 묶음 | gpt-6.1-sol | 28.827초 | 947/1359/2306 | 기존 격리 writer 2건·readback |

시간은 모델 catalog 확인·전체 stream 처리·해당 저장 대조를 포함한다. 첫 토큰 지연이나 지속 처리량 측정이 아니다.

**실제 추론 POST는 총8회**다. 호환 진단 중 짧은 요청5회는 클라이언트에서 완료 결과로 거절했고, 최종 짧은 요청1·뉴스1·묶음1의3회가 성공했다. 자동 retry나 유료 fallback은 없다. 성공3회 사용량 합계는 입력1757/출력2060/합계3817토큰이다. 거절한 진단5회의 사용량은 미계측이므로 전체 사용량 합계로 주장하지 않는다.

뉴스 DB에는 뉴스 성공 요청2회가 기록되어 입력1746/출력2055/합계3801토큰이다. 단건과 묶음에 같은 실적 발표가 들어 있으므로 실제 writer upsert 후 고유 뉴스 결과는2개다. 단건 결과는 single-output.json, 최종 묶음은 batch-output.json으로 별도 남겼다.

입력은 다음 공식 발표를 짧게 한국어로 요약한 **검증용 발췌**다. 긴 전체 원문 처리 성능으로 일반화하지 않는다. 원문이 날짜만 제공하므로 정확한 published_at을 만들지 않았다.

- [삼성전자 2025년 1분기 실적](https://news.samsung.com/global/samsung-electronics-announces-first-quarter-2025-results): 최대 매출과 반도체 수익 부담을 혼재로 판정했고, 당시 하반기 계획을 확정 성과와 구분했다. 대상 삼성전자 company_impacts와 테마 후보가 기존 저장 경로에 기록됐다.
- [하만의 ZF ADAS 사업 인수 계약](https://news.samsung.com/global/harman-to-acquire-zfs-adas-business): 삼성전자 자회사의 전장 확대 가능성과 규제 승인·종결 미확정, 매출/이익 근거 부재를 구분했다. 거래 완료로 바꾸지 않았다.

수치·회사 연결·과거/계획 구분은 이 입력 범위에서 정성 대조했다. confidence의 일반 정확도, 테마 후보의 안정성, 다른 공급자/로컬 LLM 대비 우위, 전략 성과는 검증하지 않았다. 실제 저장은 기존 schema/writer만 사용했고 뉴스/판단 엔진 전체나 자동매매 연결 완료를 뜻하지 않는다.

현재 증거: `data/diagnostics/chatgpt-plan-validation/news-ed9d-01/live-summary.json`, single-output.json, batch-output.json, 단계별 login/models/probe/news/batch JSON. 최종 회귀: `data/diagnostics/news-chatgpt-plan/live-compat-regression.log`.

## 7. 통합 담당에게 필요한 delta와 잔여

주요 파일: chatgpt_auth.py, chatgpt_plan.py, news_ai.py, naver_news.py, news_settings_dialog.py, news_workers.py/news_execution.py/stock_news_window.py의 뉴스 AI 경계, central_ai_client.py의 전송 거절, settings_backup.py의 공급자 허용값, 검증 entry/test 2개.

공통 파일은 동시에 수정하지 않았다. 다음 변경안을 통합 검토한다.

1. tests/regression_profiles.json의 기존 `dependency-audit-news-contracts` profile에 `tests.unit.test_chatgpt_plan`, `tests.unit.test_chatgpt_plan_settings`를 등록한다. 신규 검사는 Windows DPAPI/loopback/Qt를 사용하므로 Linux profile에는 그대로 넣지 않는다. all-local/CI catalog·lock/fingerprint는 통합 후 기존 runner 절차로 갱신하고 전체 검사를 한 번 수행한다.
2. MODULE_MAP의 AI 공급자 행에 인증/stream 2모듈과 Windows 책임을 추가한다. CURRENT_STATUS/OPEN_ITEMS에는 Windows mock/preflight 및 실제 OAuth/소량 추론/격리 저장 완료를 기록하고 일반 품질·지속 처리량·NAS 미검증을 별도로 기록한다. 로드맵의 로컬 LLM 대체 결정은 더 넓은 실제 입력 결과 뒤에 판단한다.
3. Windows 배포 runtime에 google-auth 및 cryptography가 실제 포함되는지 확인한다. 현재 interpreter는 google-auth 2.41.1 / cryptography 46.0.7 / PySide6 6.11.1이다. cryptography는 현재 pyproject에서 server extra에만 선언되어 있으므로 Windows 설치 계약을 통합 담당이 검토해야 한다. 누락 환경은 로그인 전 거절한다.
4. central_server/ai_service.py/ai_credentials.py/config.py는 읽기 감사만 했다. paid key owner/revision/dedup과 구독 active account/refresh owner는 서로 다른 경계다. 서버에 붙일 때 DEFAULT_MODELS/vault key owner를 구독 OAuth owner로 재사용하지 않는다. catalog·권한·selected registration·취소·usage 상태의 API delta는 실제 Windows 결과 뒤에 설계한다. 현재 `/api/v1`·DB migration delta는 없다.
5. 공유 news_ai.py/naver_news.py를 NAS release에 포함하는 통합 시 서버 dependency/build 범위를 재검토한다. NAS 동작 변경 시 app.py SERVER_BUILD, docker-compose.yml server.image, server.Dockerfile 검증 문자열의 build triple을 같은 변경에서 올린다. 이번 작업은 이 공통 파일과 운영 image/source release를 변경하지 않았다.
6. Windows 소량 검증 통과 뒤만 Synology runtime 적합성, 별도 host ID, secure transfer/backup 제외, 접근권한·단일 refresh owner를 검토한다. PC와 NAS가 같은 rotating session을 동시에 갱신하지 않는다. VM 문서는 Synology 지원 증명이 아니다. 운영 이전·설치·배포에는 별도 승인이 필요하다.

소량 통과 뒤 공식 VM 지침과 이 checkout의 Dockerfile/Compose/SSL 경계를 읽기 검토했다. VM 지침은 별도 안정 host ID, 브라우저가 실행되는 PC의 loopback OAuth, 보호된 session의 SSH 등 안전한 이전과 VM의 이후 단독 refresh를 설명한다. 전송 session의 host별 사용량 attribution/revocation은 현재 제공되지 않는다고 명시한다. PC의 활성 검증 session을 NAS로 복사해 동시에 갱신하는 방법은 사용하지 않는다.

현재 Synology source의 기본 runtime은 python:3.13-slim이며 프로젝트를 --no-deps로 설치한 뒤 서버 패키지를 명시적으로 설치한다. 현재 Dockerfile은 cryptography를 설치하지만 google-auth는 명시적으로 설치하지 않는다. 실제 배포 이미지의 패키지는 확인하지 않았다. Windows DPAPI 암호문과 msvcrt 잠금은 Linux에서 사용할 수 없고, 기존 NAS vault는 OAuth 저장 금지 계약을 가진다. 따라서 이번 Windows 모듈을 환경 변수만 바꿔 NAS에 켤 수 없다. 별도 Linux 보호 저장·접근권한/배포·일반 backup 제외와 단독 owner, 이미지 의존성을 먼저 검증해야 한다. 이는 소스/문서 예비 검토이며 실제 Synology 장치·이미지·네트워크·저장소 검증은 아니다. secret 이전·vault 변경·이미지 빌드·NAS 접속·배포는 수행하지 않았다.

전체 회귀/원격 CI·main 조회·commit/push/merge/NAS·24시간 처리량·장중 수락은 미실행이다. 별도 계정 프로필의 소량 검증은 완료했지만, 일반 뉴스 품질·지속 처리량·실사용 한도·전체 앱 운영 수락은 남아 있다. 사용자 주문 전략에서 선택 뉴스/LLM을 새 필수 조건으로 만들지 않았다.

## 공식 근거 — 2026-10-15 직접 열람

- [Overview](https://developers.openai.com/siwc/token-sharing-open-source)
- [Registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [Accounts and sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [Responses streaming completed output items](https://developers.openai.com/api/reference/resources/responses/streaming-events)
- [Token reference](https://developers.openai.com/siwc/token-sharing-open-source/token-reference)
- [Errors and recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery)
- [Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
- [Website identity verification](https://developers.openai.com/siwc/website)
- [UI/UX](https://developers.openai.com/siwc/ui-ux-guidelines)
- [Self-hosted VMs](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms)
- [Sign-in cookbook](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt)

구독·앱별 사용 한도와 계정/정책 적격성을 앱의 daily_limit과 혼동하지 않는다. 실제 계정의 모델이나 reset 시각을 문서 예제로 추정하지 않는다.

## 8. NAS 연결 준비 — 사용자 승인 2026-10-15

사용자 `나스에 붙이자` 요청으로 NAS 연결·배포 범위가 승인됐다. 앞 절의 NAS 미승인은 당시 범위를 뜻한다. 현재 실제 OAuth 이전·런타임 설치·운영 소스 선택은 아직 수행하지 않았다.

### 현재 확인한 운영 경계

- NAS: `X:\kiwoom-monitor`, SSH `k379@192.168.0.5`, 설치된 `/usr/local/bin/kiwoom-nas` 사용.
- 활성 운영 릴리즈: `2026.10.14-recorder-shared-10m-v1-af0f8072f960415b`, health=ok, trace/capture OFF, diagnostics idle.
- 현재 NAS 설정: Gemini / `gemini-3.5-flash-lite`, 일일 상한500, 설정 revision7. `capabilities.runtime_credentials_v1=true`도 직접 확인했다. 구독으로 바꾸지 않았다.
- 관리자 확인: `sudo -n id -u`는 `a password is required`로 거절됐다. 파일 샌드박스 승인이 NAS 관리자 인증을 대신하지 않는다. 무암호 기존 operator는 임의 패키지 설치·이미지 변경을 받지 않는다.
- 워크트리: detached HEAD `50a70c9ce6cdf20915fde27f2d1f47becb82eadf`, 변경은 미커밋이다. main/CI/push 상태를 현재 NAS 상태와 합치지 않는다.

### 추가한 구현

1. `chatgpt_linux_store.py`: 기존 API-key vault와 다른 `server-secrets/chatgpt-plan/session.enc` 및 독립 `session.key`. Fernet 암호화, leaf directory0700/file0600, euid 소유권·symlink/hardlink 검사, fsync/원자 교체, thread lock/flock 단독 소유권을 사용한다. DB·일반 설정 백업·Docker context에 OAuth를 넣지 않는다.
2. `central_server/chatgpt_sessions.py`: 인증된 status/models/select/disconnect/resume만 제공한다. HTTP 토큰 import는 없다. select/disconnect/resume은 수락된 구독 작업을 drain하고 변경을 소유한다. PC의 이전 토큰이나 paid vault key를 공유 owner로 사용하지 않는다.
3. `CentralAIService`: 기존 뉴스 parser/writer/revision/usage 경계를 재사용한다. imported-session generation으로 캐시를 구분하고 token refresh는 generation을 바꾸지 않는다. 구독 실패는 보호 저장소에도 pause_code를 남겨 재시작 뒤에도 자동 POST를 막는다. 실제 실패 POST의 사용량은 미계측으로 기록한다. 명시적 구독 요청을 NAS의 기존 유료 공급자로 바꾸지 않는다.
4. `news_jobs.py`: 구독 작업은 내부에서 제한된 transport 수명까지 완료를 기다린다. 기존90초 job timeout으로 shielded 요청이 살아 있는데 두 번째 POST가 생기지 않는다. 유료 공급자의 기존 timeout은 유지한다.
5. Windows 설정창: NAS의 계정·모델·중지 상태를 worker에서 조회하고 재개/해제를 지원한다. NAS 로그인은 PC의 OAuth와 SSH 이전 절차를 사용한다. 토큰을 설정 HTTP에 보내지 않는다.
6. `nas_chatgpt_session.py`, `move_chatgpt_session_to_nas.py`: NAS host ID를 먼저 보호 저장하고 SSH stdin으로 한 세션만 이전한다. 송신 전에 PC에 durable fence를 남기며, NAS의 암호화 재읽기 ACK 뒤만 PC 토큰을 지운다. 불확실 ACK는 자동 재전송하지 않는다. 원격 revoke로 NAS session을 무효화하지 않는다. 이후 `--login`은 별도 DPAPI NAS authorization profile에 NAS host ID와 같은 issued ID를 사용한다. 연결 해제/갱신 실패 뒤 같은 등록·subject만 다시 연결한다.

### 실제 운영 소스 위의 후보

`prepare_news_chatgpt_nas.py`는 활성 NAS 릴리즈의 파일별 원본 hash를 확인한 뒤 이 작업의 뉴스 delta만 적용했다. 개발 HEAD의 전체 앱을 배포본 위에 덮지 않는다. 배포본의 운영 설정 클라이언트가 RAM-selection API 추가 전이라 그 파일에는 구독 메서드만 별도로 통합했다. 다른 엔진·주문·계좌·DB schema 파일은 원본 hash를 유지한다.

최종 후보: **`2026.10.15-news-chatgpt-plan-v3-d8bb3d5479c48574`**,1085개 code/test/script 파일. source release의 core dependency/schema 계약은 배포본을 유지한다. 기본 이미지용 app/Compose/Dockerfile build triple은 v3으로 맞추고 google-auth2.41.1을 선언했다. 실제 NAS는 별도 additive runtime image 검증 후 소스 마운트 모드로 배포한다.

NAS에 게시한 것은 새 불변 검사 후보와 설치 묶음뿐이다. active.json/.env/운영 DB/server-secrets를 덮지 않았다.

### 검사·관리자 단계

- Windows 관련17모듈: **166건 실행,162통과, Linux 전용4건 skip**, native exit0. `nas-final-v3-windows-regression.log`.
- 별도 NAS 재로그인 seed 검사는 browser/network 없이 NAS host ID와 기존 issued ID를 확인했다.
- 첫 NAS 후보986849의 job`c98b788a4bf63204bb84f7c4e4b4905c`는11검사 중2오류로 실패했다. 설치 모듈 누락과 자식 프로세스의 후보 PYTHONPATH를 보완했고, lock은 호출자 오류를 저장소 오류로 바꾸지 않도록 수정했다. 실패를 통과로 합치지 않는다. cleanup=true, OOM=false, 운영 릴리즈·서버 health 보존을 확인했다.
- 최종 v3 Linux gate: job`bd71babccbbe5f4bc0707831cd9c2369`, **18/18 통과, errors/failures/skipped0**, memory4GiB/CPU0,1/network 격리. `cleanup_complete=true`, `post_job_fence_verified=true`, `active_release_and_containers_unchanged=true`. root report는 `data/diagnostics/news-chatgpt-plan/nas-final-v3/linux-gate-report.json`에 보존한다. 현재 이미지의 Linux/mock 경계 통과이며 google-auth 추가 이미지와 실제 NAS OAuth/추론 통과를 뜻하지 않는다.

관리자 설치 묶음: `/volume1/docker/kiwoom-monitor/artifacts/news-chatgpt-plan-ed9d-runtime-v1/install.sh`.

- descriptor SHA256 `197ba4156b15b06c5801562b4ddc9c2af1001c3823c0cfafe0ad41780787f691`.
- installer SHA256 `2d3b9178db8f880c5435cdf18065d53227642925f6f04551ee3ae6770c07c1a3`.
- bootstrap SHA256 `c4de6d70b014da6f78cf8b7473f2d04c8b51efa81538cc3d256bdba9f9306b1e`.

이 고정 설치는 승인된 기존 이미지의 layer를 보존하며 google-auth2.41.1만 추가하고, 설치된 worker로 exact candidate selected+storage gate를 먼저 실행한다. 통과 전에는 운영 서버를 멈추지 않는다. 이후 기존 Config/HostConfig/Env/모든 mount/network가 같은 server를 만들어 일치 검사 뒤 전환한다. 원래 container와 root-private 설정/runtime/회수 원장 백업을 보존하며 실패 시 복구한다. DB container와 active source는 바꾸지 않는다. 중단된 설치는 같은 스크립트의 `--recover`로 고정 journal 범위만 복구한다.

SSH 이전용 별도 root-owned entry는 `prepare/import` 두 인수만 받으며 같은 operator fence와 검증된 active app의 container-only importer를 사용한다. stdin은 제한하고 OAuth를 로그·HTTP로 보내지 않는다. 이 entry도 기존 operator access가 회수되면 fence에서 거절한다. 기존 권한 회수 원장의 config hash는 갱신하고 원래 복구 백업은 보존한다.

현재 관리자 설치는 **미실행**이다. 이 단계의 재시작은 정상 shutdown 뒤 같은 데이터/구성으로 server만 전환하며 수집 공백이 생길 수 있다. HTTP ready와 데이터 보존을 실시간 무손실·장중 수락으로 확대하지 않는다.

관리자 설치가 통과하면 다음 순서만 남는다: 새 런타임의 gate/identity 확인 → 기존 operator로 exact source 선택 → SSH 세션 이전/PC retire → NAS catalog/설정 대조 → 실제 저장 뉴스의 소량 분석/기존 writer readback. 기존 Gemini 설정과500회 상한, 구독 자동 큐의 활성화 범위는 실제 연결 단계에서 별도로 대조한다. 전체 뉴스 품질·장기 처리량·실사용 한도·전체 앱 수락·all-local/CI/commit/push는 완료로 주장하지 않는다.

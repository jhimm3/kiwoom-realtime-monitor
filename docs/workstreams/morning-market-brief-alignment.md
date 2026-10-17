# 장전 시황과 00·① Market Intelligence 연결 계약

2026-10-15 · ed9d HEAD `50a70c9ce6cdf20915fde27f2d1f47becb82eadf` · 미커밋 로컬 구현

## 대조 기준과 변경 범위

사용자가 지정한 [00통합](../archive/2026-09-22/references/00_전체통합_AI_Trading_Intelligence.md)의 공통 5.2·①7~10·② 입력 요구 원칙과 [① 원본](../archive/2026-09-22/references/Market_Intelligence_Codex_개발명세.md)을 설계 기준으로 읽었다. 이 문서들의 단계 지시는 과거 기록이며 현재 실행 권한으로 사용하지 않는다. 실제 상태는 현재 `context_candidates.py`, `news_observation.py`, `news_rules.py`, `news_jobs.py`, `research_repository.py`, [API 계약](../../API_CONTRACT.md), [현재 통합 보완 설계](../AI_TRADING_SYSTEM_REDESIGN.md)와 대조했다.

① 원문의 로컬 LLM 전용 실행 조건과 이번 사용자의 직접 웹 조사·ChatGPT 구독 요청은 다르다. 후자의 명시 요청을 **장전 공통 자료의 공급 방식**에 적용한다. 내부 개별 뉴스 공급자 선택, 자동 fallback, 매매 정책의 요구자료까지 바꾸는 권한으로 확대하지 않는다.

| v2의 차이·누락 | v4 변경 |
|---|---|
| 단일 `market_mode`, 숫자 `confidence` | ①의 `market_assessment`, `participation`, `concentration`을 분리. 숫자 신뢰도 제거 |
| 확정 종가에도 정확한 시·분을 일괄 요구 | 공식 확정 종가는 거래일 정밀도로 허용하고 현재 시세의 관측 시각 요구와 분리 |
| 원문 사실·해석은 있지만 사건 상태·참조 연결 부족 | `evidence → facts → events`의 로컬 참조, 사실 상태와 해석 분리 |
| 업종 설명만 있고 관계 출처·상태 부족 | `themes_to_observe`, `stock_contexts`에 출처·관계·proposed 상태·반대 근거 |
| 새로움·horizon만 있어 지속·재검토 근거 부족 | 중요도 이유·신규성 비교 근거·지속 메커니즘/전제/재검토 시각 |
| 체크리스트 문장으로는 가설별 소비 불가 | `hypotheses`별 대상과 구조화된 확인·반대·무효화 조건 |
| 장전 판단과 장중 확인 상태의 경계 부족 | 초기 `UNCONFIRMED`만 허용, 장중 관측과 기존 C1 상태 전이 분리 |
| URL/관측 시각 외 원문 시각 계약 부족 | 공개/수정/사건/관측 시각, 날짜 정밀도, 거래일·휴장 근거 |
| 조회 가능 상태가 엔진 연결로 오인될 수 있음 | `consumer_binding=not_connected`, 읽기 TTL과 정책 자료 가용성 구분 |

변경은 기존 장전 생산자의 기본 프롬프트·고정 DTO/검증·보고서 표시·실행 지문·문서·관련 검사에 한정했다. 별도 엔진·상태 전이·점수 공식·매매/주문/계좌 로직·DB 스키마는 추가하거나 수정하지 않았다. 기능을 이해하는 핵심 파일은 기존 application/central_server/presentation 3개이고 전달 계층을 늘리지 않았다.

## 두 입력 경로와 실제 구현 상태

| 입력 | 실제 생산 경로 | ①에서의 역할 | 이번 결과의 연결 상태 |
|---|---|---|---|
| 외부 웹 장전 공통 시황 | 07:55 예약 → 기존 중앙 AI 요청 소유 → 직접 웹 검색/해석 → `morning_market_brief_runs` → 조회 API | GLOBAL_CONTEXT: 해외·거시·산업 사실과 국내 초기 가설 | 로컬 생산/저장/인증 조회/화면 구현. Market State/C1/정책 소비 adapter 미연결 |
| 내부 수집 개별 뉴스 | 기존 Article/Body/AI revision 및 뉴스 jobs, `news-analysis-v2`, `supply-contract-rule-v2` | STOCK_NEWS: 특정 사건·기업 사실/영향과 기존 Event revision | 기존 경로 보존. 장전 JSON을 내부 기사로 재수집하거나 LLM 재분석하지 않음 |
| C1 맥락 가설 | `ContextEvidence`, `build_context_hypothesis`, `build_news_hypotheses`, `advance_context_hypothesis` | 근거·해석과 장중 반응을 분리한 불변 revision | 순수 계산과 ResearchRepository의 append/read 저장은 기존 구현. 새 장전 API의 실제 caller 연결 없음 |

기존 `news_jobs`의 공급계약 규칙 처리는 `GLOBAL` 대상을 무시한다. 장전 공통 보고서를 이 규칙 경로에 그대로 넣을 수 없다. 기존 개별 뉴스 LLM에는 `confidence` 필드가 남아 있지만 이번 작업에서 호환 스키마를 바꾸지 않았다. **장전 v4는 숫자 신뢰도·매매 점수·확률을 받지 않으며 기존 개별 뉴스의 confidence를 매매 확률로 승격하지 않는다.**

기존 지연 Yahoo 자료를 C1로 바꾸는 함수가 있어도 새 장전 생산자의 입력으로 호출하지 않는다. 기존 수집기 중지는 아직 수행하지 않았다. 새 공통 자료가 모든 개별 뉴스나 장중 변경을 대신한다고 주장하지 않는다.

## 엄격한 출력 계약

[편집 기본 프롬프트](morning-market-brief-prompt.txt), [JSON Schema](morning-market-brief-output.schema.json), [실행 검증 코드](../../src/kiwoom_monitor/application/morning_market_brief.py)가 같은 v4 계약을 따른다. 편집 지시 뒤에 고정 스키마와 검증 제약을 붙인다. 추가 필드·타입 변환·중복 JSON 키·중복/미정의 참조를 거절한다. 모든 필드는 필수이며 미확인은 null/unknown/빈 배열로 표현한다.

| 필드 | 의미·핵심 제약 |
|---|---|
| `schema/source_type/source_mode/as_of` | `morning-market-brief-v4` / `GLOBAL_CONTEXT` / `ai_web_search` / 실제 cutoff와 같은 aware 시각 |
| `evidence_summary` | 사람용 한국어 요약. 기계 입력 필드의 대체가 아님 |
| `calendar` | 한국/미국 휴장 여부, 최근/다음 거래일과 근거 참조. 미확인은 null/unknown |
| `market_assessment` | bullish/bearish/mixed/uncertain, `initial_hypothesis`, 이유·근거 |
| `participation/concentration` | 참여 폭·편중을 분리. 지난 완료 세션/외부 proxy/미관측을 구분. 오늘 장중 실측이라고 표현하지 않음 |
| `market_data` | 필수9개 ID 각각 한 번. 수치/단위/상품·venue/quote type/월물/비교 기준/관측 시각/상태·근거 및 trading_date/price_status/timestamp_precision/availability_basis |
| `evidence` | 검색 도구가 반환한 URL, 기관, 출처 종류, 원문 확인 수준, 게시·수정 시각과 날짜 정밀도 |
| `facts` | 사실 문장, UNKNOWN/RUMOR/PROPOSED/CONFIRMED/AMENDED/CANCELLED/COMPLETED, 사건·관측 시각·근거 |
| `events` | 사실 참조·영향 해석·범위·방향·질적 중요도/이유·신규성/비교 근거·지속성/전제/재검토 |
| `themes_to_observe` | industry/market_theme/event_specific_basket, 관계 출처·proposed·사건/근거/반대 참조 |
| `stock_contexts` | 회사명·확인한 국내 코드 또는 null, 시장, 관계 유형/출처·proposed·테마/사건/근거/반대 참조. `security_id=null` |
| `hypotheses` | `UNCONFIRMED`, 주장·방향·범위·사건/테마/종목 참조, 확인/반대/무효화 관찰 요청·누락 |
| 관찰 조건 | `data_source`, `target_ref`, `observation`, `expected_change`, `evidence_ids`. 수치 임계값/점수 공식 없음 |
| `unknowns` | 필드·누락 이유·해석 제한/대상 연결 차단/장중 확인 필요 |
| `changes_from_previous` | 현재 이전 판단을 입력하지 않으므로 반드시 `[]`. 모델 기억으로 변경 이력을 만들지 않음 |

관찰 자료 이름은 TOP20, TURNOVER, PRICE, TRADES, THEME_COHERENCE, NEWS_REVISION, MARKET_INDEX, CALENDAR이다. 이들은 소비자가 확인해야 할 자료 종류를 표시하며 새 실시간 필드나 자동 규칙 실행기를 정의하지 않는다. 해당 자료의 실제 의미·단위·세션·완전성·수치 임계값은 기존 owner와 정책이 정한다.

원문 확인 수준은 LLM의 조사 보고다. 서버가 URL과 날짜·참조를 검증해도 원문의 모든 문장·과거 버전 존재를 독립 검증한 것은 아니다. 검색 원문 snapshot과 과거 공개 가용성을 확인하는 후속 연결이 필요하다. 날짜만 있거나 공개 시각이 없으면 unknowns를 추가하고 과거 장중 입력으로 소급하지 않는다.

미래 관측/게시/수정/사건 시각을 거절한다. 미래 재검토 `review_due_at`는 계획 시각이므로 별도 허용한다. 공식 원문에서 검증한 확정 종가는 trading_date·price_status=final_close·timestamp_precision=trading_date·availability_basis=official_completed_session으로 시·분 없이 허용하며 observed_at=null을 보존한다. 실시간/지연 시세·미확정 종가에는 관측 시각이 필요하다. 나스닥 선물 현재값을 과거 종가로 대체하지 않는다. 공식 달력의 최근 완료 거래일과 일치하는 지수 확정 종가는 휴장 때문에 36시간이 지나도 유효하다. 다른 오래된 자료는 stale로 구분하며 종가를 최신 시세로 표현하지 않는다. 원문 내용/확정 여부/달력은 조사 근거이며 별도의 실시간 거래 달력 공급자가 연결됐다고 주장하지 않는다. AP URL 별칭은 실제 검색 기사 ID가 같은 경우에만 기존 제한대로 정규화한다.

## 기존 C1으로의 매핑 — 후속 adapter 요구사항

아래는 새 엔진이 아니라 **기존 계약을 소비할 때 필요한 경계**다. 현재 코드가 이 매핑을 자동 실행한다고 표현하지 않는다.

| 새 입력 | 기존 C1/① 소비 계약 | 연결 전에 필요한 것 |
|---|---|---|
| 웹 evidence/facts | `ContextEvidence.facts`, `source_type=GLOBAL_CONTEXT` | 실제 run+원문/내용 지문 기반 불변 `evidence_ref`; 중앙 자료의 버전/가용성 |
| events/방향/지속성/가설 | `inferred_impact`, `event_group_id` | 기존 Event 식별/Revision에 연결. 응답의 event_key를 영속 사건 ID로 가장하지 않음 |
| 종목·테마 관계 후보 | `target_id`, `relation_version`; ① Theme/Stock Memory 연결 | 종목 마스터 검증과 관계 revision/검토. 임시 theme_key를 ThemeMembership으로 변환하지 않음 |
| 실제 완료 기록 | `available_at`, `expires_at`, 원문/분석 시점 | cutoff와 실제 수신/완료 가용시각 분리; TTL을 조회 때 연장하지 않음 |
| 장중 자료 | `IntradayResponse` | TOP20/거래대금/가격/체결/테마 동조의 실제 owner 참조, observed/available 시각·quality·policy 계산 |
| 가설 생성 | `build_context_hypothesis` | 같은 종목·사건의 확인 가능한 불변 근거, 시작 UNCONFIRMED, `entry_eligible=false` |

시장 전체의 `market` 로컬 참조를 C1 종목 target에 넣거나 GLOBAL이라는 가상 종목을 만들지 않는다. 광역 시황은 ① Market State 입력이고, 종목별 전달 근거와 실제 관계가 검증된 부분만 종목 C1 입력 후보가 된다. 서로 같은 사건의 여러 매체/거시 파생 신호를 독립 호재로 중복 가산하지 않는다.

현재 C1은 COMPLETE 반응에 따라 FLOW_CONFIRMED/LEADERSHIP_CONFIRMED, 모순에 REJECTED, 미확인 만료 관측에 NO_RESPONSE/EXPIRED를 만든다. PARTIAL/STALE/UNAVAILABLE로 확인 상태를 만들지 않는다. terminal 상태는 되살리지 않고 새 사건/관계 revision으로 재검토해야 한다. **확인 상태는 현재 함수에서 만료 관측 후에도 보존될 수 있으므로**, 전체 가설의 강화·수정·무효화·만료 수명 관리를 이미 완성했다고 주장하지 않는다. 보고서 조회 TTL 차단은 구현했고 장중 이벤트 연결·새 revision 작성·확인 가설의 소비 만료는 후속이다. 이 한계를 주문 코드 수정으로 우회하지 않는다.

## 정책·수치화·권한

LLM은 사실 수치와 질적 해석만 제공한다. 뉴스의 `importance_score` 등 기존 규칙 계산, 정책 특징/가중치, 검증된 확률·EV·Risk·최종 매매 판단은 앱의 기존 소유자 책임이다. v4의 high/medium/low 중요도를 임의 고정 매매 점수로 치환하지 않는다.

현재 `required_data_capabilities`의 sampled_price/stock_thesis/theme_context/news_context/turnover_delta와 행동별 요구 조건을 보존한다. 보고서 `available=true`는 조회 가능한 최신 구조화 결과가 있다는 뜻이며 `news_context` 생산자 연결 완료나 정책의 필수 입력 충족을 뜻하지 않는다. 미요구 자료 누락으로 다른 전략을 차단하지 않고, 요구한 자료의 미연결/만료는 기존 정책의 결측 처리로 남긴다.

기계 조회 envelope는 `advisory_only=true`, `auto_trade_eligible=false`, `consumer_binding=not_connected`를 명시한다. 모델/추론·설정 revision·입력/편집 프롬프트/완성 프롬프트/출력 스키마 지문·cutoff·실제 available/expiry를 보존한다. 기존 v2/v3 실행은 삭제하지 않고 화면에 이전 계약으로 표시하며 v4 입력으로 자동 승격하지 않는다. 저장된 사용자 편집 프롬프트를 자동 덮어쓰지 않는다.

## 검증과 남은 범위

### 실제 실패의 재현과 생성 계약 정렬

통합 v3 NAS의 실제 검색 run `2026-10-15:cd0fee16e548477ba60376afbe529f9d`는 FRED 근거 두 개가 게시시각=null인데 timestamp_precision=datetime이어서 기존 엄격 검증에 실패했다. 저장 응답을 오프라인 재생해 같은 거절을 확인했다. 진단 복사본에서 이 두 precision만 unknown으로 바꾸면 다음 검색 출처 URL 거절이 나타났으며, 이는 수락/원본 보정/재저장이 아니다. 원래 실패 run은 유지한다.

기존 필수 조건을 생성 JSON Schema에도 if/then으로 명시했다(datetime→publisher_published_at, date_only→published_date). 고정 프롬프트는 원문 게시 정밀도와 지표 관측 정밀도를 구분하고, 도구가 실제 반환한 URL 및 확인된 시각만 쓰며 미확인은 null/unknown으로 남기도록 보완했다. 사용자의 편집 프롬프트와 v4 payload 필드는 바꾸지 않았다. 기존 validator의 세 출처/게시시각 오류에 안정적인 안전 코드와 한국어 화면 설명을 연결했다. 검증 조건 완화·시각 합성·출처 추정·자동 재시도는 없다. 기존3파일 책임/호출 깊이와 저장 owner를 유지한다.

관련 Windows 정식 Runner 89건/5worker는 실패·오류·skip0, 프로세스 종료·실행 전후 동일 LF 지문으로 통과했다. 현재 고정 NAS v5 후보의 검사/배포와 새 실제 검색 성공은 별도 근거가 필요하다. 이 로컬 결과를 실제 시황 성공으로 표시하지 않는다.

v4의 형식·원문 URL/시간·참조·초기 상태·관계 후보·임의 점수/확률 거절·구버전 보존·인증 API·예약 수명·화면 회귀를 수행한다. 결과는 [작업 상태](morning-market-brief.md)에 기록한다. 기존 v2의 실제 검색 응답이나 NAS 검사 결과를 v4 출력/운영 수락으로 재사용하지 않는다.

NAS 활성 소스는 기존 recorder release 그대로다. 기존 관리자 설치 시도는 operation_busy에서 설치 시작 전 거절됐고, 이어진 비활성 후보 격리 12건은 pass/skip 0/cleanup/운영 불변이었다. v4 계약은 NAS 미배포이며 기존 준비본을 그대로 활성화하지 않는다. 실제 검색 v4 준수·NAS 모델 연결·07:55 예약 수락·Market State/C1 adapter·장중 관측/수명·중복 수집 중지는 남아 있다. 사용자는 기존 Yahoo 나스닥 선물과 WTI 수집을 모두 비활성화 대상으로 결정했으며 이번 프롬프트 변경에서는 운영 설정을 바꾸지 않았다. 자동매매 판단과 주문 코드의 변경은 이번 범위에 포함하지 않는다.

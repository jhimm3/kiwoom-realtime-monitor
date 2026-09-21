# 현재 구현 아키텍처

기준: 2026-09-22 · 제품 2.1.0 · 현재 소스 `codex/release-2.1.0` · [문서 안내](docs/README.md)

이 문서는 현재 책임과 데이터 흐름을 설명한다. 실제 NAS 배포·실환경 검증은 [현재 상태](docs/CURRENT_STATUS.md), 새로운 기능 순서는 [로드맵](FUTURE_DEVELOPMENT_ROADMAP.md)에 둔다. 과거 단계별 진행 기록은 [아카이브](docs/archive/2026-09-22/README.md)에 보존했다.

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

## 시장 자료

중앙 REST는 `CentralRestBroker`가 우선순위·동시 요청·캐시를 관리한다. `ka00198` 순위가 분봉/일봉/백필보다 우선하며 저우선 작업이 순위 경계를 막지 않는다. NAS 순위 수집은 앱 실행과 독립된다. 화면은 저장된 NAS 자료를 우선 조회하며 정상 연결의 시장자료 부족을 PC의 자동 직접 TR 조회로 바꾸지 않는다.

중앙 WebSocket은 체결·시장·계좌 경계를 구분한다. TOP20/관심 후보의 수신 체결로 1초 OHLCV·거래대금·건수를 집계하고 분봉·TOP20 구성/지수·시장 상태를 저장한다. 1초봉은 수신한 체결의 집계이며 모든 종목·기간의 원시 틱/호가 전수 기록이 아니다. 구독 전·끊김·부분 수집을 완전한 기록이나 실제 0으로 바꾸지 않는다.

확정값·최신 조회값과 불변 observation revision은 목적이 다르다. 순위·분봉·테마·뉴스의 당시 가용시각을 보존한다. 장후 보완은 당시 장중 관측으로 소급하지 않는다. 거래일별 세션 정책, 정규장 종가와 전체일 종가, 거래소, 수정주가·금액 단위를 구분한다. 자세한 규칙은 [과거 데이터 계약](HISTORICAL_DATA_CONTRACT.md)을 따른다.

로컬 쓰기는 `MarketCacheWriter` 등 소유된 작업 경계를 사용한다. NAS 장애전환은 기존 직접 client의 토큰·잠금을 공유하고 실제 구독 승인/조회 성공을 확인한 뒤 상태를 전환한다. 로컬 장애전환 자료로 중앙의 과거 공백을 감추지 않는다.

## 뉴스·AI·테마

현재 뉴스에는 네이버 검색 API와 선택형 DART, 본문 추출·정제, AI 분석, 사건·구성원 이력, 작업/cursor가 있다. 기사·본문·AI·사건은 서로 다른 revision과 가용시각을 가진다. 현재 수집 대상의 coverage이며 전체 시장·과거 5년 뉴스를 보장하지 않는다.

네이버 증권 사이트의 과거 수집은 앞으로 연결할 입력이다. 기존 외부 백필 도구와 앱의 검색 API 경로를 동일 기능으로 간주하지 않는다. 외부 AI 공급자 경로는 존재하지만 첨부에서 언급한 로컬 LLM/Mac 작업자는 확인된 현재 기능이 아니다.

현재 테마 원본은 `theme_profiles/profile_themes/profile_stock_themes`다. 구형 `themes/stock_themes`는 이전·복원 호환을 위한 자료다. 테마의 로컬 수정·pending 재시도·NAS 수락 스냅샷과 실제 가용시각을 보존한다. LLM의 대표 테마/별칭은 앞으로 기존 프로필 경계를 확장해 연결할 기획이다.

뉴스 동기화는 문서 identity·내용 hash의 영속 delta를 사용한다. 파일 수정시각만으로 전체 업로드하지 않으며 pull 결과를 다시 echo push하지 않는다. 공유 AI 운영값은 중앙 운영 설정, 창 위치/표시 설정은 각 PC의 책임이다. 새 설정·테마 필드는 파일 백업·Drive·NAS 왕복까지 확인한다.

## 연구·타점·피드백

기존 reader/runner는 동결 export와 당시 가용했던 입력을 소비한다. 돌파와 눌림 재가속 Family, 순위 Factor, Snapshot/Decision/후보 사건이 있으며 정책은 최종 정답이 아닌 버전별 연구 대상이다.

`PaperExecutionEngine`의 역사봉 체결·현금·비용 원장과 증권사 모의계좌 실행은 별개다. 현재 strict 입력·체결 모델이 지원하지 않는 초 단위 체결 순서·VI 호가·부분체결·시장충격을 결과에 만들어 넣지 않는다.

지속 campaign은 설정 revision·job/cycle·lease·재시도·예산·완료 cache를 보존한다. 고정 범위의 새 입력 revision 준비, 독립 시간/종목 검증, final 후보 잠금·접근·명시 복구·개발 노출, 자동 후속 가설과 화면 연결이 있다. 새 거래일을 자동 확장하거나 모든 final 작업을 자동 재개하는 기능까지 완성된 것은 아니다.

일지 상세 체결 projection·대조·불변 feedback evidence·기계 복기·개선 가설·명시 채택·전략 버전 재검증이 연결돼 있다. 사용자 수동 기록과 원본 체결을 사후 AI 결과로 덮지 않는다. [연구 요청 계약](docs/RESEARCH_REQUEST_FORMAT.md)과 [전진평가 계약](docs/FORWARD_EVALUATION_CONTRACT.md)을 따른다.

## 계좌·실행·인증

모의 주문은 계좌 단일 owner, 멱등 intent, 전송 직전 gate, unknown 응답 대사·복구를 거친다. 자동 모의운용의 후보 게시·admission·복구·위험 근거·runner/supervisor/UI는 구현돼 있다. 준비 완료나 평가 PASSED가 곧 주문 시작·실계좌 승격은 아니다.

자동 O2-M은 현재 지원하는 단일 전략·단일 포지션·KRX 정규장 범위를 따른다. 수동 KRX 애프터 LIMIT probe의 별도 경계와 일반 자동 운용을 혼동하지 않는다. [모의 실행 계약](docs/KIWOOM_MOCK_EXECUTION_CONTRACT.md)을 따른다.

NAS 복수 계좌 프로필·인증 교체·런타임 owner와 서버 시세 담당 선택 API(`GET/PUT /api/v1/settings/market-profile`)는 구현돼 있다. PC에서 시세 담당을 선택하는 UI는 아직 연결되지 않았다. PC 직접/장애전환 계좌 신원 결합은 별도 경계이며 resolve/aliases·fresh credential 검증·직접 WS 결합에 미완료 항목이 남아 있다.

로컬 비밀은 로컬 암호화 저장소, NAS 비밀은 `server-secrets`의 암호화 vault가 소유한다. 키는 일반 DB·문서·백업·로그에 넣지 않는다. runtime revision과 실제 적용을 확인한 뒤 활성 상태를 표시하고 실패를 임의 이전 키 복귀로 숨기지 않는다.

## 이번 계획에서 추가하는 경계

기존 후보 DB → 원천별 과거 봉/뉴스 → 출처·시각·해상도가 명시된 사례 → 기존 연구·LLM·테마 프로필 순서다. 새 자료의 사후 후보 선택과 수집시각을 기록한 채 연구 입력을 연결한다. 기존 TOP20 revision을 위조하거나 원본 DB를 새 공급자 값으로 덮는 방식을 쓰지 않는다.

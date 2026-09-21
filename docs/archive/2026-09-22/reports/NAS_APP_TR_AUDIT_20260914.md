> **과거 기록** · 원래 경로: `reports/NAS_APP_TR_AUDIT_20260914.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# NAS 연결 중 데스크톱 TR 감사 (2026-09-14)

## NAS 책임 이동 후 운영 감사

15:21 NAS 재빌드 뒤 `top20_membership`이 첫 snapshot까지만 저장되고 자동 갱신이 멈춘 사실을 중앙 snapshot API로 확인했다. 같은 시각 `ka00198` 수동 중앙 조회는 약 0.4초에 성공하고 실제 순위도 달라져 키움 순위 TR·NAS HTTP·앱 표시만의 문제는 제외했다. 원인은 `AutonomousTop20Service.refresh_ranking_once()`가 순위 저장 뒤 종목 카탈로그 갱신과 최대 20여 종목의 NXT 적격성·계좌 편입 구독 보완을 같은 30초 루프에서 기다린 구조다. 이 중 하나가 지연되면 다음 순위 슬롯에 진입하지 못했다.

순위 조회·저장 뒤 TOP20 KRX 구독을 즉시 반영하고, 카탈로그·NXT·계좌 편입·기본정보 보완은 중복 없는 background 작업으로 분리했다. 중앙 broker의 대기열 우선순위와 요청 병합은 유지한다. 이미 실행 중인 한 개의 Kiwoom REST 요청은 중간 선점할 수 없어 그 요청의 네트워크 응답 시간만큼 순위가 늦을 수 있으나, 부가 작업 전체가 끝날 때까지 30초 루프가 정지하는 경로는 제거했다. 장후 전체일 보완은 20:05 이후 또는 07:40 이전에만 같은 scheduler에서 실행되고 07:40에 전일 반복을 중단하므로 장중 순위 정지 원인에 해당하지 않았다. 뉴스·후보·모의계좌 수집은 별도 task 또는 별도 mock broker를 사용한다.

## 범위와 판정 기준

메인 앱, 뉴스 프로세스, 매매일지, 연구·후보 화면에서 키움 API ID 문자열과 `request`, `request_with_continuation`, `query_account_pages` 호출자를 전부 추적했다. `RemoteKiwoomRestClient`의 중앙 DB GET은 TR이 아니며, `/api/v1/kiwoom/query` 또는 `/api/v2/kiwoom/account-query`가 NAS broker를 거쳐 키움으로 나갈 때만 앱이 시작한 TR로 분류했다.

## 정상 NAS 연결 자동 시장자료

| 자료 | 기존 TR | 앱의 현재 입력 | 앱 시작 TR |
| --- | --- | --- | --- |
| 순위 1~5 | `ka00198` | `top20_membership` / `ranking` snapshot | 없음 |
| 5·20·250일 신고가 | `ka10016` | `new_high` snapshot | 없음 |
| 당일 분봉 | `ka10080` | `/market/minute-bars` | 없음 |
| 오늘+직전 거래일 분봉 | `ka10080` | `/market/recent-minute-bars` | 없음 |
| 확정 일봉 | `ka10081` | `/market/daily-bars` | 없음 |
| 기본정보 | `ka10001` | `stock_fundamentals` 문서 | 없음 |
| NXT 가능 여부 | `ka10100` | `stock_nxt_eligibility` 문서 | 없음 |
| 역사적 신고가 | `ka10094/ka10083/ka10081` | `historical_highs` 문서 | 없음 |
| 외국인·기관 | `ka10045` | `investor_flow` snapshot | 없음 |
| 프로그램매매 | `ka90008` | 실시간 `0W` / `program_flow` snapshot | 없음 |
| 코스피·코스닥 차트 | `ka20005/ka20006` | `market_index_chart` snapshot | 없음 |

뉴스·테마·연구·후보 화면은 중앙 콘텐츠와 연구 API만 읽고 키움 TR을 시작하지 않는다. `0B`, `0W`, `00`, `04`, `0J`, `0U`, `1h`는 NAS WebSocket 실시간 이벤트이며 TR 조회에 포함하지 않는다.

## 남는 앱 시작 TR

- `kt00007`: 계좌별 과거 체결 조회. 매매일지 수동 동기화와 20:05 이후 자동 동기화가 사용한다.
- `kt00015`: 계좌별 실제 수수료·세금 조회. 위 체결 동기화와 같은 범위에서 사용한다.

두 요청은 `/api/v2/kiwoom/account-query`의 검증된 계좌 context로 실행하며 시장자료 자동 수집과 분리한다. 계좌 신원이 일치하지 않으면 결과를 합치지 않는다.

## 별도 조건에서만 발생

- 사용자가 직접 모드를 선택하면 PC의 `KiwoomRestClient`가 시장 TR을 수행한다.
- NAS 연결이 실제로 끊기고 로컬 장애전환이 활성화되어 있으면 PC 직접 TR이 허용된다.
- 병행 검증 모드는 중앙 저장 전용 loader에는 로컬 비교 TR을 만들지 않는다.
- API 설정의 연결 확인은 OAuth 토큰 확인이며 시장 조회 TR이 아니다.
- NAS의 자율 `ka00198/ka10016/ka10080/ka10081/ka10001/ka10100/ka10045/ka90008/ka20005/ka20006/ka10083/ka10094`는 앱 요청이 아니라 서버 스케줄과 실제 공백에 의해 실행된다.
- NAS 모의계좌 감시의 계좌 TR은 실전 5회 한도와 분리된 모의 1회 한도를 사용하며 데스크톱 앱이 실행하지 않는다.

## 이번 감사에서 발견해 수정한 경로

`ConfirmWorker(include_previous=True)`가 호출하는 `MinuteChartService.load_two_trading_days()`만 저장 전용 loader가 없어 정상 NAS 연결에서도 `ka10080`을 위임하고 있었다. 중앙 최근 2거래일 분봉 GET을 추가하고 빈 저장 결과를 그대로 반환하도록 수정했다. 중앙 순위·역사적 신고가 문서가 비정상일 때도 TR fallback 대신 형식 오류로 중단하도록 수정했다.

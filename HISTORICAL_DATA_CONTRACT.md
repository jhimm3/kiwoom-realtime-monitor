# 과거 시장 재현용 데이터 계약

상태 정리 기준: 2026-09-22. 이 문서는 저장·가용시각·재현·공백 처리 계약을 유지한다. 현재 구현과 확인된 배포 범위는 [현재 상태](docs/CURRENT_STATUS.md), 데이터 확보와 미완료 검증은 [남은 작업](docs/OPEN_ITEMS.md)을 따른다. 구현된 연구 기능과 실제 입력 데이터의 충족 여부는 구분한다.

목적은 과거 분봉·일봉을 다시 그리는 데 그치지 않고, 특정 시점에 어떤 종목이 시장의 관심을 받았고 어느 테마에 자금이 몰렸는지 재현해 돌파 조건을 시뮬레이션하는 것이다. 이 문서는 현재 실제 저장 범위와 아직 부족한 범위를 구분한다.

CR1a(2026-09-16)는 기존 일별 export를 `research_dataset_bundle/v1` index로 묶는 PC 자료 준비 단계다.
자식 manifest/원본 파일/hash·watermark·범위·품질을 보존하며 일별 ordinal을 합친 ordinal로 바꾸지 않는다.
같은 revision/테마의 동일 내용은 고유 개수로 계산하고 충돌은 거부한다. 누락·빈 날짜·잘린 sidecar는
정상 0/휴장/완전 수집으로 승격하지 않는다. CR1b의 통합 입력 reader는 bundle을 기존 KRX 분봉 연구에
연결한다. 원본 ordinal을 유지한 채 UTC available_at/accepted_sequence/revision_id 순으로 소비하고
동일 revision은 한 번만 반영한다. 다기간 runtime identity는 경로를 제외한 자식 manifest hash와 bundle ID,
정렬된 revision ID hash를 고정한다. 가용시각이 없거나 순서가 잘못된 bundle 행은 실행 전에 거절한다.
하루 묶음은 원래 일별 identity를 유지한다. 보유/현금은 이어지고 pending은 기존 명시 profile의 다음 봉/
세션 경계 정책을 따른다. 마지막에만 미완료 보유를 CENSORED로 기록하며 강제 매도하지 않는다.
테마는 각 날짜 sidecar를 함께 제공해 기존 시점 reader가 시작 전 최신 구성과 기간 중 변경을 선택한다.
CR1b 자원 경계는 입력 바이트×8 + 현재 RSS의 보수적 preflight와 로딩/실행 checkpoint RSS 검사를 사용한다.
512MiB 기본 한도는 128~4096MiB에서 설정한다. 부족하면 RESOURCE_BLOCKED이며 자료를 0으로 채우거나
전략 실패 결과로 인증하지 않는다. 협력적 검사이므로 단일 큰 할당/정렬의 순간 초과를 OS처럼 강제 차단하지는 않는다.
1/5/20일 fixture 계측은 제한된 연구 검증이며 실제 NAS 저장 품질/전체 종목 처리량을 보증하지 않는다.
기존 중앙 24시간 API·DB·실시간 순위 수집 경로는 변경하지 않았다.

## 재현에 필요한 최소 축

모든 시점 자료는 `관측시각`, `거래일`, `시장(KRX/NXT/SOR)`, `종목코드`, `출처`, `수집상태`를 잃지 않아야 한다. Raw 관측값은 파생 분석값으로 덮어쓰지 않는다.

코드의 공통 계약은 `domain/market_data_contract.py`다. 신규 분석 입력은 가능한 한 `MarketDataObservation`과 `MarketDataMetadata`로 감싸 다음 의미를 명시한다.

| 필드 | 의미 |
| --- | --- |
| `kind` | 분봉·일봉·시장 상태·후보군·TOP20 지수 중 어떤 자료인지 |
| `subject` | 종목코드 또는 시장/후보군 식별자 |
| `effective_at` | 값이 설명하는 실제 시장 기준 시각 |
| `available_at` | 수집기와 분석기가 그 값을 사용할 수 있게 된 시각 |
| `venue` | KRX·NXT·SOR·통합·미확인 |
| `unit` | 원·억원·백만원·주·퍼센트·지수 포인트 등 |
| `value_kind` | 실제값·추정값·실제값 기반 파생·추정값 기반 파생 |
| `completeness` | 진행 중·확정·부분·미확정·누락 |
| `origin` | 실시간·조회·장후 보완·파생·미확인 |
| `candidate_universe` | 조회순위 TOP20·화면 후보군·시장 전체·실제 체결 종목 등 표본 범위 |

`effective_at`과 `available_at`이 모두 확인되지 않은 과거 행은 시점 재생에서 당시 사용 가능했던 자료로 추정하지 않는다. 기존 `capture_state` 문자열은 비파괴 어댑터로 읽으며, 알 수 없는 문자열은 `확정`이 아니라 `미확정`으로 분류한다. 로컬 메인·매매일지 DB의 `market_data_observation_meta`와 중앙 SQLite/PostgreSQL의 `central_market_data_observation_meta`를 명시적 마이그레이션으로 마련했다. 중앙 순위·TOP20 지수·시장 상태·조회/실시간 분봉·일봉, 로컬 메인·매매일지 분봉·일봉, 체결 스냅샷의 신규 관측은 값과 메타데이터를 같은 트랜잭션에 저장한다. 체결 스냅샷은 필드별 `entry_context`로 나눠 체결 기준시각과 실제 가용시각을 보존한다. 기존 행은 추정해 채우지 않는다.

| 자료 | 현재 저장 위치·단위 | 현재 재현 가능 범위 | 남은 공백 |
| --- | --- | --- | --- |
| 실시간 종목조회순위 TOP20 | NAS 최신 projection `central_dataset_snapshots`와 D1 불변 원장 `central_observation_revisions`, `kind=ranking/top20_membership` | NAS가 24시간 30초 주기로 앱과 무관하게 조회한 원본 순위·실제 TOP20 구성 및 같은 시각의 정정 순서 | D1 최초 기록 전과 NAS 비가동·저장 실패 구간은 `legacy_unknown`/`recording_gap`으로 남음 |
| TOP20 거래대금 지수 | 로컬 `top20_trade_value_index`, NAS `kind=top20_index`, 1분 | 합계·KOSPI/KOSDAQ/미확인 분해·구성 종목·30초 코호트·완성 상태 | 시장 구분을 확인하지 못한 종목은 미확인으로 유지 |
| 종목 1초 체결 집계 | NAS `central_second_trade_bars`, 1초 | TOP20 또는 hot cohort 0B 구독 뒤 종목·KRX/NXT별 OHLC·거래량·거래대금·체결 건수 | 두 후보군 진입 전과 NAS 비가동/비구독 구간은 복구 불가하며 공백 이력·조회 API는 아직 없음 |
| VI | NAS `central_vi_event_revisions` | 1h 실시간과 ka10054 보완으로 수신한 발동/해제·정적/동적·가격·시각·방향·횟수·거래소 사실 | NAS 시작 전 전체 이력과 공급원이 주지 않은 필드는 unknown |
| 15% hot cohort | NAS `central_hot_cohort_current/revisions` | 저장 조건식 INITIAL/I 편입부터 다음 실제 관측 KRX 세션 종료까지의 추적 집합 | 조건식 미선택·NAS 비가동 구간과 편입 전 상태는 unknown; KRX 조건검색 범위 |
| 상한가 상태 | NAS `central_upper_limit_fact_revisions` | `upl_pric`과 추적 중 0B가 확인한 UNKNOWN/TOUCHED/CURRENT/CLOSED_AT_LIMIT | 추적 전 순간 터치가 당일 고가나 신뢰 가능한 보완으로 확인되지 않으면 추정하지 않음 |
| 종목 분봉 | 로컬 `minute_bars`, NAS `central_minute_bars`, D3a `minute_bar` revision, 1분 | 신규 NAS 실시간 수집은 delta별 누적 전체 봉과 실제 마감 가용시각을 재생하며, strict reader는 KRX·실제값·마감·수집 완전 봉만 선택 | D3a 이전 분봉은 형성 과정과 마감 가용시각을 복원할 수 없고, 전 종목 전 시간대 수집이 아니며 구독 공백 봉은 partial |
| 종목 일봉 | 로컬 `daily_bars`, NAS `central_daily_bars`, 1일 | 조회·보완된 종목의 OHLCV와 거래대금 | 전 종목 전 기간 완전성은 별도 보장하지 않음 |
| 시장 지수/시장 상태 | 로컬 시장 지수 분·일봉, NAS `market_state` 스냅샷 | KOSPI/KOSDAQ 가격·등락·거래대금의 저장된 구간 | 앱/NAS 비가동 구간은 공백 가능 |
| 테마·섹터 소속 | 로컬 프로필 테마, NAS 현재 projection과 `central_theme_snapshots` | D5 적용 뒤 수락된 전체 프로필·종목↔테마 관계, 활성 프로필, 편집 기준시각과 실제 가용시각 | 최초 이력 기록 전은 unknown이며 과거 기준정보·기업행동 이력은 아직 없음 |
| 뉴스·재료 | 로컬 `news.sqlite3`, NAS 최신 `news_article/news_ai`와 N1 불변 article/body/AI revision | N1 적용 뒤 수집기별 NAS 최초 수신·가용시각, 기사 정정, 본문 추출 상태, 정확한 입력 revision과 AI 결과/사용량 | 최초 N1 기록 전과 미수집 매체·NAS 비가동 구간은 unknown. 현재 범위는 기존 watchlist/Naver/DART이며 시장 전체 뉴스가 아님 |
| 체결 시점 복기 자료 | `journal.trade_entry_snapshots` | 실제 체결 종목의 순위·대금·테마·뉴스·수급·호가·시장 | 체결하지 않은 후보 종목 전체의 동일 수준 스냅샷은 없음 |
| 외국인·기관/프로그램 | NAS dataset snapshot, 매매일지 장후 보완 | 저장된 종목·일자의 누적 수급과 프로그램 흐름 | 순간 외국인·기관 체결 주체를 의미하지 않으며 대상 종목 범위가 제한됨 |

## 보존 계약

1. 서로 다른 `snapshot_key`의 순위·시장·수급 스냅샷은 함께 보존한다. 순위·TOP20 편입의 최신 projection은 동일 종류·대상·시각을 upsert하지만 D1 원장은 값·품질·출처가 바뀐 A→B→A 정정을 모두 append한다. 같은 관측키·출처의 연속 동일 내용 캐시 재처리만 합친다.
2. 순위 원본 배열의 순서와 키움 응답 필드는 저장 단계에서 재정렬하거나 요약하지 않는다.
3. TOP20 거래대금 파생값에는 그 분에 적용된 구성 종목과 30초별 코호트를 함께 둔다. 합계만 남겨 구성 근거를 버리지 않는다.
4. 분봉·일봉의 확정값 병합은 허용하지만 거래일·시장 구분을 합치지 않는다.
5. 테마 소속은 중앙 `theme_metadata(default,full)` 수락 트랜잭션에서만 불변 이력으로 append한다. 현재 projection 세 컬렉션의 중간 상태를 조합하지 않으며, 첫 기록 이전은 unknown이다.
6. 데이터 공백은 0으로 채우지 않는다. 수집 안 됨, 부분 수집, 장후 보완과 실제 0을 구분한다.
7. 현재 코드에는 중앙 분봉·일봉·순위·TOP20 편입 이력의 자동 삭제가 없다. 만료 API 캐시는 정리되고 `central_realtime_latest`는 최신값으로 갱신된다.
8. 하루 중 TOP20에 한 번이라도 편입된 종목은 `top20_daily_entrants`에 남긴다. 당일 이탈 종목도 삭제하지 않는다.
9. 장후 보완이 완료된 분봉·일봉에는 coverage 문서를 남기며, 완료 표시가 없는 일부 봉은 완전한 하루로 간주하지 않는다. 시행일 이후 KRX 전체일 문서는 20:00 종료 뒤의 성공한 조회에만 `scope=full_day`, `window_closed=true`, `session_finalized=true`를 기록한다. 마지막 분 체결 부재는 실패 근거가 아니지만 조회 실패와 대상일 실제 봉 부재는 완료 근거가 아니다.
10. 중앙 `/api/v1/market/coverage`는 관측 종류·대상·시간 범위·가용 기준시각으로 저장 범위를 읽기 전용 진단한다. 분봉·일봉의 행 부재는 무체결일 수 있으므로 별도 완료 근거 없이 결측으로 단정하지 않는다.
11. NAS와 키움 사이의 실시간 원본이 끊긴 TOP20 분은 0으로 만들거나 로컬 장애전환 값으로 메우지 않는다. 재연결 뒤 저장을 재개하며 빠진 타임스탬프를 수집 공백으로 해석한다.
12. `ka10016` 신고가 목록과 `ka10001` 기본정보, `ka10100` NXT 가능 여부는 NAS 경유 조회 때 관측 시각별 스냅샷으로 누적한다. 기본정보와 NXT 가능 여부의 최신 문서는 현재 화면 복원을 위한 것이며 과거 스냅샷을 대신하지 않는다.
13. 1초 체결 집계는 체결이 있는 초만 저장하고 빈 초를 0으로 만들지 않는다. 원시 틱이나 호가를 보존하지 않으며, 누적 필드가 있는 완전 동일 0B 재수신은 중복으로 제외한다. 5초를 넘긴 역순 체결은 현재 집계 범위 밖이다.
14. 테마 이력의 `effective_at`은 로컬 문서가 설명하는 편집시각이고 `received_at`·`available_at`은 중앙 서버의 실제 수신·수락시각이다. 늦은 PC 전송은 과거 가용시각을 고치지 않으며, 연속된 동일 내용 hash 재전송만 중복으로 제외한다. 삭제와 활성 프로필 전환은 수정된 전체 문서의 새 revision이다.
15. 뉴스 기사 관측은 수집기별 같은 내용 재전송만 합치고 다른 수집기의 최초 수신은 별도 revision으로 남긴다. 로컬 `first_seen_at`과 중앙 projection `updated_at`은 NAS 수신시각으로 사용하지 않는다. 본문·AI 결과와 실패도 새 revision이며 이전 결과를 수정하지 않는다.
16. 저장 조건식의 D 또는 상승률 15% 아래 하락은 cohort 이탈 신호로만 남기고 편입일과 다음 실제 관측 KRX 세션 종료 전에는 수집 대상을 제거하지 않는다. 주말·휴일을 날짜 계산으로 거래 세션으로 만들지 않는다.
16. 공급계약 규칙 사건은 정확한 기사/본문 revision을 입력으로 쓰고 서버가 수락한 `available_at` 이후에만 알려진 것으로 본다. 정정·계약 진전·해지·부인은 기존 사건의 새 revision일 수 있으나 과거 certainty나 article membership을 고치지 않는다. 관계 근거가 약한 사건은 별도 ID와 `possible_related`로 남긴다.
17. N3 query_set에서 게시시각은 source cursor의 정렬 기준일 뿐 NAS 가용시각이 아니다. page 관측·global article revision·target relation은 서버가 받은 순서와 가용시각으로 append한다. 재시작은 저장된 page/cursor에서 이어가고 start 1000 절단과 오류 구간은 coverage/gap으로 남긴다.
18. D1 순위 revision의 신규 시각은 UTC timezone-aware 값으로 저장하고 표시·거래일 해석은 KST로 한다. 최신값·관측 메타데이터·revision은 한 트랜잭션이며 저장이 실패하면 모두 되돌린다. 화면 조회 성공은 유지하되 로그에 `recording_gap`을 남긴다. `RESEARCH_OBSERVATION_HISTORY_ENABLED=false`는 최신 projection을 유지하면서 새 revision 기록만 중단한다.
19. D3a 실시간 분봉 delta는 생성 시 operation ID를 받고, 재시도에서도 같은 ID를 유지한다. 저장소는 미처리 ID만 누적한 뒤 전체 봉·메타·revision·처리 ID를 한 트랜잭션으로 확정한다. 시간상 마감은 마지막 틱 시각으로 소급하지 않고 실제 타이머 처리시각을 `available_at`으로 쓴다. 구독을 분 중간에 시작했거나 연결 공백이 있던 봉은 마감되어도 partial이며, 체결을 보지 않은 분을 0거래 봉으로 만들지 않는다.
20. D3b 판단은 평가봉보다 먼저 끝나고 판단 cutoff까지 가용했던 같은 종목·KRX·같은 세션의 연속 complete 봉만 돌파 기준으로 쓴다. TOP 순위 체류는 명시한 관측 창에서만 계산하며 부분 목록·첫 관측·허용치를 넘는 공백은 결측이다. 필수 Factor 결측은 신규 진입을 막고 선택 Factor 결측은 사유를 남기며, 비활성 Factor는 판단 입력에 들어가지 않는다.
21. D3c bar-only Paper(내부 가상 체결) 주문은 판단시각 이후 시작하는 첫 strict 봉에서만 시가 체결을 근사한다. 이미 시작된 봉의 open으로 소급 체결하지 않는다. 비용과 슬리피지는 run별 명시 모델이며 비용이 없으면 성과 부적격이다. 기록 끝의 미체결 주문과 보유 포지션은 CENSORED로 남기고 다음 재편입 가격이나 마지막 close로 자동 청산하지 않는다.
22. C1 맥락 가설은 원본 뉴스·전일 봉·신고가·상한가·테마 관계·D4 후보 revision을 복사해 고치지 않고 `source_refs`로 참조한다. 가설의 사실과 영향 추론은 별도이며, 장중 반응은 `revision_available_at` 순서의 새 revision으로만 누적한다. 사전 가설보다 먼저 관측된 반응은 확인 근거로 쓰지 않고, 세션 만료 뒤 새 반응으로 과거 가설을 확인 상태로 바꾸지 않는다. 전 거래일은 명시 세션 predecessor만 허용한다. Yahoo 외부시장은 저장된 대표 월물의 5분/일봉 지연 자료로만 표시한다.
23. 시간 정책은 봉을 처리한 오늘 날짜가 아니라 봉의 KST 거래일을 기준으로 고른다. 2026-09-14 전에는 `krx-nxt-schedule/2026-09-13`, 이후에는 `krx-nxt-schedule/2026-09-14`를 사용한다. 기존 `krx-regular/v1` 연구와 D4 shadow는 시행일 이후 KRX 09:00~15:30 봉만 허용하므로 새 16:00~20:00 애프터 봉을 자동 소비하지 않는다. 세션 metadata가 없던 시행 전 관측은 기존 strict KRX reader의 해석을 유지하고 완료 run은 재작성하지 않는다. 명시적 무필터 진단은 `legacy-unfiltered-krx/v0`를 사용한다. NXT의 확인되지 않은 주문접수·동적 phase와 키움 mock 애프터 지원은 `UNKNOWN`/미지원으로 유지한다.
24. 2026-09-14 이후 공식 정규장 종가·담보 기준 15:30과 차트용 전체일 일봉 20:00을 별도 값으로 취급한다. 전체일 일봉은 익일부터 확정 자료로 사용하고, `정규장만` 조회·연구는 09:00~15:30 범위를 명시한다. 어떤 소비자도 필드 이름이 `daily close`라는 이유만으로 두 값을 교환하지 않는다.
25. 원시 호가 전수 저장은 계속 제외한다. NXT VI 호가 연구를 추가할 때는 실제 키움 유형/FID를 확인한 뒤 VI 발생 추적 종목의 시작부터 단일가 체결·재개 확인까지 1초 호가 snapshot만 보존한다. 예상체결가·예상체결량·총/단계별 매수·매도 잔량은 source revision과 가용시각을 가지며, 0B 체결 방향 요약이나 무체결 초로 합성하지 않는다.
26. 매매일지 자동 유형 `trade-analysis/v2`는 원 체결의 origin/canonical 계좌 scope, venue, event time과 거래일별 schedule revision을 입력 지문에 포함한다. 시행일 이후 KRX 15:20~15:30 종가 단일가만 정규장 종가 진입 후보이며 15:30~16:00 장후 시간외종가와 16:00~20:00 애프터는 별도 구간이다. 원 체결에 없는 venue와 NXT 동적 phase는 시각만으로 만들지 않고 UNKNOWN/일반 장후로 남긴다. 새 자동 revision은 기존 사용자 수동 유형·메모·묶음 override를 변경하지 않는다.

27. S5 연구는 `krx-regular/v1`, `krx-after/v1`, `krx-full-day/v1` 중 하나를 RunSpec에 고정한다. full-day도 15:30~16:00 고정가 구간을 제외하고 16:00에서 Factor 연속성과 pending 수명을 다시 시작한다. 첫 미래봉 전 공백이나 거래일 변경은 wall-clock horizon을 완료시키지 않으며, 단일가·VI 호가 근거가 없는 분봉으로 next-open·손절·목표 체결 순서를 만들지 않는다. session profile이 없던 export/run은 기존 manifest와 hash를 유지한다.

## 현재 시뮬레이션 가능 수준

- NAS가 실행되어 실제로 기록한 기간은 앱 실행·조회 여부와 무관하게 TOP20 원본 순위와 수집된 종목 봉을 시각으로 결합할 수 있다. 순위의 24시간 수집과 시장 관측시간의 종목 봉 수집 범위는 구분한다.
- 로컬 TOP20 지수의 `cohort_segments`를 사용하면 해당 1분의 전반/후반에 실제 적용된 종목군을 구분할 수 있다.
- 실제 체결이 있던 종목은 매매일지 스냅샷으로 당시 시장 맥락을 추가할 수 있다.
- D5 이후 시점은 `/api/v1/themes/history?as_of=`로 당시 서버에 가용했던 테마 전체 문서를 찾을 수 있다. 최초 기록 전이나 결과가 없는 시각에는 현재 테마를 붙이지 않고 unknown으로 둔다.
- N1 이후 시점은 `/api/v1/news/history/article|body|ai?as_of=`로 그때 서버에 가용했던 기사·본문·분석 revision을 구분할 수 있다. 게시시각만 이른 늦은 수신 기사나 나중에 계산된 AI 결과를 과거 신호에 소급해 붙이지 않는다.
- N2a 이후 시점은 `/api/v1/news/history/event|membership?as_of=`로 그때까지 기록된 공급계약 판정과 기사 소속을 재현한다. 규칙의 `UNKNOWN`과 `ai_required`는 결측/충돌의 기록이며 자동 주문 신호가 아니다.
- N3 이후 `/api/v1/news/sources?days=7`은 설정된 검색어별 raw/unique/duplicate, 본문·규칙·target, 최대 gap, truncation/error/요청 예산/job queue를 점검한다. 이는 구성된 query set의 관측 품질이며 전체 시장 뉴스 coverage가 아니다.
- D3a 이후 `/api/v1/research/observations`는 고정 TOP20 revision과 KRX 분봉 revision을 함께 고정 추출할 수 있다. `replay_krx_minute_bars`는 가상시각 이후 도착한 정정을 제외하고 당시 최신 strict 마감봉을 선택한다.
- D3b 이후 검증된 export는 `rolling_high_breakout/v1`, 선택 가능한 `rank_persistence/v1`, `krx_bar_close_breakout/v1`으로 재생할 수 있다. Snapshot·Decision·후보 사건과 논리 결과 hash는 별도 연구 DB/run manifest에 남는다. D3b 단계의 ENTER/EXIT은 전략 판단이다. D3b 자체에는 체결·손익이나 실제 주문이 없으며, 후속 D3c의 Paper 실행과 broker 모의주문은 별도 경계다.
- D3c 이후 전략 Decision은 별도 mock 실행 프로파일의 Paper(내부 가상 체결)에서 주문·체결·현금·포지션·mark로 이어진다. 같은 봉에서 손절과 목표가가 모두 닿으면 보수적 손절 결과와 낙관 범위를 함께 남긴다. 1/3/5/10분 label은 성숙·공백·구간 종료를 구분하고, 현재 export에 1초 입력이 없으므로 T+1초는 UNSUPPORTED다. 이 결과는 실제 계좌 체결이나 매매일지 원본이 아니다.
- D7a `chronological_holdout/v1`은 같은 고정 export를 TRAIN→VALIDATION→OOS 순서로만 나눈다. fold 간 gap과 끝 purge 구간을 명시하고, 진입·청산 또는 사건 결과 지평이 purge 경계를 넘는 표본은 `PURGED` 사유로 제외한다. 최초 fold 이전 warmup 자료가 부족하면 보고 적격성이 없으며, final OOS는 접근 시각과 이유가 RunSpec에 기록되기 전까지 수치가 없는 `SEALED` 상태다. v1 fold 상태 정책은 `continuous_state_and_cash/v1`, 기간말 포지션은 `censor_open_position/v1` 하나만 지원한다.
- D7b 첫 비교는 strategy·비용·체결·fold가 같은 두 run에서 `rank_persistence_filter` 하나만 변경한다. 필터 없음 기준선에만 남은 candidate의 순손실은 회피 손실, 순이익은 놓친 이익으로 표시하며 총손익 차이와 별도로 보존한다. candidate identity가 없는 거래나 fold purge 경계를 넘는 거래는 이 귀속 계산에 넣지 않는다. 현재 export/체결 모델은 VI 주문 가능 여부와 부분체결을 재현하지 못하므로 보고서 limitation에 명시한다.
- C1 이후 당시 가용한 뉴스·테마 관계·전일 확정 사실과 D4 발견을 같은 종목·사건 가설로 만들고, 수급 확인·대장 확인·무반응·만료·기각을 응답 근거와 함께 `research.sqlite3` v5에서 as-of 재생할 수 있다. 현재 가설 생산은 순수 변환/연구 원장이며 기존 NAS shadow 알림이나 자동 진입 조건을 바꾸지 않는다.
- H2 이후 D5 테마 revision과 H1 종목·시장별 1초 가격·거래대금의 연속성이 `COMPLETE`인 구간에서만 대장·가격 이탈/재도달·대금 둔화/재가속을 계산한다. 진입 근거는 당시 대장과 입력 참조를 고정해 `research.sqlite3` v6에 보존한다. 공백은 0으로 채우지 않고 `UNKNOWN`으로 남기며, 원시 호가가 없으므로 `LOCKED/BROKEN/RELOCKED`를 추정하지 않는다. 네 대응 정책은 주문 권한 없는 비교 결과다.
- CR3는 독립 개발 구간의 탐색·순차 검증, 종목 그룹 분할, 명시 최종평가·실패 복구·최종 결과 노출 원장을 구현했다. CR4는 등록 Family와 허용값 안에서의 단일 파라미터 가설·campaign 예약·개발 근거 기반 후속 가설 및 설정 UI를 구현했다. 자동 final 실행이나 임의 전략 생성·실주문 권한을 뜻하지 않는다.
- A5는 계좌별 실행 원장 읽기·일지 projection·체결 출처 대조·실제 비용 포함 FeedbackEvidence·기계 복기·개선안·새 전략 버전의 CR3 개발 재검증 queue를 구현했다. 사용자 일지 원본·수동 복기를 덮지 않으며 등록·채택·재검증만으로 주문을 켜지 않는다. 실제 제한 모의운용과 24시간 전체 규모 검증은 별도 남은 작업이다.
- V1 대조 로그는 NAS와 이 PC의 직접 연결이 받은 같은 키움 자료의 전달·변환·저장 품질 기록이다. 공유 불변 source ref가 없는 변동 응답과 공급자 ID 없는 동일초 복수 사건은 값 불일치로 확정하지 않고 `NOT_COMPARABLE`로 남긴다. `MISSING`에는 시간 만료, 비교 queue skip, pending eviction을 구분하며 수신 간격과 source clock 차이를 따로 기록한다. 이 로그는 원본 시장자료나 연구 dataset을 대체하지 않는다.

## 다음 데이터 확보 우선순위

기존 조회·재생·Paper·연구 엔진은 이미 구현된 범위에서 재사용한다. 다음 우선순위는 사용자 방향에 따른 입력 자료 확보다. 아래는 수집·검증 계획이며 확보 완료를 뜻하지 않는다.

1. 기존 `kiwoom_history_backfill/data/kiwoom_history.sqlite3`의 주도 후보·일봉과 저장 범위를 읽기 전용으로 확인해 대상 종목과 부족한 구간을 정한다. 종목 종류·상장 상태·기업행동 등 전략에 필요한 기준정보의 시점 근거도 별도로 확인한다.
2. 대신증권 API의 최근 2년 1분봉과 과거 5년 범위 5분봉을 확보하는 계획을 먼저 구체화한다. 두 기간은 중복되므로 전체 범위를 7년으로 합치지 않는다. 원본 봉 단위·출처·거래소·세션·수정주가 여부·수집시각을 보존하고 실제 반환 범위와 결측을 검증한다.
3. 네이버 검색 API와 구분해 네이버 증권 사이트의 뉴스 확보 경로를 설계한다. 기사 원본·게시시각·최초 관측시각·종목 연결 근거와 중복/정정 이력을 남기며 과거 게시시각을 당시 수집기의 가용시각으로 소급하지 않는다.
4. 확보된 자료의 coverage와 시점 근거를 확인한 뒤 기존 연구 입력으로 연결한다. 최근 과거봉을 현재 내려받았다는 사실만으로 과거 실시간 순위·테마·뉴스·1초 흐름까지 복원됐다고 보지 않는다. 로컬 직접 연결의 순위 이력 저장 선택과 coverage 표시의 잔여 범위는 남은 작업에서 관리한다.

새 TR을 무제한 호출하지 않고 먼저 기존 저장 자료와 결측 범위를 계산한다. 미래 수집기는 현재 유효한 공급자 요청 제약과 실제 반환 범위를 확인하며 구현한다.

## D4 shadow 시간·보존 계약

NAS shadow는 중앙 관측 `accepted_sequence`를 앞으로만 소비한다. 최초 활성화는 최근 완료봉·TOP20을 워밍업 입력으로만 복구하고 과거 후보를 소급 생성하지 않는다. 이후 Decision/CandidateEvent는 불변 원장에 기록하며 ENTER 제안으로 open 포지션·체결·주문을 만들지 않는다. 당시 TOP20 관측이 명시한 최대 나이를 넘거나 없으면 신규 후보를 막고 quality에 stale/missing을 남긴다. 앱의 첫 목록 동기화와 각 PC 소비 cursor는 NAS 원장과 분리한다.

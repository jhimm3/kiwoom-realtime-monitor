# PC 완성 과거뉴스 DB 배치 검토

> **현재 이관 판단(2026-10-13): v2 로컬 구현·관련 검사 완료, 전체 게시 준비는 미완료.** 두 scope·다중 target·원본/요청 실패 기록·봉인·읽기 API·중단 복사 재개의 구현과 작은 DB 검사를 마쳤다. 고정 준비본 생성에 착수했으며 최신 NAS seed 추출을 보완 중이다. 전체 archive 생성, NAS 파일 복사와 서버 활성화는 실행하지 않았다. [현재 실행 상태](#v2-구현과-실행-상태-2026-10-13)를 정본으로 보고 9월 기록은 당시 범위로 읽는다.

> 문서 역할 정리(2026-10-11): 현행 archive의 출처·ID·봉인·조회·기존 자료 보존 계약을 유지한다. 코드에는 `historical_news_archive.py`, `historical_news_archive_routes.py`, `historical_news_archive_dialog.py` 연결이 있다. 아래 단계별 ‘클라이언트 미연결’ 등은 작성 당시 상태로 읽는다. 전체 실자료 봉인/게시/검증 완료 여부는 [현재 상태](CURRENT_STATUS.md)와 [OPEN_ITEMS](OPEN_ITEMS.md)를 따르며 이번에 새로 확인하지 않았다.

2026-09-29 운영 범위 예외: 사용자의 명시적 요청에 따라 9월 27일 seed에 기록된 `PENDING` job_key와 정확히 같은 운영 PostgreSQL의 과거 BODY/RULE 작업 26,982행만 별도 백업 없이 삭제했다. PC seed의 당시 상태 증거와 기사·본문·완료 job은 유지한다. 아래의 “기존 자료 삭제 미완료”는 나머지 이관 기사·종속 자료의 정리 상태를 뜻하며, 이 26,982개 작업이 운영 큐에 계속 남아 있다는 뜻이 아니다.

2026-09-27. 상태: **단발성 archive의 설계 결정, 기존 NAS 뉴스 seed 추출, 검색기사 일부의 PC ARTICLE/BODY/assessment/RULE 검증과 사건 최종화·검색 목록 projection 코드까지 진행. 봉인된 파일만 조회하는 검색 페이지·정확한 ID 상세 reader와 별도 인증 API를 로컬 구현했으나 클라이언트·실자료에는 연결하지 않았다. 전체 최종 archive·실자료 사건 검증·NAS 게시·기존 자료 삭제는 미완료.** 아래 결정은 구현 계약이며 최종 archive 기능이 이미 존재한다는 뜻이 아니다.

### A단계 진행 증거 (2026-09-27)

- `scripts/export_historical_news_seed.py`가 운영 PostgreSQL을 읽기 전용 repeatable-read transaction으로 읽어 기존 기사 ID와 연결된 뉴스 행을 seed SQLite로 추출했다. 별도 `kiwoom_monitor_diagnostic_test` PostgreSQL 검사 1건과 단위검사 4건이 통과했다. 이 파일은 완성 archive가 아니다.
- NAS 추출본을 PC `data/historical_collection/nas-news-seed-20260927.sqlite3`로 가져왔고 양쪽 SHA-256이 `55f7ffc2c9533ae3abb3be2d19cba7245e2731a2958e58a15f32c64cde3981ba`로 일치했다. SQLite `PRAGMA integrity_check`는 `ok`였다. PostgreSQL snapshot 시작은 `2026-09-26 19:09:31.870263+00`이며, 총 338,470행/785,879,040바이트다.
- 기사 revision은 68,723행이며 그중 historical scope 소유가 55,053행, 참조 유지를 위해 포함한 `support_only`가 13,670행이다. 연결된 body 41,789행, AI 233행, event 5,258행, membership 5,258행, target 27,710행, source observation 44,864행, source run 7,580행, job 110,982행, 관련 document 26,073행이다. job 중 26,982행은 `PENDING`이지만 archive 실행 큐로 옮기지 않는다.
- PC 준비본의 `ready` 330,261건과 `failed` 37건은 **서로 다른 입력 수량**이다. 실패 37건은 준비본에 모두 `기사 본문과 목록 요약을 가져오지 못했습니다`로 기록되어 있다. 원본 확보 가능 여부와 raw DB 전체 범위는 아직 대조하지 않았다. 9월 25일 고정본의 입력 manifest와 실패 사유 원장은 미완성 archive에 기록했지만, B단계 최종화·조회 DB는 아직 미완료다.
- 기존 ID의 PC 물질화는 `scripts/materialize_historical_news_seed.py`로 별도 `news-archive-20260927.building.sqlite3`에 진행했다. 338,470행과 PG seed SHA를 보존했고, 뉴스 revision 8개 테이블의 행 수 및 `accepted_sequence` 최소·최대가 seed와 일치했다. SQLite 전체 무결성 검사와 ID/순서 보존 회귀 2건도 통과했다. `scripts/stage_prepared_historical_news_archive.py`로 준비본 330,261 ready와 37 failed를 입력 hash·실패 사유와 함께 원장화했고, `scripts/finalize_prepared_historical_article_bodies.py`로 앞의 2,446 ready 기사/BODY만 원자적으로 처리했다. 기존 seed의 기사·본문 ID 각 1개를 재사용했고 NAS job은 추가하지 않았다. 처리된 BODY 출처는 원본 hash+획득시각 확인 1,968, seed와 동일한 BODY 1, 요약 477건이다. `scripts/finalize_prepared_historical_assessments.py`는 이 2,446건의 평가 문서와 RULE 입력을 원자적으로 기록했다. 사건 후보 265건, 비후보 2,181건이며 기존 seed 평가 1건은 동일 내용이라 보존했다. 미완성 DB 무결성은 `ok`; seed의 job 110,982행과 사건 5,258행은 증가하지 않았다. 나머지 ready 327,815건, 사건/조회 projection, 봉인, NAS reader 연결은 남아 있다. `archive_build_manifest.build_state=building`이므로 이 DB를 NAS에 게시해서는 안 된다.
- PC 원본 `source_pages`는 216,523행, `news_articles`는 957,864행이다. 준비본에 연결되는 원본 관측은 330,360행/고유 관측 키 64,827개다. 원본 페이지 키에는 단일 `date`와 기간 `from`~`to`가 섞여 있고 검색어에 이스케이프되지 않은 `&`가 포함될 수 있다. 이를 반영한 `scripts/select_historical_news_page_keys.py`가 원본 rowid 경계에서 실제 페이지 요청키 76,007개/페이지 버전 82,163행을 선택했으며 필요한 관측 키 누락은 0개였다. 날짜 하나로 잘못 재구성한 첫 선택본은 폐기했다. 이 선택 결과는 파싱 입력 범위일 뿐, 페이지 내용·기사 변경 이력의 검증 완료를 뜻하지 않는다.
- 사용자가 정한 기본 게시 범위는 **수집 종료 후 전체 자료**다. 필요하면 그 전에 한 시점의 입력을 고정해 별도 임시 세대로 게시할 수 있으나, 뒤의 최종 세대가 기존 파일을 덮어쓰지는 않는다. 9월 25일 검색 준비본 330,261 ready/37 failed는 중간 검증 범위다. 9월 27일 04:50 KST 전후 읽기 전용 확인에서 계속 갱신되는 검색 준비본은 556,695 ready/71 failed, 시황 준비본은 424,890 ready/2 failed였다. 이 수량은 최종 수집량이나 한 시점의 합동 snapshot이 아니다.
- 선택한 82,163개 원본 페이지 버전은 PC에서 rowid 경계와 원문 hash를 확인하며 25개씩 재개 가능한 별도 파일로 파싱한다. 입력 준비본·선택 파일의 hash가 재개 시 달라지면 혼합 처리를 거부한다. 파일의 파싱 완료 여부와 준비본 기사 식별자의 원본 페이지 연결은 `scripts/audit_historical_news_page_versions.py`로 별도 검사한다. 파싱 산출물은 최종 archive도 NAS 배포본도 아니다.
- 임시 세대 또는 최종 세대의 준비 입력은 `scripts/snapshot_prepared_historical_news.py`로 PC에서 SQLite backup API를 사용해 검색·시황 각각 새 파일로 고정한다. 완료된 사본의 전체 무결성, scope, 상태별 건수, 파일 hash와 시각을 manifest에 기록하고 기존 파일을 덮어쓰지 않는다. 소형 WAL 원본의 사본 보존·원본 후속 변경 분리·scope 불일치 검사 2건이 통과했다. 현재 계속 갱신되는 검색·시황 DB에 대해 이 새 스냅샷 도구로 실자료 고정본을 아직 만들지 않았다.
- 9월 25일 검색 준비본에 해당하는 페이지 82,163개를 PC에서 파싱한 결과, 기사 관측 813,616건과 고유 `(종목, 기사 identity)` 575,271개를 얻었고 페이지 오류는 0건이었다. 준비본 330,261 ready/37 failed의 원본 페이지 관측 연결 누락은 각각 0건이다. 같은 요청키의 응답 hash가 다른 경우는 4,295개였지만, 위치만 제외한 파싱 기사 항목이 달라진 identity는 108개다. 이 108개도 기사 revision **후보**일 뿐이며 기존 저장 content hash, 발행시각·본문 근거를 대조하기 전에는 실제 revision 건수로 확정하지 않는다. 4,295개 원응답 변화를 4,295개 기사 수정으로 간주하지 않는다. PC 파싱 DB 크기는 1,302,069,248바이트, SHA-256은 `c5a428a6dc87eb485845e78dc5fbe9a2a2539dc0c7735fc5f7c630fcd782bb87`이다. 파싱 DB는 여전히 `parsed_unverified`이며 봉인·NAS 게시 대상이 아니다.
- `scripts/audit_prepared_historical_body_provenance.py`로 위 고정 검색 준비본 전체를 읽기 전용 대조했다. 준비본 SHA-256은 `b01f8680fce196a856095ae573970eb51b3e1190f010e7d664c06b08b078e765`, 원본 본문 snapshot 행 경계는 rowid 232,597이다. ready 330,261건 중 fulltext 281,502건, summary_only 48,759건이다. fulltext 중 133,357건은 PC 원본 snapshot과 본문 SHA-256과 획득시각이 모두 일치해 `article-text-v7` 근거가 있고, 148,145건은 같은 기사 키의 원본 snapshot이 없다. hash만 일치하고 시각이 다른 경우와 snapshot은 있으나 본문이 불일치한 경우는 각 0건이다. 원본 snapshot 부재는 준비 본문 오류를 뜻하지 않으며, 해당 본문은 별도 출처·획득시각과 `version_unverified` 상태로 보존한다. 미완성 archive의 앞 2,446건도 잘못 `raw_hash_verified`로 표기한 행이 0건임을 읽기 전용으로 확인했다. 이는 9월 25일 고정본의 전수 결과이지 계속 갱신되는 원본 전체를 hash한 결과는 아니다.
- 같은 준비본의 RULE 결과 330,261개를 읽기 전용으로 확인했다. 37,816개가 `supply-contract-rule-v2` 결과이고, 292,445개는 `rule_result=null`이며 다른 결과 형식·버전이나 기사 대상과 다른 target은 없었다. 이 확인은 준비 결과의 형식·대상 감사이고, 사건 중복·novelty·기존 seed 연결을 확정한 것은 아니다. 사건 생성은 seed의 기존 사건·membership ID와 처리 순서를 함께 검증한 뒤 수행한다.
- 첫 2,446건의 사건 후보 265건을 기존 seed와 `article_revision_id`·`body_revision_id`·rule version·input hash·target으로 읽기 전용 대조했을 때 정확히 동일한 사건 revision은 0건이었다. 새 사건을 단순히 seed 뒤에 삽입하면 `_save_sqlite_news_event`가 기존 seed의 최신 사건과 membership을 기준으로 novelty·`revision_of`를 결정한다. 기존 seed ID/순서 보존과 기사 발행 순서 기반 사건 해석의 차이는 아래 B단계 계약으로 정했다. 원본 미완성 archive의 265건은 여전히 RULE 입력으로만 보존했고 사건을 생성하지 않았다.
- `scripts/verify_prepared_historical_news_rules.py`는 준비본과 PC archive의 기사·BODY·출처·대상·입력 hash를 확인하고 기존 RULE 계산을 오프라인으로 재실행해 준비 결과와 비교한다. 원본에 쓰지 않은 복사본에서 앞의 2,446건은 모두 일치했고 불일치는 0건이었다. 이후 추가한 출처 fingerprint 경계는 소형 회귀에서 통과했으며, 원본 미완성 archive의 2,446건 모두 BODY 출처 행과 입력 hash가 일치함을 읽기 전용으로 확인했다. 원본 미완성 archive에 검증 원장을 쓰지 않았다.
- `scripts/finalize_prepared_historical_news_events.py`는 기사 부모 revision을 앞에 두는 발행시각 순서를 고정하고, 기존 seed의 정확한 사건을 재사용하거나 새 사건·membership과 해결 원장을 한 transaction에 기록한다. 같은 identity나 event key가 여러 사건 그룹에 걸리거나 두 근거가 서로 다른 사건을 가리키면 후보 ID와 함께 충돌을 기록하고 중단한다. 단일 실행 lease와 재시작 원장을 둔다. 관련 ARTICLE/BODY·assessment·RULE·사건 회귀 17건이 통과했다. **전체 준비본의 나머지 327,815건이 미처리라 실자료 사건 최종화는 실행되지 않았고**, 조회 projection·봉인·NAS reader도 아직 없다.

2026-09-27 NAS 이관 원장 읽기 전용 확인: `imported_prepared_news`에는 `historical_backfill` 23,546건과 `historical_market_backfill` 103건이 기록돼 있다. 330,261건은 PC 준비 스냅샷의 ready 건수이며 NAS 적재 건수가 아니다. 원장은 입력별 성공 표시이므로 실제 운영 PostgreSQL 행 수나 완전한 기사/본문/평가/사건 상태를 뜻하지 않는다. 기존 status JSON의 `running` 표시는 갱신 시각이 오래되어 현재 프로세스 실행 증거로 쓰지 않는다.

## 결론과 범위

이번 단발성 과거뉴스 이관은 PC에서 조회 가능한 최종 SQLite DB를 완성하고, NAS 로컬 볼륨에 복사한 뒤 읽기 전용으로 제공하는 방식을 권고한다. 기존 PostgreSQL에 기사별로 다시 적재하는 방식은 이 요구의 최종안으로 삼지 않는다.

여기서 NAS 무처리는 **이관된 과거뉴스에 대한 수집·본문 추출·규칙/사건 재계산·job 생성·대량 INSERT·인덱스 생성·백그라운드 보완 없음**을 뜻한다. 파일 복사·게시 시 무결성 확인·사용자가 요청한 조회와 응답에는 CPU/I/O가 필요하다. 기존 운영 서버의 다른 작업과 로그까지 0이 된다는 뜻은 아니다.

DB를 만드는 작업은 단발성이지만, 그 파일을 기존 뉴스 API로 읽는 기능은 보관 자료를 사용하는 동안 유지해야 한다. NAS의 실시간 뉴스 수집이 현재 꺼져 있다는 사용자 설명을 기준으로 하며 자동으로 켜지 않는다.

## 현재 코드에서 확인한 차이

### 2026-09-27 PC 원본 대조에서 확인한 구현 선결조건

읽기 전용으로 확인한 `data/historical_collection/prepared-news-20260925T2215.sqlite3`에는 `ready` 330,261건과 `failed` 37건이 있다. 이 파일은 `(scope,stock_code,identity)`를 기본키로 사용하고 같은 키의 `article_json`, `body_json`, `rules_json`을 UPSERT한다. 따라서 이 파일만으로 과거 입력별 기사·본문 수정 이력이나 이전 관측시각을 복원할 수 없다. `failed` 37건도 완료로 간주하거나 조용히 제외한 채 전체 완성 manifest를 만들 수 없다. 단, 확보 실패를 명시적으로 보존한 패키지의 게시와 모든 기사 처리 성공은 구별한다.

원본 `data/historical_intelligence.sqlite3`는 `source_pages`, `news_articles`, `news_article_body_snapshots`, `news_article_fetch_attempts` 등을 보존한다. `data/naver_stock_market_news.sqlite3`도 `market_news_pages`와 현재 `market_news_articles`를 별도로 보존한다. 검색 원본의 `source_pages` 첫 1,000행 표본에는 요청 키 950개가 있었고, 같은 요청 키에 서로 다른 응답 hash가 있는 사례 50개가 관측됐다. 이는 **원응답이 달라진 표본**이지 기사 내용이 50개 변경됐다는 증거는 아니다. 원본의 전체 변경 기사·본문 수는 아직 계측하지 않았다. 원본은 9월 27일에도 갱신되고 있으므로 입력별 일관된 스냅샷과 포함 기준 시점을 기록한다. 최종 세대는 기본적으로 수집기 종료 뒤 고정하고, 요청이 있으면 수집 중 시점의 임시 세대도 독립적으로 고정한다. 서로 다른 DB의 캡처 시각을 같은 순간이라고 주장하지 않는다.

기존 `SQLiteQueryStore`의 기사·본문·RULE·사건 저장 함수를 PC 최종화에 재사용할 수 있지만, 새로 발급하는 article/body/event ID로 기존 NAS ID를 대체하면 안 된다. 기존 이관분은 ID와 이력을 그대로 seed하고 새 입력만 추가한다. 준비 결과는 입력 hash·대상·처리 버전이 검증될 때 재사용한다. 현 준비 파일의 최신 결과만으로 새 DB를 만들고 이를 `complete`로 게시하는 구현은 이력 보존 조건에 맞지 않는다.

기존 ID 보존, 원응답과 기사 변경의 구별, 확보 실패 표시의 결정은 아래 구현 계약에 정리했다. 운영 import 재개로 대체하지 않으며 최종 archive 게시와 기존 NAS 뉴스 삭제는 정합성 검증 뒤의 별도 단계다.

| 경계 | 확인한 구현 | 완성 DB에 필요한 변경 |
| --- | --- | --- |
| `scripts/preprocess_historical_news_locally.py:_prepare`, `_open_results` | PC에서 BODY/RULE 결과를 계산하지만 `prepared_news`에 article/body/rules JSON을 저장 | 현재 파일은 중간 결과다. 조회 테이블·관계·인덱스까지 PC에서 생성해야 한다. |
| `scripts/import_prepared_historical_news_to_nas.py:_prepare_articles`, `_complete_batch` | NAS 기존 writer 및 job 완료 경로 호출 | 완성 DB 방식에서는 이 경로로 전량 이관하지 않는다. |
| `central_server/database.py:_append_postgres_news_articles`, `_save_postgres_news_body` | 기사/body revision 및 후속 BODY/RULE job 작성 | 과거 archive에서 NAS job을 만들지 않는다. |
| 같은 파일 `_complete_external_news_job`, `_save_postgres_news_event` | 해시·소유권 확인, assessment 문서, 사건 연결·novelty·membership/revision 작성 | 필요한 최종 결과와 기존 ID 연결을 PC에서 완성한다. 검증 가능한 BODY/RULE 결과는 재사용하고 불일치·버전 불명은 PC에서 처리한다. |
| `central_server/news_service.py:_load_cached`, `_merge_confirmed`, `_stored_item` | store 결과 병합; 저장된 판정이 없는 일부 legacy 자료는 조회 중 `assess_stock_news` 실행 | archive는 완성 판정을 반환한다. 누락은 명시적 미완료 상태로 표시하며 NAS 자동 재계산으로 숨기지 않는다. |
| `central_server/app.py`의 stored-page/history/market-feed | 같은 store의 기사·본문·평가·이력 조회 | 각 API가 archive를 읽도록 연결하고 ID 조회 및 페이지 계약을 보존한다. |
| `SQLiteQueryStore.initialize`, `_connect`, `_connection` | migration, WAL 설정, commit을 수행하는 쓰기 가능한 store | 이 store를 그대로 archive 파일에 연결하지 않는다. 좁은 읽기 전용 연결 경계가 필요하다. |

운영 25건 표본은 article 단계 누적 4,055ms, completion 단계 32,235ms였다. 이 값은 기존 NAS 완료 경로가 여전히 무겁다는 증거이며, 그 내부 CPU/SQL/COMMIT 기여를 분리한 결과는 아니다. 앞선 PostgreSQL 통합 3건 통과는 기존 importer의 정합성 검증이며 새 archive 설계 검증이 아니다.

PC 최종화 구현 전 추가 확인: `_append_sqlite_news_articles`는 새 UUID/`available_at`과 BODY `PENDING` job을 만들고, `_save_sqlite_news_body`는 RULE job을 예약한다. `_complete_external_news_job`은 `RUNNING` 소유권과 정확한 BODY/RULE 처리 버전을 요구하며 평가 문서와 사건을 저장한다. `_save_sqlite_news_event`의 novelty·membership은 앞서 저장된 사건과 기사 이력에 의존한다. 따라서 준비본을 단순 UPSERT하거나 job helper를 순서 없이 병렬 호출하면 기존 ID, 사건 연결 또는 역사적 가용 시각이 달라질 수 있다. 기존 seed의 `PENDING` 26,982행은 운영 실행 큐가 아니라 당시 상태 증거로 다룬다. 9월 25일 준비본 2,000건 표본에서 BODY는 fulltext 1,763건/summary_only 237건이었고 RULE `rule_result`는 dict 231건(`supply-contract-rule-v2`), null 1,769건이었다. 준비본 BODY 결과 JSON 자체에는 extractor 버전이 없으므로 현재 버전이라고 추정하지 않는다. PC 최종화는 버전 확인 또는 재계산, 결정적 기사/사건 처리 순서, 기사별 원자적 재개 원장이 필요하다.

검색 준비본을 무작위 1,000건 뽑아 원본 `news_article_body_snapshots`와 읽기 전용 대조했을 때 420건은 본문 텍스트가 같고 원본 extractor 버전 `article-text-v7`이 확인됐다. 580건은 해당 기사 키의 원본 본문 snapshot 자체가 없었다. 앞쪽 2,000건 연속 표본은 전부 원본 snapshot이 없어 표본 위치에 따른 편차도 컸다. 이는 **전체 비율 추정이나 본문 오류 확정이 아니라**, 준비 결과만 보고 모든 BODY의 처리 버전을 입증할 수 없다는 직접 근거다. 대응하는 원본이 없는 준비 본문은 출처/획득시각을 기록해 별도 상태로 보존하고, 검증되지 않은 버전으로 job 완료를 꾸미지 않는다.

## 대안 비교

| 방식 | NAS에서 남는 이관 작업 | 판단 |
| --- | --- | --- |
| PC 준비 결과 → 기존 PG 일괄 적재 | INSERT, 인덱스 갱신, WAL/COMMIT, 관계 연결 | 현재 구조 변경은 작지만 사용자 목표와 차이가 남는다. |
| PC에서 별도 PostgreSQL 클러스터 완성 → NAS 이전 | 호환 환경 준비와 별도 DB 서버 운영 | 가능성을 배제하지 않지만 단발성 뉴스 보관에는 운영 비용이 크다. 기존 클러스터에 뉴스 테이블 파일만 복사할 수는 없다. |
| PC에서 완성 SQLite archive → NAS 읽기 전용 조회 | 파일 복사·게시 확인과 요청 시 조회 | **권고안.** 기존 PG의 시장·계좌 데이터와 물리적으로 분리하고 대량 재적재를 없앤다. |

PostgreSQL 물리 파일의 개별 테이블/DB 이식은 클러스터 트랜잭션 상태 때문에 지원되는 복원 방식이 아니다: [PG17 파일 백업 문서](https://www.postgresql.org/docs/17/backup-file.html).

## PC가 완성할 범위

1. 기사 identity와 모든 보존 대상 content revision, 본문 및 원문 출처, 게시/수집/관측 시각.
2. 종목별 assessment, 핵심 문장, 기존 RULE 결과와 처리 버전. 유효한 준비 결과를 재사용한다.
3. 사건 그룹, event/membership revision, `revision_of` 관계, 중복 판정 결과. 같은 URL의 내용 변경 이력을 삭제하지 않는다.
4. 종목별 목록·시황·상세·이력 조회에 필요한 projection과 정렬/검색 인덱스, DB 통계.
5. 실패/접속불가/본문미확보 자료의 사유와 상태. 빈 결과를 완료된 본문이나 정상 판정으로 꾸미지 않는다.
6. DB 버전, dataset ID, 입력 스냅샷, 처리 버전, 건수, 해시, 포함 범위 및 기존 NAS ID 대응 정보가 있는 manifest.

현재 준비 파일이 원래 source의 모든 과거 revision을 보존한다고 단정하지 않는다. 최종화 전 원본 PC DB와 대조하고 필요한 이력은 원본에서 함께 읽는다. PC 규칙 결과와 사건 연결 정책은 기존 정책을 재사용하며 별도 계산식을 복제하지 않는다.

## 기존 NAS 자료와의 관계

- 새 archive 완성과 검증 전에는 기존 PG 뉴스와 시장·계좌·주문 데이터를 보존한다. 전체 운영 DB 파일을 덮어쓰지 않는다.
- PC 최종화 전에 관련 뉴스의 기존 ID·이력·참조에 필요한 범위만 일관된 읽기 전용 스냅샷으로 얻는다. 전체 운영 DB 덤프를 전제하지 않는다.
- 기존 ID는 그대로 유지한다. 동일 내용이라도 기존에 서로 다른 ID로 참조된 행을 임의로 합치지 않는다. 같은 ID에 다른 payload가 있으면 충돌로 중단한다. 기존 AI/일지의 article/body/event 참조가 끊어지면 게시 불가다.
- archive가 포함하는 기존 자료의 정확한 coverage를 고정한다. 날짜만으로 PG 결과를 일괄 제외하지 않는다. 중복 제거는 content revision과 target/버전을 구별하고, 화면의 최신 기사 선택은 기존 정책을 따른다.
- 첫 구현의 목록은 기존 현재 뉴스와 `과거 수집 자료`를 구분해 제공한다. archive 목록은 고정 dataset의 keyset 페이지로 읽는다. 기존 PG 목록의 offset 계약은 유지한다. PG와 archive에 같은 offset을 적용해 합치거나 일부 후보만 합쳐 전체 목록이라고 표시하지 않는다. 하나의 통합 목록은 별도 후속 요구로 남긴다. 상세의 기존 ID 해석은 두 저장소를 지원한다.
- historical event 결과는 명시한 입력 스냅샷과 순서에 대해 PC에서 확정한다. 기존 사건 ID 연결도 PC에서 만든다. 게시 이후 새로운 live 기사와의 관계를 자동으로 소급 재구성하는 일은 이 단발성 이관 범위 밖이다. 기존 자료의 연결을 조용히 끊는 것으로 대체해서는 안 된다.
- 추후 수동 AI나 수정 기능이 필요하면 원 archive를 고치지 않고 기존 가변 저장소에 archive ID를 참조하는 결과를 기록한다. 이번 배치로 자동 AI/RULE 작업을 생성하지 않는다.
- 연구용 `available_at`은 게시일로 소급 조작하지 않는다. 오늘 재구성한 결과는 역사적 당시 관측과 구분한다. `accepted_sequence` 등 서로 다른 저장소의 번호를 하나의 전역 순서로 간주하지 않는다.

사용자가 이미 적재된 과거뉴스도 PC에서 다시 완성한 뒤 NAS에서 정리하는 방향을 요청했다. 정리 범위는 원장 23,649건을 그대로 DELETE한다는 뜻으로 확정하지 않는다. 기사 revision, body, jobs, assessment 문서, event/membership, AI·일지 참조를 실제 DB에서 서로 대조해 **archive로 대체된 이관 전용 행**만 식별해야 한다. 일반 실시간 뉴스와 다른 collection의 문서는 포함하지 않는다. 새 과거 목록과 기존 ID 상세의 결과 대조, 참조 무결성, 롤백 경로, 보존 사본을 확인한 뒤에야 별도 정리 단계에서 제거한다. 조건은 `identity`나 날짜만이 아니라 scope·행 PK·payload checksum·종속 관계의 명시적 목록이다. 공유 사건이나 live 행이 참조하는 지원 자료는 정리 대상에서 제외한다. 캡처 후 달라진 행은 삭제하지 않으며 정리 직전 현재 참조를 다시 검사한다.

## NAS 게시와 실행 경계

PC에서 무결성 검사와 finalization을 마친 뒤 SQLite backup API 또는 `VACUUM INTO`로 단독 사용 가능한 파일을 만든다. 작업 중인 `.sqlite3`만 복사해 WAL 내용을 빠뜨리지 않는다. [SQLite backup 문서](https://www.sqlite.org/backup.html).

NAS에는 새 버전 파일을 임시 이름으로 복사하고 해시·manifest를 확인한 다음 활성 참조를 전환한다. 큰 해시 검사는 게시 때 한 번 수행하며 시작마다 전체 데이터 검사를 반복하지 않는다. 이전 파일은 복구용으로 보존하고 열린 요청은 이전 세대를 끝까지 사용한다. 초기 구현은 복잡한 hot swap보다 명시적인 게시/재시작도 허용한다.

파일은 NAS 로컬 볼륨에서 `mode=ro`로 열고, 내용이 절대 바뀌지 않는 버전 파일에만 `immutable=1`을 사용한다. 열린 immutable 파일 자체를 덮어쓰지 않는다. [SQLite URI 문서](https://sqlite.org/uri.html).

NAS 시작 시 archive migration·인덱스 생성·전체 순회·job backfill을 수행하지 않는다. 형식 불일치나 미완료 패키지는 자동 수리하지 않고 해당 archive를 사용 불가로 표시한다. archive가 없을 때 기존 PG 조회 동작은 유지한다.

## 구현 전후 확인 항목

- 기존 기사/본문/판정/사건/일지 참조와 archive 결과를 고정 입력으로 대조한다. 단순 건수 비교로 끝내지 않는다.
- 같은 기사 다종목, 같은 URL 다른 내용, 중복 입력, 판정 누락, source 간 ID 충돌, 기존 AI 결과 참조를 검증한다.
- 전체 목록/상세/history/market-feed와 연구·일지의 실제 소비 경로를 연결한다. 현재 검토는 주요 뉴스 API와 일지의 revision/available_at 요구를 확인했으며, 모든 연구·일지 경로의 archive 호환성이 입증된 상태는 아니다.
- 연속 페이지에 누락/중복이 없는지, as-of 조회와 revision chain이 유지되는지, 버전 교체 중 응답이 섞이지 않는지 확인한다.
- 읽기 전용 파일 권한 아래 실제 조회를 실행한다. NAS BODY/RULE 함수, job enqueue, archive 대상 PG writer를 호출하면 실패하는 테스트를 둔다. 누락 판정의 fallback 재계산도 포함한다.
- idle·조회·재시작 전후 archive 해시 불변, 새 처리 job 없음, 전체 스캔 없음, archive 유래 대량 PG 쓰기 없음으로 검증한다. 운영 PG의 다른 writer WAL을 archive 비용으로 귀속하지 않는다.
- 중단된 복사·틀린 해시·불완전 manifest는 활성화되지 않아야 한다. 이전 archive로 복귀했을 때 기존 ID 조회도 유지돼야 한다.
- 실제 자료 크기에서 조회 지연과 메모리를 측정한다. 지금은 파일 전송 시간이나 최종화 완료 시간을 확정할 근거가 없다.

## 확정한 구현 계약

### 1. 입력 고정과 기존 NAS ID 보존

- PC raw DB, prepared DB, 관련 NAS export를 각각 입력 manifest로 고정한다. 파일/추출본 hash, 스키마, 캡처 시작·끝, 포함 scope·행 범위, 파서·규칙 버전과 코드 식별자를 기록한다. 현재 330,261건 파일만 전체 수집 범위라고 가정하지 않는다.
- NAS export는 기존 historical scope와 import ledger로 seed를 식별한 뒤 기사 부모 revision, body, assessment, AI, event/membership 및 필요한 source observation의 참조를 닫힌 집합으로 수집한다. 명시한 뉴스 테이블/collection만 읽고 계좌·주문·일지 전체를 복제하지 않는다. 참조 관계를 통한 범위 확장과 건수는 보고하며 제한을 넘으면 중단하고 조용히 잘라내지 않는다.
- 하나의 읽기 전용 일관된 PostgreSQL transaction에서 선택한 행을 스트리밍한다. 전체 DB dump나 운영 테이블 수정은 하지 않는다. 종속성을 위해 포함한 일반/live 행은 `support_only`로 표시해 목록 소유권·삭제 권한을 주지 않는다.
- 기존 PK, `revision_of`, `available_at`, table별 `accepted_sequence`, 처리 버전, JSON 필드를 보존한다. 새 sequence는 해당 table의 seed 최댓값 이후에 할당한다. seed와 신규 자료를 하나의 전역 과거 순서로 오인하지 않는다.
- 기존 가변 assessment projection의 원본도 seed 증거로 보존한다. 새 규칙 결과로 이전 결과를 소실시키지 않는다. pending job은 당시 상태 증거일 뿐 NAS 실행 큐로 옮기지 않는다.

### 2. PC 최종화와 누락 자료

- `historical_backfill.parse_naver_historical_search_page` 등 기존 파서로 원응답을 읽는다. 페이지 hash 변화는 파싱 필요성을 나타낼 뿐 기사 revision 생성 조건이 아니다. 기사 식별자와 기존 content hash 의미를 기준으로 기사 내용 변화를 구별한다.
- source body snapshot의 서로 다른 본문은 보존한다. 어느 기사 revision과 대응하는지 입증할 수 없는 본문은 출처 증거로 남기고 임의의 revision chain이나 사건을 만들지 않는다. raw 응답 원본은 PC에 보존하며 약 175GB raw DB 전체의 NAS 복사를 전제하지 않는다.
- prepared BODY/RULE은 기사·본문 hash, target, 처리 버전이 일치하는 결과만 재사용한다. 현재 준비 JSON에 버전 근거가 없으면 현재 버전이라고 꾸미지 않는다. 필요한 RULE 재처리는 PC에서 저장된 본문으로 수행하고 본문 출처/추출 버전 불명도 기록한다.
- 최종화는 기본적으로 네트워크를 호출하지 않는다. 접근 불가 자료의 재수집은 수집기의 별도 명시 작업이며 NAS에 retry job을 넘기지 않는다. 본문 없음, 요약 대체, 규칙 미평가, 출처 연결 불명은 서로 다른 상태로 보존한다.
- 기존 seed 이력은 불변이다. 새로운 계산의 `available_at`은 실제 계산/확보 시각으로 저장하며 게시일로 소급하지 않는다. 입력 처리 순서와 tie-break를 고정해 재시작 시 결과가 중복되지 않게 한다.
- 기사/본문/평가/사건의 한 처리 단위와 resume 원장 갱신은 같은 PC transaction으로 묶는다. 실패하면 그 단위 전체를 rollback한다. 완료로 체크된 기존 job 때문에 변경 입력을 건너뛰지 않도록 입력 fingerprint를 검사한다.

manifest는 `build_state=building|sealed|failed`와 `coverage_state=complete|complete_with_source_failures|incomplete`를 분리한다. `sealed`는 구조적으로 검증된 파일을 뜻하며 모든 본문 확보 성공을 뜻하지 않는다. 모든 입력을 성공·확보 실패·명시 제외로 설명하고 pending/원인 불명 오류가 없어야 게시할 수 있다. 실패 37건도 이유별로 실제 대조하며 모두 같은 오류라고 단정하지 않는다. 잘못된 ID 관계·hash 충돌·DB 손상은 허용된 확보 실패로 분류할 수 없다.

### 3. NAS 읽기와 기존 소비자

- archive 전용 reader는 읽기만 소유한다. `SQLiteQueryStore.initialize`나 쓰기 connection helper로 열지 않으며 migration, job enqueue, BODY/RULE/사건 계산을 연결하지 않는다. PC가 목록 표시용 정제 텍스트·assessment·상태까지 만들어 `news_service._stored_item`의 정제/재평가 fallback을 거치지 않게 한다.
- 기존 `/api/v1/news` 목록 계약은 유지하고 별도 archive 조회 계약을 추가한다. UI에는 `현재 뉴스`와 `과거 수집 자료` 범위를 분명히 표시한다. 기존 API를 읽는 구형 앱은 archive를 지원한다고 표시하지 않는다. capability가 있는 새 클라이언트만 과거 자료 기능을 사용한다.
- archive 종목/시황 목록은 `(정규화한 발행시각, 고유 projection key)`의 고정 정렬과 keyset cursor를 사용한다. cursor에는 dataset ID·필터 hash·마지막 key를 넣고 위변조 및 다른 필터 재사용을 거부한다. 날짜 없는 행의 정렬 규칙도 PC에서 고정한다. 최신 목록은 target/identity당 하나이고 전체 변경 이력은 history로 보존한다.
- 상세 요청은 dataset ID와 정확한 article/body/event ID를 전달한다. 기존 ID 요청도 PG에 없으면 archive에서 같은 ID를 찾는다. URL만 같은 다른 revision의 본문/평가를 붙이지 않는다. 양쪽에 같은 ID가 있으면 seed checksum과 대조해 충돌을 드러내고 임의 선택하지 않는다.
- `presentation/news_workers.py`의 article→body→membership→event ID 연결을 검증한다. archive의 본문 확보 실패를 현재의 `처리 중` 문구로 표시하지 않는다. 기존 일지 링크는 PG에 두고 참조된 뉴스 ID의 읽기를 연결한다.
- `journal_enrichment.news_evidence_timing`의 revision/available_at 의미를 보존한다. 연구 observation export와 PC `historical_reconstruction`의 원본 조회는 별도 계약이므로 자동으로 archive로 바꾸지 않는다. 해당 소비자가 필요한 기존 행의 삭제는 실제 호환성이 입증될 때까지 금지한다.

### 4. 게시·진단·복구

- 기본은 archive 미설정이다. 명시한 manifest로 서버 시작 시 한 dataset을 고정하며 첫 버전은 hot swap을 구현하지 않는다. 재시작으로 세대가 달라지면 이전 cursor 요청에 재조회 필요 오류를 반환한다.
- PC에서 schema/index/projection 생성, 무결성·참조·건수 대조를 완료하고 단독 읽기 가능한 DB를 봉인한다. NAS 임시 경로 복사 → 전체 hash 확인 → 버전 파일 게시 → 설정 변경/서버 재시작 순서다. 이미 게시된 파일을 덮어쓰지 않는다. 시작 시 전체 hash를 재계산하지 않는 대신 게시 검증 기록과 파일 메타정보를 확인하며, 메타정보 검사만으로 변조 검증을 했다고 주장하지 않는다.
- 과거자료 reader 중지와 상세 계측은 진단 master 하위에 둔다. master OFF이면 override/capture가 해제되며 정상 archive 읽기는 유지한다. 진단 중 읽기를 중지하면 빈 목록 대신 명시적 사용 불가를 반환한다. TTL과 OFF→ON 복구를 검증한다.
- PC 최종화는 명시 실행한 프로세스만 동작한다. 진단 master 하위 pause는 transaction 경계에서 적용하고 TTL 후 이어받는다. PC SQLite 계측을 NAS PostgreSQL writer 통계로 표시하지 않는다. 실행 상태/재개 원장은 작업 정확성을 위한 필수 기록이고, 상세 성능 로그는 capture 때만 생성한다. 상시 polling daemon은 추가하지 않는다.
- 이전 dataset 파일과 설정을 보존해 되돌린다. 기존 NAS 뉴스 정리는 게시와 별도 단계이며 대상별 보존본·참조 검사·복구 검증이 선행한다.

## 구현 순서와 통과 조건

| 단계 | 구현 경계 | 반드시 확인할 결과 |
| --- | --- | --- |
| A: 입력·seed | PC manifest/범위 감사, 관련 PG 읽기 전용 export | 실제 scope별 수량, 고유 ID와 관계, 원본 실패 사유, 기존 ID 보존. 운영 쓰기·삭제 없음 |
| B: PC 완성 DB | 전용 최종화 CLI, 기존 parser/정책 재사용, 재개 원장 | 같은 URL 내용 변경·다종목·중복·버전 불명·실패·중단/재시작·rollback. 입력 전수 회계와 referential integrity |
| C: 읽기 연결 | archive reader, API/client/뉴스 상세, 진단 하위 스위치 | 1/200/201건 및 동일 시각 페이지, 모든 ID 상세, as-of, 종목/시황, 없는 본문 상태, 진단 OFF 비용·TTL·복구 |
| D: 격리 실자료 검증 | PC 완성본과 PostgreSQL 전용 테스트 DB의 seed 대조 | 원본 ID/payload/관계/시간 동일, 필요한 일지·연구 소비자 대조. 읽기 전용 권한에서 조회·시작·종료, writer/계산 호출 금지 테스트 |
| E: NAS 게시 후 확인 | 버전 파일과 reader 배포 | health/capability/dataset 확인, idle 및 실제 조회 전후 불변, 새 archive 처리 job 없음, 실패 게시·이전 세대 복귀 |
| F: 기존 이관분 정리 | 별도 대상 PK/checksum manifest | 새 조회 검증 후만 실행. 공유 참조 제외, 캡처 후 변경 감지, 삭제 전후 전수 참조와 복구 확인 |

A~D가 완료되기 전 운영 게시·전량 import·삭제를 수행하지 않는다. 첫 실자료 표본으로 PC 처리 단계별 속도/행 크기를 측정한 뒤에만 남은 시간과 NAS 전송량을 추정한다. 기존 25건 NAS importer 시간으로 새 최종화 시간을 계산하지 않는다. 현재 설계 검토만으로 B~F의 기능·성능이 검증됐다고 보고하지 않는다.

후속 담당의 모델 선택은 [현행 모델 정책](MODEL_HANDOFF_POLICY.md)을 따른다. 상세 방향이 결정된 기존 ID seed·가변 projection 보존·재시작 transaction·여러 소비자 연결 구현은 **GPT-6.1 Sol High**가 적절하며, 단순 반복 테스트/문서 단계에서도 Sol을 유지하고 High 유지 선호를 적용한다. 아래 B 단계는 당시 검증 원장과 사건 최종화 범위의 기록이다. 새 범용 저장소나 별도 판단 엔진을 만들지 않는다.

## 2026-09-27 B 단계 사건 순서·규칙 출처 설계 결정

### 확인한 동작과 실자료 범위

- `database._save_sqlite_news_event`는 정확한 article/body/rule/input/target 조합을 먼저 재사용한다. 새 결과는 같은 target+기사 identity의 기존 사건을 우선하고, 없으면 target+event_key로 연결한다. `revision_of`와 history 최신값은 발행시각이 아니라 `accepted_sequence`를 따른다. `available_at`은 실제 저장 시각이다.
- `news_rules._event_key`는 종목·거래상대·금액의 조합이며 날짜나 계약 고유번호가 없다. 사건 동일성의 확정 증거가 아닌 기존 그룹화 휴리스틱이다. `possible_related`는 별도 참고 연결이고 사건 병합 근거가 아니다.
- 9월 25일 고정 검색 준비본의 사건 후보 37,816건을 article content hash, body hash/status, RULE input hash와 target까지 대조했다. seed 사건 그대로 재사용 2,448건, 정확한 기존 결과가 없는 후보 35,368건이다. 후자 전체는 현재 seed의 identity/event_key 그룹과도 겹치지 않았다. 따라서 이 고정본에서 새 결과의 seed 재연결 충돌을 발견했다고 보고하지 않는다. seed에는 여러 사건에 대응하는 event_key 6개가 있고, 이후 검색·시황·중간 세대 입력은 별도 전수 검사해야 한다.
- 임시 SQLite에서 9/12 확정 계약을 먼저 저장하고 9/1 MOU 기사를 뒤늦게 추가했다. 기존 함수는 같은 사건에 UPDATE를 추가하고 최신 저장 certainty는 CONFIRMED→POTENTIAL이 됐다. 원래 seed payload와 과거 available_at 기준 조회는 보존됐다. 이는 저장 최신값이 실제 사건의 최신 상태를 뜻하지 않음을 입증한 재현이며, 실제 자료가 모두 오분류됐다는 증거는 아니다.

### 선택: seed 불변 + 사후 계산 append

기존 seed를 발행순으로 다시 계산하거나 기존 novelty·부모·ID를 변경하지 않는다. 완전한 발행순 재해석은 별도 해석 이력 모델이 필요하고 일지/AI 참조의 의미를 바꾼다. 새 자료를 무조건 별도 사건으로 끊는 방법도 기존 연속성을 잃으므로 채택하지 않는다. 이 단발성 이관은 **기존 기록 보존과 새 사후 계산 자료 추가**를 선택한다.

1. 세대별 입력을 고정하고 모든 대상 article/body/RULE 검증이 끝난 뒤 사건 작업 목록을 한 번 고정한다. 현재 처리된 2,446건 prefix만으로 사건을 먼저 확정하지 않는다. 부모 article/body revision은 자식보다 먼저 처리하고, 의존성이 풀린 후보들 사이에는 `(발행시각 없음 여부, 정규화 UTC 발행시각, target, identity, article_id, body_id, input_hash)` 오름차순을 사용한다. 발행시각 불명은 뒤에 둔다. work ordinal과 정렬 정책 버전을 저장하여 재시작 후 순서를 바꾸지 않는다. 이 순서는 새 세대 안의 결정적 계산 순서이며 seed를 포함한 전역 역사 순서가 아니다.
2. 정확한 기존 결과가 있으면 event/membership ID를 그대로 재사용하고 payload·시간·sequence를 검증한다. 여러 target을 결과 하나로 축약하지 않는다. 입력 조합에 대응하는 membership이 없거나 둘 이상이면 무결성 오류로 처리한다.
3. 정확한 결과가 없으면 같은 identity의 사건이 유일할 때 그 ID를 따른다. 그것도 없을 때 event_key가 가리키는 사건이 유일하면 기존 휴리스틱대로 연결하되 연결 이유를 원장에 남긴다. 여러 사건이 후보이면 최신 sequence만으로 선택하거나 합치지 않고 충돌로 남겨 봉인을 막는다. 후보가 없으면 새 사건을 만든다. 기존 seed의 모호한 그룹 자체를 수정하지 않는다.
4. 새 event/membership은 각각 table의 기존 최댓값 다음 sequence에 append한다. `revision_of`는 선택한 사건의 저장 이력 부모다. 실제 PC 계산 시각을 available_at에 기록하고 발행시각으로 소급하지 않는다. 새 원장의 `posthoc` 표시와 연결 이유는 보존하되 기존 seed JSON에 필드를 덧씌우지 않는다.
5. 상세 화면은 article/body에 대응하는 정확한 membership/event revision을 읽는다. `accepted_sequence` 최신값은 '최근 저장 판정'이며 사건의 현재 상태·당시 이용 가능 정보로 표시하지 않는다. 발행일은 목록 정렬이고 as-of는 available_at 조건이다. 과거 실행 시점 이후 계산된 결과는 일지의 기존 `post_trade` 의미를 따른다. archive가 완전한 역사적 사건 상태 복원을 제공한다고 주장하지 않는다.
6. 중간 게시 후 최종 세대는 이미 게시한 archive도 불변 seed로 포함하고 ID를 재사용한다. 이전 세대와 NAS seed에 같은 ID가 있으면 checksum이 일치해야 한다. 늦게 추가된 과거 기사 때문에 게시된 부모·sequence를 재배열하지 않는다. 아직 게시하지 않은 building 파일만 재생성할 수 있으며 원본 입력·실패 증거는 보존한다.

### RULE 검증 선행 조건

`preprocess_historical_news_locally._prepare`의 prepared rules_json은 assessment/core_sentences/rule_result를 담지만 전체 처리 버전·입력 fingerprint를 담지 않는다. non-null 결과의 rule_version과 달리 null 결과는 어느 분류기를 실행했는지 증거가 없다. 현재 `finalize_prepared_historical_assessments.py`가 v2 및 assessment-v1을 상수로 기록한 것만으로 출처 검증이 된 것은 아니다. 2,446건의 `assessment_done`은 중간 staging 상태이고 사건·봉인의 완료 조건으로 쓰면 안 된다.

- seed의 기존 결과는 원래 버전과 checksum으로 그대로 보존한다. 새 입력은 PC에서 고정된 저장 본문으로 RULE/assessment/core_sentences를 오프라인 재검증한다. prepared 원문 JSON도 별도 증거로 보존하며 결과가 달라지면 차이를 기록한다. seed projection을 새 결과로 덮어쓰지 않는다.
- 검증 원장은 article canonical hash, body hash/status 및 출처 상태, target code/name, 정제·assessment·RULE 구현 식별자, 결과 hash, 실제 계산시각을 묶는다. null도 명시적인 '검증된 사건 없음' 결과다. BODY 버전 불명을 RULE 검증 성공으로 지우지 않는다.
- 네트워크 BODY 경로를 호출하지 않고 기존 `prepare_job`의 RULE 계산/기존 순수 함수를 재사용한다. 검증된 결과와 prepared가 다르거나 target 대응이 모호하면 미검증 상태를 완료로 숨기지 않는다. 기존 assessment 문서와 충돌하는 새 결과는 seed 보존 원칙하에 정확한 입력별 archive 결과로 저장하고 reader가 그 입력을 지정해 읽는다.

### 다음 구현과 완료 조건

- 기존 staging/finalizer를 확장해 검증 fingerprint 원장과 고정 사건 worklist를 추가한다. 새로운 범용 판단 엔진은 만들지 않는다. 기존 live/PostgreSQL 사건 연결 정책은 바꾸지 않는다.
- PC 단일 builder를 보장하고 사건 연결 preflight부터 기존 SQLite 저장 helper, event+membership, resolution 원장, resume 상태까지 같은 transaction으로 묶는다. helper가 자체 commit하면 connection을 받는 내부 경계로 연결하며 중첩 commit은 금지한다. 실제 스키마 UNIQUE key가 target별 fingerprint와 충돌하지 않는지도 검증한다.
- resolution 원장은 work ordinal, 입력 fingerprint, exact/new/no_event/conflict 구분, 연결 이유, event/event_revision/membership ID, 부모 ID, 계산시각, 정책 버전을 기록한다. 재실행은 ID를 재발급하지 않고 저장 결과를 대조한다. 예외·중단은 처리 단위 전체 rollback한다. archive pending job을 실행하지 않는다.
- 회귀: exact 재사용, 같은 identity 정정, 다른 기사 같은 key, 모호한 key 충돌, 늦은 과거기사, 부모가 입력 뒤에 오는 경우, 동일시각/시각 없음, 다종목, null/버전 불명, seed assessment 충돌, 실패 주입·재시작·두 builder, 중간→최종 세대 ID 보존, seed checksum/as-of 불변을 검증한다. NAS writer·job·BODY/RULE 호출을 금지하는 reader 검사는 C~D에서 별도로 필요하다.

이번 단계는 읽기 전용 실자료 감사와 임시 SQLite 경계 재현, 설계 문서 갱신까지다. 위 검증 원장·사건 최종화·reader는 아직 구현/통과한 것으로 보고하지 않는다. 운영 NAS 변경·재빌드·삭제는 하지 않았다.

## 전체 이관 구현 결정 (2026-10-13)

### 현재 증거와 결정 범위

사용자 요청은 지금까지 받은 종목·시황 뉴스를 PC에서 완성해 NAS에서는 읽기만 제공하는 것이다. 기존 단일 SQLite archive 방향을 유지하며 새로운 범용 저장 엔진이나 NAS 재처리 파이프라인을 만들지 않는다. 이 절은 앞의 검색 단일 target 구현 제약을 보완하는 설계이며 아래 항목의 구현 완료 보고가 아니다.

| 항목 | 이번 읽기 확인 |
| --- | --- |
| 실행 소스 | main `941347e` 위 작업본. 여러 다른 작업의 미커밋 변경이 있어 전체 누적 소스를 일괄 배포하지 않는다. |
| 검색 준비본 | `data/historical_collection/prepared_news.sqlite3`, 27,164,303,360 bytes. 유지 집계의 ready 4,629,368 / failed 1,706, fulltext 3,530,237 / summary_only 1,099,131. |
| 시황 준비본 | `data/historical_collection/prepared-market-news.sqlite3`, 29,860,728,832 bytes. 유지 집계의 ready 4,867,932 / failed 715, fulltext 4,669,716 / summary_only 198,216. |
| 수량의 한계 | 위 값은 `monitor_counts` 유지 집계이며 이번에 원본 전체를 재계수한 결과가 아니다. 종목별 동일 기사·검색/시황 중복을 포함하므로 합계를 고유 기사 수로 표현하지 않는다. 최종 입력 고정 뒤 실제 건수·키 대조가 필요하다. |
| 실행 상태 | 검색·원문 상태 파일 complete. 사용자 권한의 프로세스 읽기에서 해당 수집·준비·최종화 명령줄은 발견되지 않았다. 준비본 두 파일 WAL 크기 0. 이 확인만으로 영구 불변 snapshot이라고 간주하지 않는다. |
| 디스크 | C: 여유 약 97.4GB, 준비본 합계 약 57.0GB. 검색 raw 약 172.4GB, 시황 raw 약 14.8GB. 준비본 사본을 모두 C:에 만들면 출력·임시파일 전 여유 약 40.4GB다. 최종 archive 크기는 아직 측정하지 않았다. |
| 다중 target 실자료 | 시황 준비본 identity순 512행 표본 중 68행이 다중 target, 최대 6개. 표본 source는 flash이므로 world 전체 분포로 일반화하지 않는다. |
| 기존 building 파일 | `news-archive-20260927.building.sqlite3`은 9월 준비본 330,298행의 일부만 처리했다. 새로운 전체 세대의 재개 파일로 재사용하지 않는다. |

### 실제 코드의 결손

1. `stage_prepared_historical_news_archive.py:SCOPE`, assessment/RULE/event/projection의 필터와 manifest가 `historical_backfill`로 고정돼 있다. 같은 파일의 `archive_input_progress` 존재 검사는 scope별이 아니어서 두 번째 scope를 단순 추가할 수도 없다.
2. `finalize_prepared_historical_article_bodies._complete_one`은 검색 document 안의 stock_code/identity를 요구하고 검색 hash·collector/scope를 쓴다. 시황 prepared document에는 이 필드가 없으며 기존 importer는 GLOBAL identity를 합친 `_news_article_content_hash`를 사용한다. 검색 저장 코드를 그대로 적용하면 seed ID 재사용 의미가 달라진다.
3. `finalize_prepared_historical_assessments._finish_one`, `verify_prepared_historical_news_rules._verify_one`은 RULE 한 개를 요구한다. 입력·검증·사건 원장의 PK에 target이 없고 event `_ordered_worklist`도 article ID당 한 행만 허용한다. 시황의 GLOBAL+확인된 종목을 처리할 수 없다.
4. `build_prepared_historical_search_projection._row`는 본문을 display JSON에도 복제한다. `historical_news_archive.article_by_id`는 최신 검색 projection을 통해서만 assessment를 찾으므로 과거 revision 또는 시황의 여러 target 판정을 온전히 조회하지 못한다.
5. reader/router/client/dialog는 종목 검색 페이지와 기사 상세만 연결돼 있다. 시황 목록, 두 scope 전체 회계, 실제 실패/제외 승인 원장, 봉인 CLI·게시 검증은 남아 있다. readiness의 `market.state=complete` 표식 확인만으로 시황 건수·참조 검증이 끝났다고 할 수 없다.
6. ARTICLE/BODY는 raw 본문 근거 전체를 Python dict로, 사건은 모든 입력을 list/heap으로 읽는다. 수백만 입력에서 기존 소형 표본 메모리·시간을 그대로 기대하지 않는다. 단계별 행당 COMMIT과 반복 전체 hash 읽기도 실행 계획에서 측정해야 한다.

### 선택한 데이터 계약

- **한 dataset에 두 scope를 포함한다.** 검색의 기사 소유자는 종목코드, 시황의 기사 소유자는 GLOBAL이다. URL이 같아도 기존 소유자·content hash가 다른 기사 revision을 강제로 합치지 않는다. 시황 한 기사/BODY를 target 수만큼 새로 복제하지 않는다. 기존 ID·sequence·시간과 support_only 보존은 앞의 seed 계약을 유지한다.
- 입력 식별자는 `(scope, stock_code, identity, input_hash)`다. 별도의 target 작업 식별자는 여기에 `target_id`를 더한다. 기존 archive 내부의 prepared/verified RULE, event worklist/resolution을 target별로 확장하고 input progress는 **모든 기대 target**이 끝나야 완료된다. 기사 수와 판정/사건 처리 수의 equality 검사를 분리한다. 같은 target 중복 payload는 한 번 처리하되 연결 근거를 보존하고, 서로 다른 payload가 한 target에 중복되면 충돌로 중단한다.
- 시황 target 집합은 prepared의 GLOBAL과 확인된 종목을 보존한다. `source_item.targets`의 불확실한 종목 연결도 관계 증거로 남기되 confirmed로 승격하거나 RULE 대상을 임의 추가하지 않는다. FLASH/WORLD 관측은 raw의 `(source, office_id, article_id)` 관계를 보존하며 prepared에 남은 단일 source만으로 다른 채널 관측을 지우지 않는다. 이전에 합의한 WORLD 서비스 이전 제외 범위는 coverage 정책으로 기록한다.
- 시황 canonical 생성은 기존 historical market 입력의 document 정규화·hash 계약을 재사용한다. pure helper를 가져오되 job 생성·운영 writer를 호출하지 않는다. 검색은 기존 검색 계약을 유지한다. seed와 정확히 같은 결과이면 원래 ID를 재사용하며 충돌 시 새 ID로 숨기지 않는다.
- 새 archive 계산은 `(article_revision_id, body_revision_id, target_id, processing_fingerprint)`에 대응하는 정확한 판정으로 보존한다. seed의 가변 `central_documents.news_assessment`와 다른 결과를 발견해도 seed를 덮어쓰지 않는다. prepared 원래 결과·새 계산 결과·실제 계산시각·출처를 별도 보존하고 상세가 해당 입력을 지정한다.
- RULE 처리 버전 근거가 없는 기존 입력은 PC에서 저장 본문으로 재검증한다. 일치하면 `verified_equal`; 입력·출처가 유효하지만 고정한 현재 계산 결과가 다르면 차이를 남긴 `recomputed_posthoc` 결과로 새 세대에 보존한다. 이는 과거 판정의 정확한 재현이라고 표시하지 않는다. **입력 hash/target 불일치, seed 충돌, 손상은 이 상태로 통과시키지 않는다.** 기존 v1의 mismatch 결과를 이름만 바꿔 승인하지 않고 v2 고정 코드·입력으로 실제 계산한다. null도 target별 결과이며 BODY extractor 버전 불명을 지우지 않는다.
- seed 사건을 재배열하지 않는 앞의 결정은 유지한다. 새 사건은 두 scope의 target별 검증 완료 후 하나의 고정 순서로 처리한다. article 부모의 대상 작업이 모두 선행하고 나머지는 기존 발행시각/tie-break 뒤에 scope와 target을 명시한다. 같은 article ID의 여러 target은 서로 다른 작업이며 기존 target별 event helper를 사용한다. 모호한 사건 연결은 계속 봉인을 막는다.
- 모든 확보 자료의 회계가 필요하다. raw 기사/관측 키와 prepared 키를 비교해 준비본에 없는 목록·원문 실패·명시 제외를 원장화한다. 저장된 목록/본문으로 준비할 수 있는 누락은 PC의 기존 offline 준비 경로로 처리한다. 재수집은 자동 실행하지 않는다. 사용자 제외는 원문 요청 중지 정책이며 확보된 제목·요약·URL 삭제나 fulltext 성공을 뜻하지 않는다.
- source_failed도 목록 정보와 원인·근거 키를 보존한다. 실패 이유가 설명되면 coverage를 `complete_with_source_failures`로 허용할 수 있지만 입력 미처리·관계 손상·판정 실패를 source failure로 숨기지 않는다. 중단·truncated/repeated-page 검색 범위는 별도 수집 공백 원장에 남기며 수집 전 기간의 무누락을 주장하지 않는다.
- raw 페이지의 여러 버전과 본문 snapshot을 읽어 입증된 article/body 변경과 단순 응답 위치 변화를 구분한다. 대응 불명 본문은 증거로 남기고 가짜 revision을 만들지 않는다. prepared의 최신행만 보고 이력 보존 완료로 봉인하지 않는다.

### 공간·실행·재개 결정

1. 먼저 기존 seed·prepared 소형 임시 DB로 두 scope와 다중 target을 검증한다. 그다음 실제 입력의 scope/body 상태/target 수를 나눈 유한 표본으로 archive bytes/row, 처리시간, index·WAL·temp 최대 사용량을 측정한다. 표본은 별도 building 파일이며 게시하지 않는다. 임의의 전체 완료 ETA를 제시하지 않는다.
2. 기본 입력 고정은 기존 SQLite backup 도구로 **새 파일**에 만든다. raw는 필요한 관측·본문 근거를 read transaction에서 범위를 고정해 스트리밍 추출하고 범위·캡처시각·hash를 기록할 수 있다. append rowid 경계만으로 가변 raw 기사 테이블을 snapshot이라고 취급하지 않는다. 전체 HTML 원본은 보존하며 NAS 전량 복제는 요구하지 않는다.
3. 전체 실행 전 `frozen inputs + 예상 archive + index/temp/WAL 최대치 + 여유분`을 확보해야 한다. 현재 97.4GB만으로 충분하다고 승인하지 않는다. 필요한 작업 공간이 부족하면 용량 수치를 제시하고 별도 PC 디스크 또는 검증된 보관/정리를 결정한 뒤 시작한다. 원본을 자동 삭제하거나 NAS SMB 위에 쓰기 가능한 SQLite builder를 올려 해결하지 않는다.
4. raw 근거와 사건 정렬은 인덱스가 있는 디스크 작업표·keyset/유한 배치로 처리한다. event 부모 의존성은 article별 완료 상태와 준비된 작업 인덱스로 풀고 work ordinal을 고정한다. 전체 기사·본문·target를 RAM에 적재하지 않는다. 동일 입력 재시작은 ID를 재발급하지 않는다.
5. builder는 한 프로세스만 소유한다. 여러 건을 제한된 transaction으로 묶을 때 **각 기사 단계 결과와 progress, target 결과와 resolution**을 함께 commit한다. 내부 helper 자체 commit은 금지한다. 실패 시 batch 전체를 rollback하고 마지막 commit부터 재개한다. 실패 입력을 범위를 좁혀 기록하되 조용히 건너뛰지 않는다. 먼저 정합성 회귀 후 실제 표본으로 batch 크기를 정한다.
6. 고정본 전체 hash는 freeze/단계 경계 검증에 사용한다. 명시한 입력 hash와 코드 fingerprint가 바뀌면 resume를 거부한다. 기존처럼 매 소형 batch마다 27~30GB 전체를 다시 hash하지 않는다. 입력을 실제로 고정하지 않은 상태에서 mtime만 믿는 최적화는 허용하지 않는다.
7. display projection의 본문 중복은 v2에서 없앤다. PC 정제 본문은 body ID별로 저장하고 목록은 제목·요약·상태·정확 ID만 읽는다. 상세만 해당 본문과 target별 판정을 읽는다. 원래 body bytes/hash와 정제본은 구분한다. 이는 단일 archive 내부의 중복 완화이며 파일 분할/공유 blob 저장소 같은 별도 구조는 이번에 도입하지 않는다.

### 조회·봉인·NAS 활성화 결정

- 현행 search v1 계약은 유지한다. 새 **v2 capability/목록 응답**으로 compact 검색 목록과 시황 `FLASH/WORLD/전체` 목록을 제공한다. 동일 기사 여러 source의 중복은 전체 목록에서 identity 한 개로 표시하고 source 연결은 보존한다. cursor는 dataset·목록종류·필터·고정 정렬키를 모두 묶는다. 종목코드 필터가 필수인 search 경로에 GLOBAL을 억지로 넣지 않는다.
- 상세는 정확한 article/body ID의 저장 이력과 target별 assessment/event를 조회한다. 최신 목록에 없는 과거 revision도 해당 판정에 접근할 수 있어야 한다. 기존 v1 reader/fixture와 새 v2를 명시적으로 구분하고 구형 파일을 자동 migration하지 않는다. 새 화면은 기존 과거자료 대화상자 안에서 종목/시황 범위를 선택한다. 현재 뉴스·일지 PG 조회는 기존 자료가 남아 있으므로 이번에 임의 통합하지 않는다.
- v2 readiness는 검색과 시황의 실제 입력·기대 target·판정·사건 resolution·source relation·projection·실패 원장을 대조한다. `market.state=complete` 같은 표식만으로 통과하지 않는다. seed 전수 checksum, 부모/본문/target/event 참조, 명시 실패와 미처리 구별, full integrity/foreign-key 및 명시적 관계 검사가 필요하다.
- 새 봉인 CLI만 `building → sealed`를 바꾼다. builder 종료·pending/conflict 없음·coverage 검증 뒤 인덱스/정제/단독 파일 준비를 PC에서 마치고, 모든 connection을 닫아 WAL 의존성이 없음을 확인한다. sealed 파일의 전체 SHA-256은 파일 밖 게시 manifest에 기록한다. 자기 자신을 포함한 hash를 DB 안에 넣는 순환 계약을 만들지 않는다. 해시가 바뀌는 봉인 후 VACUUM/인덱스 생성은 금지한다.
- NAS에는 새 dataset 버전 경로로 파일을 복사하고 전체 hash와 게시 manifest를 검증한다. 확인 전 파일을 활성 경로에 연결하지 않는다. 같은 버전 파일 덮어쓰기·열린 파일 교체는 금지하며 이전 버전을 남긴다. 새 reader/schema 계약을 지원하는 고정 서버 후보를 현행 NAS 배포/gate 절차로 검증한 뒤 archive 설정을 연결한다. 다른 작업의 활성 release·녹화·잠금을 우회하지 않는다.
- 서버는 manifest와 지원 schema/capability만 작게 검사하고 읽기 연결만 연다. 시작/조회가 migration·인덱스 생성·BODY/RULE·job enqueue·PG 대량 쓰기를 호출하지 않아야 한다. 진단 master 하위 pause/TTL 계약을 새 목록·상세에도 적용한다. NAS는 복사/해시/조회 I/O를 수행하며 CPU/I/O 0이라고 표현하지 않는다.
- 기존 NAS seed는 오래됐으므로 실행 단계에서 현행 historical scope/관련 참조를 새 readonly 일관 export로 확인한다. schema 호환·기존 ID/내용 대조 후 PC seed로 사용한다. 이전 seed와 변경된 가변 행은 명시적으로 대조하고 옛 checksum을 현재 값인 것처럼 쓰지 않는다. 운영 기사·job·일지 연결 삭제는 이번 게시와 분리한다.

### 구현 담당자가 이어갈 순서와 검사

| 순서 | 기존 파일/책임 | 통과 조건 |
| --- | --- | --- |
| 1 | stage/finalize ARTICLE·assessment·verify RULE·events, archive 내부 v2 schema | 검색 1 target / 시황 GLOBAL+2종목 / 후보 관계 / 동일 URL 다른 scope / 정확 seed 재사용 / target 중복 / seed 판정 충돌 보존 / 입력 변경 차단 |
| 2 | 기존 raw 페이지·본문 provenance 감사 + v2 coverage | raw-only, 준비 실패, 원문 제외, summary-only, 다중 페이지 버전, 연결 불명, FLASH/WORLD 겹침과 과거 서비스 제외를 실제 입력별 설명 |
| 3 | search projection 확장 + market projection, reader/routes/client/dialog | 1/200/201행·같은 시각·시각 없음·필터 cursor 변경·정확 과거 ID·다중 target 상세·누락 상태, v1 호환과 v2 capability |
| 4 | readiness 강화 + 봉인 CLI, PC 실행 도구 | 시황 가짜 complete/target 누락/관계 손상/미처리/진짜 checksum 불일치 차단. 중간 실패/두 builder/재개 결과·ID·transaction 보존 |
| 5 | 격리 SQLite + 실제 PostgreSQL 전용 DB seed 검증 | seed PK/sequence/payload/as-of 불변. reader가 쓰기·계산 helper를 부르면 실패하는 검사, 파일 hash 불변. 실제 API는 지원되는 ASGI 경로 사용, httpx2 추가 금지 |
| 6 | 실제 유한 표본 → 용량 gate → 입력 고정·전체 PC 실행 | 표본 처리율·크기·RSS·SQL 횟수/commit 수 기록. 충분한 공간 확보 뒤 전체 입력 회계·무결성 검사; 표본 성공을 전체 성공으로 보고하지 않음 |
| 7 | NAS 비활성 파일 복사·검증 → 고정 후보/gate → 활성화 | dataset/capability·종목/시황/정확 ID 조회, idle/조회/재시작 후 파일 불변·archive job 생성 없음, 중단 복사/잘못된 hash 거부·이전 버전 복귀 |

단발성 builder가 상태·소유권·transaction·다중 target·기존 ID를 연결하는 구현 단계는 **Sol High**로 이어간다. Astra의 이번 설계 결정은 여기서 끝나며 구현을 설계 모델로 계속하지 않는다. 이번 실자료 확인은 상태/크기/유지 집계와 512행 표본의 읽기 감사이고, 신규 회귀 실행·전체 준비본 검증·NAS 활성화는 미실행이다.


## v2 구현과 실행 상태 (2026-10-13)

사용자 요청으로 전체 생성 입력은 기존 PC 준비 DB를 직접 읽는다. 별도의 준비 DB 사본은 필수가 아니다. 수집·준비 writer가 없는지 확인하고 비어 있는 WAL 및 원본 SHA-256을 기록한 뒤 읽기 전용 transaction으로 처리한다. 각 단계 전후 전체 hash가 다르거나 WAL 내용이 생기면 같은 세대의 재개·봉인을 거절한다. 원본 `historical_market` alias는 연결의 TEMP view에서만 canonical scope로 대응하며, 겹치는 key를 거절하고 기존 scope/hash를 결과 원장에 보존한다. 원본 파일과 schema를 수정하지 않는다. 최종 NAS 아카이브 결과물과 작은 실행 코드 고정본은 별도로 유지한다. 이미 만든 입력 사본은 이번 실행에 사용하지 않으며 자동 삭제하지 않는다.

검색/시황의 여러 대상은 기사/BODY를 공유하되 판정·사건 연결을 각각 보존한다. 기존 ARTICLE/BODY 및 사건 finalizer 정책을 호출하고 그 내부 commit은 caller-owned batch에서만 끈다. 원본 발행시각·BODY 수집 근거·기존 prepared 판정과 현재 규칙의 결과를 따로 보존한다. 현재 규칙 재계산 결과는 `recomputed_posthoc`이며 과거 당시 판정으로 표현하지 않는다. source page의 원문 응답은 PC 원본에 남고 모든 파싱 버전·응답 hash와 BODY 버전은 근거 원장에 보존한다. 연결 불명 원본은 canonical revision으로 만들지 않는다. WORLD 서비스 이전 제외는 한국시간 2021-01-01 경계를 따른다.

`prepared_historical_archive_v2.py`가 두 scope의 입력/target/사건/projection을 소유한다. `capture_historical_archive_sources_v2.py`는 raw received key·페이지/본문 버전을 읽기 대조하고 설명되지 않은 미준비/실패를 차단한다. `audit_prepared_historical_archive_v2.py`가 실제 입력·원본·seed·관계·hash·무결성을 검증하고 `seal_prepared_historical_archive_v2.py`만 봉인한다. 고정 입력은 전체 hash로 검증하고 quiescent raw 입력은 경로/크기/mtime/rowid 경계 및 개별 근거 hash로 확인한다. raw 파일 전체 cryptographic hash나 서로 다른 원본 DB의 동시시점 보장을 주장하지 않는다. 원본 파일이 바뀌면 같은 세대의 재개를 거부한다.

NAS 파일 복사는 `publish_historical_archive_file.py`로 새 `historical-news-archives/<dataset>/archive.sqlite3`에만 한다. prefix 검증 뒤 중단 사본을 재개하고 전체 SHA가 맞을 때 overwrite 없는 게시를 수행한다. 동일 버전의 다른 파일은 거부하며 파일 검증과 서버 활성화는 별도다. 활성화는 현행 NAS 고정 release/gate·운영 잠금 절차를 따른다. reader는 쓰기/계산/마이그레이션을 수행하지 않고 master 하위 `historical_news_archive` pause/TTL을 목록·상세에 적용한다. 단발성 PC builder 자체는 운영 생성 작업이며 NAS 진단 제어 파일로 자동 제어되는 기능은 아니다.

관련 회귀에서 두 scope/다중 target, 정확 seed ID, 준비 판정 불일치 보존, 한 batch의 rollback, OS 잠금/프로세스 종료 뒤 재개, 입력 변경 거부, source 실패 기록, 누락 차단, 봉인 hash/읽기 불변, 잘못된 BODY ID·필터 cursor·인증·diagnostic pause, 중단 복사/동일 파일 재게시/손상 파일 덮어쓰기 거부를 검사했다. 최신 실제 PostgreSQL seed 경로 2건 검사는 NAS /tmp에 준비했으며 sudo 인증 뒤 실행 결과가 필요하다. 구형 시험본/v1 building 파일을 전체 archive로 승격하지 않는다.

실자료 표본과 용량은 [준비본](../artifacts/news-archive-capacity-20261013-v2-verified/profile.json), [원본 이력](../artifacts/news-archive-capacity-20261013-v2-final/raw-capacity.json)에 분리 기록한다. 준비본 1,024행과 원본 320행은 유한 표본이다. 약 332GB+새 seed 추정은 전체 데이터 크기·최대 임시공간 보장이 아니며, 생성 중에는 transaction 시작 전 2GiB 미만 여유를 감지해 기존 완료 batch를 보존하고 중단한다. C: 공간 확보 후 검색 고정 사본 생성에 착수했다. 전체 파일의 완성/봉인/실자료 API/NAS 게시·재시작 확인은 여전히 남아 있다. 원본/기존 NAS 자료는 삭제하지 않았다.

최종 로컬 검사 기록: 관련 40건 묶음 통과, canonical hash 검사 추가 후 v2 18건+입력 사본 2건=20건 통과, catalog unit=436/integration=26 통과 및 구문/범위 diff 검사 통과. 두 묶음은 중복을 포함하므로 합계 60건으로 보고하지 않는다. 확대 재검사는 60초 제한에서 기존 finalizer 회귀 도중 중단돼 미완료로 남긴다. NAS 실행 helper의 CRLF로 첫 사용자 실행이 `set -eu`에서 시작 전 실패했고 LF로 고쳐 NAS `sh -n` 구문 검사를 통과했다. PostgreSQL 실제 검사 결과는 사용자 실행 대기다.

추가 최종 증거: v2·고정 사본·참조 추출 회귀 26건이 38.789초에 통과했다. 실제 PostgreSQL의 시장 scope/비과거 부모 closure 2건도 2.358초에 통과했다. 운영 seed 추출은 도구의 연결-local 30초 timeout으로 중단됐으며, SQL 종류를 추측해 인덱스를 바꾸지 않았다. 추출 세션만 명시적 최대 300초 timeout을 선택하고 실패 테이블/filter/keys/query/경과시간을 남기는 보완과 오류 부분 파일 제거 검사 6건을 통과했다. 조회 경과시간은 client yield 처리도 포함하며 개별 PostgreSQL 실행시간으로 해석하지 않는다. 보완한 실제 추출 결과는 대기 중이다.

실제 raw에는 `news_article_fetch_attempts`도 있어 403·timeout·원문 제외 등의 저장된 요청 기록을 v2 근거 원장에 보존하도록 추가했다. 준비자료 누락을 자동 제외하는 근거로 쓰지 않는다. 이 테이블 64행 표본은 payload 약 4.22GB, 여유 포함 약 6.33GB를 추가 추정하며 [원장](../artifacts/news-archive-capacity-20261013-v2-final/fetch-attempt-capacity.json)에 기록한다. 따라서 현재 총 필요 추정은 약 **338GB+최신 seed**다. 앞의 332GB는 요청 실패 원장 추가 전 측정이며 실제 peak의 보장은 아니다. 전체 봉인 시 raw stat/WAL/요청 원장 존재도 재확인한다.

대량 사건 처리 진도는 frozen target_count와 transaction 안의 next_ordinal로 갱신하며 매 batch의 pending 전수 COUNT를 제거했다. SQL trace와 주입 실패로 같은 batch rollback 뒤 재개 결과/남은 수가 실제 작업 원장과 일치하는지 검사한다. 이 변경은 처리 순서나 사건 판단 정책을 바꾸지 않는다.

운영 seed 재추출 완료: 전용 PG 2건(2.037초) 통과 후 repeatable-read snapshot `2026-10-12 16:42:14.005301+00`, owned 기사 55,053개/support 13,670개/총 311,488행/732,139,520바이트를 확보했다. NAS와 PC의 SHA-256 `dc58ccee8c0c289b5374fd147427caf0cd424bbf53b9b8870cc21366c3d8beca` 및 PC full integrity `ok`를 확인했다. [추출/복사 근거](../data/historical_collection/archive-v2-20261013/seed-copy.json)는 완성 뉴스 파일이 아니라 기존 ID 보존 입력이다. 과거 BODY/RULE job 84,000행도 당시 상태 근거로 보존하며 실행하지 않는다.

부모 저장 함수·DB schema·staging/hash·seed/목록 helper까지 construction digest에 포함했다. 다른 채팅의 작업본 수정과 긴 생성 작업을 분리하기 위해 실제 실행에 필요한 src/scripts 536파일(8,331,864바이트)을 PC 세대 안에 고정했다. source HEAD는 main `941347e`, dirty 작업본이며 [파일별 hash](../data/historical_collection/archive-v2-20261013/code/code-files.json)와 construction digest `58cc80ab29d0569721f43c7435130d3d184c4b8c820c513775a2518d7a916a1d`를 기록했다. 실행 import 경로도 이 세대 안임을 확인했다. 이 고정본으로 PG seed의 SQLite 물질화를 시작했고 검색 준비본 27.16GB의 복사 뒤 무결성 검사는 진행 중이다. 최신 진도 시험의 연결 정리를 명시한 최종 로컬 27건은 26.483초에 통과했다. 완성 전체 archive/봉인/실자료 NAS 읽기 검증은 남아 있다.

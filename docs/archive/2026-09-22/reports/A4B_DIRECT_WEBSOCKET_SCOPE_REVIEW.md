> **과거 기록** · 원래 경로: `reports/A4B_DIRECT_WEBSOCKET_SCOPE_REVIEW.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# A4b 재검토 결정과 Sol 구현 순서

기준일: 2026-09-13. **설계 재검토와 단계 0a~0c 로컬 구현 완료, 단계 1 이후는 미구현**. 0a~0c는 PC 코드와 합성 임시 DB만 변경·검증했으며 실데이터·NAS 설정은 변경하지 않았다. [전체 설계](CONTINUOUS_RESEARCH_ACCOUNT_SCOPE_REVIEW.md) §7과 [전체 구현 계획](CONTINUOUS_RESEARCH_ACCOUNT_IMPLEMENTATION_PLAN.md) A1/A3/A4의 보완 계약이다.

결정: PC가 키움에서 직접 확인한 계좌를 인증된 HTTPS 요청으로 NAS의 기존 registry와 대조한다. 확인 뒤에는 PC의 DPAPI 보호 지문으로 직접 REST/WS를 재검증한다. 서버 HMAC 키를 PC에 복사하거나 사용자가 UUID를 골라 연결하지 않는다. **0a~0c 완료 뒤 다음 구현은 단계 1의 로컬 신원 저장과 고정 자격 수명이다.**

## 1. 현재 구현과 감사 결과

뉴스 DB v2·일지 DB v7과 개별 저장소 기능은 존재한다. 그러나 A4b의 뉴스 연결·구형 수입 방어가 끝났다는 직전 완료 판정은 철회한다. A3도 NAS 조회 세션은 구현됐지만 직접 자격과 저장 binding의 일치 검증은 미완료다.

| ID | 확인된 경로/원인 | 영향 | 확인 수준 |
| --- | --- | --- | --- |
| B01 | 일지→메인 `_poll_journal_news_request/_send_news_command`에서 scope 누락→뉴스 프로세스 legacy 기본값 | 정상 뉴스 선택이 계좌별 일지와 연결되지 않음 | 실제 relay 메서드 추출 실행: `relay_preserved_scope_keys=[]` |
| B02 | 일지 sync는 먼저 `journal_sync_states` GET, 서버 허용 목록에는 누락 | 첫 요청에서 일지 동기화 중단 | 실제 FastAPI route: v1 states 404, v2 states 200; 실제 sync service+TestClient API adapter도 첫 GET에서 404 |
| B03 | 묶음 편집 merge/split에서 `assign_group`에 scope 미전달 | 검증 체결의 수동 묶음이 legacy/v1으로 저장됨 | 임시 DB split: `fill:v2` key의 override가 `legacy/unknown` |
| B04 | cycle/group 편집·삭제 표식의 collection이 v1에 고정; merge/delete의 실제 대상 scope 검사 부족 | 삭제 부활, v1 문서가 기존 검증 행을 덮어씀 | 임시 DB: 삭제 뒤 scoped override 재등장; 같은 key의 v1 merge 뒤 `kiwoom → legacy` |
| B05 | 기존 `journal_v2_sync=true`를 새 뉴스 collection 지원으로 해석 | A4a NAS에서 콘텐츠 동기화 중단 | 구 서버 fake: 새 collection 404, 먼저 받은 기사도 반영 0건 |
| B06 | 뉴스 unlink에 tombstone 없음 | 원격 연결이 다시 나타남 | 임시 DB: 삭제 직후 `set()`, 원격 replay 뒤 `{'article'}` |
| B07 | v1 뉴스 key에 group_id를 새로 포함 | 같은 owner에 중복 문서 생성 | 변경 전 코드 대조 및 실제 export key 확인 |
| B08 | sync scope 검증이 빈 값/legacy 검사에 그침 | UUID·환경·키 불일치 문서 수락 가능 | 뉴스 validator가 `not-a-uuid` 수락; 일지 경계는 코드 확인 |
| I01 | 직접 REST adapter는 supplied binding을 붙이기만 함; factory는 환경 내 단일 binding 선택 | 다른 키로 바뀐 계좌를 기존 계좌로 표시할 수 있음 | 호출 경로 확인; 실제 계좌 교체는 미실시 |
| I02 | 직접 WS는 token만 받고 00을 resolver 없이 파싱; fallback WS가 REST와 별도 client 생성 | 직접 체결 legacy 저장, 신원 TR 추가 시 limiter 중복 위험 | 호출 경로 확인 |
| I03 | DPAPI는 최신 profile binding만 저장하고 고정 .tmp로 갱신 | 로컬 재확인 정보 부족, A→B→A 복원·프로세스 동시 쓰기 미보장 | 코드 확인; 동시 실행 재현은 미실시 |
| I04 | 범용 v1 query가 ka00001 원문도 반환 가능 | 신규 HTTPS 경계 외 원문 전달 경로 잔존 | route/broker allowlist 확인; 실제 원문 요청은 미실시 |

B01~B08 재현은 운영 DB를 사용하지 않았고 종료 코드 0이다. B02는 실제 서비스/라우트 연결 검사이며 urllib 기반 CentralContentClient의 HTTP 전송까지 실행한 것은 아니다. 기존 저장소·migration·정상 namespace·동일 source 재수입 테스트 5개도 통과했다. 직전 전체 회귀 879개 통과는 당시 범위의 결과이며 위 경로의 정상 동작 증거가 아니다.

추가 코드 확인: `journal_legacy_imports`에는 source owner가 없고 fills revision으로 `filled_at`을 쓴다. 체결 시각은 문서 수정 revision이 아니므로 같은 시각의 내용 정정과 다른 owner의 동일 key를 충분히 구별하지 못한다. 이 두 변형의 실행 회귀는 0c에서 추가한다.

## 2. 확정한 계좌 연결 계약

기존 설계의 “원문 전달이 필요한 등록은 인증된 암호화 연결로 제한한다”와 현재 단일 NAS 소유자 신뢰 모델을 구체화한다. 새로운 원문 전송 허용 여부를 사용자에게 다시 묻을 사안은 아니다.

1. PC가 **실제 조회에 사용할 고정 자격의 client**로 ka00001을 호출한다. 상품 구분을 포함한 전체 계좌값을 비교한다. 부분값·마스킹·임의 문자를 제거해 억지로 정상값을 만들지 않는다.
2. PC→NAS 대조는 인증서 검증이 켜진 HTTPS와 기존 NAS API 인증만 허용한다. raw 계좌는 POST body의 요청 메모리에서만 사용한다. 키움 App Key/App Secret/access token은 보내지 않는다.
3. NAS는 기존 HMAC 키로 요청값을 지문화해 **이미 키움으로 검증된 registry 계좌를 조회**한다. 일치하면 canonical scope와 기존 검증 근거를 반환한다.
4. 이 요청으로 registry나 NAS 활성 profile/binding을 생성·교체하지 않는다. 계좌번호를 안다는 사실은 PC의 App Key 소유를 NAS가 독립 증명한 것이 아니다. 신뢰하는 소유자 PC의 관측값 대조이며, 다중 사용자 계좌 접근권 증명으로 재사용하지 않는다.
5. 일치 없음은 `ACCOUNT_NOT_REGISTERED`. PC에서 확인한 local origin을 유지하고 중앙 결합만 보류한다. NAS 신규 등록은 기존 NAS broker 검증 절차로 수행한 뒤 자동 대조를 재시도한다. 미등록 계좌를 임의의 기존 UUID에 연결하지 않는다.
6. PC는 별도 랜덤 로컬 secret의 계좌 HMAC을 DPAPI에 보존한다. 서버 지문/비밀을 내려받지 않는다. NAS 장애 중에도 키움의 fresh ka00001 결과로 로컬 지문을 재확인한다. 키움까지 단절되면 저장 binding만으로 신규 계좌 자료를 인증하지 않는다.

nonce/challenge는 선택하지 않는다. NAS가 raw를 보존하지 않아 추가 broker 조회가 필요하고, 공개 nonce와 계좌 digest는 계좌값을 아는 요청자의 키 소유를 증명하지 못한다. TLS는 여전히 필요하므로 추가 세션 프로토콜의 이점이 작다. 수동 alias 선택도 오선택 위험 때문에 기본 흐름으로 쓰지 않는다.

### 식별자와 수명

- origin scope/저장 ID는 불변이다. 최초 온라인 대조 성공이면 중앙 UUID를 바로 사용한다. 오프라인에서 먼저 발급했다면 그 local UUID와 기존 fill/group/news ID를 계속 보존한다.
- canonical scope는 검증된 중앙 매핑이 있으면 해당 계좌, 없으면 local origin으로 독립 조회한다. 중앙 결합 때 origin key를 재발급하지 않는다.
- PC profile ID는 키와 독립된 안정된 ID다. profile의 활성 identity 포인터와 과거 identity별 지문/origin을 분리해 A→B→A에서 원래 A를 찾는다.
- NAS binding revision과 PC profile revision/generation은 다른 소유자의 값이다. 직접 context는 로컬 검증 revision을 쓰고 중앙 대조 근거는 별도 보존한다. NAS revision을 PC 자격의 검증 revision처럼 복사하지 않는다.
- 고정 settings/client + 검증 session + generation을 한 런타임에 묶는다. 자격 변경·인증 무효화·WS 재연결 시 재검증한다. REST 각 응답/완료 직전 generation이 달라지면 batch 전체를 버린다.
- 같은 프로세스/profile의 신원 TR·직접 REST·WS token 공급은 같은 client를 사용한다. 메인과 매매일지는 서로 다른 프로세스여서 인스턴스를 공유할 수 없으므로 아래 1단계의 프로세스 간 요청 슬롯을 함께 사용한다. real 5회/초와 mock 1회/초는 계속 분리하며 여러 PC/NAS 전체의 quota 중앙화는 이번 범위에 넣지 않는다.
- WS는 연결/재연결 때 신원을 확인하고 이벤트마다 9201을 메모리의 로컬 verifier와 비교한다. 체결마다 REST를 호출하지 않는다. 누락·불일치에서는 계좌 이벤트만 버리고 일반 시세를 유지한다.
- 미검증 **신규** 직접 계좌 자료를 legacy로 자동 수입하지 않는다. legacy는 기존 자료 읽기/이전 전용이다.

### HTTPS 운영 조건

확인한 PC 설정은 `personal_server`, `http://192.168.0.5:8787`, fallback 활성이다. **현재 앱 URL이 HTTP인 사실만 확인했으며 NAS reverse proxy의 HTTPS 제공 여부는 미확인**이다. 신규 대조 활성화 전에 실제 HTTPS origin·인증서·연결을 검증한다.

HTTP·인증서 불일치·redirect를 거절한다. `verify=False`, 사설 IP 예외, 임의 `X-Forwarded-Proto` 신뢰는 허용하지 않는다. reverse proxy의 신뢰 범위와 backend 직접 접근 제한을 배포 절차에 명시한다. raw는 request repr, validation 오류의 `input`, 예외, 로그, query cache, 관측 저장, 백업에 남기지 않는다.

ka00001은 **내부** broker allowlist/limiter에 유지하되 **공개 범용 v1 전달은 명시 거절**한다. 이는 민감 원문 전달의 좁은 정책 변경이다. v1 시세 API 형태는 유지하며 해당 TR의 과거 공개 호출이 더 이상 성공하지 않는다는 점을 API 계약/회귀에 명시한다.

## 3. 단계별 구현 인수인계

아래 수정 대상은 `src/kiwoom_monitor/` 기준이다. 새 계층 없이 기존 책임을 보완하고 각 단계 검증 뒤 다음으로 이동한다. **단계 0의 오류를 남긴 채 A4b NAS 이미지를 배포하지 않는다.**

### 0a — UI 명령과 수동 묶음 scope 복구 (PC)

**상태: 완료 (2026-09-13).** 일지 sender의 origin/canonical scope를 메인 relay와 뉴스 프로세스가 그대로 전달하며, 두 scope가 모두 없는 구 명령만 legacy로 처리한다. 하나만 있거나 구조·UUID·broker/environment 관계가 잘못된 명령은 화면 문맥을 바꾸기 전에 거절한다. 뉴스창은 origin 변경도 문맥 변경으로 판단한다. merge/split은 모든 입력 체결의 origin/canonical 동일성을 확인하고 저장소에 두 scope를 명시하며, 저장소는 실제 fill 행과 두 scope를 대조한 뒤 override와 v2 export 행에 보존한다. 기존 v2 fill-key/legacy override도 다음 수동 편집에서 scope가 복구된다. 집중 69개와 DB/export 인접 49개 unittest가 각각 종료 코드 0으로 통과했다.

**목적:** 정상 화면 동작이 계좌별 저장 계약을 유지하게 한다.

**수정 대상:** `journal_process.py`, `presentation/main_window.py`, `news_process.py`, `presentation/stock_news_window.py`, `application/trade_group_edit_service.py`, `infrastructure/persistence/journal_trade_repository.py`.

**입출력:** 일지 origin/canonical scope→메인 JSON relay→뉴스창→repository까지 같은 값 전달. scope 없는 구 명령의 legacy 호환과, scope가 있는데 잘못된 새 명령의 거절을 구별한다. merge/split은 입력 체결 scope 동일성을 확인하고 assign_group에 명시 전달한다. 후착 요청에 현재 선택 계좌를 붙이지 않는다.

**완료 기준:** real A/mock A의 같은 종목·group도 뉴스/묶음 독립. malformed scope로 legacy 행을 만들지 않음. 기존 legacy 편집 유지.

**테스트:** `test_journal_detached_flow`, `test_main_window`, `test_news_process`, `test_stock_news_repository`, `test_trade_group_edit_service` 확장. sender→relay→receiver→임시 DB 통합 회귀, 전환 직후 이전 요청, merge/split 뒤 DB scope/export collection 검사. 중계를 생략한 저장소 시험만으로 완료하지 않는다.

### 0b — 서버 컬렉션과 구 NAS 호환 복구 (PC+NAS)

**상태: 로컬 구현 완료 (2026-09-13), NAS 배포 보류.** 서버 v1 allowlist에 `journal_sync_states`를 추가했고 새 뉴스 연결은 독립 `journal_news_links_v2` capability로만 협상한다. 0b 단계에서는 0c 삭제 계약 전이라 새 flag를 false로 유지했다. 현재는 0c 계약과 검증이 완료되어 서버가 true를 광고한다. optional 뉴스 연결 404는 pending 진단으로 남기면서 일반 기사·AI·테마를 계속 처리하고 해당 cursor는 올리지 않는다. 일지 삭제 상태 404는 해당 실행의 merge/upload를 시작하지 않으며 401/500/timeout은 그대로 실패한다. FastAPI 실제 app과 `CentralContentClient`, 두 임시 일지 DB의 전체 HTTP 왕복을 포함한 집중 회귀로 확인했다.

**목적:** A4a 서버 연결에서도 일반 뉴스·테마를 유지하고 일지 동기화 오류를 드러낸다.

**수정 대상:** `central_server/app.py`, `central_server/contracts.py`, `infrastructure/central_content_client.py`, `infrastructure/central_content_sync.py`, `infrastructure/central_journal_sync.py`.

**입출력:** 서버에 v1 journal_sync_states 허용 추가. 새 뉴스 동기화는 독립 `journal_news_links_v2` capability로 협상하며 없으면 요청/업로드하지 않는다. 기존 journal_v2_sync만 있는 A4a를 뉴스 v2 지원으로 해석하지 않는다. 신규 flag는 0c 삭제 계약까지 준비된 서버에서만 true다.

optional collection 부재는 보류 상태로 남기고 일반 콘텐츠는 계속 처리한다. 일지 삭제 상태를 읽지 못하면 해당 일지 merge/upload를 보류한다. 404와 인증/네트워크 실패를 구별하고 실패 collection의 cursor를 완료로 올리거나 scoped 자료를 v1으로 내려보내지 않는다.

**완료 기준:** 현재 app의 실제 HTTP API와 일지 전체 sync 왕복 성공. 구 NAS의 새 collection 부재가 기사/테마 반영을 막지 않음. 기능 부재 진단 표시.

**테스트:** `test_central_server_app`, `test_central_content_client`, `test_central_content_sync`, `test_central_journal_sync` 확장. 임시 FastAPI+실제 client+임시 DB, 구 flag만 있음/신규 flag 없음·false, optional 404, 401/500/timeout, 첫 수신 뒤 다음 collection 실패, 재시도.

### 0c — 삭제·namespace와 원본 식별 보완 (PC+NAS)

**상태: 로컬 구현 완료 (2026-09-13), NAS 배포 보류.** news v3/journal v8 신규 migration으로 tombstone과 source owner/content hash 원장을 추가했다. 동기화 상태는 origin owner까지 키에 포함하고 v7의 v1 tombstone을 legacy scope로 보존하며, 복원 불가능한 v2 scope만 unknown으로 격리한다. v1/v2 기존행·UPSERT·DELETE scope를 분리하고 malformed UUID, owner/key 불일치, alias 증명 없는 canonical 변경을 보류한다. 뉴스 v2는 origin 기반 hash key를 사용하고 삭제 우선·명시 재연결 revision을 적용한다. 중앙 입력 검증 뒤 `journal_news_links_v2=true`로 전환했다.

**목적:** 구 앱·다른 PC의 재업로드가 검증 계좌를 덮어쓰거나 삭제를 되살리지 못하게 한다.

**수정 대상:** 위 두 sync 모듈, `infrastructure/persistence/journal_trade_repository.py`, `stock_news_repository.py`, `journal_schema.py`, `news_schema.py`(마지막 세 파일도 같은 persistence 경로), 중앙 콘텐츠 입력 검증. 범용 sync 엔진을 새로 만들지 않는다.

**입출력 계약:**

- 표식은 실제 origin scope와 정확한 collection을 따른다. 명시 allowlist로 v1/v2를 분리하고 DELETE/UPSERT의 기존 행도 scope를 검사한다. v1에서 v2 key가 오면 verified 행에 접근하지 않고 충돌로 보류한다.
- v2 입력에 AccountScope 수준 broker/environment/UUID 검증, origin/canonical 동일 broker·환경, key와 scope 일치 검증을 적용한다. canonical이 origin과 다르면 검증된 alias가 필요하다.
- v1 뉴스는 **owner=group_id, key=stock_code|identity** 유지. v2 뉴스는 owner=불변 origin_account_ref, key=버전 접두어+`(origin_scope, group_id, stock_code, identity)` 정규 JSON의 SHA-256. canonical alias 변경은 key 변경 사유가 아니다.
- 뉴스는 물리 삭제 대신 `is_deleted/updated_at`을 보존하고 일반 조회는 활성 행만 반환한다. linked_at은 최초 연결시각을 유지한다. 같은 revision 충돌은 삭제 우선, 재연결은 명시 사용자 편집의 새 revision이다. 재전송은 revision을 바꾸지 않고 서버 envelope updated_at으로 삭제를 취소하지 않는다.
- legacy 원본은 `(source_collection, source_owner, source_key, normalized_content_hash)`로 구별하고 target key·원본 관측/수정시각을 보존한다. filled_at/captured_at을 수정 revision이라 부르지 않는다. 같은 내용 echo는 owner/업로드 시각만 달라져도 target tombstone을 넘지 못한다.
- 같은 체결시각의 다른 내용은 조용히 버리거나 최신으로 추정하지 않는다. 명확한 수정 revision이 없으면 충돌로 보류한다. 삭제 target의 legacy 복원은 명시 복원만 허용한다. 수정된 새 앱끼리 삭제를 전파하며 구 앱 화면까지 계약을 지킨다고 보장하지 않는다.
- news v2/journal v7은 이미 PC에서 열렸을 수 있다. 기존 migration을 다시 쓰지 말고 다음 버전(news v3/journal v8 예정)을 추가한다. 기존 원장·origin·참조 보존, 뉴스 상태/시각 초기화와 rollback을 검사한다.
- v7에 없던 owner/hash는 복원할 수 없으므로 unknown으로 보존한다. 기존 수입 원장·삭제표식을 버리거나 과거 hash를 지어내지 않는다. 기존 뉴스 v2의 canonical owner/평문 key와 새 origin owner/hash key 공존도 호환 읽기·동일 원본 중복 차단·삭제표식 연결로 처리한다. 이전 문서의 즉시 삭제를 migration 전제로 삼지 않는다.

**완료 기준:** v1 merge/삭제가 verified 행을 갱신하는 경우 0개. 재시작·다른 owner·재업로드 뒤 삭제 부활 0개. v1 key 유지. alias 전후 뉴스 원본 ID 보존. 애매한 legacy 정정은 충돌로 남음.

**테스트:** schema/repository/sync/backup에 B03~B08 정식 회귀. PC A 편집/삭제→서버→PC B→오래된 PC replay, 같은 key 다른 계좌/v1-v2, 같은 filled_at 정정, source owner 충돌, 높은 envelope 시각의 같은 내용, migration 실패/최신 backup restore 거절. PostgreSQL 실제 왕복은 배포 후보 통합 검사에서 별도 수행한다.

### 1 — 로컬 신원 저장과 고정 자격 수명 (PC)

**목적:** 저장 UUID만으로 현재 직접 계좌를 신뢰하는 I01/I03을 제거한다.

**수정 대상:** `infrastructure/kiwoom_rest/account_identity.py`, `local_account_binding.py`, `client_factory.py`, `settings.py`, `client.py`(같은 kiwoom_rest 경로), `bootstrap.py`, `journal_process.py`, `application/account_identity.py`.

**입출력:** 실제 client의 ka00001→로컬 지문+identity별 origin+profile 활성 포인터+로컬 revision. DPAPI v2에는 별도 로컬 verifier secret을 보존하고 raw 계좌·키움 App Key/App Secret/access token은 중복 저장하지 않는다. v1 binding은 verifier 없는 참고값이다. fresh 관측과 2단계 중앙 대조에서 기존 ref 일치를 확인한 뒤에만 승격한다. 확인 전에는 과거 UUID를 재사용하지 않고 복구 대기로 둔다. 환경 내 binding이 하나라는 이유로 선택하지 않는다.

파일 갱신은 **프로세스 간 잠금** 아래 최신 파일 재읽기→generation 검사→unique 임시 파일→atomic replace다. thread lock만으로 다중 프로세스 안전을 주장하지 않는다. 해독/키 유실은 IDENTITY_RECOVERY_REQUIRED이며 새 ref를 조용히 만들지 않는다. 상태 holder가 필요하면 검증 수명/저장 책임만 소유한다.

실제 `journal_process.py`와 `bootstrap.py`는 각각 client를 만들고 요청 잠금/최종시각도 인스턴스별이다. 기존 `KiwoomRestClient._wait_for_request_slot()`에 PC 데이터 경로·안정된 profile ID·환경별 프로세스 간 잠금과 마지막 요청 시각을 쓰는 작은 공통 슬롯 경계를 연결한다. 인스턴스 RLock은 토큰/응답 상태 보호에 유지한다. 계좌값·키를 슬롯 파일에 쓰지 않고 real/mock의 슬롯을 분리한다. 신원 확인·직접 REST·token 공급이 이 경계를 우회하지 않게 하며 새 broker 프로세스나 범용 scheduler는 만들지 않는다.

**완료 기준:** 같은 계좌 키 교체는 같은 origin, 다른 계좌는 분리, A→B→A에서 A 복원. real/mock 분리. 자격 교체 뒤 구 context 사용 불가.

**테스트:** `test_account_identity`와 factory/보호파일/client 회귀 추가. 정상/마스킹/누락/임의 문자, DPAPI v1→v2, 두 프로세스 저장, 교체 직전 실패, 보호 키 복원/미복원. 두 프로세스 같은 profile의 합산 요청 간격과 real/mock 독립, 프로세스 종료 후 잠금 해제, PC 재시작/시계 변경 후 무한 대기 방지도 확인한다. 실제 DPAPI는 이 PC의 임시 파일에 합성 값으로 확인한다.

### 2 — 인증된 NAS 계좌 대조 API (PC+NAS)

**목적:** 로컬 확인 결과를 기존 중앙 UUID와 자동 연결한다.

**수정 대상:** `central_server/app.py`, `contracts.py`, `database.py`의 registry read 책임과 SQLite/PostgreSQL 구현, `infrastructure/kiwoom_rest/remote_client.py`, `application/account_identity.py`, 기존 접속 설정/배포 문서.

**입력 계약:** 새 `POST /api/v2/accounts/resolve`, 인증된 HTTPS, body `{environment, account_number}`. broker는 kiwoom 고정. client가 account_ref·서버 profile·callback URL을 지정하지 않는다.

**출력 계약:** `{canonical_scope, matched_binding: {credential_profile_id, binding_revision, verified_at, verification_method}, matched_at}`. matched binding은 NAS가 이미 검증한 근거이며 PC의 키 검증시각/로컬 revision과 구별한다. 오류는 TLS_REQUIRED/ACCOUNT_NOT_REGISTERED/ACCOUNT_IDENTITY_UNVERIFIED/IDENTITY_RECOVERY_REQUIRED 같은 고정 코드이며 raw를 포함하지 않는다. `account_identity_resolve_v2` capability는 기능과 인증된 배포 경계가 준비된 경우만 true다.

lookup은 registry/binding을 쓰지 않는다. HMAC key 유실·교체를 새 UUID 자동 발급으로 숨기지 않는다. 공개 v1 ka00001 거절과 내부 broker 정상 동작도 이 단계 범위다.

**완료 기준:** 새 PC·같은 계좌 키 교체에도 같은 중앙 ref. 다른 계좌/환경 연결 불가. HTTP·인증서 실패는 raw 전송 전 차단한다. 최초 정상 HTTPS 요청 뒤 redirect 응답이 오면 다른 URL로 raw를 재전송하지 않는다. 모든 실패에서 raw 로그/오류 노출이 없고 NAS 활성 binding은 불변이다.

**테스트:** fake registry/reader+API 인증/오류/미등록; 로그·validation input·예외·DB의 합성 raw 누출 검사, proxy 헤더 위조, TLS 인증서/redirect 거절, 구 NAS capability 없음, v1 거절/내부 조회 정상. 실제 HTTPS 종단은 구현 후 운영 검증한다.

### 3 — 직접 REST batch와 NAS 장애전환 (PC)

**목적:** 현재 자격을 재확인한 완료 batch만 일지에 저장한다.

**수정 대상:** `infrastructure/kiwoom_rest/account_query.py`, `client_factory.py`, `failover_client.py`(같은 kiwoom_rest 경로), `application/trade_history_service.py`, `trade_cost_service.py`(같은 application 경로), `presentation/journal_workers.py`.

**입출력:** batch 시작 fresh 확인→scope/profile/revision/generation 고정→같은 client로 페이지 조회→각 응답/완료 시 generation 검사→기존 AccountQueryBatch. 동시 신원 요청은 같은 runtime에서 합치되 다른 자격 세대에 이전 검증을 재사용하지 않는다. context 확장은 선택 필드 우선이며 시세 tuple 계약은 유지한다.

현재 AccountQueryContext는 scope 하나뿐이다. 기존 scope는 origin 의미로 유지하고 선택 canonical_scope와 effective_scope 해석을 추가한다. history/cost 변환은 두 값을 각각 기존 TradeFill/DailyTradeCost 필드로 전달한다. failover/화면 비교는 검증된 effective_scope, 저장 key는 origin을 사용한다. alias 없는 기존 NAS context는 canonical 기본값=origin으로 읽는다. alias 뒤 같은 직접 자료를 재조회해도 origin key가 바뀌지 않는 회귀를 포함한다.

NAS A→직접 A는 검증된 canonical 매핑이 있을 때만 첫 페이지부터 재시작한다. A→B/미매핑 local origin은 저장 중단한다. transport별 binding revision 숫자의 일치로 같은 계좌라고 판단하지 않는다. UI 후착 저장에도 원래 context를 유지한다.

**완료 기준:** mirror=A/현재 키=B이면 A 행 0개. NAS 단절·PC 재시작 뒤 fresh 직접 검증 가능. 부분/중간 변경 batch를 완료로 저장하지 않음.

**테스트:** `test_account_query`, `test_failover_kiwoom_client`, `test_journal_workers`와 history/cost 회귀. 동일 계좌 키 교체, A/B, 2페이지 중 자격/인증 갱신, 완료 직전 generation 변경, NAS 실패 첫 페이지 재시작, real/mock 제한 분리와 같은 runtime의 신원/조회 간격 공유.

### 4 — 직접 WS와 오프라인 origin 결합 (PC+NAS)

**목적:** 직접 모드·NAS 장애 중 체결에도 정확한 계좌 출처를 유지한다.

**수정 대상:** `infrastructure/kiwoom_rest/realtime_worker.py`, `realtime.py`(같은 경로), `bootstrap.py`, `application/account_identity.py`, 기존 alias 저장/해석과 journal/news scope 조회. 새 WS 연결은 추가하지 않는다.

**입출력:** 같은 runtime의 fresh session→00의 9201 대조→raw 제거→origin/canonical scope+generation 이벤트→snapshot. 재연결 전 재검증하고 이전 세대 이벤트를 새 계좌로 재표시하지 않는다. 무효 generation은 저장 보류/폐기와 진단을 남긴다.

04는 현재 직접 worker가 구독하지 않고 직접 소비자도 없다. 이번 필수 완료는 00이다. 04 소비자를 연결할 때 같은 검증 후 비공개 재조회 알림으로만 사용하며 예수금을 주문가능금액으로 쓰지 않는다.

local origin은 2단계의 동일 계좌 대조 성공 후 기존 alias 경계로 중앙 UUID와 연결한다. immutable·같은 환경·무순환·재시도 멱등을 유지한다. origin ID와 메모/뉴스 참조는 보존하고 canonical 해석만 갱신한다. raw는 일반 alias 콘텐츠로 보내지 않는다. 실제 체결 ID 없는 중복 후보는 임의 합산하지 않는다.

중앙 alias 함수는 현재 내부 함수이므로 별도 인증 HTTPS `POST /api/v2/accounts/aliases`를 추가한다. 입력은 `{environment, account_number, origin_scope}`이고 PC는 현재 session의 fresh 관측값만 보낸다. 서버가 같은 요청에서 registry를 대조해 canonical/binding을 결정하고 기존 `link_local_scope_to_verified_binding`을 호출한다. client가 임의 canonical ref나 NAS profile을 지정하지 않는다. 출력은 기존 AccountScopeAlias 직렬화이며 같은 연결은 멱등, 다른 계좌/환경/기존 대상 변경은 거절한다. raw 처리·TLS·오류 규칙은 resolve와 동일하고 일반 콘텐츠 sync에는 익명 검증 alias만 노출한다. 이 API까지 준비된 빌드에서만 `account_identity_resolve_v2`를 true로 광고한다.

**완료 기준:** NAS/직접 00이 같은 canonical 계좌에 연결되고 다른 계좌/환경은 분리. 최초 offline origin도 나중에 결합해 참조 유지. 검증 실패 중 시세 지속/오염 행 0개.

**테스트:** realtime/bootstrap/entry snapshot/alias 회귀; 9201 정상/누락/불일치, 재연결·자격 전환 후착, NAS→직접→NAS, REST/WS client 공유, 이벤트당 신원 TR 없음, offline→재시작→alias→두 PC sync→뉴스 보존. 장중 실수신 검증을 위해 주문을 생성하지 않는다.

### 5 — 통합 검증과 배포 판정

**목적:** 구현·사용 가능·NAS 반영 상태를 구별한다.

**수정 대상:** 기존 집중/핵심 회귀, `scripts/check_postgres_integration.py`, API_CONTRACT/DB_SCHEMA/MODULE_MAP/ARCHITECTURE_CURRENT와 NAS 배포 상태 문서.

**입출력:** 완료 코드+동결 build+합성 두 계좌 fixture→UI/API/DB/재시작/두 PC 왕복 보고. 0~4 이후 관련 집중 회귀와 필수 핵심 묶음의 범위·종료 코드를 기록한다. 문서만 수정한 이번 재검토에서 전체 제품 회귀를 재실행하지 않는다.

**완료 기준:** B01~B08/I01~I04 회귀, SQLite migration/backup, 실제 PostgreSQL 왕복, HTTPS 연결, 보호 키/registry 보존, 실제 앱→NAS/직접 읽기와 계좌별 뉴스/묶음/삭제 확인. 장중 00 미수신은 별도 미검증으로 남긴다.

**테스트 방법:** 합성 real/mock 계좌의 UI 편집→API→임시 DB→재시작→다른 PC 동기화를 연결해 전체 참조와 삭제 상태를 비교한다. 실제 NAS에서는 PostgreSQL 검사기의 rollback 결과와 익명 context만 확인한다. 단계별 fake/SQLite 결과, 실제 HTTP/HTTPS 결과, PostgreSQL 결과, 장중 수신 결과를 따로 보고한다.

NAS는 코드/세 build 값/검사기 준비 뒤 기존 파일 백업과 추가·덮어쓰기로 동기화한다. .env/postgres-data/server-data를 보존한다. 사용자 이미지 빌드 뒤 health.server_build/capability/인증/연결을 확인한다. 이번 계획은 주문 활성화 범위를 변경하지 않는다.

## 4. 보고와 후속 단계

각 구현 보고에 변경·검증·설계 차이·남은 항목을 구별한다. 이번 선택으로 추가 Astra 판단을 요청할 문제는 없다. 실제 구조와 계약이 근본 충돌할 때만 해당 부분을 재검토한다.

A4 완료 뒤 전체 계획의 CR1(다기간 데이터)로 진행한다. 24시간 자동 연구·가설 생성·자동 모의운영은 CR1~CR4/A5/O2-M의 후속 기능이며 계좌 경계 보완만으로 구현됐다고 보고하지 않는다.

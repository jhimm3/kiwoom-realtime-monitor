# 현재 상태 요약

**R3 열두번째 통합(2026-10-21, `7e4fb607ceb09479f6b9c9ed2c03cbf6cdc42bbb`):** 18개 독립slice 통합/등록범위8완료·10진행. 현재 통합 소스 관련62개 실제 모듈·고유1060건/825.210 worker초 통과(경과415.748초, native2개 독립 lane·모듈별 격리). 계획=발견=실행=통과 ID, 전체1154파일/변경41파일·46번째 증분원장·worker/tree 종료·native TEMP 제거를 대조했다. 6073→6128/추가55·수정7·삭제0이며 기존45기록/미결49는 보존한다. packet972ID에서 역사 검토5개를 보강하고 변경검사 모듈 전체를 포함해 중복 없는1060으로 선정했다. 구조1373site/위반0이며 adoption/rules를 초기화하지 않고 exact review3개만 추가했다. 최초 root adoption 수정은 실제 gate가 거절했고 append-only review로 바로잡은 실패 기록도 보존했다. 전체85/검증 완료61/미완료24(진행19·대기1·명시보류4). DAG 완료61·준비2(039/079 동결제출, 실제PG 경계 별도)·잔여검토13·선행/계약대기5(043 실제PG/058 의미/068 출처이행/084 BODY cut/085 주문 소유)·명시보류4(006/008/020/024). 다음은 새 전수조사 없이 잔여13의 미완료 경계부터 선택하고, 같은 prepare/상태/transaction 변경은 선행 완료 후 배정한다. 공유 백로그/F·계약·검사원장은 root만 갱신한다. 재시작후총31slot/보조30생성성공,actualPG/새CI/push/NAS/운영·로컬전수회귀미실행. 085후보는실제잔여주문소유공백으로root미통합. [정본 백로그](../REFACTOR_BACKLOG.md)와[기능별 책임](architecture/r3-ownership.md)을따른다.

## 2026-10-20 열한 번째 병렬 통합: 042·044·051·056·074 / 068 부분

로컬 `736fce8`. 최종 검증2026-10-21: 현재 통합 소스 관련42개 실제 모듈·고유533건/398.798 worker초 통과. 최초483건 뒤 증분 gate가 같은 파일의 기존7ID 누락을 감지해 MainWindow61을 보강했고, 중복11실행을 포함한 aggregate도 거절해 유일한 완료 worker로 선택했다. 이어 root의 검토기록2개(Drive snapshot→056, statistics→074) 분류 오류를 수정하고 제품/검사 본문 변경 없이 수정 원장까지 동결한 현재533건을 다시 실행했다. 이전483+61·최초 gate 실패/중복 거절/수정 전 검증은 예비 기록으로 보존한다. 최종 증분45기록/6073개·추가36개/기존 메서드 변경·삭제0, 전체 소스/계획·실행·통과 ID, 동결22파일/함수별 병합, worker/tree 종료·native TEMP 제거가 일치한다. 구조1374site/위반0/기준 변경0. 별도 WT 비용·반례는 현재 root533과 구분한다.

전체85/검증 완료53/미완료32(진행11·대기17·명시보류4). DAG 완료53·준비0·계약/영향 확인26(독립작업/검토3포함)·선행/외부대기2(043 실제PG/058 의미)·명시보류4(006/008/020/024). 실제보조3개는047 Runner JSON중복slice·075 숨김live일지순수read/render slice·041독립통합검토에배정했다. 033관련112/별도비용1·041관련54/별도비용1·039로컬82/별도비용1·076 preview slice관련48/별도비용1·079 PC관련61은동결제출·root통합대기다. 039신규PG1/079변경PG1은미실행,047/075/076전체책임·복사/matcher잔여는진행유지. 공유원장/F/계약·통합은메인만갱신한다.

전역 config.toml에 공식 agents.max_concurrent_threads_per_session=30을 저장하고 TOML 재조회30·무관 설정 불변/원본 백업을 확인했다. 현재 실행 중인 보조3개에 추가 생성1회를 시도했으나 agent thread limit reached로 거절되어 실제 세션은 메인 포함4/보조3이며 30개 적용 성공이 아니다. 기존 작업 보존을 위해 앱/daemon 재시작·세션 중단·우회 실행은 하지 않았다. CLI0.163.0-alpha.10.2의 features 명령은 --strict-config를 지원하지 않아 설정 수락 검증으로 인정하지 않는다. 새 실행 환경에서도 실제 생성 한도 재확인이 필요하다.

실제 키움/계좌·주문/운영DB·Drive/망/다중PC/NAS·새 hosted GitHub CI/push/배포·로컬 전수회귀는 미실행. R043 실제PG2ID는 외부대기 그대로다. 기존394모듈/77경로·부모F25개/깊이25행·미결49·보류4를 보존한다.

## 2026-10-20 열 번째 병렬 통합: 028·029·045·082

로컬 `8cc2d19`. 통합 현재 소스 관련11개 실제모듈 고유107건/75.253 worker초 통과. 계획/실행/통과 ID·전체지문·동결8파일·worker/tree 종료/native TEMP 제거를 대조했다. 증분44기록/6037건(추가3·기존보강1·삭제0), 구조1374site/위반0/기준변경0. WT의 별도 원본/후속 비용2·legacy seed1은 보존하되 root107에 합산하거나 재실행했다고 보고하지 않는다.

전체85/완료48/미완료37. 준비후보3(042/044/074 검증제출), 계약/영향확인27(051 선택write·085 identity 포함), 선행/외부대기3(043 실제PG/047←042/058 의미), 명시보류4(006/008/020/024). 실제 보조3개는056 Drive ACK·051 선택write 경고/실패·068 NXT 증분 고가를 격리 개발한다. 메인은042/044/074 제출본을 순차 검토·통합하며 공용 백로그/F/계약을 단독 갱신한다.

085는AUTO실행ID를activation UUID로해석하는기존credential prepare 경계의별도미해결후보이며042에서키정책/검증을변경하지않았다. 실계좌/주문/운영DB·실제키움·Drive/네트워크/다중PC/NAS/실제GitHub CI/push/배포/로컬 전수회귀는 미실행. R3-043 실제PG2ID는 미실행/외부대기 그대로다. 원래394모듈/77경로·부모F25개/깊이25행·미결49·보류4를 보존한다.

## 2026-10-20 아홉 번째 병렬 통합: 051 N+1 slice·055

로컬 `d6da121`. 통합 현재 소스 관련15모듈 고유185건/104.818 worker초 통과. 별도 기존 native 진단5건도 통과했다. 계획/실행/통과 ID·전체 소스 지문·함수별 병합·worker/tree 종료와 native TEMP 제거를 대조했다. 증분43기록/6034건·추가4/fixture2·삭제0, 구조1374site/위반0/기준변경0.

전체84/완료44/미완료40. 준비후보3(028/074/082), 계약/영향확인30(051 선택write 오류정책 포함), 선행/외부대기3(043 실제PG/047←042/058 의미), 명시보류4(006/008/020/024). 029·045·028·082·042는 격리 검증제출/메인통합대기, 실제 보조3개는044 동기화ACK·074 렌더비용·068 NXT 증분 고가를 독립 진행한다. 공유 백로그/F/계약은 메인만 갱신한다.

055 등록범위는 완료,051 전체는 진행이다. 운영DB/실계좌·주문/실제키움·망/NAS/실제GitHub CI/push/배포/로컬 전수회귀는 미실행. R3-043 실제PG2ID는 여전히 미실행/외부대기다. 051 선택쓰기의 유효read/UI경고/자동task정책과075 봉보존·다른주기 문제는 별도다.

## 2026-10-20 여덟 번째 병렬 통합: 052·054·060·061

로컬 `017bb03`. 현재 통합 소스 관련17개실제모듈 고유212건/307.507 worker초 통과(일지118/catalog65/외부시장29). 별도현재MainWindow→두QThread→row editor/name lookup/preview 진단1건·8상태도29.698초통과/원본관측JSON동일. 변경16ID·계획/실행/통과·전체지문/동결7파일·worker/tree종료/nativeTEMP제거를대조했다.

전체84/완료43/미완료41. 준비후보5(028/051/055/074/082),계약/영향확인29,선행/외부대기3(043실제PG/047←042/058의미),명시보류4(006/008/020/024). 051N+1 slice는검증제출/메인통합대기이며선택write오류정책은미결유지,055는필수custom봉복사보존을검증해재수정중이다. 실제보조3개는029→045동일테마owner직렬·042bundle/Runner수명·055분리차트를진행한다. 042/029/045의추가계약검증은동시에공유상태를수정하는후속작업으로확대하지않는다. 메인포함4slot·공유원장/F는메인만갱신한다.

복기flush/실패보존·미사용helper제거·기존poll복구·결측volume보존을완료했다. 054의초기OCR장애추정은반증했으며필수동작을새로연결했다고과장하지않는다. [정본](../REFACTOR_BACKLOG.md#r3-052054060061-여덟-번째-병렬-통합--2026-10-20)의정확한비용·연결·잔여를따른다. 실제PG(043대기2건포함)·운영DB/실계좌/주문·실제키움/외부시세·모델정확도/NAS/network·push/hostedCI/배포·로컬전수회귀는미실행이다. 물리I/O/운영오류빈도·강제kill/전원/독립journal child종료및기존미확정031nativeabort원인해결로확대하지않는다.

## 2026-10-20 일곱 번째 병렬 통합: 063·067·072

로컬 `964ddbe`. 현재 통합 소스 관련20모듈 고유231건/317.202 worker초 통과(연구153·뉴스65·기존구조차단13). 변경8 ID·전체 계획/실행/통과, 현재 전체지문/동결9파일, worker/tree종료·nativeTEMP제거를 대조했다.

전체84/완료39/미완료45. 준비후보9(054실제경로확인후제출포함), 계약/영향확인29, 선행/외부대기3(043실제PG/047←042/058의미), 명시보류4(006/008/020/024). 보조3개는052리뷰저장·055분리차트·060→061외부시장동일owner직렬을 진행하며054미사용helper제거는검증제출·메인통합대기다. 052/055는같은파일의서로다른함수·리뷰상태/차트상태를소유하고메인이함수별병합한다. 실제세션4slot(메인+보조3),공유원장/F는메인만갱신한다.

063native전이원자화·067peak기준낙폭정정·072기존AI가용성공유를완료했다. 정확한안전/계산/설정비용·과거결과보존·실제연결한계는 [정본](../REFACTOR_BACKLOG.md#r3-063067072-일곱-번째-병렬-통합--2026-10-20)과기능별계약을따른다. 실제PG(043변경2건포함)·키움/장중/실계좌/주문·운영DB·외부provider/NAS/network·push/hostedCI/배포·로컬전수회귀는미실행이다. 과거 immutable연구결과 재작성/재승인과 원격POST취소·late결과폐기는수락하지않았다.

## 2026-10-20 여섯 번째 병렬 통합: 030·064·065·066

로컬제품·검사 `0a54c35`. 현재 통합 소스 관련 27개 실제 모듈 고유394건/431.634 worker초 통과(journal167/index112/real115). 변경14 ID·계획/실행/통과, 전체 소스 지문, 동결9파일과035 기존본문을 보존한065 추가2메서드 병합, worker/자식 종료·native TEMP 제거를 대조했다.

전체84/완료36/미완료48. 준비후보11(개발·검증제출 포함), 계약/영향확인30(027 잔여·054 실제caller 확인·084 포함), 선행/외부대기3(043 실제PG/047←042/058 의미), 명시보류4(006/008/020/024). 063·067·072는 격리검증 제출/메인 통합대기, 보조3개는060→061 같은owner직렬·054실제연결재평가·052리뷰저장을 독립 진행한다. 실제 세션은 메인 포함4slot이며 공용원장/F는 메인만 작성한다.

R3-030 검증 완료: 기존 EntrySnapshotWriter에 짧은 cache lock 하나로 loader/cache 동시 포착·setter의 dict 교체·현재dict/loader 확인후게시를 묶었다. provider/native I/O·signal·job/backfill은 lock 밖이다. 동일callable/None/A→B→A도 옛 in-flight 결과가 새cache를 채우지 못하고 원래job의 provenance는 보존한다. 실제 AppController의 기존 public setter 적용/rollback을 재사용(제품0). A→B old1/new0→old1/new1은 필요한 새조회 복구이며 이후cache hit 추가조회0, TTL60초/code-only/얕은copy·오류uncached 유지. Controller의 별도 private cache backup/update 전체를 새lock으로 원자화하거나 cross-date/global singleflight를 해결한 것은 아니다.

R3-064 검증 완료: 같은 execution_key의 serialized 빈news [] 재전달은 이미 저장된 news_json을 native CASE로 보존한다. nonempty 정정·명시 save_snapshot_news_backfill(empty) 의미는 유지한다. 같은transaction의 기존stored조회 결과로 retained news metadata UPSERT만 생략해 원래 BACKFILLED/source/effective/available/hash를 유지한다. CASE-only 중간안은 시각을 소급/연기해4assertion 실패했으며 원문을 보존했다. 보완receipt11:30·재전달10:30/11:30/12:30 모두 news1/동일metadata, 실제 was_available_by는11:00불가/11:30가능. retained news INSERT시도11→10, SELECT/연결/BEGIN/COMMIT 각1 유지. 최초empty는MISSING·legacy metadata없음은unknown, 명시empty/nonempty정정의기존metadata전반정책까지재정의하지않는다.

R3-065 검증 완료: JsonRecordOutbox는 missing만{}로 처리하고 read/JSON오류·최상위non-object는 명시실패로 전달한다. 정상dict/member filtering/원자write·replace는 유지, 손상/read실패 put의write/replace1→0으로원본bytes를보존한다. 실제Top20 start는subscriber/task/native전실패하고, put실패의RAM입력과 native성공뒤 remove실패의파일/pending을 명시retry/start복구까지 유지한다. 새로운repair/archive/format/queue/자동retry없음. 원본6method28subcase실패를보존, 실제Windows ACL/NAS 손상을재현한것은아니다.

R3-066 검증 완료: 기존 variadic drain을 모든context에 한번적용해 producer를멈추고 monitor/writer/read를실제합류한뒤에만 dependency를닫는다. closing예외조건을제거해 미저장pending/실패를정상종료로보고하지않는다. 이미끝난failed close에대한새명시close만 완료된noncancelled eventwriter로원본pending/queue를drain한다. closing/admission/binding/settings/order gate는닫힌채유지,자동retry/REST재조회/새queue없음. 실제native precommit/ACKloss 두실패+두event복구 save4/행2/dependency close0→1,추가REST0. waiter취소/동시close는같은살아있는owner를합류한다. native event readback/diagnostic archive까지검증했고 genericcontent route는이collection을노출하지않아실제일지/API end-to-end는확인하지못했다. processkill RAM유실·직접ownertask취소·socket자동복구는해결범위밖이다.

영향F09/F18/F22/F23/F24·책임/비용계약갱신. 실제 키움/장중·실제PG(기존043 변경2 ID 포함)·운영 DB/계좌/주문·외부망/NAS·push/GitHub CI·배포·로컬 전수 회귀·물리I/O/운영발생률은 미검증이다. 이번 증거 검사 통과는 이전043의 외부검증 미완료를 해소하지 않는다.

## 2026-10-20 다섯 번째 병렬 통합: 035·046 완료 / 043 실제PG 대기

로컬 제품·검사 `64a74e0`. 현재 통합 소스 관련 20개 실제 모듈, 고유 305건/124.603 worker초 통과(index107/news113/research85). 계획·실행·통과 ID, 변경 로컬14 ID, 전체 소스 지문·동결12파일, worker/자식 종료와 native TEMP 제거를 대조했다.

전체84/완료32/미완료52. 준비후보16(개발·검증제출 포함), 계약/영향확인29(027 잔여·084 포함), 선행/외부검증 대기3(043 실제PG/047←042/058 의미), 명시보류4(006/008/020/024). 030·064·065·066은 격리 검증제출/메인 통합대기, 063·067·072는 독립 격리개발 중이다. 실제 세션 한도는 메인 포함4이며 보조3 슬롯을 연속 배정하고 공유 원장/F는 메인만 갱신한다.

R3-043 실제 PostgreSQL 변경2 ID 및 해당87건 모듈은 미실행이다. 전용 diagnostic DSN 부재로 증분 실행 증거 검사는 누락을 감지해 실패했으며 성공 처리하지 않았다. 기존 push/PR의 필수 postgres-integration이 postgres_required 전체 모듈을 실행하고 실패·누락을 required 증거로 전달하는 본문만 확인했다. 현재 hosted CI 실행은 없다.

R3-035 검증 완료: 실제 index loop에서 collector advance와 frozen record 추출만 기존 RAM lock 안에 두고, 같은 outbox/native save await를 밖으로 옮겼다. 제어 native/outbox 100ms 대기에서 lock 보유 true→false, 실제 ranking prepare/Hub subscription의 barrier 해제 전 완료 false→true. RAM membership은 원래도 먼저 공개됐으며 신규 개선으로 세지 않는다. 동일120억/시장60·60/cohort와 native 연결3/SELECT2/BEGIN·COMMIT2/INSERT5/DELETE2/save1을 유지한다. R036 caller-cut/owned task·실패·취소·close/start 복구를 보존하고 새 queue/계층/transaction은 없다.

R3-043 진행(PC 구현통합·실제PG 대기): 기존 claim attempts를 필수 keyword로 모든 finish/retry caller와 QueryStore/CentralNewsStore 계약에 전달한다. native WHERE job_key/RUNNING/attempt 일치 때만 상태·output_ref를 갱신하고 실제 matched PENDING commit 뒤에만 깨운다. 정상 retry SELECT1→0, UPDATE/연결/BEGIN/COMMIT 각1 유지. 120초 stale/3회 FAILED/None 반환·immutable 원문과 결과는 보존한다. old/new 성공·실패 역순, ACK 유실/취소복구를 native SQLite와 PG SQL/rollback fixture로 확인했다. 행 삭제 후 같은 key 재생성 ABA는 보장 범위 밖이다. R3-043 실제 PostgreSQL 변경2 ID 및 해당87건 모듈은 미실행이다. 전용 diagnostic DSN 부재로 증분 실행 증거 검사는 누락을 감지해 실패했으며 성공 처리하지 않았다. 기존 push/PR의 필수 postgres-integration이 postgres_required 전체 모듈을 실행하고 실패·누락을 required 증거로 전달하는 본문만 확인했다. 현재 hosted CI 실행은 없다.

R3-046 검증 완료: 일반 research CLI의 request/result/cancel resolved alias를 parsing/오류 게시 전에 보호하고, 기존 request 경로 helper로 선언된 DB/dataset도 보존한다. 정상 독립 오류 envelope/atomic result·등록/leader/final/validation/comparison 기존 분기는 유지한다. 기존 보호계약을 재사용했고 새 path 계층/설정/시계/DB 호출은 없다. 원본20 subcase 실패→수정 후 통과; 실제 child4개 conflict/success/cancel/resume 종료2/0/2/0과 원본 bytes·결과 소비를 확인했다. 일반 GUI가 평소 충돌한다는 증거는 없고 전체 ResearchDialog 클릭·동시 파일교체 보안 경계의 수락으로 확대하지 않는다.

R3-036 후속 검증 보완(제품 변경0): 기존 replay의 lost-ACK 기대값1은 native thread와 추가된 owned save task의 실패 관측을 합산하는 RuntimeScope 실제 계약을 놓쳤다. thread/task OSError 각각1, raw error_count2, execution_succeeded=False·동일 save2회·outbox 복원을 함께 검증한다. 사건 중복 제거 정책이나 DB 시도 횟수를 바꾸지 않았다. 옛 기대값은 R065의 원본 load를 복원해도 동일 실패, 보강한 기대는 원본/현재 load 모두 통과한다. 초기 R036 관련90건에 replay-outbox 검사가 빠졌던 범위를 명시하고 향후 해당 소비자 검사를 함께 선택한다.

R3-084는 새 구현 지시가 아닌 P2 계약확인 대기다. 실제 두 BODY runner 뒤 job output_ref는 B, 최신 append body는 A로 갈라졌다. claim 순서/fixture 라벨은 원문 신선도 증거가 아니다. 기본 NAS 목록은 latest body와 article-key assessment를 독립 결합하지만 PC detail core/event는 body ID를 맞추므로 경로별 계약을 확인해야 한다. 원문2개는 보존하고 임의 winner 정책을 만들지 않는다. 영향 F03/F04/F09/F19/F21/F24/F25 및 책임·비용 계약 갱신. 실제 키움/장중·운영 DB/계좌/주문·외부망/NAS·push/GitHub CI·배포·로컬 전수 회귀·물리 I/O/WAL/운영 발생률은 미검증이다.

## 2026-10-20 네 번째 병렬 통합: 031·080

로컬제품·검사`539cbad`. 현재통합소스 관련8개실제모듈 **고유121건/79.524 worker초 통과**(journal/controller85+실제MainWindow기존signal1+OCR/controller/theme35). 11개변경ID·계획/실행/통과·현재전체지문/frozen7파일·worker/tree종료/nativeTEMP제거를대조했다.

완료30/미완료53. 준비후보18(실행/제출포함),계약/영향확인29(027잔여포함),선행·의미대기2(047/058),명시보류4(006/008/020/024)다. 035는036선행충족후격리개발/검증제출,043은뉴스native claim CAS개발,046은CLI원본보호검증제출·root통합대기다. 030은031새실패payload보존경계를승계한worktree에서실제개발중이다. 메인포함실제4slot을계속활용하며공유원장/F는root만작성한다.

R3-031 완료(등록저장/종료경계): 기존단일FIFO에서실패한현재job·단계/이미관측한backfill값만보존한다. native저장실패는failed를전달하고후속queue를소비하지않고멈춘다. AppController는기존cache_failed/안내를재사용해거짓ready를막고새명시close만멈춘writer를재시작한다. base5회재시도·정상scope/뉴스/수급/0w원문·optional loader오류/None continue는그대로다. 이미조회한값은setter(None)뒤에도저장하고명시retry의외부loader재호출0/논리중복행0(기존native후보조회는유지)·prefix commit/후속원본을보존한다. 새queue/schema/relay/자동restart·poll/durability는없다. 영구저장오류면다음job도명시retry까지RAM에대기하며process kill/전원복구를제공하지않는다. R030provider cache/R064snapshot덮어쓰기는별도다.

R3-080 완료(기본추론중복): 같은engine의첫predict결과를지역pages로한번물질화해첫row/badge해석에인계한다. 3mode×1/2/5장기본predict2→1/3→2/6→5,필요badge추론N/constructor1과정상rows/progress·검사한model/predict/callable-page/준비취소경계를유지한다. public서명과controller제품은그대로며실제QThread완료/실패/cancel/deleteLater를검증했다. 새cache/owner/모듈/모델다운로드없음. 이미초기화된extract_rows의후속파일삭제와malformedpage대Image.open오류우선순위는전후비교하지않았고기존is_file공통화/물질화순서로메시지·우선순위가달라질수있다. 전체오류계약/실제모델출력동일로과장하지않는다.

031의중간동일소스Qt native abort3221226505는보고된통과0/원문ERROR traceback미확보·정확원인미확정으로실패TEMP/로그를보존한다. 이후단독/모듈·계측86건과root현재121건에는재현되지않았지만원인해결로보고하지않는다. 080은provider를통제한실제parser/Qt검증이며WT필수6모델파일이없어실제모델정확도/추론latency를수락하지않았다.

영향F10/F18/F22/F23(좁은setterNone)/F24·책임/비용계약갱신. 실제모델정확도/처리시간·실제PG·장중/키움·운영DB/계좌·주문·외부망/NAS·push/GitHub CI·배포·로컬전수회귀는미검증이다.

## 2026-10-20 세 번째 병렬 통합: 036·038·040·073

로컬제품·검사`8f3bade`. 현재 통합소스 관련21모듈 **고유479건/428.669 worker초 통과**(index90+ingest/app241+gateway90+news58,중복0). 계획/실행/통과·14개변경ID/현재전체지문·frozen7파일·worker/tree종료/nativeTEMP제거를대조했다.

완료28/미완료55. 준비후보20(실행/제출포함),계약/영향확인29(027잔여포함),선행·의미대기2(047/058),명시보류4(006/008/020/024)다. 036완료로035를실행가능으로해제했다. 031은native실패값/종료보존개발·재검증,046은CLI원본보호개발,080은검증제출·root통합대기다. 실제총4slot(메인+보조3)을개발/교차검토에사용하고공유원장은root만쓴다.

R3-036 완료: 기존지수pending의caller진입key cut을전용flush lock으로직렬화하고동일service의shield save task가실제native수명을소유한다. 같은cut의native/연결/BEGIN/COMMIT/provider각2→1·INSERT/DELETE각4→2·반영행4→2,최종snapshot동일. 새caller는진입cut의최신payload를저장한뒤반환하고진입후다른key를무한drain하지않는다. 원본barrier PASS→중간합류-only FAIL→최종PASS를보존했다. 취소/실패합류/명시retry/ACK유실/outbox제거실패/start복구·실제RuntimeScope task/thread drain을검증했다. close는진행owner join만하고새강제retry0,collector/outbox lock범위와기존11메서드는그대로다.

R3-038 완료: 분봉COMPLETE-only page는불필요한ACTUAL사전조회없이기존native권한판단을따르고,IN_PROGRESS는기존metadata 시간범위로좁힌다. SOR는실제소비page의기존start/end reader를쓴다. 동일120행 fixture에서metadata반환120→1(혼합)/0(완결),SOR120→2;완결연결5→4/SELECT4→3,Python commit호출5→4지만실제BEGIN/COMMIT각2→2다. 최종전체봉/meta/비교문서·반영행/시계동일,UTC-KST/23:59/lateSOR/component merge/필수사전조회실패와저장뒤SOR실패를구분했다. 다른venue component가필요한기존owner문서전체조회와sparse범위내중간행은남는다.

R3-040 유지·검증완료: 제품변경0. 첫read뒤기존offline unowned apply가native JSON의account/run을바꿀수있고reader가그JSON을소비하므로두번째최신scope read를제거하지않았다. frozen DTO는native불변성을보장하지않는다. 실제활성lease/독립두store의normal/state/account/run반례와둘째read제거음성대조2assertion실패로지속검사를보강했다. 정상/state는기존첫record반환·추가transport/refresh0,scope변경은거절한다. SELECT2/실행loop동기I/O는그대로이며제거완료나지연개선으로보고하지않는다. R3-009안전조회근거에연결하고042owner/047runner·legacyHTTP오류매핑은별도다.

R3-073 완료: 기존StockNewsRepository의11개UPDATE필드를DB현재행과NULL-safe IS NOT으로비교한다. 동일5기사의article반영5→0·필수receipt1유지로총6→1,정정1건포함총6→2/partial2총3→1. 연결/ID SELECT/BEGIN/COMMIT각1과raw원행/first_seen/발행시각·pin/retention/limit·새건수반환은보존한다. 각11필드외부DB정정/NULL·새identity·receipt trigger실패전체rollback/retry와기존201건pin검사를확인했다. actualsearch/prepare→기존창/contentsync소비는그대로이며같은기사라도확인시각저장을생략하지않는다.

F03/F08/F09/F16/F19/F22/F24와책임/비용계약을해당범위만갱신했다. 실제 PostgreSQL·장중/키움/운영 DB/실계좌·주문·외부망/NAS·push/GitHub CI·배포·로컬 전수 회귀와물리IO/WAL/운영latency는미검증이다.

## 2026-10-20 병렬 두 번째 통합: 034·069·070 완료 / 027 일부

로컬 제품·검사 `9955a24`. 현재 소스 관련7모듈 **고유157건/79.485 worker초 통과**(수집/과거자료/테마74+화면83, 중복0). 계획=실행=통과ID·현재 소스/frozen worktree SHA·worker/자식 종료·native TEMP 제거를 대조했다.

완료24/미완료59. 준비후보23(실행/검증제출포함),계약/영향확인29(027잔여포함),선행·의미대기3,명시보류4(006/008/020/024)다. root는036보완/040안전조회유지/073저장조건의제출결과를검토·통합하고,보조3개는031실패저장/038page범위/080OCR중복을독립개발한다. 036이최종통합되기전035는대기,042→047과058지표의미선행은그대로다.

R3-027 일부: MainWindow의 숨김 TOP20 주기 처리만 collector.advance→완료 native 저장→cohort 구독 이후에 종료한다. 실제 Qt timeout4회에서 동일120행의집계4회/처리480행/렌더4회→0, NAS periodic set_series/data4→0이며 네트워크요청은원래도0이다. 완료분60억·시장별30/30·구독·열기/날짜/모드·재오픈 최신값은유지한다. 이미진행중인 NAS callback은숨김에서도렌더할수있다. 표/테마 same-cut·기간계산의남은027은미완료다.

R3-034 완료: Collector의 stock-reference와 account-day entry 저장실패 복원에 기존 다른 snapshot과 같은 setdefault를 사용한다. awaited native 저장중 도착한 최신pending은그대로두고없는key만복원한다. KRX/NXT 양방향·precommit/ACK유실·원거래일/다음날·credential drain/close의원본9개실패반례가통과하며, writer호출2/3과기존arrival순서·day key를보존한다. 원본FILL원장/봉delta coalescing정책은변경하지않았다.

R3-069 완료: 과거뉴스 initializer의 기존identity index를 original_url/portal_url 기존ALTER 이후에 생성한다. URL열0/1/2개 legacy·기존index·fresh logical schema31·원행/URL/시각보존과 ALTER/index실패후닫기/같은DB재시도를검증했다. 기존SQL의위치만옮겨같은schema/index·연결/commit을유지한다. executescript의부분DDL영속은그대로며초기화전체rollback/운영archive수락을주장하지않는다.

R3-070 완료: 제외종목찾기의 SimilarStockDialog 호출에 parent=self를명시해 QWidget이themes로전달되던TypeError를복구했다. 실제 MainWindow→Settings→modal검색/선택·중복/취소/없음→기존worker/native저장→테마총140억/제외40억·정렬40→재오픈을검증했다. 올바른themes 명시import caller·원래dialog시그니처는유지했다. F22는일반설정저장소간접공유만확인했고원격sync를새로수락하지않는다.

영향받은 F02/F06/F07/F09/F10/F19/F21/F22/F23/F24의해당경계와책임·비용계약을갱신했다. F부모운영판정을승격하지않는다. 실제 PostgreSQL·키움/장중·NAS/외부망/운영 DB/실계좌·주문·앱 전체 수락·push/GitHub CI·배포·로컬 전수 회귀는 미실행이다.

## 2026-10-20 병렬 첫 통합: R3-037·078 완료

로컬 제품·검사 `fba8994`. 현재 소스 관련4모듈 **고유89건/12.839 worker초 통과**(WS51+reconciliation38, 중복0). 계획/실행/통과ID·5개 변경ID·현재 소스와 frozen worktree SHA·worker/자식 종료·native TEMP 제거를 대조했다.

의존성 준비큐를 유지한다. 완료21/미완료62이며 준비후보27(검증제출/실행 포함), 계약/영향확인28, 선행/의미대기3, 명시보류4다. 027숨김chart slice·034·036·069는격리검증 제출/통합대기,070은개발중이다. 027전체와035(036선행)는 아직 완료/준비 승격하지 않는다. worker slot은 개발/다른 작성자 교차검토에 연속 배정하며 root만 원장/F·통합을 맡는다.

R3-037: 기존 route의 sender cancel/join을 nested finally로 감싸 RealtimeHub.disconnect를 보장한다. 원래 송신 오류/취소를 숨기지 않고 정상 subscriber/구독union을 보존한다. 실패 sender 뒤 수신 종료 시 client 잔류를 native router+제어 socket으로 재현해 제거했고 ready/snapshot/ping·gap·정상 종료/취소도 확인했다. sender만 죽고 receive가 계속 대기하는 경우의 양방향 감시는 기존 별도가설이며 추가 구현하지 않았다.

R3-078: 기존 _inputs의 coverage2N SELECT만200code(최대400 owner) batch로 읽는다. 같은 cursor/SQLite transaction·PG repeatable-read wrapper/decoder·subject순서·missing과empty{}·source/generation을 유지한다. 0/3/20/200/201code SELECT는 각각1→1/10→5/44→5/404→5/409→9, coverage는0→0/6→1/40→1/400→1/402→2다. native 반환행/최종input/payload/generation 동일·연결/BEGIN/COMMIT각1/DML0. 실제PG 대신%s SQLite-backed driver shape만 검증했으며 물리I/O/WAL/latency는 미측정이다.

F04/F24(037)·F09(078)와 책임·재현 비용 계약을 갱신했다. 실제 PostgreSQL·키움/장중·다중PC/NAS/운영 DB/계좌/주문·앱 전체 수락·push/GitHub CI·NAS 배포·로컬 전수 회귀는 미실행이다.

## 2026-10-20 의존성 기반 병렬 실행 전환

R3 83개 중 완료19·미완료64. 기존 감사 근거를 재사용해 준비 후보29(현재027/037/078 실행중 포함), 계약/영향 확인28, 선행/의미 대기3, 명시보류4를 구분했다. 5개 엄격선행 edge의 순환 없음과83개 ID를 대조했다. 준비29개 전체의 상호 독립성 완료를 뜻하지 않는다. 실제 독립 경계3개를 별도 Git worktree에 배정했고 root만 백로그/F·공통 계약을 갱신하며 결과를 순차 통합한다. 현재 도구의 총4slot(메인포함)을 사용하며 더 큰 한도가 실제 적용되면 독립 범위까지 확대한다. 전역 Codex 설정·NAS/운영/push 변경은 없다. [전체 배정·의존/충돌 원장](../REFACTOR_BACKLOG.md#의존성-기반-병렬-실행-원장--2026-10-20)을 정본으로 사용한다.

## 2026-10-20 R3-012 열 설정 저장 완료

로컬 제품·검사 `47e780d`. 현재 소스 관련 **9모듈·고유152건/127.308 worker초 통과**(독립2묶음 병렬). 계획/발견/실행/통과 ID·변경7개 ID·소스 불변·worker/자식 종료·native TEMP 정리를 대조했다.

**R3-012 검증 완료(등록 코드·로컬 관련검증)**. 후속은 사용자 지정 **의존성 DAG 준비큐**로 전환했다. R3-027·037·078은 격리 worktree 개발 배정, 다른 후보는 기존 근거의 상태/계약 충돌 분류 후 배정한다. 번호순013 강제 대기는 해제하되006/008/020/024의 명시 보류는 유지한다.

기존 ColumnSettingsRepository.save가 같은 native writer transaction에서 nonempty의 기존 writer lock을 BEGIN IMMEDIATE로 확보한 뒤 시계를 얻고 변경 전 DB의 visible/position/width로 version을 조건부 갱신한 뒤 다른 value row만 UPDATE한다. 같은 값+기존 version은 편집 시각을 보존하고 누락 version은 최초 등록한다. 별도 SELECT/새 RAM 상태·계층·schema·debounce를 추가하지 않았다. reset은 명시적 편집으로 원래 전체 값/version 갱신을 유지한다. 기본16열의 실제 native SQLite 반영행은 동일 저장32→0, 한 열(폭 또는 layout) 변경32→2, 누락 version 등록1이다. 명시 reset은32행을 유지한다. 비어 있지 않은 save의 연결/BEGIN/COMMIT/시계 각1·UPDATE16/INSERT16 시도는 그대로이며 기존 version 비교에 인덱스 기반 inline lookup이 있다. 별도 SELECT 왕복0, 물리 I/O/WAL·잠금 대기·장중 latency·신호 빈도는 미측정이다.

F01(간접)/F22/F23/F24·책임/DB 계약과 [정본](../REFACTOR_BACKLOG.md#r3-012-열-설정-변경행revision-저장-완료--2026-10-20)을 갱신했다. 실제 다중PC/NAS/외부 네트워크 동기화·앱 전체 bootstrap/daemon 종료·운영 DB/계좌/주문·장중·push/GitHub CI·NAS 배포·로컬 전수 회귀는 미실행이다.

## 2026-10-20 R3-011 설정 no-op·cache·UI batch 완료

로컬 제품·검사 `aeb63c6`. 현재 소스 관련 **14모듈·고유175건/140.776 worker초 통과**(독립2묶음 병렬). 계획/발견/실행/통과 ID, 변경8개 ID, 소스 불변, worker/자식 종료와 native TEMP 제거를 대조했다.

**R3-011은 검증 완료(등록 개선 코드·로컬 관련검증)**다. 다음 미보류 대기 작업은 **R3-012(열 설정 저장의 변경셋)**이다. R3-032의 bootstrap daemon/restore 수명과 R3-056의 전송 중 편집 ACK 문제는 별도 미결로 유지한다.

SettingsRepository의 기존 native transaction에서 version UPSERT가 변경 전 DB 값을 비교한 뒤 조건부 value UPSERT를 수행한다. 같은 값+기존 version은 revision을 보존하고, 같은 값+누락 version은 최초 공유 등록을 남기며, 실제 변경은 값/version을 함께 commit한다. 같은 값의 저장을 새 편집·재승인 시각으로 취급하지 않는 것이 의도한 변경이다. 혼합 UTC/KST의 실제 순간 비교와 더 최신 원격 편집 수락·진짜 로컬 편집의 업로드를 기존 CentralSettingsSyncService+격리 SQLite로 검증했다.

기존 RAM cache에 짧은 Lock과 무효화 세대를 적용했다. get의 DB 조회 중 clear/새 write가 끝나면 이전 결과를 cache에 다시 넣지 않는다. write의 commit·close 성공 및 동일 세대일 때만 값을 게시하고, 늦은 ACK/실패는 해당 키를 무효화한다. DB I/O는 Lock 밖이며 진행 중 get 자체의 이전 snapshot 반환은 허용한다. 외부 프로세스가 clear_cache 없이 바꾼 값을 자동 감지하는 기능은 추가하지 않았다.

격리 native SQLite의 동일4키 저장은 연결1/BEGIN1/COMMIT1/시계1/INSERT 시도8을 유지하며 실제 반영행8→0이다. 모두 실제 변경한 geometry/Alert4키는 각 연결·BEGIN·COMMIT·시계4→1, 반영행8 유지다. version 비교에는 인덱스 기반 scalar subquery가 있으며 별도 SELECT 왕복은 추가하지 않았다. no-op도 연결/transaction/SQL 시도는 남는다. 물리 I/O·WAL·잠금 대기·장중 latency와 cache Lock 비용은 미측정이다.

F01(간접)/F22/F23/F24와 책임·DB 계약 및 [백로그 최신](../REFACTOR_BACKLOG.md#r3-011-설정-no-opcacheui-batch-완료--2026-10-20)을 갱신했다. 실제 다중PC/NAS 동기화·앱 전체 bootstrap/timer/daemon 종료·운영 DB/계좌/주문·장중·push/GitHub CI·NAS 배포·로컬 전수 회귀는 미실행이다. 이번 결과를 기존 hosted CI 성공이나 운영 수락으로 확대하지 않는다.

## 2026-10-20 R3-010 stage·binding 등록 개선 완료

로컬 제품·검사 `fc21038`. 현재 소스 관련17모듈 **고유 546건/440.307 worker초 통과**(두 묶음 병렬). 계획/발견/실행/통과 ID와 변경6개 ID·소스 불변·worker/자식 종료·TEMP 정리를 대조했다.

**R3-010은 검증 완료(등록 개선 코드·로컬 관련검증 범위)**다. 이전2bd43df의 per-key/Shadow 검증을 승계하고 이번 stage predecessor·binding 범위·SQLite commit 경계 진단으로 등록 잔여를 마쳤다. context 재사용은 같은 안전시점 중복이 입증되지 않아 기존 R3-009처럼 보호 조회를 **유지**한다. 여러 문서 publication은 기존 ordered/idempotent 계약을 유지하며 전체 원자성을 새로 구현하거나 완료로 주장하지 않는다. 다음 미보류 대기 작업은 **R3-011(설정값과 revision의 no-op)**이다.

stage의 같은 이전 단계 확인과 append를 기존 native writer transaction으로 묶었다. 기존 동일 ID는 읽기 전용으로 재시도하며 SQLite BEGIN IMMEDIATE, PG의 해당 transaction만 READ COMMITTED→strategy scoped advisory lock→단건 key 재확인→기존 reader와 같은 이력 범위/순서→단계 비교/insert→commit 순서다. broker-mock PASSED report gate·원래 hash·계좌/증거·단계/시각 정책은 유지한다. 동일 시점에 stale stage와 missing report가 겹치면 거절 오류의 우선순위만 달라질 수 있다.

기존 identity owner에 load_latest_account_binding(profile, broker, environment)를 추가해 native 인덱스/LIMIT1을 쓰고 route helper와 Forward의 승인 사양/후보 게시 3개 caller를 연결했다. account_ref나 기대 revision으로 조회를 좁히면 rotation을 숨기므로 사용하지 않는다. 기존 full history API·각 안전시점의 독립 최신 조회·STOP/Risk/control/native claim은 유지한다.

stage 신규 native SQLite 연결3→2·SELECT3/INSERT1/BEGIN1/COMMIT1/clock1 유지, 기존 동일 ID는 연결1/SELECT1·쓰기/BEGIN/COMMIT/clock0이다. binding fixture는 실제 native 반환·변환1031→1, SELECT/연결 각1·쓰기/clock0이며 기존 index SEARCH를 확인했다. 물리 I/O·WAL/장중 latency는 미측정이다. PG stage의 명시 isolation/lock 추가 SQL은 올바른 snapshot과 직렬화를 위한 안전비용이다.

격리 SQLite의 별도 owned-child 강제 종료 진단2건/2.697초가 통과했다. INSERT 후 commit 직전 종료는 행없음/재시도True, commit 성공 후 ACK 직전 종료는 행보존/동일retryFalse/다른본문충돌·첫 receipt 보존이다. 소유 job의 child6개/outer worker·tree 종료와 누수0/TEMP 제거를 확인했다. 앞선 v4 PID fixture 실패는 보존하며 실제 PG·호스트 전원/스토리지 내구성·stage helper 자체의 OS crash로 확대하지 않는다. 진단2건은 정규 고유 검사 수와 합산하지 않는다.

구조 site1373/위반0·retirable0, QueryStore 서명/site/candidate/forward delta0, PG 직접 연결 승인60/60을 확인했다. 새 SQL key5개와 native2메서드×3owner 서명·binding caller3개 교체/새 stage 직접호출1개만 기존 계약에 등록했다. 규칙/채택점/승인/이력은 초기화하지 않았다. 32번째 증분 검토는5931→5937/추가6·수정0·삭제0이며 원래 미결49를 유지한다.

실제 PostgreSQL 경합/강제종료·운영 DB/계좌/주문·장중·PC 앱 전체 수락·push/GitHub CI·NAS 배포·로컬 전수 회귀는 미실행이다. PG는 실제 SQL/관측 wrapper의 scripted driver 계약 검증이다. current-stage 값 비교이지 latest revision ID/head 또는 chronology 단조 증가를 새로 보장하는 것이 아니다. 기존 decided_at/revision_id tie-break·updated_at 기준10000행 범위/과거 시각 입력을 보존하며 혼합 구버전 writer와 운영 배포 수락은 별도다.

## 2026-10-20 R3-010 불변 저장·충돌/ACK 경계

로컬 제품·검사 `2bd43df`에서 ForwardEvaluationRepository의 기존 typed 저장→동일 QueryStore의 insert_immutable_document→reader/승인·Risk·Runner 소비와 CandidateMonitor→native Shadow decision/candidate 저장 경계를 보완했다. generic은 SELECT-first로 기존 동일 문서의 read-only 경로를 유지하고, missing만 UNIQUE INSERT DO NOTHING 후 경합 winner를 비교한다. Shadow는 INSERT-first로 writer 경계 뒤 canonical 내용을 비교하며 decision/candidate는 같은 transaction으로 rollback한다. 첫 본문·receipt·monitor/expiry를 보존하고 성공/동일 ACK는 context commit 성공 뒤 반환한다. 신규 일반 불변 문서 저장의 native SQLite 연결3→1·SELECT2→1, INSERT/BEGIN/COMMIT/clock 각1을 확인했다. 처음 조회에서 이미 존재한 동일 문서는 연결1·SELECT1·쓰기/BEGIN/COMMIT/clock0을 유지한다. 처음 missing인 경합 loser는 충돌 재조회로 SELECT2·INSERT시도1·clock1일 수 있다. 물리 I/O·WAL/lock wait·장중 latency는 미측정이다.

관련18모듈 **고유 739건 통과**(worker 소요 합계 629.437초, 두 묶음 병렬 실행). 변경10개 검사ID·발견/실행ID·현재 소스 일치·worker/자식 종료·TEMP 정리를 대조했다. 정적 구조 site1368/위반0·retirable0, QueryStore 서명/호출 delta0, PG 직접 연결 승인60/60을 확인했다. guardrails는 새 native 메서드의 정확한 SQL key2개(각3곳)에 한 review만 추가했고 기존 채택/규칙/이력은 유지했다. QueryStore baseline은 3owner 서명1개씩과 Forward helper 호출 identity1개 교체만 반영했다. PG 직접 연결 승인은 변경하지 않았다. 30·31번째 증분 의미 검토(정적5925→5931, 추가6/수정4/삭제0)와 실제 실행을 대조하며 원래 미결49를 유지한다.

**R3-010 전체는 진행**이다. 이번 per-key immutable/Shadow 경계는 로컬 검증했고, 다음은 기존 save_stage_revision의 서로 다른 revision ID 간 predecessor CAS와 binding 전체 history의 범위 조회다. stage/profile/binding/lineage 검증·최신 STOP/Risk/control read·native claim은 보존한다. admission/recovery context 재사용은 동일 안전시점 근거가 생긴 범위만 검토한다. 여러 문서/현재 포인터를 묶는 원자성은 이번 per-key 작업으로 해결되지 않는다.

실제 PostgreSQL·운영 DB/계좌/주문·키움/장중·PC 앱 전체 수락·push/GitHub CI·NAS 배포·로컬 전수 회귀는 미실행이다. PG는 실제 SQL/observed wrapper와 scripted connection의 충돌/commit 계약만 확인했다. 별도 child 재조회·ACK 재시도는 통과했으나 강제 종료 진단 v4는 launcher PID 가정 오류로 실패했고 원문을 보존했다. commit 경계 OS crash는 미검증이며 R3-010 완료 조건에 남긴다.

## 2026-10-19 R3-005 기준선 분봉 범위·native 입력 연결

로컬 제품·검사 `4841f7a`에서 기존 CandidateMonitor의 정책 기준선 조회를 현재 KST 거래일과 정책 window의 교집합으로 제한했다. SQL은 cut.floor 기준 start 포함/end 제외이며 기존 (cut-2초).floor/commit token 캐시는 그대로다. 빈 교집합·미설정·subminute는 조회하지 않는다. 실제 native 반환의 nested metadata는 기존 codec로 품질/출처/가용시각4필드에 연결해, 정상 완결봉도 UNAVAILABLE이던 입력 연결을 복구했다. 20종목×450분 fixture의 실제 native 반환9,000→100(5분×20), SELECT20·연결20 유지/분봉 쓰기0이다. 같은 cache key의 추가 read0/commit token 변경 뒤 추가20이다. 정상 native UNAVAILABLE→COMPLETE는 연결 복구에 따른 의도한 동작 변화이고, 이를 포함한 정규화 full-day oracle와 bounded reference/observation hash는11조건에서 동일하다. 관련5모듈 **361건/127.269초 통과**. 구조·29번째 증분 검토/실행 ID·소스 불변·worker/자식 종료·임시 정리를 확인했다.

R3-005는 **검증 완료(로컬 native·PG SQL 계약 범위)**다. 백로그 요약·상세와 F12(직접)/F08·F24(관련 경계), 책임·저장소 계약을 갱신했다. 다음 미보류 대기 항목은 **R3-010(불변 승인 문서 조회·저장·재조회)**이다. 앞선 R3-006/008은 기존 근거대로 보류, 007/009는 검증 완료를 유지한다. 실제 PG wait 근거 없이 pool/새 계층을 만들거나 완료 항목·전수 감사를 다시 시작하지 않는다.

이번 push/GitHub CI·NAS 배포·로컬 전수 회귀·실제 키움/장중·운영 DB/주문·실제 PostgreSQL/외부 네트워크 수락은 미실행이다. PG는 기존 실제 SQL 생성·인자/decoder의 fake-connection 계약으로만 검증했다. 분봉으로 가짜 틱을 만들거나 미정 매매 조건/주문 승인을 추가하지 않았고 F 부모의 부분/확인불가 상태는 유지한다.

## 2026-10-18 R3-003 동일 시총 쓰기 제거·가격 확인시각 보존

로컬 제품·검사 `622c2d9`에서 기존 StockRepository.update_price_cache의 시총 UPDATE에 null-safe 조건만 추가했다. 시총과 현재 DB 유통비율로 계산한 유통시총이 모두 같으면 행/일반 updated_at을 보존하며, 후행 비율·NULL/0 파생값 보정은 수행한다. 같은 가격의 last_price_updated_at과 기본정보 당일 gate는 유지한다. 동일 시총20종목의 SQLite 반영행20→0, UPDATE시도20·연결1·SELECT0·BEGIN1·COMMIT1은 그대로다. 같은 가격20개의 후속 확인시각 갱신/반영20은 유지한다. 물리 I/O·WAL/lock wait·실제 장중 지연은 미측정이다. 관련8모듈 **165건/101.115초 통과**. 구조·28번째 증분 검토/실행 ID·소스 불변·worker/자식 종료·임시 정리를 확인했다.

R3-003은 **검증 완료(로컬)**다. 백로그 요약·상세와 F06/F10/F22/F24·책임/시각 계약을 갱신했다. 다음은 **R3-005(0B 후보 기준선의 일전체 분봉 read)**다. 기존 항목과 R3-004 reader 범위에서 이어가며 전체 감사·완료한001/002/003/004/023은 반복하지 않는다. R3-029의 가격 receipt와 테마 revision 결합·R3-033 RAM gate 등 별도 미결은 유지한다. 새 차단 요인은 없다.

이번 push/GitHub CI·NAS 배포·로컬 전수 회귀·실제 키움/장중·운영 DB/주문·PostgreSQL·다중PC/외부 동기화 수락은 미실행이다. F 부모의 부분 연결/확인 불가를 전체 정상으로 승격하지 않는다.

## 2026-10-18 R3-023 history 비용·backfill 실패 시각 / 등록 코드범위 완료

로컬 제품·검사 `4ac627a`에서 `_history_received`의 기존 수신 시각1회를 계좌 검증 후 저장 전에 얻어 `upsert_history_sync(now)`의 비용 confirmed_at과 후속 task/설정에 공유했다. `_backfill_failed`도 기존 client 시각1회를 `mark_bar_backfill(now)`의 updated_at과 저장 성공 후 task 실패에 전달한다. 원체결·정산일·봉 값/일자·계좌 범위·기존 transaction/상태·재시도 정책은 보존했다. 관련10모듈 **128건/81.536초 통과**. 구조·27번째 증분 원장/실행 ID·소스 불변·worker/자식 종료·임시 정리를 확인하고 F08/F18/F22/F24와 책임 계약을 함께 갱신했다.

R3-023은 **검증 완료(등록된 정밀 코드감사·로컬 관련검증 범위)**다. 기존 등록 잔여인 history 비용·backfill 실패 receipt2곳까지 완료했으며, 초기/직전 단계 잔여 기록은 당시 이력이다. 앱 전체 시계 통일이나 모든 운영 분기 실행 완료를 뜻하지 않는다. 다음 제품 작업은 **R3-003(동일 시총·가격 UPDATE와 확인시각 의미)**이다. 기존 측정·백로그에서 이어가며 R1/R2·001/002/004/023과 전체 감사를 반복하지 않는다. R3-033 등 별도 미결은 유지하고 새 차단 요인은 없다.

이번 push/GitHub CI·NAS 배포·로컬 전수 회귀·실제 키움/장중·운영 DB/주문·PostgreSQL·외부 동기화 수락은 미실행이다. API 조립 전 startup/offline KST fallback과 기존 저장자료 전환·운영 수락은 별도 time-basis 범위이며 키움 동기화 완료로 표시하지 않는다.

## 2026-10-18 R3-023 자동 보완 task 수명 시계

로컬 제품·검사 `0abc062`에서 기존 JournalEnrichmentService에 query-client 시계를 지연 주입해 task 등록/시작/완료/부분/실패/불가/복구에 전달했다. 자동 분석은 등록/선별 batch와 각 started/completed/failed callback의 시각1회를 공유한다. 명시적 legacy 시각·상태/재시도·원본 task ID/hash는 보존하고 혼합 시간대 retry 비교를 실제 순간으로 통일했다. 관련9모듈 **106건/58.938초 통과**. 구조·26번째 증분 원장/실행ID·소스 불변·worker/자식 종료·임시 정리를 확인하고 영향 F01/F08/F10/F18/F22/F24와 책임 계약을 갱신했다.

R3-023 전체는 **진행**이다. 이번 task 시계 선택 범위는 완료했고 다음은 연결 본문에서 확인한 `_history_received→upsert_history_sync(now 미전달)→비용 confirmed_at`과 `_backfill_failed→mark_bar_backfill(now 미전달)→backfill updated_at`이다. 두 업무 저장 receipt는 task 상태 시각과 별개로 남겼으며 운영 영향은 아직 미측정이다. 해당 caller/원자 저장/reader만 좁혀 확인하고 끝낸 task·Window/legacy/dataset/봉/setup/revision 경계는 재조사하지 않는다. 실제 키움/장중·운영DB/주문·외부 Drive·PostgreSQL·push/GitHub CI·NAS 배포·로컬 전수 회귀는 미실행이다. API 조립 전 시작 복구는 명시적 공통 KST fallback이며 동기화 완료가 아니다. 앱 전체 시계 통일·기능 전체 정상으로 확대하지 않는다.

## 2026-10-18 R3-023 판정 저장·revision 시계

로컬 제품·검사 `2c4addf`에서 TradeAnalysisPreparationService.prepare의 기존 KST observed_at1회를 legacy/scoped 판정 저장2branch와 revision 생성에 그대로 전달했다. 일봉 수락·판정 updated_at·새 revision created_at은 같은 prepare 시계를 쓰고, 동일 입력의 기존 revision ID/hash·최초 created_at은 보존한다. 관련10모듈 **121건/68.202초 통과**. 구조·25번째 증분 원장/실행ID·소스 불변·worker/자식 종료·임시 정리를 확인하고 영향 F08/F10/F18/F22/F24와 책임 계약을 갱신했다.

R3-023 전체는 **진행**이다. 이번 판정/revision caller는 완료했고 다음은 기존 원장에 남긴 자동 보완 task 등록/시작/완료/실패의 현재시각 인계다. JournalWindow의 해당 caller→JournalEnrichmentService→상태 저장/reader·재시도/중단 범위만 좁혀 확인한다. 이미 끝낸 Window/legacy/dataset/봉/일봉복사/setup/revision 경계는 재조사하지 않는다. 실제 키움/장중·운영DB/주문·외부 Drive·PostgreSQL·push/GitHub CI·NAS 배포·로컬 전수 회귀는 미실행이다. startup/offline 공통 KST fallback과 원본/canonical 경계는 보존하며 앱 전체 시계 통일·기능 전체 정상으로 확대하지 않는다.

## 2026-10-18 R3-023 사후분석 일봉 복사 시계

로컬 제품·검사 `442a83e`에서 JournalWindow가 기존 query-client의 보정 시계를 TradeAnalysisPreparationService에 지연 전달하도록 연결했다. 서비스는 prepare마다 한 번 받은 KST observed_at을 기존 일봉 복사 API로 전달한다. 수동 사후분석과 자동 AnalysisEnrichmentWorker가 같은 preparer를 소비한다. 관련9모듈 **102건/52.764초 통과**. 구조·24번째 증분 원장/실행ID·소스 불변·worker/자식 종료·임시 정리를 확인하고 영향 F08/F10/F18/F22/F24와 책임 계약을 갱신했다.

R3-023 전체는 **진행**이다. 이번 일봉 복사 caller는 완료했으며 다음은 같은 prepare의 `save_trade_analysis_setup(now 미전달)` 및 `journal_analysis_revision(now 미전달)`이 만드는 판정 updated_at/revision created_at이다. 실제 본문에서 기존 host fallback 소비를 확인했으나 이번 일봉 복사 범위 밖으로 남겼다. 같은 주입 시각을 넘기면서 계좌 범위·원자 저장·revision ID/hash·동일 ID 원래 created_at 보존을 검증한다. 끝낸 Window/legacy/dataset/봉 경계는 재조사하지 않는다. 실제 키움/장중·운영DB/주문·외부 Drive·PostgreSQL·push/GitHub CI·NAS 배포·로컬 전수 회귀는 미실행이다. startup/offline 공통 KST fallback은 동기화 완료가 아니며 앱 전체 시계 통일·기능 전체 정상으로 확대하지 않는다.

## 2026-10-18 R3-023 JournalWindow·legacy 수락 시각

로컬 제품·검사 `f72a85f`에서 JournalWindow 현재 시각과 봉 receipt를 기존 query-client 시계에 연결하고, legacy 일지 수락 기록과 SettingsBackup 카탈로그 INSERT/UPDATE를 기존 서버 시계/KST로 저장하도록 보완했다. MainWindow→Drive v1/v2 복원에도 같은 동적 시계를 전달한다. 관련14모듈 **265건/167.970초 통과**. 구조·22번째 증분 원장·실행ID·소스 불변·worker/자식 종료·임시 정리를 확인했다. 영향 F01/F08/F10/F18/F22/F23/F24와 책임 계약을 갱신했다.

R3-023 전체는 **진행**이다. 다음은 실제 온라인 `TradeAnalysisPreparationService.prepare`의 `import_monitor_daily_bars(monitor_db, code, episode.ended_at.date(), 250)` 미주입 caller(현재148행 부근)다. 이 서비스는 현재 provider가 없으며 기존4인자 Protocol/호환은 보존했다. 이미 끝낸 Window·legacy·constructor/dataset·봉 helper는 재조사하지 않는다. 실제 키움/장중·운영DB/주문·외부 Drive·PostgreSQL·push/GitHub CI·NAS 배포·로컬 전수 회귀는 미실행이다. startup/offline 공통 KST fallback과 원본/스키마 migration은 유지하며 앱 전체 시계 통일·기능 전체 정상으로 확대하지 않는다.

## 2026-10-18 R3-023 봉 관측 fallback·품질 시각

요약표83행 중 상세 최신 상태와 다른 R3-002·023·083의3행을 `5c4814a`에서 먼저 동기화했다. 이후 로컬 제품 `4280ca5`에서 서버 봉 관측의 결측/invalid 시각을 기존 provider로 전달하고, PC gap·빈 분 확인·시장지수 metadata의 누락3경로에 기존 writer 시계를 배치당1회 전달했다. 관련12모듈 **370건/188.485초 통과**, 구조·21번째 증분 원장/실행ID·소스 불변·worker/자식 종료·임시 정리를 확인했다.

R3-023 전체는 **진행**이다. 다음은 JournalWindow의 `_backfill_received`·`_daily_chart_received`·`import_live_bars`에서의 기존 시계 전달과 standalone/offline 경계를 좁혀 확인하고, SettingsBackup/일지 legacy SQL 시각 잔여를 이어간다. 이번에 끝낸 두 생성자·dataset saved_at·봉 helper/PC3경로를 재조사하지 않는다. 실제 PG·키움/장중·운영DB/주문·push/GitHub CI·NAS 배포·로컬 전수 회귀는 미실행이다.

## 2026-10-18 R3-023 시계 주입·dataset 저장시각

R3-023의 남은 생성자 시계 주입과 dataset saved_at 선택 경계를 로컬 커밋 `4e97fe1`에서 보완했다. 기존 app.server_now를 MarketDataIngestor/AutonomousTop20Service에 전달하고, SQLite/PG dataset saved_at은 기존 store._now_provider의 epoch를 쓴다. 관련6모듈 **273건/98.049초 통과**, source/계획·실행ID/worker·자식 종료·임시 정리를 확인했다. 구조 검사와 20번째 증분 원장·실행 대조도 통과했다. 새 계층·DB 경로·조회·clock polling·스키마 변경은 없다.

실제 PostgreSQL은 실행하지 않았고 PG SQL/transaction fixture만 검증했다. 새 제품 push/GitHub CI·NAS 배포·실시간 키움·운영 DB/주문·로컬 전수 회귀는 미실행이다. R3-023 전체는 결측봉 host fallback/legacy SQL 잔여 때문에 진행을 유지한다. 다음은 `_bar_available_at`와 local bar의 결측 시각 경계를 좁혀 확인하며, 기존 정상 epoch·원본 시각·명시적 offline fallback을 일괄 변환하지 않는다.


## 2026-10-18 R3-002·023 후속 로컬 검증

문서eef5563의 [후속 CI39119680966](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/39119680966)은9job/13분1초·Windows5581건으로 통과했다. 이후 제품 로컬커밋 `c0cf7ce`에서 가격 retry의 원일·원batch시각/최신값·고가MAX/실패종료 보존과 두 naive/aware 비교 경계를 병렬 보완했다. 관련5모듈 **128건 통과**, 동일native 전후5조건·구조가드·18번째증분원장/실행ID 대조를 확인했다. 정상 batch의 연결/COMMIT1·SELECT0·기존3owner를 유지했다. [백로그](../REFACTOR_BACKLOG.md)의 F04/F06/F08/F10/F18/F22/F23/F24와 책임계약을 갱신했다.

R3-002는 이번 선택범위 로컬 검증 완료, R3-023은 constructor provider·dataset saved_at/legacy 잔여 때문에 진행이다. 다음은023의남은 작은 시계 연결이며002를 재조사/재구현하지 않는다. F06의 별도 당일RAM gate 후보는033에 보존했다. 새제품 push/CI·전수로컬·NAS/PG·실계좌는 미실행이다.

**추가 CI 단축 검증 완료(2026-10-18):** `a325e31`을main에push한 [실제 CI39118437411](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/39118437411)이9job전체성공/13분59초였다. Windows320worker·463모듈·5581고유ID가현재전체계획/source/lock/종료·두shardjob성공과일치했고원래required까지통과했다. 직전33의20분10초보다6분11초단축, 빠른d5의15분38초대비1분39초단축이다. Windows총VM job시간은19분19초→22분54초로늘었으며환경편차/토큰·실제요금미측정한계는 [R3-083](../REFACTOR_BACKLOG.md) 말미에기록했다. F08/F13/F24/F25도같이갱신했다. 제품소스·DB·주문·NAS변경0/다음제품002+023유지. 이결과기록후속문서와검증된코드CI를구분한다.

## 2026-10-18 R3-083 push·실제 CI 지연 원인 보완

`c7a6cef`와 후속 `d5bec26`를 main에 커밋·push했고 [전체 CI39115204095](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/39115204095)가 **7job 성공·15분38초**에 완료됐다. Windows320worker·463모듈·5577건 전부 통과, 계획/실행 ID·소스 동일·종료를 확인했다. 고정 fake시계로1800초 timeout되던 계좌 worker는3.518초/8건 통과했다. 구형 시계 fixture·stdout 계약·sealed 실행 근거 전달만 보완했고, native writer PID 종료 경합도 실제 관찰해 원래 restore fence를 유지한 채 검증했다. 최초 실패와 미실행269worker 기록, 전후 측정·잔여 한계는 [R3-083 정본](../REFACTOR_BACKLOG.md) 말미에 보존했다. 제품/운영DB/주문/NAS변경0, CI 전체 범위·안전 판정·다음제품002+023은 유지한다.

## 2026-10-18 R3-083 가드레일 최종 차단 검증

아래는 push 전 로컬 검증 당시 이력이다. 후속 커밋과 실제 전체 CI 최종 결과는 바로 위 절과 R3-083 말미를 따른다.

전수 재감사 없이 기존 구조/CI56건·성능 mutation4건·격리 CLI2건(19조건)을 통과했다. 정상 기능/정당 변경은 허용하고 금지 의존·cycle·DB/RAM I/O와 기존 비용 위반은 실패했다. 기존 CI 단위검사의 비활성화/실패무시 등6변이 검출 공백만 보완했다. [R3-083](../REFACTOR_BACKLOG.md#r3-083--개선된-책임실행-비용기능-연결의-퇴행-방지)에 자동/설계검토·잔여위험·측정 부담을 기록했다. 실제 push `2c3888f`의 가드 단계 성공은 확인했으나 조회 시 전체 CI는 진행 중이다. 새 검사 hosted·PR/의도 위반 hosted 실패는 미검증, 이번 commit/push·제품/운영 자료·배포·전체 회귀0이다. 다음 제품 작업002+023은 유지한다.

## 2026-10-18 PC·NAS·GitHub 저장소 이력 통합

최신 PC `main`의 `63dc3c9`와 NAS 파일 커밋 `3a59b18`을 양쪽 이력을 보존하는 병합으로 통합한다. NAS의 과거 문서135개와 배포 스크립트 원문은 [NAS 보존본](archive/2026-10-18/nas-snapshot/README.md)에 남기고, 현재 문서 배치와 최신 DB 호환·실패 복구 스크립트는 유지했다. 생성 산출물·백업·운영 자료의 Git 제외 항목을 합쳤으며 제품 코드 변경은0이다.

기존 배포 실패·복구 회귀5건, 운영 자료 제외 예시12개와 소스 비제외 예시3개를 통과했다. 검사 중 소스 불변·worker/자식 종료·임시 자료 정리와 NAS 원문 Git blob 보존을 확인했다. 근거는 `data/diagnostics/git-unify-20261018/`다. 저장소 일치는 NAS 운영 릴리즈 배포나 재시작·전수 회귀 수락을 뜻하지 않는다.

## 2026-10-18 누적 변경 통합 확인·PC 테스트 앱 반영

사용자 요청으로 기존 미커밋 제품·검사·도구·감사 문서를 함께 확인했다. 뉴스 이력의 기존 `before_revision_id` 계약은 구현/API 문서/페이지 경계 검사와 대조한 뒤 QueryStore·SQLite·PostgreSQL의 정적 계약 기록 3곳만 맞췄다. 다른 호출·전달·중복 항목은 바꾸지 않았으며, 최초 정적 감사 실패와 보완 근거는 별도로 보존했다.

변경 검사와 기존 소비자 **170모듈의 고유 3,050건이 현재 동일 소스로 통과**했다. 실패/오류/skip/expectedFailure/중복 실행/미실행은0이며, 모든 worker·자식 종료와 임시 자료 정리를 확인했다. 실행 중 소스 변경0, 증분 원장/실행 ID 대조도 통과했다. 이는 전체463모듈의 전수 회귀가 아니다. 근거는 `data/diagnostics/commit-cumulative-20261018/`이며 전체 로그와 최초 실패를 보존한다.

기존 테스트 바로가기가 현재 작업 폴더의 `scripts/run_test_app_with_data.py`를 사용함을 확인하고, 같은 진입점·기존 개인 데이터로 PC 앱을 기동했다. 실제 테스트 창의 응답·순위표 갱신·기동 이후 오류0을 확인했다. R3 백로그와 원래 해석 미결49건은 유지한다. PC 반영은 NAS 배포·실제 PostgreSQL/장중/외부 AI 수락이나 새 GitHub CI 성공을 뜻하지 않으며, 이번 요청의 Git 작업 범위는 로컬 커밋이다.

## 2026-10-18 R3 감사 종결과 구조 퇴행 방지 기반

R3 잔여 코드감사를 종결해394모듈 코드정밀 완료394/부분0/정적만0으로 갱신했다. 기존62항목·77경로·이전 판정은 보존하고 신규063~082는 미해결로 남겼다. 아래25/175/194는 직전 깊이 대조의 역사적 상태다. 실제 운영 전체 정상 판정과 코드감사 완료는 구분한다. [현재 백로그](../REFACTOR_BACKLOG.md).

R3-083으로 [기능별 책임 계약](architecture/r3-ownership.md), 구조 delta 검사와 기존 비용 회귀·CI 연결을 구축한다. 기존 QueryStore/PG/기능 검사를 재사용하고 새 반복 전수검사 일정은 추가하지 않는다. 각 R3 개선 시 관련 책임·비용·F 원장을 함께 갱신하며 최종 종료 조건에 체계 검증을 포함했다. 다음 제품 구현은002+023을 유지한다. 로컬 실행 근거는 `data/diagnostics/structure-regression-20261018/`이며 새 hosted CI·push·NAS·운영 적용은 미실행이다.

## 2026-10-18 선택적 감사 조회·실행 명령 보완

기존 `check_test_changes --query`와 `select_ci_groups`에서 Git 작업 변경·변경 전 import·fixture/계약/동적 의존성의 선정 근거를 조회하고, 관련 감사/미결/후속 항목만 반환한다. 압축 원문은 최초 파생 인덱스 생성 뒤 반복해서 읽지 않는다. 기존 `run_regression --selection`이 소스와 선정을 재대조해 기존 worker에서 실행하고 실제 모듈/ID를 확인한다. 성공 요약·실패 상세는 `--compact`/`--summarize`로 제공한다. [명령과 한계](INCREMENTAL_TEST_REVIEW.md#선택적-조회실행-명령).

현재 소스의 관련92건 통과, 실제 선택 명령의17건 통과(92건에 포함된 동일 검사), 증분 원장/실행 대조 통과를 확인했다. 처음92건의 fixture 경로 오류1건과 수정 후 결과는 별도 보존했다. 넓은 범위는 `full_required`로 실행 전 거절하며 전체회귀로 자동 전환하지 않는다. 기존 감사 판정/미결49·동시 미커밋 변경을 보존했고 제품 코드/로컬 전체회귀/commit/push/NAS/운영 작업은 없다. 새 GitHub 실행과 외부 Linux/PG 환경 수락은 미검증이다. 근거는 `data/diagnostics/selective-tests-20261018/`이다.

## 2026-10-18 R3 394모듈 감사 깊이 대조·누락 범위 보강

1·2차의정적목록394개와77개실행경로를일대일대조했다. 모듈전체본문책임의정밀완료 **25**,부분정밀 **175**,정적분석만 **194**,정적목록도미검토0이다. 정밀미완료 **369개**는정본의G01~G09와394행/상세JSON에남겼다. 파일접근·AST·함수검색·테스트수만으로완료를인정하지않았고기존77경로를모듈전체완료로확장하지않았다.

누락본문110모듈과14개오프라인진단조건을추가했고신규R3-051~062는대기/미해결이다. [정본](../REFACTOR_BACKLOG.md)에서기존R3/F이력과추가근거·실측/가설/미검증·완료기준을함께관리한다. 이번제품수정0·운영DB/NAS/실제GUI·주문·외부AI/전수회귀·commit/push/배포없음. QueryStore정적계약은현재도review_required(exit1)로기록하고baseline을승인하지않았다. 다음구현은기존002+023원거래일실패계약,감사만이어갈때는G01미확인nativeleaf한경로부터다.

## 2026-10-18 증분 테스트 관리 도입

[증분 테스트 관리](INCREMENTAL_TEST_REVIEW.md)를 기존 실행기·필수 CI에 연결했다. 원래 5,504개/460모듈 감사 판정과 후속 수정 근거·미결49건은 보존하며 재감사하거나 자동 완료 처리하지 않는다. 이후 테스트 본문·fixture·skip·등록·ID/개수 변화와 제품 변경의 관련 실행을 추가형 원장으로 대조한다. 기계 목록의 최초 채택은 다른 작업의 기존 미커밋 변경을 소급 승인하지 않는다.

관리 도구20건과 기존 실행기/PG 실행기 단위를 포함한 관련75건이 현재 소스로 통과했다. 실제 실행 보고서의 계획/실행 ID·발견 수·소스 일치·worker 종료/임시 자료 정리와 증분 CLI 대조도 통과했다. 테스트 추가·수정·삭제의 미검토 거절/근거 반영, ID 연결과 이전 결과 재사용 거절을 확인했다. 근거는 `data/diagnostics/test-management-20261018/verified-run/` 및 `verified-execution.json`이다. 제품 코드 변경·로컬 전체회귀·commit·push·NAS/운영 변경은 없으며 실제 GitHub·Linux/PG 실행 수락은 아직 확인하지 않았다. 의미·요구사항·독립 기대값과 전체 소비자 선정은 Codex 검토로 남는다.

## 2026-10-18 R3 2차 정밀 코드 감사 완료

기존 변경을 보존하고 실제 caller·상태 owner·저장/재조회·UI소비·실패/동시/종료를 77개 세부 경로로 보강했다. 정본 [REFACTOR_BACKLOG](../REFACTOR_BACKLOG.md)에 신규 R3-030~050(21개), 기존011/023/027 등의 추가 재현, F01~F25 세부상태와 미검증 범위를 반영했다. R3-029는 기존 ID라 재사용하지 않았다. ‘유지’ 계층의 필요성과 내부 책임 적절성을 별도로 재평가했다.

격리 진단21조건은 현재 문제/비용의 재현 성공이며 해결 완료가 아니다. 별도 매매 관련 기존 회귀21건이 통과했다. 이번 제품/공유테스트 변경0, 운영DB/NAS/실제주문/외부AI/전수회귀/commit/push/배포 없음. 기존 R3-002 transaction·R3-004 range 구현과 당시 검증은 유지한다. **다음은 R3-002의 실패고가 원거래일 유실(R3-023) 후속**으로, 기존 batch를 다시 만들지 않고 caller→writer→DB→readback/종료를 보완한다. 아래 이전 ‘다음003’ 표현은 이 2차 감사 전 기록이다. 실제PG/설치앱/장중전체연결과 기존뉴스3서명 sourceaudit1실패는 미해결이다.

## 2026-10-18 검사 보강·확인된 제품 오류 후속 수정

사용자 요청으로 보강216건 가운데 미반영117건을 추가 반영했다. 기존99건과 최초 감사 원장은 보존하며 검사별 현재 실행 여부를 별도로 기록한다. 혼합 offset 일지/metadata 비교, 뉴스 후보 백업 유실·이력 페이지 누락, Daishin 부분 커밋, 저장 실패 버퍼 유실, API 설정 부분 교체 복원, NAS worker 오통과·runner 후속 실행과 확인된 시계 연결을 수정했다. **관련 고유1,879건 통과이며, 추가 보강117건도 모두 포함한다.** 기존 보강까지216건 중171건을 현재 실행했고 변경 없는45건은 이전 감사의 검증으로 구분한다. 화면·API·저장 worker·재시도/종료 소비 경로는 [후속 수정 기록](workstreams/test-case-review-20261018/FOLLOWUP.md) 및 [정확한 현재 실행 ID](workstreams/test-case-review-20261018/followup-verification.json)를 따른다.

해석 미결49건은 그대로 두었으며 generic-report의 분류 정답도 임의로 만들지 않았다. 과거 비교용 기준 JSON은 보존하고, 업무 시계가 달라진 열은 주입값을 직접 확인한 뒤 비교 사본에서만 과거 표현으로 맞춘다. 최초 실패·Qt core/widget 혼합 종료·안전한 중단/재개와 통과 기록을 분리했다. 로컬 미커밋·미배포이며 전수 회귀/새 CI·운영 DB/실제 주문·외부 추론은 수행하지 않았다. 별도로 진행 중인 순위표·R3 변경은 보존하고 이 작업의 실적으로 합치지 않는다.

## 2026-10-18 R3-002/004 저장·조회 비용 개선과 독립 항목 병렬 진행

R3의 현재/다음과 F01~F25 연결 상태는 [REFACTOR_BACKLOG](../REFACTOR_BACKLOG.md)가 정본이다. R3-002는 기존 queue job의 가격·시총·고가/정리를 기존 저장소의 단일 transaction으로 바꿨다. 20종목 fixture 연결/COMMIT3→1, SELECT0·반영60 유지와 숫자·timestamp 실제 순간의 동일성을 확인했다. 기본정보 확인시각·유통시총 계산·고가 MAX/원래 거래일·35일 정리·기존 queue/실패 최신값 병합·drain을 보존한다. 기존 키움 보정 `ranking_now`를 주입하며 새 계층/파일/schema·운영 저장 변경은 없다.

R3-002의 관련140건이 통과했다. 이후 공유 시계가 writer의 분봉/일봉 저장소에도 연결된 뒤 writer7+Qt 연결/종료5건을 부분 재실행해 모두 통과했고 소스 변경0을 확인했다. 중간의 분봉 naive/aware 저장 실패는 비교2곳만 보완했고 교체 검사 Mock 불일치는 실제 writer fixture로 바로잡았다. 최초 실패/timeout과 변경 전후 결과를 별도 보존하며140+12를152고유검사로 합치지 않는다.

독립 에이전트의 R3-004는 실제 Runtime→Collector→기존 SQLite/PG reader에 선택 범위를 전달하도록 구현했다. Shadow만 기존10분 window를 opt-in하고 다른 caller의 당일 전체 기본값/API와 RAM overlay/권한/중복 방지를 유지한다. 고정 fixture의 native 반환9000→200행·최종200행 값/metadata 동일, 연결20/SELECT22 유지와 기존 index의 minute 범위 탐색을 확인했다. 기능 고유58건 통과이며 실제 PG/장중은 미검증이다. 정적 source/audit는8통과/1실패: R3의 reader3서명/중복 identity는 제한 갱신했고 남은 차이는 병행 뉴스 `load_news_history(before_revision_id)`3서명뿐이다. 전체 정적 gate 통과로 보고하지 않는다.

R3-003 독립 조사는 cap-only 후보의 동일값 반영20→0을 검증했으나 제품은 대기다. 테마 revision이 stocks 전체 열에 결합되어 같은 cap의 timestamp만 변해도 실제 import 충돌/재시도가 발생함을 확인해 R3-029로 추가했다. R3-008은 외부 control 변화/늦은 binding 발견 역할 때문에 현재 polling 유지·개선 보류다. 이후에도 독립 항목은 병렬 진행하고 공유 백로그/F01~F25를 주 담당자가 동반 통합한다. 다음은 가격 확인시각을 유지하는 R3-003 cap 조건부 UPDATE다. 로컬 main/미커밋·CI/배포/실제 장중 수락 없음.

## 2026-10-18 전수 구조 감사·R3-001 첫 개선

실행 비용·구조·기능 연결의 현재 정본은 [REFACTOR_BACKLOG](../REFACTOR_BACKLOG.md)다. 제품394 Python 모듈/정의6,806개와 별도 스크립트174개의 정적 목록을 만들고 주요25경로의 실제 조립·호출·저장/소비·수명을 대조했다. 28항목을 제거/통합/유지/재설계와 우선순위/상태·실측/가설·완료 조건으로 관리한다. 전체 함수/외부 연동의 실행 검증과 구분하며, 실제 앱/브로커/NAS/PG/장중 수락이 없는 경로는 확인 불가 또는 부분 연결로 남겼다.

첫 개선 R3-001은 로컬0B 분봉의 동일 metadata 중복 조회다. 한 제품 파일 안에서 기존 행을 권한/품질 판단에 재사용하고 순수 권한 predicate를 공유했다. 기존 BEGIN IMMEDIATE·transaction/commit·writer와 업무 시계는 유지한다. 고정20종목 fixture에서 SELECT40→20, 연결/BEGIN/COMMIT 각1과 값/metadata 전체 동일을 확인했다. 변경 전 독립55건, 변경 후23+5+30=58건이 통과했고 실제 Qt signal→현재 MainWindow/controller→writer→DB·실패재시도·종료 후 재조회도 확인했다.

최초 합동 baseline의 Qt native 종료는 결과 부재/실패로 보존했고 내부 원인을 제품 결함으로 확정하지 않았다. 같은 소스의 독립 worker는 통과했다. 서버 연결 계약258건과 직접 create_app/QueryStore/인증 coverage ASGI1건을 더해 이번 변경 후 관련317건이 실패/오류/skip0으로 통과했다. 서버 검사는 실제 app lifespan/binding·공유 cut/checkpoint/선택 API도 포함하며 실제 Kiwoom/PG 수락과 구분한다. 자세한 실행·종료·자료 정리 근거는 `data/diagnostics/refactor-r3-20261018/`와 백로그에 있다. 다음은 R3-002 가격 cache job의 다중 transaction이다. 로컬 main/기준HEAD6cf3d4c의 미커밋 변경이며 기존 테스트 정리와 직전 순위표 변경은 보존했다. 전수 회귀·새 CI·commit/push·NAS 배포·운영 DB 변경·실제 주문은 수행하지 않았다.

## 2026-10-18 순위표 저장값 복원의 반복 조회 정리

필요한 경로부터 정리하라는 요청의 첫 범위로 `bootstrap → MainWindow._apply_ranking_view → StockRepository → RAM/표`를 수정했다. 시장·현재가·당일고가·기본정보·NXT의 개별 조회5회를 기존 저장소의 묶음 읽기로 바꿨다. 동일 fixture에서 연결5→1회, SELECT5→2회이며 쓰기는0이다. 새 캐시 수명·서비스·전달 계층·파일·스키마는 추가하지 않았다. 기본정보 변환은 기존 개별 reader와 공유한다. 고정 구성 종목의 시장 분류와 화면 종목의 값 범위, 원래 누락/0/음수 처리, NXT의 미확인/명시Y/N·당일 기준, 날짜별 고가와 카탈로그 등록 전 고가를 보존한다. 일봉·역사적 신고가의 별도 조회/갱신과 부분 lookup의 기존 계약도 유지한다.

변경 전64건, 변경 후 저장소/Qt 화면67건과 관련 소비자27건(고유94건)이 통과했다. 실제 SQL trace의 기존 reader 대조와 다음 읽기의 값 변경 반영, Qt 순위 신호 후 RAM/표의 NXT 전환을 확인했다. 각 사용자 환경 worker는 종료코드0·자식 전체 종료·임시 자료 정리를 확인했다. 최초 샌드박스의 SQLite 임시 폴더 접근 오류는 별도 보존했다. 근거는 `data/diagnostics/ranking-cache-cleanup-20261018/{before,before-user,after,consumers}/`다.

로컬 main/기준HEAD `6cf3d4c`의 미커밋 변경이다. 기존 테스트 정리 변경은 보존했다. 이 수치는 해당 다섯 항목의 조회 비용이며 전체 순위 갱신 지연이나 장중 성능 수락이 아니다. 반복 저장·다른 책임/계층의 정리, 실제 앱/NAS·장중 수락, 전수 회귀·CI·commit/push·배포는 이번 범위에 포함하지 않았다.

## 2026-10-18 고유 검사 5,504건 개별 의미 검토 완료

사용자가 각 검사를 현재 요구사항·실제 코드에 대조하도록 요청했고 검토 에이전트
3개와 병렬 분담을 승인했다. 직전 CI의 5,516회 실행에서 고유 검사 5,504건을
확정했으며, 중복 등록으로 반복 실행된 12회는 별도로 구분한다. 검사 본문·fixture와
현재 제품 경로·요구사항을5,504건 모두 개별 대조했다. 원장 집합의 누락·중복 배정은0이다.
아래492파일 정적 선별과 이번 개별 의미 검토는 별개다. Windows 기준 밖의 별도
Linux/PostgreSQL 전용 검사는 이번5,504건에 합치지 않았다.

판정은 유지5,208·보강 대상216·기대값 수정19·정리12·해석 미결49건이다. 보강 대상
전체가 구현됐다는 뜻은 아니다. 삭제12건의 대체 범위를 확인했고 실제 저장 결과·Qt 종료·
독립 기대값·실패 원자성·시계 fixture를 관련 범위에서 보완했다. 혼합 UTC/KST 비교,
뉴스 후보 백업 유실, Daishin 오류 뒤 부분 커밋, NAS expectedFailure 통과 등을 격리
재현하거나 정적 확인해 별도 원장에 남겼다. 이번 감사는 제품 코드를 바꾸지 않았다.
관련 고유1,086건이 통과했고 최초 실패와 재실행을 별도 보존했다. 전수 실행·새 CI·commit/push·
NAS 배포·운영 자료 변경은 없다. 같은 작업 공간의 순위표 조회 변경은 별도 작업이다.

[전체 판정·한계·개별 원장·실행 ID](workstreams/test-case-review-20261018/README.md).

## 2026-10-18 테스트 중복·유효성·결과 검증 정리

사용자 5개 기준으로 unit/integration 492개 파일의 정적 선별과 유사 후보21쌍,
mock 호출 assertion만 있던 후보18건을 검토했다. 테마 색상 모듈의 중복 등록을
제거해 반복 실행12건을 없앴고, 구형 고정 횟수/위치 기대를 등록 목록의 1회 실행과
lifecycle 범위 확인으로 바꿨다. 현재 혼합 UTC/KST 동기화 검사는 변환 도구 검사에서
실제 동기화 테스트로 옮겼으며, 제공 중인 변환 도구8건과 과거 비교/보존 계약은 유지한다.

REST는 실제 송신 간격, 뉴스는 Qt 키 입력 뒤 저장 원문의 표시를 검사하도록 보완했다.
각 기능을 격리 메모리에서 제거한 대조에서 기존 검사는 통과하고 보완 검사는 오류를
잡았다. 관련176건과 대조2건이 통과했으며 catalog465 unit/27 integration 배정도
정상이다. 첫 화면 fixture 실패1건과 재검사 결과를 별도로 보존했다. 계획상 Windows
460개 고유 모듈/317개 실행 묶음이며 예상5,504건이다. 변경 후 전수/새 CI는 실행하지
않았고, 직전6cf3d4c의5,516건 CI 통과를 현재 수정본의 결과로 승계하지 않는다.

[5개 기준별 판단·보존 이유·남은 운영 검증](TEST_FINAL_QUALITY_AUDIT.md#2026-10-18-사용자-5개-기준에-따른-검사-정리).
제품 코드·운영 자료·주문/권한·NAS 배포는 변경하지 않았으며 이 단계는 로컬 수정이다.

## 2026-10-18 새 연구 프롬프트 1일 실제 수신·읽기 완료

미완료 첫 날짜인 2026-01-05 07:55 KST를 기존1720일치와 분리해 시험했다. 최초 웹 요청은 기존600초 제한으로603.057초 뒤 종료됐다. 사용자 요청으로 같은 프롬프트·웹 검색을 유지하고 이번 요청의 전체 스트림 제한만 `None`으로 지정했다. 2026-10-17 06:03:36 KST,618.613초 뒤 실제 응답을 수신했다. 웹 동작8회·출처26개·사실16개·재료5개·추가 수치45개·관계 각2개·가설4개가 원응답에 보존됐다.

최초 읽기는 날짜/시간대 설명이 섞인 시각 문자열 때문에 실패했다. 연구 경로에서 해당 표기·수치·참조를 지우거나 임의 시각으로 바꾸지 않고 unknowns로 구분해 재생했다. 기준 날짜 이후 원문 수정 표기도 명시해 당시 버전으로 자동 확정하지 않는다. 새 AI 호출0으로 대상별 영향10행을 읽었고, 원응답 hash·전체 사실/사건/출처/측정값/관계의 보존과 기존 batch 상태 hash/STOP 유지를 확인했다. 관련 시황/API/화면68건 통과, 시간 제한 옵션의 인증/취소/스트림/검색 관련40건도 통과했다. 기존 일반/연구 요청의 기본180/600초는 보존하며 운영 설정·NAS 배포는 변경하지 않았다.

[실제 결과](../data/research/news-market-research-pilot-20260105/unlimited/report.html) · [보존·읽기 기록](../data/research/news-market-research-pilot-20260105/unlimited/read-receipt.json). 전체 수집은 중지/1720일 그대로이며 자동 연구·매매 연결이나 과거 전체 재생성은 수행하지 않았다. 아래의 ‘실제 호출 없음’은 프롬프트 준비 단계 기록이다.

## 2026-10-17 00 종합 기준의 뉴스 연구 입력 프롬프트 준비

사용자 정정에 따라 00 종합의 기능·입력 구조를 기준으로 삼고 첨부 뉴스레터의 분석 관점을 참고한 [연구 프롬프트](workstreams/news-market-research-prompt.txt)와 [대응·연결 범위](workstreams/news-market-research-prompt.md)를 작성했다. 기존 장전 DTO를 재사용하는 별도 `news-market-research-v1` 출력, 과거 동결/직접 검색 입력 구분, 사건 변화·대상별 영향·추가 수치를 받는 형식과 문장을 해석하지 않는 순수 읽기를 준비했다. 보조 분류를 00의 원래 고정 코드나 매매 규칙으로 취급하지 않는다. 출력 스키마에서 annotation `title` 제거가 실제 `properties.title`까지 제거하던 오류도 수정했다.

관련 계약·연구·Runtime53개 개별 검사와 화면13개 재검사가 통과했다. 최초 제한 환경의 비동기 검사는 대기하여 종료했고, 사용자 환경 실행의 화면 모듈1개 로딩 오류는 기존 tests/unit 지원 경로를 넣어 재검사했다. 최초 로그도 보존했다. 수집은 `stopped`/1,720일/STOP 유지이며 기존 동결 입력·결과·원자료는 수정하지 않았다. 운영 프롬프트/저장 설정 교체, 과거 batch·연구 loader·자동 연구/매매 연결, 새 실제 LLM 호출, NAS 배포·commit/push·CI·전수 회귀는 수행하지 않았다.

## 2026-10-17 PC 아침 시황 자동 갱신의 스크롤 보존

10초 타이머가 같은 HTML을 반복 설정해 읽던 위치840이0으로 바뀌는 것을 Qt 화면에서 재현했다. 기존 dialog 안에서 동일 실행/HTML의 재설정을 생략하고 같은 시황의 내용 변경은 기존 스크롤을 보존한다. 새 시황 전환은 처음부터 표시하며 빈 결과 후 재조회도 정상이다. 직접 관련 화면13건이 통과했다. PC 소스 수정이며 실행 중인 앱에는 재실행 후 반영된다. NAS 활성 release는 아래 연구 자료 보존v2 그대로이고 서버/자료/추론 변경은 없다.

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

## 2026-10-17 통합 후속 검증·장전 출처 보완 배포

장중 원 revision254건으로15:02 KST의 COMPLETE/REPLACED·후보009150을 확인했다. 실제 저장 기사1건은 새 Gemini 호출18.045초·HTTP200·cache_hit=false·본문 hash/저장 output/분석 revision 일치를 확인했다. 당일 전체 뉴스 범위나 과거 non-JSON 원인 확정은 아니다.

기존 웹 출처 수집기의 open_page/find_in_page URL 누락을 재현해 보완하고 실제 동작 종류/반환 URL만 실패 run에도 보존한다. URL 임의 정규화·검증 완화·설정 변경은 없다. PC 관련101건과 NAS 관련69건이 실패/오류/skip0으로 통과했다. 첫 NAS 실행의 Windows msvcrt 오류32건은 실패 기록으로 보존하고 Linux 검증으로 구분했다. 활성 release=`2026.10.17-brief-web-evidence-v1-dfcb5c32623f3638`, build=`2026.10.17-brief-web-evidence-v1`로 기존 배포를 완료했고 DB 컨테이너·의존성/스키마/승인값을 유지했다.02:04 KST HTTP health=ok·시각+09:00·인증·613분봉/2일봉·WebSocket ready/pong을 확인했다. 실제 장중 무손실/실거래 수락은 아니다. 승인받은 장전 검색1회는518.291초 뒤 `brief_evidence_source_unverified`로 실패했다. 웹 동작14개(search11/open_page3)·출처248개가 보존됐고, 모델도 unverified로 표시한 KRX/CNBC 주소2개가 실제 웹 출처에 없었다. 두 미확인 근거와 참조만 제외해 unknowns에 남긴 로컬 미리보기는 같은 검증기를 통과했으나 원래 run/운영에는 미적용이다. 전체 실패 대신 이런 미확인 분리로 나머지를 표시할지와 추가1회 검증은 사용자 확인 대기다. **장전 결과 수락은 미완료**다.

근거: `data/diagnostics/nas-cumulative-deployment-20261016/integrated-after-kst-20261017/`. 새 commit/push/CI·전수 회귀는 실행하지 않았다. 아래 누적v2 활성/조회 대기 표현은 이전 단계 기록이다.

## 2026-10-17 KST 기존 자료 변환 완료·통합 검증 재개

변환 후01:37 KST 실제 GET에서 누적v2·health 정상·KST 시각·기존 뉴스 revision 보존·07:55 설정과 실제 예약 이력을 재확인했다. 동일 저장 본문/해시의 일반 뉴스는 HTTP200·cache_hit=true·저장 output 일치·추가 사용량0으로 확인했다. 원래 non-JSON 실패 원인은 아직 미확정이며 새 비캐시 추론 성공으로 대체하지 않는다. 10월16일15:00~15:02의 현재 분봉 투영본은 장후 보완/추정 대금 자료여서 당시 후보 재생은 UNKNOWN이다. 장중 원 revision의 고정 읽기를 준비했고 실제 조회는 관리자 인증 대기다. 예약 시황의 원 응답을 로컬 재생한 결과 출처 URL의 `%3A`/`:` 차이를 확인했으며, 그 URL만 바꾼 진단 복사본은 검증을 통과했다. 원래 run은 실패 그대로이며 코드/설정·검증 조건을 바꾸거나 재추론하지 않았다. 새 근거는 `data/diagnostics/nas-cumulative-deployment-20261016/integrated-after-kst-20261017/`이다.

v5 실제 운영 변환이 완료됐다. 기존 저장 셀 **15,013개**를 같은 순간의 KST 표현으로 바꿨고 **22개 대상 전체 후검사에서 변환 대상 UTC 잔여는 모두0**이다. 이는 중첩 JSON 시각의 개수가 아니라 변경된 DB 셀 수다. 보존 복원본166행·12트랜잭션의 대조/롤백 시험, 변경 전 원본 보존, 컨테이너 정리·운영 후검사를 통과했다. 이미 KST·epoch·봉 키·canonical 원본·ID/hash 입력은 유지했으며 스키마 변경·운영 재시작·DB 재복사는 없다. 결과는 `data/diagnostics/kst-existing-storage-20261016/v5-result.json`이다. PC28,813셀 변환과합쳐 앞서 변경한 저장 경로의 기존 자료 변환을 완료하고 원래 통합 수락 검증을 이어간다. 아래 v5 실행 대기·미완료 표현은 각 단계 당시의 기록이다.

## 2026-10-17 KST 저장 경로 대조·누락 보완

무암호 도구의 승인 비교 오탐은 수정했다. 실제 서버·DB 모두 DNS 빈 값3개의 null/빈 배열 차이만 대조하면 기존 승인 해시와 정확히 일치했다. 승인 설정 파일은 바이트까지 그대로이며 로컬 관련77건/적용 원인 확인3건·NAS Linux100건·실제 무암호 status가 통과했다. KST PostgreSQL8건도 무암호 실행으로 오류/실패/skip0·정리/후검사를 통과했다(job `c091be002a8ebd0a5d491619e92b7fb6`). sudoers·운영 release·컨테이너·DB는 보존했고 재시작은 없다. [원인·수정·실측](NAS_OPERATOR_COMMANDS.md#2026-10-17-재부팅-후-dns-빈-값-표현의-승인-비교-오탐-수정).

이전 시계 변경 기록137개 지점과 관련 diff85개 파일을 저장 경로/읽기 계산/epoch·TIMESTAMPTZ/불변 canonical 원본으로 대조했다. PC settings.value의 UTC 시각2개가 빠진 것을 확인해 별도 원본 보존 후 변환했으며 확인된 PC 변환은 총28,813셀이다. Drive 최신값 비교는 같은 순간으로 비교하고 새 로컬 marker도 KST로 유지한다. 관련64건·계측3건 통과,3개 DB 무결성/추가 변환0을 확인했다.

v4는 실제 보존 복원본166행·12트랜잭션의 변환/대조/롤백을 통과했다. 운영 실행은 새 백업 폴더가 요청0700과 달리0777로 만들어져 DB 연결 전 `private_backup_mode_invalid`에서 중단됐다. 오류 원문·소스 실행 순서·실제 stat으로 운영 DB 변경0을 확인했고 과거 보고서의 null은 보존했다. NAS 실제 fixture에서 mkdir 후0777→FD fchmod 후0700을 재현했다. 새 백업 폴더만 FD로 권한을 확정하는 v5는 로컬7건·무암호 NAS PostgreSQL9건의 오류/실패/skip0·정리/후검사를 통과했다(job `077874b83f2af1d7b97e293c99d6a585`). 권한 상속 재현과 권한 설정 실패 시 DB 미접속도 검증했다. 검사한 동일 바이트로 게시했으며 실제 v5 관리자 실행 결과는 대기다. **NAS 기존 자료 변환과 원래 통합 수락은 아직 남는다.** [저장 경로·진단·근거](workstreams/time-basis-kst.md#2026-10-17-이전-변경-경로-대조누락-보완과-nas-시험-중단).

## 2026-10-16 기존 저장 자료 KST 변환 진행

사용자 지시에 따라 원래 통합 검증 전에 기존 저장 자료 변환을 재개했다. PC 실제3개 DB의 명시 UTC 시각28,811셀은 같은 순간의KST 표현으로 변환했고, 원본 셀 파일 보존·DB별 재실행 변경0·관련36건을 확인했다. NAS 읽기 전용45테이블/59컬렉션 조사와 유지보수 묶음4건·NAS 구문/해시 검증은 통과했으나 실제 v2는 이후 복원본 시험에서 실패했다. 최신 누락 보완과 미완료 범위는 위10월17일 기록을 따른다.

원래 통합 검증을 재개하면서 실제 NAS 이력을 읽었다. `2026-10-16:scheduled`는07:55:00.434946 KST에 claim돼 검색13회를 수행했고 `brief_evidence_source_unverified`로 실패 결과가 저장됐다. 따라서 예약 기동·결과 저장 근거는 있으며 시황 내용 수락은 미완료다. 누적v2의23:33~34 KST HTTP/KST/WebSocket 정상과 별도로 기록한다. 새 추론 요청·예약 변경은 하지 않았다. 근거는 `data/diagnostics/nas-cumulative-deployment-20261016/integrated-resume/`다.

## 2026-10-16 통합 검증 완료 범위 정정

누적 NAS v2 배포·기본 응답 검증 완료는 원래 약속한 통합 검증 전체 완료가 아니다. 원래 범위는 NAS 격리 검사/런타임·배포, 정상 시각 분봉 후보 변화, 실제 뉴스 분석·저장, 실제07:55 예약 확인이다. 10월15일 후보 변화의 실제 관측과 일반 뉴스 분석/저장 근거는 [통합 수락 원장](workstreams/nas-integrated-validation-20261015.md)에 있다. 뉴스의 non-JSON HTTP 오류/응답 대조와 실제07:55 예약 수락은 마지막 확인 기록에서 남아 있고 최신 운영 이력은 다시 확인해야 한다. 마지막 수동 시황 생성의 출처 검증 실패도 완료로 바꾸지 않는다. [복구한 잔여 목록](OPEN_ITEMS.md#2026-10-16-원래-통합-검증-범위-복구).

## 2026-10-16 누적 NAS v2 배포·실제 응답 검증 완료

23:15 KST 실제 서버의 build=`2026.10.16-cumulative-kst-0b-breakout-v2`, 활성 release=`2026.10.16-cumulative-kst-0b-breakout-v2-e1fcf512d535cdc1`와 PID1이 사용하는 source 경로를 확인했다. 배포 journal=`complete`, DB 컨테이너 불변, 진단 idle/trace OFF·예약 작업 없음이다. 기존 source 런타임 이미지/컨테이너를 재사용했으며 이미지 재빌드·컨테이너 재생성·sudoers/운영 설정·계좌 운용 활성 변경은 없다.

확인된 `/health` 연결 오류를 재현·최소 수정한 뒤 PC 관련37건과 NAS health ASGI·0B·분봉·KST·실제 PostgreSQL41건이 각각 실패/오류/skip0으로 통과했다. NAS는 CPU/메모리 격리와 임시 컨테이너 정리·운영 후검사를 확인했다(job `1bf6bd245ff34994cd33aecfe6f0fcdf`). 기존 누적 PC224건 근거와 새 health 수정 후37건 근거를 구분한다. 도구 관련81건과 실제 Linux97건도 별도 통과했으며 전수 회귀는 추가 실행하지 않았다.

배포 후 실제 HTTP에서 health=ok·server_time의+09:00, capabilities 인증401/정상 조회, 기존 분봉613행·일봉2행, WebSocket ready/pong을 확인했다. 새 분봉 source-check는 무인증401·잘못된 budget422·revision 충돌409이며 현재 thesis 미설정 요청도409다. 운영 selection revision0은 바꾸지 않았으므로 해당 운영 선택의 정상 데이터 응답을 확인했다고 보고하지 않는다. 그 경로의 본문·저장 읽기/변경 차단은 PC 관련 검사로 구분한다. 현재 `WAITING_MARKET`이므로 실제 장중0B 수신·거래 판단/체결 수락과 무손실은 미검증이다.

원본 DB·보존 백업/격리 복원본과 과거 초봉 자료는 보존했다. 새 코드의 초봉 생산/저장 중단·실시간0B/기존 봉 계산·분봉 연구 연결을 배포했으며, 기존 DB의 시각을 일괄 덮어쓰는 보정은 하지 않았다. 돌파·운용의 미정 조건은 채우지 않고 관측과 주문 권한을 구분한다. Windows 실행 중 앱 재시작/설치본 게시도 수행하지 않았다.

기록: `data/diagnostics/nas-cumulative-deployment-20261016/`의 `nas-v2-related.txt`, `nas-deployment-v2-result.txt`, `nas-status-after.json`, `live-after.json`, `related-verification.json`. 아래 v1 실패·v2 대기 표현은 이전 단계 기록이다. 로컬 main/HEAD=`93883dff76171398d858e9c9f5c3cfcf7fd115d2`, 확인한 원격 main=`941347e30ce9ff55603fced66e90fd75de094672`와 배포한 미커밋 snapshot을 구분한다. 이번 작업의 커밋·push·CI 실행은 없다.

## 2026-10-16 누적 NAS 배포의 health 연결 오류 보완

기존 도구의 NAS 관련97건과 관리자 갱신은 통과했다. 누적 v1 후보의 NAS 지원 관련38건도 실패/오류/skip0·정리/운영 후검사 통과 후 배포했으나, 새 0B 후보 모니터의 상태에는 없는 `universe`를 `/health`가 필수로 읽어500이 발생했다. readiness 한도에 따라 기존 `2026.10.15-integrated-market-news-v5-6fafc127b0b95b1f`로 자동 복구됐으며 journal=`rolled_back`, health/인증/저장 분봉613행·일봉2행/WebSocket ready·pong을 확인했다. v1 배포를 완료로 보지 않는다.

실제 NAS 로그와 같은 ASGI 요청에서 오류를 재현했다. health에서 선택적 universe만 조회하도록 한 줄 수정해 새 0B 상태와 기존 universe 판정을 함께 보존했다. 새 ASGI3건(네 평가 상태·기존/비활성 경로)과 기존 health/인증·0B·돌파·모의 실행 관련37건이 통과했다. 검사 초기 fixture의 임시 vault close 누락은 자격증명이 필요 없는 health fixture로 바로잡고 원래 실패 기록은 보존했다. 새로운 기준·매매 조건·내구성/권한 변경은 없다.

수정 후보는 `2026.10.16-cumulative-kst-0b-breakout-v2-e1fcf512d535cdc1`이며 NAS 관련 재검사·배포·실제 응답 검증은 아직 남는다. 기존 v1·원본 DB/격리 복원본은 보존한다. 새 제품 바이트에 대한 비활성 소스 후보 준비이며 DB 복사/복원은 하지 않는다.

앞선 NAS 선택 검사의 별도 환경 실패는 `httpx` 없는 API 검사와 공통 fixture의 `fastapi.testclient` import에서 선택적 클라이언트가 없어 테스트 로딩 전에 발생한 RuntimeError였다. 같은 조건으로 PC에서 재현했고 운영 의존성 문제로 간주하지 않는다. `httpx2` 설치·위장 import·조용한 skip은 하지 않았다. 해당 API/모의 검사는 PC 근거와 구분하고, NAS에서는 지원되는 native 경로와 실제 HTTP 요청을 검증한다. 새 health ASGI 검사는 선택적 클라이언트에 의존하지 않는다.

## 2026-10-16 누적 NAS 배포 준비와 검증 범위 정정

최신 실행 상태: 사용자가 최종 관리자 갱신 묶음을 실행했고 NAS Linux 관련96건과 helper 갱신·무암호 status 검증이 통과했다. 설치된 operator/worker/source runner 바이트도 최종 묶음과 일치한다. sudoers·DB 컨테이너·기존 revoke 백업을 보존했으며 운영 서버는 재시작하지 않았다. 아래 관리자 실행 대기 설명은 이 성공 전 기록이다.

누적 후보의1133개 소스를 관리자 전용 사본에 입고한 뒤 NAS `selected` 관련 검사8개 대상을 시작했으나 worker가 `RuntimeError`로 종료했다(job `25d7251f8f82c1ee2962a1296601138a`). PG readiness·자원/격리 확인은 통과했고 OOM 표식은 없으며 임시 컨테이너 정리는 완료됐다. 기존 worker가 시작 예외의 메시지·발생 단계를 보존하지 않아 정확한 원인은 미확정이다. 검사 통과나 배포 완료로 처리하지 않는다. 운영 health·활성 v5·진단 idle은 유지한다.

기존 worker/report에 고정 시작 단계와 알려진 오류 코드만 보존하는 진단을 추가했다. 새 판정/게이트·권한 변경은 없다. 관련 로컬81건과 NAS11파일 해시·Python/셸 구문이 통과했으며, 관리자 전용 helper를 갱신하는 기존 묶음 `nas-operator-update-1bfac9f11668181d.sh`를 준비했다. 이 묶음은 제품 소스 release를 다시 복사하지 않는다. 관리자 적용 뒤 이미 완성된 같은 후보 사본으로 검사를 재실행해야 실제 원인을 확인할 수 있다. 기본 샌드박스 검사는 오류 후 지연돼 중단·두 프로세스 종료를 확인했고 통과 근거로 쓰지 않는다. 사용자 환경81건의 전체 로그를 별도 보존한다.

초반에 만든 격리 DB 복원본과 이번 소스 사본은 서로 다른 자료다. 완료된 같은 release의 관리자 전용 소스 사본은 후속 검사·배포에서 재사용한다. 배포 전 저장 분봉613행·일봉2행 읽기/WebSocket ready·pong을 확인했으나 활성 release는 여전히10월15일 v5이며 누적 제품 배포 완료가 아니다.

사용자 정정에 따라 배포·커밋·push는 수정 코드와 연결 기능의 관련 테스트로 검증하며 전수 회귀는 명시 요청 때만 실행한다. 배포 준비 중 자동 실행한 `all-local`은 사용자 정정 후 중단했고 자식 프로세스 종료를 확인했다. 보고서는 `data/diagnostics/nas-cumulative-deployment-20261016/all-local-first`에 미완료로 보존하며 통과 근거로 쓰지 않는다. 빌드/시각 기록 변경까지 관리자 재승인으로 묶던 계약 비교와 selected 관련 검사 배제는 로컬에서 단순화했다. [도입 이력·사용처·중복·유지 경계](NAS_OPERATOR_COMMANDS.md#2026-10-16-계약-지문-조사와-코드-변경의-재승인-축소).

최종 배포 도구 관련80건과 누적 기능9모듈224건이 통과했다. 제품 검사 최초224건의 import 경로 오류6건은 기존 runner와 동일한 `src`·repo·`tests/unit` 경로로 재실행해 해결했으며 제품/테스트를 바꾸지 않았다. 별도 NAS overlay 검사는1126파일 입고 중 I/O 대기가 지속돼 취소했고 사용자 소유 sudo 전달 프로세스를 통해 정상 신호를 전달한 뒤 두 프로세스 종료를 확인했다. 검사 worker는 시작되지 않아 NAS 통과 근거가 아니다. 운영 health/활성 source·진단 idle/기존 job·배포 journal은 정상이다. 관리자 인증으로 기존 updater의 최종 Linux 관련 검사와 고정 helper/런처 갱신을 수행하는 단계가 남는다.

누적 소스 `2026.10.16-cumulative-kst-0b-breakout-v1-c531cbda0941870c`의1133파일은 비활성 NAS release에 준비했다. 최초 `253d591fa4268011` 묶음은 보존했고 이후 변경은 installer와 Linux fixture 두 파일뿐이므로 제품 소스와 관련224건 검사 근거는 같다. NAS 계약 차이는 Dockerfile/시각 기록 driver뿐이며 의존성/DDL 정의는 같다. 도구 갱신 후 이 후보의 NAS 관련 검사·실제 배포/운영 검증을 이어간다. 누적 제품 소스는 아직 활성화하지 않았고 현재 NAS는 `2026.10.15-integrated-market-news-v5-6fafc127b0b95b1f`다. 로컬 main/HEAD `93883df`에 누적 미커밋 상태이며 새 커밋·push/CI 실행은 하지 않았다. 기존 DB 자료/보존 백업·계좌 운용 상태는 변경하지 않았다.

관리자 실행 최초 묶음의 Linux 관련96건은 통과했으나 실제 교체 전 mode 제한에서 실패했다. 실제 공유 런처0777을 updater가0600/0644로 제한한 것이 원인이며 기존 mode를 그대로 보존하도록 최소 수정했다. 관련 로컬80건 재통과 및 NAS Python3.8 구문·게시 묶음/압축 파일 무결성·기존 설치 helper/runner 동일 바이트와 운영 정상 상태를 확인했다. 새 묶음은 `nas-operator-update-8cd291abe4c1b4bb.sh`이며 Linux의 실제0777 성공/실패복구 fixture를 포함한다. 기존 sudo 인증 캐시는 사용할 수 없어 `sudo -n`은 password required로 종료했으며 새 도구의 관리자 실행·NAS 적용은 대기다. 최초96건을 수정 후 새 묶음의 통과로 재사용하지 않는다.

## 2026-10-16 저장 신고가 우선 표시·갱신 전 파란 점 (로컬)

저장된 신고가를 첫 순위표에 먼저 표시하고 새 조회로 갱신한다. 갱신 전 가격 오른쪽 위 작은 파란 점은 새 결과 반영 시 사라진다. 5/20/250일·역사적 신고가와 연결된 거리·강조를 확인했고, 누락/실패 시 값은 보존한다. 저장값의 수집·기간 근거를 갱신 완료로 승격하지 않는다. 관련130건 통과·동일 소스 지문·Qt 실제 픽셀의 점 표시/제거와 NXT 유지 확인. 로컬 미커밋·미배포이며 실행 중 앱은 재시작 후 반영된다. [구현·보존·검증](workstreams/second-trade-dependencies.md#2026-10-16-저장-신고가-우선-표시와-갱신-전-파란-점).

## 2026-10-16 실시간 거래대금 표시·강도·알림 복구 (로컬)

사용자 요청에 따라10월11일 변경 직전 코드와 대조해 현재/직전1분 및 연결된 기간 거래대금·강도·기존 임계값 알림을 수신값 기준으로 복구했다. ‘부분·미확인’은 별도 툴팁으로 안내하며 값 사용을 막지 않는다. 테마 합계·체결 당시 관측 기록도 같은 기준이고 원래 품질/누락 복구 근거는 보존한다. 관련116건 통과, 동일 합성 자료의 직전 Git renderer 대조와 Qt 화면 확인을 마쳤다. 제품 변경은 MainWindow 한 파일이며 로컬 미커밋·미배포, 실행 중 앱 재시작/운영 DB 변경은 하지 않았다. [경로·보존·검증](workstreams/second-trade-dependencies.md#2026-10-16-거래대금강도알림을-11일-변경-전-동작으로-복구).

## 2026-10-16 00종합 가격 돌파 기준선 연결 (로컬)

00종합의 직전 구간 고가·현재 구간 제외·관측 가격 교차를 실제0B 관측과 기존 완료 분봉 판단 연구에 연결했다. 기존 기록/API와 Windows 기준선 표·연구 결과도 연결했다. 기준 구간 숫자 등 미정 조건을 채우지 않으며 주도주 적격·확정 진입·주문 승인과 가격 교차 사실을 구분한다. 새 프로필은 관측 전용이고 기존 명시 비교 정책과 보호 경계를 유지한다. 관련449건 후 최종 표시/미정 항목126건 통과·실행 중 소스 변경0·Qt 격리 화면 확인. 로컬 미커밋·미배포이며 all-local/CI·NAS/장중 수락은 남는다. [정의·호출 경로·한계·검증](workstreams/realtime-entry.md#2026-10-16-00종합에-명시된-가격-기준선-연결-로컬).

## 2026-10-16 테마 H2 시작가격 의미 복구 (로컬)

초봉 제거 후 H2 상승률의 기준이 첫 관측 초의 마지막 가격에서 첫 개별 체결 가격으로 바뀐 회귀를 확인했다. 같은 합성10건에서 대장 선정까지 반전되는 것을 재현하고 현재0B의 시작 가격만 원래 의미로 복구했다. 모든 체결의 고가·마지막 가격·대금·건수와 기존 정책/보호·역사 초봉/분봉 연구는 유지한다. 초봉 생산/저장을 복원하거나 새 전략 조건을 넣지 않았다. 새 결과의 명시 계산 기준을 context 내용 hash에 포함하고 Windows 대장 tooltip도 그 근거가 있을 때만 표시한다.

관련9모듈547건/468.901초와 API89건/108.161초가 각각 실패/오류/skip0·worker/자식 종료 확인으로 통과했다. 실행 전후/최종432·424파일 지문 변경0이며 같은 자료의 모든 H2 metric·대장이 역사 계산과 일치했다. 실제 Qt tooltip도 확인했다. 현재 로컬 미커밋·미배포이며 all-local/CI·장중/NAS 수락과 미정 연구 규칙/돌파 조건은 남는다. [경로·재현·보존·검증](workstreams/second-trade-dependencies.md#2026-10-16-테마-h2-상승률-시작가격-의미-복구). 아래 H2 검토 대기는 이전 기록이다.

## 2026-10-16 동적 비중 RMS 표본 의미 복구 (로컬)

초봉 제거 과정에서 RMS의 표본이 관측초별 마지막 가격에서 개별 체결 가격으로 바뀐 회귀를 실제 adapter와 보존된 역사 집계기의 같은 합성 입력으로 재현했다. 같은 마지막 가격인데 체결 반복/초 안의 가격 변화에 따라100bp가43/1,157bp가 됐다. 기존0B 행에서 관측초별 마지막 수신가격을 선택해 원래 공식으로100bp를 복구했다. 초봉 객체·집계/저장·추가 상태·무체결 초 보간은 없고 대금 합산·0B 현재가·분봉 연구/비교는 유지한다. 화면과 응답에 기준·실제 표본 수를 명시했다.

관련6모듈337건이 실패/오류/skip0·279.641초·worker/자식 종료 확인으로 통과했다. 실행 전후 소스 지문은 변경0이다. 일반 연구 규칙의 실시간 적용 방법·미정 전략 조건은 추가하지 않았다. 현재 로컬 미커밋·미배포이며 all-local/CI·실제 장중/NAS 수락은 남는다. [재현·검증·보존](workstreams/second-trade-dependencies.md#2026-10-16-동적-비중-rms의-기존-표본-의미-복구). 아래 RMS 검토 대기 표현은 이전 단계 기록이다.

337건 이후 격리 화면에서 확인한 열 제목 잘림은 RMS 열 너비 한 줄만 조정했고 UI5건을0.863초·실패/오류/skip0·전후 지문 동일로 재검증했다. 최종 화면과 acceptance 기록은 위 원장에 연결한다.

## 2026-10-16 분봉 연결 복구·0B 일괄 차단 제거 (로컬)

사용자 승인에 따라 동적 비중의 기존 분봉 모의 비교에 완료 분봉 RAM 요청을 복구하고,0B snapshot이라는 이유만으로 그 비교를 중단하던 조기 반환을 제거했다. 시장 관찰의 기존1분 후보·5분 지속도 같은 Runtime의 공유 비동기 읽기/cache/종료 경계로 복구했다. 현재가격·체결·보호는0B를 유지한다. 과거 비교의 가격·시각·상태/규모·정밀도 조건은 해당 비교에 남겼으며 일반적인 실시간 연구 규칙을 만들거나 주문 권한으로 승격하지 않았다. 화면은 봉 계산과0B의 역할 및 분봉 모의 비교/미정 적용 방법을 구분한다.

관련25모듈932건을 동일 소스로 끝까지 실행해 오류12건(두 원인)을 모았다. 공통0B 인덱스의 mappingproxy deepcopy 오류11건을 복사 경계 보완으로 고치고, 지연 체결로 실제 비중 계산이 보류됐는데 비교 필드를 기대하던 검사1건을 실제 보류·미승인 검증으로 바꿨다. 실패 검사와 관련6모듈334건은 실패/오류/skip0·308.142초·worker/자식 종료 확인으로 재통과했다. 각 실행 전후 관련 소스 지문 변경0이며 마지막334건은429파일을 대조했다. 이는 현재 all-local 전체 통과가 아니다. 한국어 글꼴을 지정한 격리 Qt 안내/결과 영역도 렌더링·확인했다. [기록·남은 범위](workstreams/second-trade-dependencies.md#2026-10-16-승인된-분봉-연결-복구와-관련-재검증).

현재 로컬 미커밋·미배포다. NAS 조회/배포·실계좌 활성·주문 송신·기존 자료 변경은 하지 않았다. 연구 규칙의 일반 실시간 적용 방법, 새로운 일봉/5분봉 연구 지원 확대, 초봉→개별체결 RMS 의미 검토는 이번 복구에서 임의 구현하지 않았다. 아래 조사-only/미구현 표현은 각 이전 단계 기록이다.

## 2026-10-16 연구 역할·봉 계산 유지 기준 명시와 전환 재검토

사용자는 연구의 목적을 일봉·5분봉·1분봉 등으로 유효한 규칙을 발견·검증하고 자동매매에 적용할 방법을 찾는 것으로 명확히 했다. 실시간 현재가·체결 변화·실행은0B이며 필요한 기존 봉 계산도 유지한다. 로컬 초봉 전환에서 완료 분봉 support 요청 제거·0B 연구 support 일괄 차단·시장 primary의0B 고정으로1분/5분 관찰이 실제 Runtime에서 빠진 것을 확인했다. 이전부터 있던 분봉종가/현재가격·시각 일치와 exact 연구 셀 조건도 일반 적용 계약과 구분할 재검토 대상이다. 계좌/Risk/주문 경계의 차단과 혼동하지 않는다. [확인 경로·영향·미확정 항목](workstreams/second-trade-dependencies.md#2026-10-16-후속-사용자-기준과-전환-재검토).

이번 요청에서는 문서만 수정하고 제품·스크립트·테스트 코드는 바꾸지 않는다. 기존 전환 회귀 기록을 현재 전환 완료의 근거로 사용하지 않으며 NAS 배포·운영 저장 중단·실제 자동매매 적용은 확인하지 않았다. 아래 build와 완료/미구현 설명은 각 이전 단계의 기록이다.

## 2026-10-16 새 돌파 자동매매·연구 캠페인·Windows 연결 (로컬)

새 Family를 기존 모의 Runner와 판단/성과 campaign·독립 final·후보 게시/forward/admission 및 화면에 연결했다. 실시간은 승인된 실제0B, 연구는 완료·보완 분봉이며 새 경로는 초봉을 읽지 않는다. 명시 실행·위험·비용 계약이 없으면 차단하며 주도주/구조 producer나 미정 조건은 만들지 않았다. 진입 context·절대 하드 경계를 실제 FILL과 연결해 복원하고 주문 claim에서도 원문·시각·수량·가격·방향을 재검증한다. [연결과 보존](workstreams/realtime-entry.md#2026-10-16-자동매매연구-캠페인windows-연결-구현).

최종 보완 인접16모듈306건과 마지막 새 돌파/서버 API160건(새 돌파71+API89)이 실패/오류/skip0·종료/정리 확인으로 통과했다. 각각 제품 코드/관련 스크립트/시험413개, API 기준 자료까지424개 파일 지문이 실행 전후 동일하다. 새 자료 인계·취소/재개·성과/final/게시·0B 위조/지연/overflow·고정 하드 보호·ACK 유실/재시작·원본 폴더 출력 차단을 검증했다. 기존 API 원본 기준은 보존하고 진단 writer 두 항목의 명시 추가 계약만 별도 fixture로 남겼다. Qt 연구/자동운용 창도 격리 렌더링했다.

그 전32모듈1,081건의 시험은 통과했으나 별도 역사 뉴스 작업3파일의 동시 변경으로 전체 저장소 지문이 달라 전수 동일 소스 통과로 인정하지 않는다. 최초585건의 준비 코드 오류2건과 후속 시험에서 수집한 fixture/경계 오류·재검사 기록은 `data/diagnostics/leader-breakout-20261016/execution-campaign/`에 보존한다. Catalog unit462/integration27 통과. 현재 소스 all-local·PG/NAS·실제 장중 수락으로 확대하지 않는다.

로컬 build는 `2026.10.16-leader-execution-campaign-v3`이며 이 작업에서 commit/push·배포·계좌 RUN/주문 송신은 하지 않았다. 앱 전체의 시장/테마·비중 초봉 제거와 초봉 저장 중단은 미완료다. 아래 미연결 설명은 이전 단계다.

## 2026-10-16 Windows Shadow 선택·후보 상태 연결 보완

Windows의 기본 선택을 새0B 정책으로 고치고 서버의 실제 저장 정책과 화면 편집을 구분했다. 기존 보유 행동 비교와 자동주문은 새 정책 지원으로 표시하지 않는다. 이미 표시한 후보의 무효화/만료도 bounded 상태 재확인으로 갱신하며 cursor·알림을 보존한다. 관련432건 통과 후 추가121건에서 fixture 오류2건을 수집·보완하고 동일121건이 재통과했다. 새 정책 자동주문·성과 캠페인 전환, NAS 배포와 운영 수락은 미완료다. [실제 연결과 보존/미연결](workstreams/realtime-entry.md#2026-10-16-windows-선택창과-연결-경로-재대조).

관찰 비활성화/기존 정책 전환 후 새0B 후보가 ACTIVE로 복귀하던 API 경계도 SUPERSEDED 투영으로 보완했다. 최종 관련4모듈210건은 실패/오류/skip0·종료/정리 확인으로 통과했으며 원본 원장 불변을 확인했다. 소스 build는 `2026.10.16-kst-leader-breakout-v2`, 기존 의존성 runtime·운영 NAS는 유지한다.

## 2026-10-16 주도주 구조 돌파 실제 구현 — 실시간0B·분봉 판단 연구

새 로직37건·서버 API89건의 최종 재검증126건이 실패/오류/skip0·종료/정리 확인으로 통과했다. 관련8모듈425건의 최초 확인과 fixture 오류/재검사는 별도 기록으로 보존한다. Catalog unit462/integration27 통과이며 전수 all-local·NAS 수락 결과는 아니다.

새 `leader_breakout/v1`을 실제 Collector0B→기존 CandidateMonitor→후보 API와 연구 실행기/화면에 연결했다. 암묵적인5봉·3%·5%·10분 기본 프로필은 새 draft로 해석하고 주도주/구조 근거·이격·확인 관측 수/유지 시간·대금·신선도·이탈/위험 기준이 미정이면 진입을 차단한다. 분봉은 최종 query 값·종가 교차를 쓰며 초봉/가짜 틱·체결/손익을 만들지 않는다. 실제 포지션·자동주문·새 정책의 성과 계약은 아직 연결하지 않았고 수량·운용값을 채우지 않았다. 기존 실험·저장 원문을 보존했다. 현재 로컬 구현이며 NAS 배포·초봉 저장 중단은 없다. [구현·검증 범위](workstreams/realtime-entry.md#2026-10-16-실제-돌파-로직-구현--0b-관측과-분봉-연구).

## 2026-10-16 입력 기준 확정·초봉 연결 조사

사용자 확정은 **실시간 자동매매 0B / 연구·백테스트 분봉**이다. 로컬 Python1,065개와 활성 NAS release의565개를 읽기 전용 스캔했고 초봉 직접 참조22개 제품 소스·4개 도구의 참조 내용이 일치했다. 현재 테마 주도력·시장 보조 관찰·동적 비중 창 계산·원본 대조 진단 등에 RAM/저장 초봉 연결이 있다. 기존 완료봉 돌파·눌림 표준 연구와 0B 최신가격/손절 전달은 초봉 DB를 읽지 않는다. DB reader 호출은 명시 원본 대조 진단1곳과 제품 호출자가 없는 adapter1곳이다. 조사 완료와 실제 전환은 별개이며 집계/저장 중단·삭제·운영 설정·배포는 하지 않았다. [전체 목록·직접/간접 호출·미연결 구분](workstreams/second-trade-dependencies.md).

## 2026-10-16 Trading 방향 정정 — 기존 실험과 사용자 구상 분리

사용자가 미완성 00 AI 인텔리전스의 주도주 돌파 구상과 기존 돌파·눌림 연구/Shadow의 방향 차이를 지적했다. 두 화면에서 기존 완료봉 Family를 비교 실험으로 표시하고 새 전략 정의·연구·Shadow 연결이 미완료임을 명시했다. 실행 알고리즘·기존 실험 ID/결과·운영 설정은 바꾸지 않았다. 초 단위 RAM 돌파 함수도 사용자 전략의 완성본으로 연결하지 않는다. 초봉은 현재 0B에서 파생 집계·저장되며 일부 Shadow와 연구 입력에 연결돼 있다. 사용자의 미사용 의향에 따른 의존성 분리·수집/저장 중단은 아직 미실행이다. [현재 방향과 소스 경로](workstreams/realtime-entry.md#2026-10-16-사용자-정정--연구와-shadow의-방향).

## 2026-10-16 Windows 전수 회귀 완료·수집 오류 재검사

같은 소스로313개 배치·5,344건을 끝까지 실행했다. 미실행·skip0, 실행 중 소스 변경 없음, worker/자식 프로세스 종료 확인이다. 실패264건·오류4건을19개 메서드로 모아 원인을 수정했고 해당19개는 모두 재통과했다. 공통 시계의 모의 전용 앱·아침 시황·coverage 연결, legacy replay 경계, 계좌 snapshot의 기존 canonical ID를 확인했다. 원본 DB의 HTTP 전후 무변경 지문과 기존 API 기준 파일을 보존하며 KST 형식·같은 순간을 함께 검증한다. 인접15개 모듈446건도 통과했으나 현재 소스의 전수 통과나 NAS 검증으로 표기하지 않는다. 운영 DB·기존 자료 변환·배포는 아직 변경하지 않았다. [오류별 근거와 범위](workstreams/time-basis-kst.md#2026-10-16-전수-회귀-완료오류-수집-후-재검사).

## 2026-10-16 전체 KST 통일 구현 진행 — 로컬 시계 주입과 저장·PC 전달

기존 키움 보정 시계는 변경하지 않고 app의 같은 aware KST provider를 DB·collector·뉴스/AI·외부시세·자격정보 처리·모의실행에 연결하는 중이다. health·수집 상태, revision 수신/분봉 operation 처리 시각, 모의 owner lease의 시계 전달을 수정했다. PC remote client는 기존 응답에서 `X-Kiwoom-Clock: 1` 요청에 대한 `X-Kiwoom-Server-Time`을 재사용하고 monotonic 경과를 더한다. PostgreSQL 연결은 timeout/read-only 옵션을 보존하며 Asia/Seoul을 요청한다. 첫 시계 주입136건과 저장/remote 관련127건이 통과했으며 확장 회귀에서 확인된 fixture/계약 차이를 처리·재검사 중이다. 이 수들은 전체 앱 통일 완료나 NAS 검증이 아니다. NAS 제품 배포·운영 DB 변경·기존 자료 변환은 아직 없다. 로컬 SQLite5개 DB의 schema만 읽어 시각 저장 열을 목록화했고 기존 TEXT/JSON·나머지 호출 경로의 전환은 남는다. [작업 경계](workstreams/time-basis-kst.md).

## 2026-10-16 원래 요청 재확인 — 앱·신규/기존 저장 시각 전체 KST 통일

표시만 변경하거나9시간 후보 조사로 축소하지 않는다. 키움 시계는 이미 KST이므로 값 변경 대상에서 제외한다. NAS 현재 배포10파일 manifest/hash와 보존17배포의 collector binding을 확인했다. 당시 collector는 키움 HTTP Date 보정시계→명시 KST→epoch, revision은 UTC 정규화와 별도 UTC 수신 시계를 사용했다. 추가 projection535,058건 실자료 진단은 중단했고 DB 연결 시험안을 원복한 뒤 실제 경로 감사부터 다시 시작했다. 이 문단은 범위 재확인 당시의 상태이며 이후 로컬 구현은 위 진행 항목을 따른다. 전체 KST 통일과 기존 자료 변환은 아직 완료되지 않았다. [확정 범위와 실제 경로](workstreams/time-basis-kst.md#2026-10-16-범위-재확인과-실제-시계-호출-경로-감사).

## 2026-10-16 완료 분봉 분석 기준 확정·시각 후보 대조 준비

사용자 기준에 따라 조회 보완분봉의 최종 OHLCV를 우선하고 해당 분 종료 이후의 역사 분석에 사용한다. 장후 수집만으로 분봉을 배제하거나 사용 시각을 장후로 미루지 않는다. 535,058건의 추가 projection 대조 준비는 위 범위 재확인에 따라 실자료 실행 전에 중단했다. [분석 기준](workstreams/time-basis-kst.md#2026-10-16-사용자-확정--완료-분봉의-분석-기준).

## 2026-10-16 대상4테이블 전수 집계·시각 후보 분류 완료

v4는 KST05:07:47~05:30:31(22분44초)에 남은22,030,953행을 읽어 census_complete로 완료했다. 완료된 revision/metadata는 기존 결과 그대로 인계했고 분봉 prefix7,005,000행·원장406건을 검증한 뒤 나머지를 이어갔다. 동일한10/15 21:56:05 KST 백업 snapshot의4테이블64,841,207행/EOF·구간 지문·정리/post fence가 모두 통과했다. 최종 전체 행/약9h 후보는 revision18,894,683/809,872, metadata16,910,571/360,427, 분봉11,698,784/174,631, 초봉17,337,169/5,541,914다. metadata/revision의 시각 분류는 minute_bar 범위이며 다른 kind는 건수만 확인했다. 이력과 projection의 후보를 합산해 고유 오류 건수로 쓰지 않는다. 초봉에는10/15의음수 gap88,551행도 별도로 남겼다. 수신 근거 없는 event 차이와 정상 늦은 query는 보정 대상으로 확정하지 않았다. 운영 통합v5·health 정상/diagnostics idle/trace OFF이며 운영 조회/보정/재시작·재덤프/새 전체 복원은 없다. 승인/보정0이다. 대상 전수 집계는 완료됐고 남은 작업은 후보의 projection/수신·계보/operation 대조와 적용 manifest·격리 보정/replay/no-op·운영 적용이다. [최종 실측과 범위](workstreams/time-basis-kst.md#전수-v4-실측--대상4테이블-집계와-시각-후보-분류-완료).

아래 v4 준비/실행 대기·v3 partial 표현은 각 실행 전후의 경과 기록이다. 현재 대상 전수 집계 상태는 위 완료 결과를 따른다.

## 2026-10-16 잔여 전수 v4 준비·무암호 NAS23건 통과

사용자의 나머지 전체 조사 지시에 따라 소규모 보정 전환은 보류했다. v4는 완료된 revision/metadata를 재조회하지 않고 기존 구간 지문으로 인계하며, 분봉 prefix7,005,000행/후보406건 검증 후 남은4,693,784행과 초봉17,337,169행을 같은 accepted snapshot에서 전수 조사한다. 로컬13건·기존 무암호 NAS23건(동일 단위13건+PostgreSQL10건)이 실패/오류/skip0·정리/fence·운영 release/컨테이너 보존으로 통과했다(job bf490a7f9e66da2a8ab55427a8f75aba). NAS Python3.8/셸 구문·해시도 통과했으며 관리자 전용 원장/PGDATA 접근 실행만 대기다. 각 SQL5초·전체3600초 제한/같은 cursor 축소·부분 보존을 유지하고 운영 조회/보정/재시작·새 전체 복원은 없다. 보정 승인0이다. [실측과 실행 범위](workstreams/time-basis-kst.md#전수-v3-실측과-잔여-분봉초봉의-전체-조사).

## 2026-10-16 v3 revision·metadata 완료·잔여 분봉/초봉 전체 조사 유지

v3는 KST03:57:41~04:54:49(3427.692초)에 작업 한도 scan_work_budget으로 partial 종료했으며 정리/post fence·원본/선행 구간 지문 확인은 통과했다. revision18,894,683행 전체(minute scope18,756,047행)에서 수신 기준 약9h 후보809,872건을 기록했다. metadata16,910,571행 전체(minute scope11,319,685행)의 event 기준 후보360,427건은 realtime305,727/query54,700건으로 독립 수신 대조가 더 필요하다. 분봉은7,005,000행/후보406건/마지막9/9 15:07 123690 KRX까지 완료했고 초봉은 미시작이다. 이들 수는 서로 다른 테이블/이력 단위여서 고유 오류 건수로 합산하지 않는다. 승인/운영 보정0·현재 통합v5 health 정상이다. 사용자의 나머지 전체 조사 지시에 따라 소규모 보정 전환은 보류하고 남은 분봉4,693,784행+초봉17,337,169행=22,030,953행을 이어 읽는 v4를 준비한다. 완료2단계는 재조회하지 않고 같은 snapshot·구간 지문으로 이어간다. [실측·남은 범위](workstreams/time-basis-kst.md#전수-v3-실측과-잔여-분봉초봉의-전체-조사).

## 2026-10-16 부분 원장 검증 후 재개 v3 준비·무암호 NAS26건 통과

v3는 기존 v1b 원장을 gzip EOF/CRC·전체 stream hash/바이트·203,035건/분류별 건수·중복 키/상한·preimage hash·미승인 상태로 확인한 뒤 동일 accepted 복원본의 cursor4600063 다음부터 읽는다. 이전 파일은 그대로 보존하고 새 후보는 별도 gzip 구간에 기록한다. revision은1천행 단위로 시작하며 정확한 SQL timeout 때만 같은 cursor에서 단위를 줄인다. SQL5초·전체3600초 제한, EOF/4테이블64,841,207행/구간 지문/정리·후 fence를 모두 통과해야 대상 전수 완료다. 로컬17건, 기존 무암호 kiwoom-nas test로 동일 실행기의17건+PostgreSQL9건=26건이 실패/오류/skip0·정리/fence·운영 release/컨테이너 보존으로 통과했다(job b1e86713cfb277eb71e03eb42d8725ba). NAS Python3.8/셸 구문/hash도 통과했으며 관리자 전용 PGDATA 접근 실행만 대기다. 실자료 재개/전수 완료·시각 보정은 아직 없고 승인0이다. [검증 범위](workstreams/time-basis-kst.md#전수-v2-실측과-동일-snapshot의-검증된-구간-재개).

## 2026-10-16 실패 페이지의 데이터 읽기 대기 재현·분할 결과 일치

v2는 KST03:44:07~03:44:53(46.247초)에 진단·정리/post fence를 완료했다. 동일 cursor 다음5천행의 원 조회는5초 취소를 재현했고 관찰20건 모두DataFileRead·blocking PID 없음이었다. 1천행씩5개는0.228~2.801초에 완료했고 이후 원 조회0.255초와5천행/scope3,716행·그룹이 일치했다. 구간 후보는0건이다. 후속 캐시가 채워진 실행계획은PK Index Scan·3373hit/0read·67.275ms였으며 이를 cold 성능이나 디스크 하드웨어 원인으로 확대하지 않는다. 재현된 읽기 대기를5초 SQL 제한과 양립하게 나누는 재개 실행기를 준비 중이며 기존 실패 원장203,035건의 EOF/지문/건수·중복/원본 값을 검증하기 전에는 이어가지 않는다. 운영 상태 정상·보정0·전수 집계는 미완료다. [실측과 재개 경계](workstreams/time-basis-kst.md#전수-v2-실측과-동일-snapshot의-검증된-구간-재개).

## 2026-10-16 전수 revision 조회의 5초 제한 종료·실패 페이지 진단 준비

전수 v1b는 KST03:31:29~03:35:49에 revision 4,600,000행/920페이지를 읽고 다음 조회의 statement_timeout으로 실패했다. 실제 stderr의53바이트/SHA256이 PostgreSQL 취소 메시지와 정확히 일치한다. minute_bar 범위4,558,888행·미승인 후보203,035건은 부분 결과이며 metadata/분봉/초봉 단계는 시작하지 않았다. 임시 컨테이너 정리 오류0, 실패 실행의 post fence는 미완료다. 별도 현재 status는 정확한 통합v5·health ok·diagnostics idle·trace OFF다. 5초를 넘긴 내부 대기 원인은 아직 미확정이다. 실패 cursor4600063 다음5천행만 기존 조회→1천행씩5개→기존 조회와 EXPLAIN ANALYZE로 대조하는 v2를 준비했다. SQL5초·전체420초 제한, wait/blocking PID/버퍼 계측·같은 키 범위와 후보 내용 대조를 수행하며 전수 재시작/기존 원장 이어쓰기/운영 보정은 하지 않는다. 로컬12건·NAS Python3.8/셸 구문/hash 통과, 관리자 전용 복원본 접근 실행 대기다. 보정 승인0이며 기존 백업 복원 수락은 유지한다. [실패 근거·진단 범위](workstreams/time-basis-kst.md#전수-v1b의-개별-sql-시간-초과와-실패-페이지-진단).

## 2026-10-16 전수 분류·후보 원장 준비와 무암호 NAS 검사 완료

기존 `kiwoom-nas test`로 새 분류 SQL의 합성 PostgreSQL 검사8건을 직접 실행해 실패/오류/skip0·정리/post fence·운영 release/컨테이너 보존을 확인했다(job `06f807b37832669695304e6c0e53b175`). 제품 source hash가 같은 별도 후보의 테스트2파일만 추가했으며 활성 source를 바꾸지 않았다. 전수 실행기는 로컬19건(기존 분류12건 포함), NAS Python3.8 import/파서·셸·hash 검증을 통과했다. `clock-census-20261016-v1b-candidates`는 accepted v8 복원본의4테이블64,841,207행을5천행씩 읽어 분류하고 후보의 PK/이전 값/row hash·제안 시각을 관리자 전용 압축 원장에 남긴다. SQL5초·전체3600초 제한이며 미완료/오류를 성공으로 판정하지 않는다. 기존 test worker는 이 보존 PGDATA를 mount하지 않으므로 실제 전수 읽기만 별도 관리자 인증 대기다. 운영 조회/보정/재시작·재덤프/전체 복원은 없고 보정 승인0이다. [검증·분류 범위](workstreams/time-basis-kst.md#전수-시각-분류와-후보-원장-준비).

## 2026-10-16 표본 계보 대조 v2 완료·무암호 테스트 경로 명시

v2는 KST03:07:07~03:08:04(57.524초)에61개 제한 조회·정리/post fence를 통과했다. v1 실패는 소수 초5자리 시각의 Python3.8 파싱 오류로 실제 traceback/당시 metadata/구형 파서 재현까지 일치했다. 분봉10키 중9키의 봉/metadata와revision15건을 확인했고 hash/effective·payload 키15/15 일치, 표본 parent·sequence 오류/한도 도달0이다. 현재 query 보완본9건은 숫자가 현재 봉과 일치하고 가용시각이 실제 수신보다0.255~4.037초 이르다. 과거 realtime revision6건은 실제 수신 대비+32,395.875~32,399.289초 차이이며 현재 metadata source는 아니다. 초봉9키는 정상 근접6개/+9h후보3개이며 별도 수신 근거가 없어 보정 승인으로 올리지 않는다. 운영 상태 정상·보정0이며 전 기간 분류/manifest·격리 보정/replay/no-op·운영 적용은 남는다. 사용자가 설치한 `kiwoom-nas test` 무암호 경로를 우선 직접 사용하는 규칙을 AGENTS/불변 규칙/NAS 명령 문서에 명시했다. [완료 근거](workstreams/time-basis-kst.md#표본-v2-실측--정상-query와-과거-realtime-이력의-분리).

## 2026-10-16 표본 v1 Python 오류·원문 대조 조건의 v2 준비

lineage v1은 KST03:01:06~03:01:28에5개 조회 후 Python ValueError로 실패했다. read-only/자원 확인과 소유 컨테이너 정리는 통과했지만 post_fence_verified=false로 후검사는 미완료다. 별도 현재 status는 정확한 통합v5·health ok·diagnostics idle·trace OFF다. NAS Python3.8.15가 ISO 소수 초1/2/4/5자리를 거절하는 호환 결함을 가짜 입력으로 재현했다. 실제 실패 원문은 관리자 전용이므로 이 결함과 동일한 원인인지 아직 확정하지 않는다. v2는 보존 traceback·실패 metadata 표본·기존 파서 재현/정확 microsecond 대조가 모두 맞을 때만 읽기 전용 표본을 다시 실행한다. 로컬31건/NAS 파서15건·구문/hash 통과, 관리자 인증 실행 대기다. 원본 archive 복원 수락은 유지하며 운영 DB 변경·시각 보정은 없다. [실패 근거와 실행 조건](workstreams/time-basis-kst.md#표본-v1-실패와-시각-파서-원문-대조-v2).

## 2026-10-16 검증 복원본의 시각·revision 표본 대조 준비

원본 백업 복원 수락은 완료 상태를 유지한다. 다음 단계 `clock-lineage-20261016-v1-samples`는 검증된 v8 격리 PGDATA만 재기동해 분봉 최대10키·초봉 최대9키를 읽고 metadata/revision/수신 시각·payload hash·operation 표식을 대조한다. 실제 배포본7개 소스와 v8 수락 결과/원본 archive hash에 고정하며 읽기 전용 세션·3초 SQL·전체360초 제한·정리/fence를 요구한다. 로컬22건과 NAS Python/셸 구문·SHA256 검증을 통과했다. 현재 운영 통합v5·health ok·diagnostics idle·trace OFF다. Docker/관리자 전용 복원본 접근 인증 실행은 대기 중이고 실제 표본 결과·전 기간 오류 분류·보정 manifest·격리 보정/운영 적용은 미완료다. 운영 DB 조회/수정/재시작이나 전체 복원 재시도는 없다. [실행 범위](workstreams/time-basis-kst.md#검증-복원본의-시각revision-표본-대조-v1-준비).

## 2026-10-16 원본 DB 백업의 전체 복원 검증 완료

recovery v8은 KST01:07:46~02:42:34에 전체 복원·COMMIT·정상 종료/재기동·카탈로그·주요5테이블 내용 지문·원본 archive hash·정리·운영 fence를 모두 통과했다. `valid_restored_backup=true`다. 복원54분14초, 전체 검증94분47초였고45개 테이블/103개 valid·ready 인덱스/73개 필수 제약을 확인했다. 원본5,648,562,856바이트 archive의 snapshot은 **10/15 21:56:05 KST**이며 분봉11,698,784행·초봉17,337,169행이 복원본과 일치했다. 이는 백업 snapshot의 실제 행 수이며 현재 운영 DB 건수가 아니다. 독립 live source 지문은 미완료로 남고 전역 role/외부 비밀 파일까지 포함한 호스트 백업을 뜻하지 않는다. 현재 운영 통합v5·health ok·diagnostics idle·trace off를 재확인했다. 운영 조회/재덤프/보정/재시작은 없으며 역사 시각 보정 대상 manifest·격리 보정 검증·운영 적용은 남는다. [수락 범위와 근거](workstreams/time-basis-kst.md#v8-실측--전체-복원과-내용-대조-완료).

아래 준비·실패·대기 표현은 각 실행 전후의 경과 기록이며 현재 백업 검증 상태는 위 결과를 따른다.

## 2026-10-16 v7 비교 통과·전체 복원 검증 v8 준비

v7(KST00:50:13~00:56:50)은 동일 archive 첫30만 행의 COPY 시간52.806~53.871초/9.432~10.516초/50.092~51.160초를 확인했다. 보수적 속도 개선4.763배·데이터 바이트당 WAL 비율0.003145(약99.69% 감소), 대조 drift1.075로 사전 gate를 통과했다. fsync/full_page_writes/synchronous_commit ON·CPU0/768MiB·정리/fence 정상이다. 이는 제한된 COPY 비교이며 당시 v4 지연 전체 원인·전체 복원 수락은 아니다. v8은 이 프로필의 새 격리 PG에 필터 없는 전체 archive를 단일 transaction으로 복원하고 정상 종료/재기동·45테이블/명시32인덱스/73제약 존재·전체 인덱스 valid/ready·5테이블 행 수/COPY 지문·원본 hash·정리/fence를 확인한다. 로컬23건/NAS 구문·hash 통과, 관리자 인증 실행 대기다. 실제 백업 검증·운영 시각 보정은 아직 미완료다. [실측·완료 조건](workstreams/time-basis-kst.md#v7-비교-통과와-전체-격리-복원-v8).

## 2026-10-16 v6 COPY 진행·WAL 대기 관찰과 v7 비교 준비

v6는 diagnostic_complete·cleanup/fence 정상이다. 마지막 유효 표본101.301초에 COPY503,959,345바이트/509,697행으로 약4.975MB/s·5,032행/s였으며 WalSync3회/WALWrite1회가 관찰됐다. blocking PID는 없었지만 표본 비율을 시간 비율로 해석하지 않는다. Docker BlockIO/PIDs의0 값은 유효 계측으로 확인되지 않아 사용하지 않는다. COPY는 진행 중120초 관찰 한도로 종료했고 전체 복원/유효 백업 수락은 미완료다. v7은 새 격리 PG3개를 순차 사용해 schema 선행 commit(A)→생성/COPY 단일 transaction(B)→A를90초씩 비교한다. 모든 군에 minimal WAL·fsync/full_page_writes/synchronous_commit ON·CPU0/768MiB를 동일 적용하며 첫30만 COPY행 시간의 보수적 경계와 WAL 증가량을 비교한다. 로컬22건/NAS 구문·hash 통과 후 관리자 인증 실행 대기다. [실측·판정 조건](workstreams/time-basis-kst.md#v6-실측과-durable-copy-비교-v7).

## 2026-10-16 v5 진단 완료·짧은 COPY 계측 준비

v5(KST00:27:49~00:28:20)는 diagnostic_complete·정리 오류 없음·post fence 정상으로 끝났다. archive SHA256 동일, public table45개/index0개이며 metadata/operations/minute heap은 각각 약2.76/1.03/1.37GB, revisions/second heap은0이다. 이는 데이터 구간이 부분 복원된 상태와 일치하지만 특정 당시 테이블·wait 원인은 확정하지 못한다. 추정 row 수는 실제 건수가 아니다. v4 restore stdout/stderr는0바이트이고, v5 PG 로그의 FATAL 표식1개는 원문 사유 미분류로 별도 확인한다. v6는 새 격리 PG에 pre-data schema를 만든 뒤 revisions COPY만 최대120초 관찰해 실제 처리량/wait/blocking/container 자원을 수집한다. 운영 DB/기존 부분 클러스터는 사용하지 않고 전체 복원/유효 백업 수락을 하지 않는다. 로컬14건/NAS 구문·hash 통과, 관리자 인증 실행 대기다. [실측·계측 범위](workstreams/time-basis-kst.md#v5-완료와-짧은-copy-진행-계측-v6).

## 2026-10-16 v4 격리 복원 시간 한도 종료·부분 복원 진단 준비

v4는 KST10/15 23:27:36~10/16 00:13:27에 실행됐다. 임시 PG 기동과 CPU0/메모리805,306,368바이트 실제 제한은 통과했고 PID 제한은 NAS 미지원으로 확인됐다. 실제 pg_restore의 개별45분 한도에서 process_timeout으로 종료했으며 어느 COPY/인덱스 구간이 느렸는지는 미계측이다. 마지막 Docker action=info는 자원 확인 기록이므로 복원 오류 원인으로 해석하지 않는다. 원본 archive SHA256은 동일하고 임시 컨테이너 정리 오류 없음·부분 PGDATA 보존·운영 재덤프/보정/재시작 없음이다. 전체 복원/5테이블 대조/유효 백업 수락은 미완료다. v5는 실패 로그와 archive TOC 및 부분 복원본의 카탈로그만 확인한다. 기존 격리 PGDATA 재기동의 복구/체크포인트 쓰기는 가능하며 복원 SQL·테이블 본문 전수 조회는 실행하지 않는다. 로컬12건/NAS 구문·hash 통과, 관리자 인증 실행 대기다. [실측·진단 범위](workstreams/time-basis-kst.md#v4-실측과-부분-복원-카탈로그-진단-v5).

## 2026-10-15 NAS CPU quota 미지원 확정·cpuset 복원 v4 준비

v3(KST23:21)는 원래 quota+cpuset의 생성이 cpu_quota_unavailable로 거절되고 기존 operator의 cpuset0 단독 생성이805,306,368바이트 메모리/network none으로 성공함을 확인했다. 두 시험 컨테이너는 기동 없이 정리됐고 fence 정상·원본 변경 없음이다. PidsLimit은null로 실제 지원/강제 여부는 별도 확인한다. v4는 --cpus1만 제거하고 기존 단일 CPU cpuset을 유지하며, 격리 PG 기동 후 실제 메모리 cgroup/CPU affinity를 확인한 뒤 복원한다. 기존 operator의 디스크 PGDATA 부모0711 조건과 거절 분류의 cleanup 전 보존도 적용했다. 로컬27건/NAS 구문·hash 통과 후 관리자 인증 실행을 요청했다. 원본 archive는 재덤프하지 않으며 실제 복원/내용 대조/유효 백업 수락은 아직 미확인이다. [확정 원인·후속](workstreams/time-basis-kst.md#v3-실측과-cpuset-복원-v4-준비).

## 2026-10-15 v1 timeout 확정·v2 복원 전 Docker 거절

v2는 KST23:14:30~55에 기존5.65GB archive의 크기/SHA256 동일성을 재확인한 뒤 Rejected로 종료했다. restore_running 전 컨테이너 준비 구간에서 거절됐고 복원 미실행·운영 재덤프/전수 읽기/보정/재시작 없음이다. v1 backend6587의 DB 로그는22:39:11.330의 statement_timeout과 revision COPY를 확인해 원인이 확정됐다. v2의 Docker 세부 거절 분류는 cleanup 전 저장되지 않아 정확한 사유는 미확정이다. 시험 컨테이너를 생성/inspect/제거만 하고 시작하지 않는 v3 사전 점검을 준비했다(실제 v2 생성 인수 동등 비교 포함 로컬7건/NAS Python·셸·hash 통과). 관리자 인증 실행을 요청했으며 복원은 재실행하지 않았다. 기존 백업·실패 기록·정상 운영 source/health는 보존했다. [실측·사전 점검](workstreams/time-basis-kst.md#v2-실측--원본-timeout-확정과-복원-전-docker-거절).

## 2026-10-15 백업 생성 후 원본 지문 실패·기존 archive 복원 후속

v1은 KST21:56에 시작해 단일 DB archive5,648,562,856바이트/SHA256 `2b3f2502178953e6517c4b7a5835d16dc332e2b623c45ac893799b3d395e9883`를 생성했다. 뒤이은 원본 central_observation_revisions 지문 단계는22:39에 bridge_failed로 종료했고 복원 미실행/valid_restored_backup=false다. bridge의 세부 예외를 호스트가 보존하지 않아 취소/메모리/연결 등 원인은 미확정이다. cleanup_errors 없음·운영 보정/재시작 없음·후속 health 정상/정확한 통합 v5·diagnostics idle·trace OFF를 확인했다. 실패 backend 로그 사유와 보존 archive의 동일성을 확인한 뒤 별도 PG 전체 복원 및 archive/복원본 COPY 데이터5테이블을 대조하는 v2를 준비했다. 로컬15건/NAS Python·셸·hash 통과, 관리자 인증 실행을 요청했다. 운영 재덤프/전수 읽기는 반복하지 않으며 실제 복원 수락은 아직 미확인이다. [실패 증거와 후속](workstreams/time-basis-kst.md#백업-v1-실패-실측과-기존-archive-복원-v2).

## 2026-10-15 원본 백업·격리 복원 검증 준비

`clock-recovery-20261015-v1`을 준비해 로컬16건 실패/오류/skip0·NAS Python3.8.15의 호스트/bridge 구문·셸·SHA256을 검증했다. 서버 DSN의 read-only REPEATABLE READ snapshot을 pg_dump와 공유해 단일 운영 DB 전체 논리 백업을 관리자 전용 경로에 보존하고, 네트워크/포트 없는 별도 PG에 전체 복원한 뒤 시각 관련5테이블의 동일 snapshot 원본/복원본 행 수·내용 지문을 대조한다. 최대2시간·저자원 단일 작업이며 중단/실패 원본·부분 파일과 복원 자료를 보존한다. 운영 보정·서버 재시작·제품 코드 변경은 없다. 관리자 인증 실행을 요청했으며 실제 백업/복원 수락은 아직 미확인이다. [범위·완료 조건](workstreams/time-basis-kst.md#원본-백업과-격리-복원-검증-준비).

## 2026-10-15 장 종료 후 전체 저장 범위 확인 완료

v10의 KST20:32:23 snapshot에서15조회가0.791초에 모두 완료됐다. 분봉 PK 범위는2025-09-01 08:00~2026-10-15 19:59, 초봉은2026-09-14 08:00:01~2026-10-15 19:59:59다. 카탈로그 추정은 분봉10,809,818행·초봉17,330,238행이며 정확한 전체 행 수/오류 수가 아니다. DB 크기51,959,101,107바이트(48.391GiB), 관측 revision 테이블은 인덱스 포함26,810,105,856바이트다. 고정4키 중3키의6개 revision은 available_at이 received_at보다32,395.875~32,399.289초 앞섰다. 나머지1키의 해당 source 표본은0개이며 전 기간 lineage 누락으로 확대하지 않는다. 당일 재계산 snapshot은 없고 장후 보완 완료는 미확정이다. 후속20:34 health 정상·실시간 저장 대기0·정확한 통합 v5·diagnostics idle/trace OFF를 확인했다. 백업·DB 보정·재시작은 없고, 다음은 원본 백업/복원 검증과 실제 저장 기간의 선별 분류다. [실측과 한계](workstreams/time-basis-kst.md#v10-실측--전-기간-저장-범위와-revision-표본).

## 2026-10-15 장 종료 후 전체 저장 범위 확인 준비

KST20:27 인증 health는 WAITING_MARKET/observation_expected=false, 마지막 체결 수신20:00:00.020580·초봉 저장20:00:45.904648, 실시간 pending/inflight/waiting_flushes=0·미해결 저장 실패 없음이다. 통합 v5·health 정상·diagnostics idle·trace OFF를 다시 확인했다. 장후 보완 configured/effective=true는 완료 증거가 아니며20시대 로그의 관련 성공/실패 표식도 없어 완료 여부는 미확정이다. 전 기간 오류 수를 추정하지 않고 분봉/초봉 최초·최종 PK·통계상 행 수/용량·고정4키 revision을 읽는 v10을 준비했다. 로컬9건 및 NAS 셸/hash 확인 완료, 관리자 인증 실행 대기이며 백업·보정·재시작은 하지 않았다. [장 종료 후 범위 확인](workstreams/time-basis-kst.md#v10-장-종료-후-전체-저장-범위-확인-준비).

## 2026-10-15 지정 기간 시각 후보 집계 완료

초봉 전용 v9은 KST16:54:45.767308 snapshot에서10/13~15의2,270,843행/228페이지를44.716초에 모두 집계했다. 가용시각 약9시간 후보2,129,063행·0~60초108,507행·그 외33,273행이며 v8 부분 건수는 합산하지 않았다. 분봉10/1~15의1,678,587행 집계는 앞선 v8 snapshot에서 완료했고 완료 realtime/actual의 가용시각 후보는48,176행이다. 두 집계는 서로 다른 snapshot/자료 단위이며 DB 전 기간·원본 revision 전수 검증이나 승인된 복구 수를 뜻하지 않는다. read-only/보정 없음/fence와 후속 health 정상·정확한 통합 v5/source 유지·diagnostics idle·trace OFF를 확인했다. [최종 범위·복구 잔여](workstreams/time-basis-kst.md#v9-실측--초봉-기간-집계-완료).

## 2026-10-15 분봉 기간 집계 완료·초봉 전용 후속

v8(KST16:42:40 snapshot)은 동일1,500키/행 수의 버퍼 접근량을95,866→1,475로 줄여 조건을 통과했고 분봉10/1~15의1,678,587행/1,120페이지를 끝까지 집계했다. 완료된 realtime/actual 분봉52,393행 중 약9시간 가용시각 후보48,176행, 정상0~60초4,173행이며 query/estimated·partial/in_progress는 별도 분류다. 초봉10/13~15는1,790,000행까지 읽은 뒤 전체180.202초 예산으로 부분 종료했고 statement timeout/실패 단계는 없다. 전체 두 범위 complete=false와 보정 없음은 유지한다. 초봉만 새 snapshot에서 처음부터 읽는 v9은 로컬10건/NAS 셸·hash 검증 후 관리자 실행 대기다. [실측·정확한 분류](workstreams/time-basis-kst.md#v8-실측--분봉-기간-완료와-초봉-부분-집계).

## 2026-10-15 분리 계측 결과와 시작 조건 비교 진단

v6(KST16:02)·v7(16:04)은 동일1,500키의 세 읽기 단계를 모두 마쳤다. v6의 큰 문자열 Sort는 실제 계획에서 확인됐고 v7에는 없다. 분봉 값은 두 실행 모두 Shared Read94,533/Hit1,333블록, metadata는 Read406/Hit7,094였다. 실행 순서·캐시·동시 부하가 달라 v5 시간 초과의 근본 해결로 처리하지 않는다. 전체 census/보정은 아직 하지 않았다. 중복된 시작 조건(날짜 하한과1900년 tuple cursor)이 읽기를 늘리는지 비교하는 v8은 동일 키·행 수와 버퍼 접근량 1/10 미만을 모두 요구하며, 통과할 때만 각15초/전체 약180초의 읽기 전용 census를 이어간다. 로컬22건/NAS 셸·hash 통과, 관리자 실행 대기다. 운영 src/DB/설정/서버 수명은 변경하지 않았다. [실측과 다음 범위](workstreams/time-basis-kst.md#v6v7-실측과-v8-조건부-집계-준비).

## 2026-10-15 DB 감사 실제 결과와 계측 전환

후속 v5 실측은 EXPLAIN7ms 완료 뒤 첫1,500행 실행15.002초에서 statement_timeout을 기록했다. 실행 표본577개 중544개는IO/DataFileRead, blocking PID 관측은0개이며 계획은 두 테이블의 PK Index Scan이다. 이번 조회의 파일 읽기 대기는 확인됐으나 관계별 읽기·캐시/스토리지/동시 부하의 근본 원인은 미확정이다. 전체 집계/보정은 하지 않았고 후속 health 정상·diagnostics idle·trace OFF·활성 통합 v5 유지를 확인했다. [단계·대기 실측과 다음 증거](workstreams/time-basis-kst.md#v5-실측--첫-페이지의-db-파일-읽기-대기).

v3 감사6조회는 모두 성공했고 제품 조회와 API를 대조해 감사 SQL의 HH:MM:SS/HH:MM 연결 오류를 확인했다. 제품 조회는 이미 올바른 분 키를 사용한다. 기존 인증 GET6회·2종목/3일2,635행을 대조한 완료 실시간 분봉의 약9시간 가용시각 후보는381행이며 전체 건수/보정 승인 수가 아니다. 같은 PK 표본10행은 DB/API updated_at 일치·메타데이터10/10 존재다. [계획·표본·해석](workstreams/time-basis-kst.md#v3-실제-계획api-표본-대조와-v4-취소).

작은 묶음 v4도15.05초 statement_timeout(SQLSTATE57014)으로 중단돼 전체 건수는 미확정이다. read-only/fence/보정 없음은 확인됐다. 실패 전 단계·계획을 결과에 보존하지 못했으므로 특정 병목을 단정하지 않는다. 같은 첫 페이지 한 번의 단계/실행 계획/wait_event/blocking PID만 기록하는 v5 진단을 로컬5건 검증 후 준비했다. 기존 관측 함수의 실패 callback까지 DB 연결 없이 확인했고 NAS 활성 코드 hash도 동일하다. 운영 앱/DB 수정·배포·시간 제한 상향은 하지 않았다. 활성 source는 앞서 적용한 통합 v5이며 뉴스 실제 수락 잔여는 아래 원장대로 유지한다.

## 2026-10-15 시황 근거 생성 계약 보완·DB 감사 후속

실제 실패 응답의 게시시각/정밀도 모순을 재현했고 기존 엄격 조건을 생성 schema/고정 프롬프트에 맞췄다. 출처/게시시각 거절에 안전한 원인 코드와 한국어 설명을 추가했으며 원본 응답 수정·검증 완화·자동 재시도는 없다. 관련 Windows89건/5worker가 실패·오류·skip0과 동일 지문으로 통과했다. KST 표시 후속을 포함한 고정 v5 후보를 NAS 비활성 경로에 게시했으며 검사·운영 반영은 해당 실행 결과로 별도 기록한다([근거 계약](workstreams/morning-market-brief-alignment.md#실제-실패의-재현과-생성-계약-정렬)).

후속 v5 NAS selected175/storage114가 실패·오류·skip0·cleanup/fence/운영 불변으로 통과했고, `2026.10.15-integrated-market-news-v5-6fafc127b0b95b1f`를 활성화했다. 새 실행 경로·health 정상·운영 PG 보존을 확인했으며 runtime은 유지했다. KST 표시 후속도 이제 그 고정 소스에 포함된다. 새 실제 검색1회는 요청 후 결과 검증 중이다. [정확한 소스·job·배포 근거](workstreams/nas-integrated-validation-20261015.md#최신-후속--시황-생성-근거와-kst-표시-v5-후보).

새 실제 검색은14회·367.544초 뒤 출처 불일치로 실패했고 오프라인 검증도 같다. 출처239/evidence17 중 접근 실패 환율 URL1개가 불일치하며, 그 항목만 제거한 진단 복사본도 확정 사실의 원문 검증 조건에 거절됐다. 원본/이전 실패 run·영속 설정은 보존했다. 실제 시황 수락은 남고 추가 추론/조건 완화는 하지 않았다. 후속은 검색·열람 action 관측과 사실별 검증 근거 참조 확인이다.

사용자가 실행한 DB 감사 v2는 분봉/초봉 QueryCanceled·컬럼 ProgrammingError로 영향 건수 미확정이다. 읽기 전용/보정 없음/fence는 확인됐다. 빈 매개변수 오류를 고친 v3는 집계 재실행 대신 EXPLAIN(ANALYZE 없음)·통계/인덱스·최대12개 PK 표본만 읽도록 준비·검증했다. 실제 실행과 전체 영향 범위는 남고 DB 재작성은 하지 않았다([감사 범위](workstreams/time-basis-kst.md#운영-db-읽기-감사의-실패와-후속-범위)).

## 2026-10-15 통합 NAS 배포·구독 연결과 한국시간 표시

관리자 v4 runtime 교체와 통합 source `2026.10.15-integrated-market-news-v3-543cbeddae623b53` 활성화를 완료했다. 실제 health/build/source path·운영 PG 보존·trace OFF·diagnostics idle을 확인했고 새 runtime의 storage114건은 통과했다. 구독 OAuth는 NAS로 이전하고 PC 세션을 사용 중지했으며 실제 모델 목록 조회가 성공했다. Gemini 설정은 사용자 앱에서 선택하도록 유지했다. 저장 과거 기사1건의 실제 Gemini 결과·본문 revision·input hash는 일치하지만 클라이언트의 non-JSON 오류 응답 원인은 미확정이다. ChatGPT 직접 검색1회는 검색13회 뒤 게시 시각 없는 근거로 엄격 검증에서 실패했다. 07:55 실제 예약·이 두 응답 경로의 전체 수락은 남는다. [실제 배포·검증 근거](workstreams/nas-integrated-validation-20261015.md#v4-실제-설치와-통합-소스-활성화).

배포 후 실제 TOP20의 한 cut에서20/20 완전한1분 비교와 NO_CANDIDATE를 확인했다. 후속 cut의 새 코호트402340 부분 분봉은19/20·UNKNOWN/5분 UNAVAILABLE로 거절했다. 최종 KST14:44:15에는20/20·1분 REPLACED, 최근5분 지속은NOT_CONFIRMED였다. 그 앞5분과의10분 비교는176750의 이전 이력 부족으로19/20·PARTIAL/UNKNOWN이며 지속 평가와 구분한다. 최근 실제 봉 가용시각은 분 종료2.195~3.405초/미래행0이다. 과거9시간 오기록을 덮어쓰거나 주도주 정확도/OOS 수락으로 확대하지 않았다.

사용자 요청에 따라 PC 로그와 뉴스·시황·후보·과거 기사·일지·모의/TOP20의 사람용 시각을 명시적 KST로 통일하는 로컬 후속을 반영했다(115건/12worker 통과). 서버 로그는 기존부터 KST다. 이 표시 변경은 앞 NAS 고정 후보보다 나중 작업이며 DB 원본/기계 API/epoch/hash는 바꾸지 않았다. 정상 UTC의 표시 변환과 잘못 저장된 과거 값의 선별 보정은 별개이고, DB 재작성은 수행하지 않았다. [시각 기준·저장 형식·보정 범위](workstreams/time-basis-kst.md). 아래 설치 대기/기존10/14 release 설명은 각 이전 단계 기록이다.

## 2026-10-15 관리자 설치의 네트워크 수명 검사 수정

통합 runtime-v3 설치의 `replacement_network_changed`는 Docker24의 생성 상태 컨테이너에서 아직 비어 있는 NetworkID를 실행 중 서버의 ID와 비교해 발생했다. 사용자 실행 진단은 이름/모드/별칭 유지, 이름으로 조회한 실제 네트워크 ID 일치, probe 미시작·삭제·post-fence를 확인했다. 생성 단계는 원래 네트워크의 별도 ID 조회와 설정 보존을 요구하고, 시작 뒤는 실제 연결 ID를 엄격히 재검사하도록 설치 도구만 수정했다. 관련 Windows81건과 그중 독립 네트워크 NAS6건이 모두 통과했다. v4 관리자 묶음은 같은 통합 v3 앱 후보에 고정하며 과거 묶음은 보존했다. 기존10/14 운영 서버는 정상이며 v4 설치·실제 시작 후 연결 확인·최종 source 선택·구독/뉴스/시장 수락은 남는다. [실패·재현·새 설치 명령](workstreams/nas-integrated-validation-20261015.md#네트워크-수명-원인-확정과-v4-설치-도구).

## 2026-10-15 통합본 NAS 격리 검증과 관리자 설치 대기

통합 v3 `2026.10.15-integrated-market-news-v3-543cbeddae623b53`의 NAS 시장·뉴스·인증·시계 selected **198건**, 격리 PostgreSQL storage **114건**이 실패·오류·skip0으로 통과했다. 두 작업의 정리·post-job fence·운영 릴리즈/컨테이너 불변을 확인했다. Windows 전수 **5,321건/313 worker**는 등록 수 기대값·새 DB 호출 감사 원장 **2건 실패**, 오류·skip·미실행0으로 종료했다. 보완 후 관련 **145건**과 정식 뉴스 API **106건**은 모두 통과했다. 원래 전수 실패는 보존하고 보완 후 전수를 재실행했다고 표시하지 않는다.

추가 제품 src 수정 없이 테스트 경로·fixture·시계 검사 모듈·감사 원장을 보완했다. 최종 후보와 작업본의 제품 의미 차이는 빌드명이고 테스트 말미 공백은 AST 동일성이 별도 확인됐다. 실제 NAS는 여전히 10/14 recorder 릴리즈/기존 runtime·health 정상·trace OFF다. google-auth 추가 runtime 관리자 인증·설치와 최종 source 선택·실제 구독/뉴스 readback·정상 시각 시장 관찰·07:55 예약 수락은 남는다. 관리자 설치 묶음은 최종 후보에 고정했으며 암호 입력이 필요하다. 로컬 main `93883df…`/원격 main `941347e…`/비활성 후보/운영 NAS를 구분하며 commit·push·새 외부 CI는 수행하지 않았다. [검증·실패 이력·설치 명령](workstreams/nas-integrated-validation-20261015.md).

## 2026-10-15 뉴스·장전 시황과 시장 관찰 통합

현재 main HEAD `93883dff76171398d858e9c9f5c3cfcf7fd115d2`의 기존 작업을 보존하고 ed9d/a5af의 출발점 대비 변경을 통합했다. main 작업폴더의 미커밋 통합이며 commit/push/NAS 배포와 구분한다. 완료 분봉(A03)과 TOP20 관찰 범위, aware KST 기록시각, 뉴스 구독/장전 v4를 함께 유지한다. [통합 범위·보존·검증](workstreams/main-integration-20261015.md). 아래 워크트리별 HEAD/검사/미배포 설명은 개별 작업 당시 근거이며 최종 통합 소스의 배포 완료를 뜻하지 않는다.


## 2026-10-15 시장 관찰을 1분·5분 중심으로 전환

후속 NAS 읽기 검증에서 오늘09:02~09:10의 실제 TOP20 합집합26종목/저장 KRX 분봉271행을 대조했다. 9개 cut 모두 실제 대금 봉의 가용시각이 약9시간 미래로 기록돼 후보 UNKNOWN/5분 지속 UNAVAILABLE이며 별도 원본 시각 검사와 일치한다. 최신 projection 검사이므로 과거 불변 revision/정확도 수락과 구분한다. NAS에는 여전히 시계 수정 전 release가 실행 중이다. 제품 코드 변경/배포 없이 이 경계를 확정했으며 다음 실제 검증에는 정상 시각의 완료 분봉이 필요하다. [최근 실제 자료 검증](workstreams/market-context.md#후속-확인--10월15일-실제-top20-분봉의-시점-적격성).

조회순위 변화와 독립적인 **1분 가격·실제 거래대금 후보 변화**, **5분 지속 확인**을 기존 Runtime/UI에 연결했다. 초봉은 보조이며 초봉 부족/짧은 TTL이 분봉 판단을 차단하지 않는다. 기존 분봉 reader의 공유 비동기 읽기는 기존 H1/H2/보호 처리 앞에서 기다리지 않고 원래 cut/정확한 결과의 부가 관측만 갱신한다. 관련129건/native 종료0으로 단위·실제 SQLite 연결·2/8 공유·지연 중 기존 판단 진행·종료·UI/만료를 확인했다.

NAS 기록은 읽기만 했다. 선택 원본 분 저장7회/마감4회 입력을 격리 SQLite에 적용한 네 당시 TOP20 시점은 모두 가용시각 오류로 UNKNOWN이다. 과거 초봉80 cut/17종목 결과를 새 분봉 방식의 정확도 검증으로 재사용하지 않았다. 로컬 미커밋·미배포이며 정상 시각의 완전한 기록을 통한 실제 후보/OOS 수락이 남는다. 이전202/5,102건·전수 지문은 현재 소스 수락과 구분한다. [현재 구현·검증](workstreams/market-context.md#최신-상태--1분5분-중심-초봉-보조로-전환).

## 2026-10-15 TOP20 후보 자료 부족 사유 표시

앞 시장 시계 수정 뒤에도 후보가 미확인인 원인을 같은 기록의80 cut/1,600 종목×시점으로 분해했다. 최대17/20 자료 확인과 세 부족 코드의 원문 수신 시각·표본을 대조했으며 수신 부족을 거래소 체결 유실로 단정하지 않았다. 이는 당시 초봉 기준의 진단이며 위 분봉 기준과 구분한다. 당시 관련112건/native 종료0이다. [당시 근거](workstreams/market-context.md#이전-확인--top20-코호트-부족-사유-분리표시).

## 2026-10-15 시장자료 시계 연결 로컬 수정

시장 조회순위와 가격·대금 후보를 분리한 후 NAS 기록의 약9시간 가용시각 차이를 추적했다. 기존 KiwoomRestClient의 naive KST 시계를 app이 Collector/MarketEventService에 그대로 전달해 UTC에서 epoch로 변환하던 경로를 확인하고, 기존 두 시계 주입점에 as_kst를 적용했다. 기존 클라이언트 계약/수집·writer·transaction·주문 경로는 유지한다. 실제 앱 구성→0B→RAM/SQLite 시점 reader와 별도 UTC 오차32,400→0초·관련202건/native 종료0을 확인했다.

현재 소스는 기준50a70c9의 로컬 미커밋 변경이며 **NAS 미배포**다. NAS 활성2026.10.14-recorder-shared-10m-v1-af0f8072f960415b는 읽기만 확인했다. 과거 기록/시각을 보정하거나 당시 UNKNOWN·전체 trace incomplete를 승인으로 바꾸지 않았다. 정상 시각의 TOP20 전원 자료·완전 녹화·전진/OOS와 전체 통합/운용 수락이 남는다. 아래 이전 전수검사 지문/5,102건은 이 후속 소스의 현재 수락으로 재사용하지 않는다. [당시 시계 수정 보고](workstreams/market-context.md#이전-확인--시장-시계-연결-원인-수정검증).

## 직접 검색 아침 시황 — ed9d 워크트리 (2026-10-15)

현재 이 작업의 실제 HEAD는 detached `50a70c9ce6cdf20915fde27f2d1f47becb82eadf`이며 변경은 미커밋이다. 07:55 KST 직접 웹 검색·생성/저장/인증 조회/앱 프롬프트·모델·추론 편집을 구현했다. 사용자가 지정한 00·① 계약에 맞춰 장전 출력은 `morning-market-brief-v4`로 수정했다. 시장 방향/참여 폭/집중도, 사실·재료·관계 후보·UNCONFIRMED 가설과 확인/무효화 조건을 구조화하고 숫자 신뢰도·매매 점수·확률을 금지한다. 기존 C1/뉴스 점수/매매·위험·실행 6파일은 HEAD와 동일하다. [대조·매핑·미연결 범위](workstreams/morning-market-brief-alignment.md) · [프롬프트·검증](workstreams/morning-market-brief.md).

확정 종가와 현재 시세를 구분하는 v4 계약·프롬프트·화면을 추가했다. 공식 확정 종가는 거래일 정밀도로 허용하고 현재 시세는 관측 시각을 요구한다. 현재 정식 뉴스 API 프로필 결과는 `data/diagnostics/morning-market-brief/v4-final-platform-profile/run.json`에 둔다. 105건/9 작업자 pass, skip0/native exit0이며 검사 중 소스 변경은 없었다. 이전 v3 프로필99건 통과는 과거 결과다. API의 `available`은 보고서 조회 가능 상태이며 `consumer_binding=not_connected`다. 기존 정책별 required_data_capabilities와 주문 코드에는 연결하거나 변경하지 않았다.

이전 비활성 후보 `2026.10.15-morning-market-brief-v1-bceef847d1802424`의 NAS 격리12건은 pass/skip0/cleanup/운영 불변이었다. 관리자 설치는 operation_busy에서 시작 전 거절됐으며 새 v4 계약은 NAS에 게시/설치하지 않았다. 실제 활성 소스는 아직 `2026.10.14-recorder-shared-10m-v1-af0f8072f960415b`다. NAS 구독 연결·source 활성화·실제07:55 수락·Market State/C1 adapter·장중 검증/수명·기존 중복 수집 중지는 미완료다. 이 작업의 all-local 전수·원격 CI·commit/push도 실행하지 않았다.

## 2026-10-15 반복 검사 실패 원인과 Windows 임시 자료 격리

**왜 필요했나:** 서로 다른 실패를 전체 재실행으로 처리해 완료가 지연됐다. 검사 목록 누락 5개, 추가된 RAM API/recorder 설정과 기존 API 기대값 불일치, 실제 시각을 사용하는 모의 주문 fixture, Windows 임시 디렉터리 rename 경합을 구분했다. 앞의 세 문제는 실제 변경/계보를 확인해 목록과 정확한 추가 API 기대값·고정 거래 시각만 수정했다. 기존 API와 운영 Risk·시간 검사는 보존한다.

**무엇이 달라졌나:** Windows 검사 자료를 관찰되는 checkout 밖의 사용자 AppData Temp로 격리한다. 실패를 재시도로 감추지 않고 원래 코드의 즉시 오류·활성 버전 보존을 유지한다. 자동매매 동작을 새로 활성화하는 변경은 아니다.

**연결과 직접 원인:** 기존 Runner가 현재 소스/Windows Job으로 실행하면서 TEMP/TMP/TMPDIR만 worker마다 분리한다. 로그·결과는 지정 output에 남고 통과·모든 자식 종료 확인 후 해당 임시 자료를 제거한다. 실패/중단 자료는 경로와 함께 보존한다. 원래 fixture의 Documents 300회 중 10회 실패/AppData 300회 무오류, 좁힌 실제 게시 160회 중 7회 실패를 확인했다. 마지막은 4회 중 첫 실패에서 추적을 종료했다. native rename의 1.887ms 호출 구간과 `ChatGPT.exe` PID4600의 같은 staging 하위 `tests` 디렉터리 핸들 2개가 겹쳤다. 핸들이 수십 마이크로초에 닫혀 사후 조회에서 사라졌다. 공유 권한7/읽기 속성0x80의 하위 디렉터리·파일 핸들을 유지한 통제 검사에서도 WinError5가 발생했고, 최상위 디렉터리 핸들만 유지하면 rename은 가능했다. [원인 대조](../data/diagnostics/20261015-windows-denial-evidence/failure-handle-correlation.json) · [핸들 조건 비교](../data/diagnostics/20261015-windows-denial-evidence/controlled-handle-matrix.json). Windows의 하위 열린 핸들 rename 제약은 [Microsoft 문서](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/ns-ntifs-_file_rename_information)와 대조했다. 첫 WPR 전체 profile은 부하·이벤트 유실로 직접 증명에 쓰지 않았고 마지막 제한 이벤트 추적은 유실0이다.

**사용·검증 상태:** 관련 검사71건·누적 archive 실행 계보4건 통과. 수정한 실제 Runner의 원래 source-runtime 모듈200건이 동일 소스·계획/실행200·자식 종료·임시 제거까지 통과했다. 잠금 유지 검사는 native 게시가 1회 실패하고 기존 active를 보존함을 확인했다. 마지막 Windows all-local은 한 번 실행해 5,102건/305 worker 전부 통과했다(58.052분). 누락·실패·중단0, 모든 worker의 프로세스 종료 확인과 소유 임시 자료 제거305건을 확인했다. LF 소스 지문 `83ecb1c2fc3bf2da99d1c946cf7988268344df3481f8c91aa26541fb76cd7763`은 실행 전후/현재가 같고 required Windows local source gate도 통과했다. [전수 결과](../data/diagnostics/20261015-root-cause-final-all-local/run.json) · [동일 소스 검증](../data/diagnostics/20261015-root-cause-final-source-gate.log). 실제 키움 모의계좌 전송/부분체결·실계좌·NAS 배포·외부 CI 수락을 뜻하지 않는다.

**아직 남은 것:** 관찰 프로그램 자체의 모든 파일 열기를 수정한 것은 아니다. 이번에 재현한 검사 directory rename 경합의 제거를 과거 연구 파일 교체·다른 경로의 ACL 접근 거부 해결로 확대하지 않는다. R4 native 모델 적용/최종 자동 주문 연결과 기존 모의계좌·운용 승인 잔여는 기존 순서를 유지한다.

아래 단계의 미커밋·전체 검사 미실행 표현은 당시 기록이다. 이번 체크포인트는 누적 변경을 보존하고 로컬 Windows 전체 검증만 갱신하며, 외부 CI·배포·장중 운용 수락과 구분한다.

## 2026-10-15 A01 행동별 요구자료와 A02 상위 맥락 분리

사용자 지정에 따라 A01~A03을 우선하고 A04는 뒤로 옮겼다. A01의 현재 R4 v2 정책에 선택 `action_required_data_capabilities`를 연결했다. 공통 자료와 행동별 추가 자료를 구분하며 ADD만 뉴스가 필요하면 ADD만 제외하고 요건이 맞는 REDUCE를 비교할 수 있다. 기준 HOLD의 필수 자료 결측은 WAIT이고 공통 계좌/Risk/UNKNOWN/자금·비용 검사와 강제 보호는 우선한다. R3 판단을 특정 행동이 요구할 때는 그 행동에 HOLD/WAIT/REDUCE 제한과 TTL을 적용한다. 유효한 R3 EXIT는 기존 전체 보호 경로를 우선한다. 선택하지 않은 타점 자료의 짧은 TTL이 다른 행동의 lease를 줄이지 않으며 선택한 행동의 TTL은 전체 receipt에도 반영한다. 선택 필드 생략/None의 결과·ID는 동일하고 UI와 기존 선택 기록에 행동별 결측을 표시한다.

A02의 가중치0 점수 소비자는 기존 owner의 typed ThemeMembership을 위험 소속으로 전달하고 사용 H1/H2 점수 구성요소의 시각·TTL·품질만 검사한다. 시장 점수만 쓰면 미사용 leadership 초 창이 전체 lease를 만료시키지 않는다. 미래 소속·결측·상이한 중복은 거절한다. 모두 양수인 기존 가중치와 독립 owner 자료를 명시하지 않은 직접 호출 계약은 유지한다. Runtime→기존 allocation adapter의 동일 호출에서 이미 보유한 소속을 전달하며 hot 호출 깊이·owner/collector/writer·공통 판단을 유지한다. 별도 수집/조회/Risk 계산/엔진/relay가 없다.

신규10 포함 관련 고유168건 통과, 선택 필드 생략 호환성1건을 추가 확인했다. 2/8 공통 RAM·계좌/계산1회, 조회100회 추가 DB/계산0, leadership 결측 중 시장 점수 기반 상위 금액→하위 수량 계산, 필수 점수/위험 소속 차단, 보호·UNKNOWN·만료·원본 publication/계보·native owner·UI/선택/기록/drain을 검증했다. 기존 완전한 결과4개와 ID 동일, compile·QueryStore 소비 감사delta0다. [검증](../data/diagnostics/20261015-capability-context-support/verification.json). R4용 theme/news/turnover 추가 맥락 생산자는 계속 미연결이며 요구한 행동만 결측으로 남긴다. 미구현 전략 registry 전체나 새로운 뉴스 엔진을 구현한 것은 아니다.

A03의 현재 native 모델 지원은 구현하지 않았다. 원본 Paper publication을 기존 reader로 재생하고 현재 native owner 계약과 대조했다. source_continuation은 code/family/parameters/비용·분봉 실행/session을 설명하며, 원본 case proof에는 native 위험 한도·보호 요청·session clock·목적별 미체결 후속 정책의 동등성을 검증할 기록이 없다. 현재 정상 Risk나 family 일치만으로 과거 모델의 미래 정책 지원을 인정할 수 없고 과거 REENTER의 명시 목표 적용은 native 쿨다운 해제 권한도 아니다. [계약 대조](../data/diagnostics/20261015-capability-context-support/source-contract-audit.json). 조건부 현재 상태 지원과 실제 native 실행 적용을 구분할 계약 판단이 필요하다. EV_UNAVAILABLE/result=None/orderfalse는 유지한다. [A03 설계 재검토](AI_TRADING_SYSTEM_REDESIGN.md#a03-설계-재검토).

main/HEAD232737d·누적 로컬 미커밋·미배포·build `2026.10.15-action-context-gates-v1`. 168건 회귀 후 원본 맥락 외피의 미래 가용시각 거절을 한 줄 보완하고 해당 구성요소/공유 경계6건을 재검증했다. build 표시3파일은 별도 정적 일치를 확인했다. 원래 회귀 소스 지문과 마지막 좁은 보완 지문을 검증 기록에서 구분한다. explicit KRX scientific hash는 `6c694ec2ea9fa23a88ceeb17603c5bd141a5ebdaa2bd2595aa087eedfa0ab961`이며 legacy None hash는 유지한다. 기존 패키지의 hash/TTL/지원 범위를 자동 재표지하지 않는다. 전체 회귀·CI·원격 main·실제 PG/NAS·브로커/장중 수락은 이번 실행 범위가 아니다. 다음은 A03 지원 계약 판단/구현이고 A04·A05·실제 모의계좌 통합/별도 운용 승인은 잔여다.

이하 설명은 해당 시점의 기록이다.

## 2026-10-15 R4 정책별 필수 자료와 조건부 비중 점수 연결

종합 대조 A01의 현재 R4 계약을 구현했다. 기존 `paired-net-action-selection/v1`과 `owner_ram_risk_and_thesis`는 그대로 유지하고, 명시적으로 선택하는 `paired-net-action-selection/v2`·`owner_ram_risk` 정책에 `required_data_capabilities`를 추가했다. 가격과 기존 R3 타점 판단 생산자를 연결하며, 선언하지 않은 테마·뉴스·거래대금 맥락을 공통 차단 조건으로 쓰지 않는다. 선언했지만 검증된 R4 생산자가 없는 `theme_context`·`news_context`·`turnover_delta`는 각각 결측으로 보고한다. 자료 결측을 0점이나 가짜 테마로 채우지 않는다. 계좌·미해결 주문·자금/비용·신선한 native Risk·scope·원래 TTL은 공통 필수이며, 신선한 하드 보호 조건은 자료 결측과 EV보다 먼저 기존 위험 경로 필요성을 반환한다. 주문 권한을 부여하지 않는다.

테마/타점 판단을 요구하지 않는 v2와 native 계좌 관측을 명시한 경우에는 빈 테마 선택으로 시작·저장·재시작할 수 있다. 상위 allocation이나 테마/타점 요구 정책은 기존 테마 조건을 유지한다. 같은 Runtime·Collector·공통 판단·선택 기록을 재사용하고, R4만 실행하면 테마/순위 생산자를 조회하거나 계산하지 않는다. 가격은 기존 승인된 체결 RAM 참조이며 초봉 DB 읽기/쓰기 반복을 추가하지 않는다. 시작에 계좌가 없으면 UNAVAILABLE이며 과거 저장 결과를 현재 승인으로 복원하지 않는다.

A02 상위 비중에서는 가중치가 0인 시장·테마·종목 점수의 결측을 `None`으로 보존하고 요구하지 않는다. 실제 사용하는 점수는 결측 시 차단한다. 테마 집중 위험 검사에 필요한 소속 정보는 계속 필수다. 현재 native 상위 입력이 사용하는 leadership/context 생산자의 품질 경계까지 완전히 분리한 것은 아니며, 현재 study 지원 판정(A03)도 여전히 EV_UNAVAILABLE다. 전체 전략/행동의 자료 registry와 새로운 맥락 생산자 구현 완료를 뜻하지 않는다.

신규11 포함 관련 고유131건 통과: v2 계약/파서·조건부 점수·2/8 공통 RAM/Risk·보호/UNKNOWN/만료·UI·인증 ASGI·빈 테마 시작/저장/재시작·기존 선택 기록/취소 drain·원본 publication 재생을 검증했다. RAM 한 회차 관측/계좌/비교 각1, 조회100회 추가 계산/DB0, R4 단독 테마/순위 조회0을 확인했다. 완전한 기존 결과4개와 ID 동일, compile 및 QueryStore 소비 감사delta0다. 초기 시험 import 경로 누락과 기본 샌드박스 실행 정지는 성공으로 세지 않았고, 기존 사용자 Python과 workspace 임시 DB/가짜 계좌로 최종 검증했다. `httpx.AsyncClient/ASGITransport`를 사용하며 httpx2 설치/위장은 없다. [검증 기록](../data/diagnostics/20261015-r4-policy-capabilities/verification.json).

기능 이해 경로는 기존 policy/판단→RAM adapter/Runtime→API/UI이며 새 owner·engine·Collector·DB writer·relay 계층이 없다. 불변 정책 DTO와 설정 시 의존성 판정만 추가했다. explicit KRX scientific hash는 `ad438956b2335d6f1c7517dc6e1932e8b0c27ab84f13345f2fcf96c4c3f8b617`로 변경되며 기존 패키지를 자동 재표지하지 않는다. legacy None hash는 유지된다. main/HEAD232737d·누적 로컬 미커밋·미배포·build `2026.10.15-r4-policy-capabilities-v1`. 전체 회귀·CI·원격 main·실제 PG/NAS·브로커/장중 수락은 이번 실행 범위가 아니다. 다음은 A04의 검증된 순자산 분모와 주문가능금액 분리이며, A03/A05 및 실제 모의계좌 통합/별도 운용 승인은 남아 있다. 사용자 지정 큰 개발 순서는 유지한다.

이하 설명은 해당 시점의 기록이다.

## 2026-10-15 R4 가격 입력의 테마 의존 제거

종합 대조 A02의 가격 연결을 수정했다. 기존 Collector의 승인된 최신 KRX 체결 참조를 같은 RAM snapshot에 포함하고 R4가 직접 소비한다. R4 종목은 테마 관계가 없어도 기존 snapshot 조회 집합에 유지한다. 초봉 이력/테마 연속 창을 단일 가격의 필수 조건으로 쓰지 않으며, 구독 승인·source 시작·Collector/전달 gap 이후 관측·가용시각·원래 가격 TTL을 검사한다. 가격의 input_ref는 기존 collector-trade 참조이며 확정 저장/주문 승인이 아니다. 계좌 대사 품질과 가격/전략 맥락 품질을 구분한다.

테마 제거와 빈 초 이력의 2/8 Shadow에서 순수 R4 비교가 가능하고 한 회차 RAM 읽기/계좌 관측/계산 각1·상태100회 추가 DB/계산0·원본 dirty 상태 동일을 확인했다. native risk-and-thesis 정책의 필수 판단 누락은 계속 BLOCKED다. 신규3 포함 관련26건 통과·[검증 기록](../data/diagnostics/20261015-r4-independent-price/verification.json). 기능 수정은 기존 Runtime 한 파일이며 새 Collector/engine/task/writer/DB 계약/네트워크/주문이 없다.

A01 정책별 required_data_capabilities와 A02 상위 비중의 맥락 요구조건 분리는 아직 남는다. 현재 연구 모델의 양의 지원 판정·순자산/STOP 보호 계약·실제 모의 수락을 이 수정으로 승인하지 않는다. main HEAD232737d·누적 로컬 미커밋·미배포·build2026.10.15-r4-independent-price-v1. 전체 회귀/현재 CI·PG/NAS/장중 수락 미실행.

## 2026-10-14 실제 연구 입력 접근 복구 확인과 v2 재진입 비교 지원

사용자가 접근 복구를 알려준 뒤 두 실제 연구 패키지의 manifest와 TRAIN/VALIDATION4분할을 기존 load_research_input/partition/readiness 함수로 읽기 전용 검증했다. 접근 문제는 해결 확인했으며 로컬 디렉터리·manifest는 정상이다. package hash, 원본 observations/ordinal/revision hash·partition ID/경계를 확인했고 OOS 입력을 열지 않았다.

| 패키지 | TRAIN 관측 | VALIDATION 관측 | 분봉 준비 상태 |
|---|---:|---:|---|
| monthly-2024-09-to-2026-01-clockfix-v2 | 181,708 | 63,218 | READY |
| multi-period-2024-2026-stocks-pairs-v4 | 14,976 | 16,541 | READY |

총276,443관측(분봉276,423·역사 후보 모집단20)이다. 실제 데이터는1분봉/후보 복원이며 당시 TOP20 관측이 아니고 theme snapshot은 모두0이다. 기존 historical-context 변환으로 첫 원래 bar cut을 확인했으며 D2/D5 context가 없는 상태를 그대로 보존했다. 파일 무결성/분봉 READY를 당시 공통 맥락·통계적 독립성·현재 R4 적용 승인으로 바꾸지 않는다. [실제 입력 감사](../data/diagnostics/20261014-real-r4-input-audit/real-input-audit.json).

별도의 통제 연구 시험으로 기존 native v2 reader의 REENTER 지원을 연결했다. 기존 registered family/Paper/ResearchRepository가 실제 계산한 선행 매수·전량 SELL 체결과 pending0·빈 포지션·cooldown 상태를 요구한다. 기존 v1 target 가치 adapter의 prefix 읽기 코드를 같은 파일의 공통 helper로 추출해 재사용하고, 원래 빈 계좌 {}와 원래 goal/TTL을 정확히 재생한다. flat HOLD는 검증된 v2 안에서 같은 현금/수량 가치 adapter를 쓰되 HOLD에 target declaration/주문을 만들지 않는다. 짝 경제 상태의 position=None을 보존하며 기존 평균/표준오차·비용 계산·native writer/게시 경로를 사용한다.

이것은 명시 재진입 가설의 과거 Paper 비교다. 통제 시험의 쿨다운 중 명시 목표 적용을 실제 native 재진입/쿨다운 해제 승인으로 해석하지 않는다. 원래 v1 DTO/공개 비교·held HOLD/ADD/REDUCE 의미와 현재 R4 차단을 유지한다. Native RISK_FILL 선행 청산, partial/pending 초기 상태 및 EXIT의 v2 확장은 승인하지 않았다. 새 engine/collector/table/writer/task/실시간 조회가 없다. 기존 과거 목표 TTL 뒤 완결한2case/4run의 REENTER19주 대 flat HOLD 비교는 COHORT_COMPARABLE·평균 순가치-29원이며 현재는 EV_UNAVAILABLE/usablefalse/orderfalse다. [통제 원본 게시](../data/diagnostics/20261014-real-r4-input-audit/controlled-reentry-publication.json).

선행 완전 SELL 증명이 빠지면 HOLD/REENTER4branch 모두 제외하고 전체 cohort를 미확인으로 만든다. 공개 원본 준비는 기존4borrow/SELECT16/COMMIT0·원본 SQLite bytes 동일이며 최초 게시 재생은 DB0이다. 원래 outcome/정책/가용 시각/기한을 덮어쓰지 않는다. 실제276,443행을 이 통제 fixture의 모델 학습 자료였다고 주장하지 않는다. explicit KRX scientific source hash는 코드 변경으로 바뀌며 기존 패키지를 자동 재표지하지 않는다. legacy None hash와 supplied v1 전체 예제/decision ID는 보존한다.

신규3 포함 관련 고유44건 통과, compile·소비 감사delta0·기존 required Windows route/core fingerprint 유지. [검증 기록](../data/diagnostics/20261014-real-r4-input-audit/verification.json). 다음은 현재 R4용 공통 맥락·상위 입력 독립 계보와 native 보호/미체결 후속 정책 근거를 실제 생산자에서 연결하는 작업이다. 없는 과거 테마/초 단위 입력이나 운영 승인을 합성하지 않는다. 사용자③ 전체·④ 실제 모의계좌00/04/REST·잔고·청산/운용 승인 및 현재 R4 적용은 미완료다. 원래00 상위 Position·Risk·Capacity/Liquidity 자동 비중을 유지한다. main/HEAD232737d·누적 로컬 미커밋·미배포·build2026.10.14-position-reentry-study-v2. 전체 회귀/HTTP 전체 묶음·CI·원격main·실제PG/NAS/장중 수락 미실행, catalog 무관 archive5개 보류 유지.

이하 설명은 해당 시점의 기록이다.

## 2026-10-14 자동 목표 HOLD/EXIT 변환 오류 수정과 현재 R4 근거 확인

현재 근거 확인 중, 근거 부족과 별개인 자동 목표 변환 오류를 재현해 수정했다. 계좌 운용 한도가 기존 보유 평가액과 같아 HOLD가 되거나 0으로 줄어 EXIT가 되는 경우, 상위 비중 계산은 정상이나 RAM 변환이 ADD/REDUCE/REENTER 전용 automatic sizing DTO에 HOLD/EXIT를 넣어 ValueError를 발생시켰다. 같은2/8 Shadow 입력에서 HOLD/EXIT4개 실패를 재현했고 REDUCE를 대조군으로 유지했다.

기존 market_theme_shadow_inputs의 하위 요청 생성 한 곳에서 ADD/REDUCE/REENTER는 기존 sizing 요청을 그대로 사용하고, HOLD/EXIT는 기존 명시 수량 계약(actual/0)으로 전달한다. 상위 자동 목표/정책/ref·원래 receipt TTL은 유지하며 조회 만료 때 결과/하위 목표를 함께 제거한다. 사용자 종목별 수동 금액 정책을 추가한 것이 아니다. DTO 허용 범위·과거 study 계약·native Risk/보호/주문 경로를 넓히지 않는다. 새 함수/owner/collector/table/writer/task나 호출 단계를 추가하지 않았고 기능 이해 파일/호출 깊이는 기존과 같다.

신규2 포함 관련 고유52건 통과: 2/8 HOLD·EXIT·REDUCE의 목표/수량/공통상위ref·매수0·계좌borrow1·hot SQLite접근0·만료 제거, v2 HOLD/EXIT에 ADD 연구 scope를 잘못 승인하지 않는 경계, 기존 ADD/비중/수량/게시·기록/취소 drain을 검증했다. 신선한 native 위험 우선, 만료 위험으로 새 보호를 만들지 않는 경계, 8개 Shadow 계산 정지 중 실제 기존 보호 처리(가짜 전송)3건도 통과했다. 현재 R4는 EV_UNAVAILABLE/result=None/orderfalse를 유지한다. 소비 감사delta0·compile·기존 supplied v1 전체 예제/decision ID 동일·자체 required Windows CI/core fingerprint 유지. [검증 기록](../data/diagnostics/20261014-allocation-hold-exit/verification.json).

기존 controlled native 연구 publication을 같은 decoder로 재검증했다: 원래4run·2context의 COHORT_COMPARABLE과 원래 availability/expiry를 보존한다. D2/D5 맥락에는 초 단위 leadership·breakout·시총·상위 EV/출구/stress 현재 지원 근거가 없고, source spec에는 native 위험 한도/미체결 후속 정책이 없다. 현재 맥락/독립 source와 native 보호·후속 모델의 적합성을 승인할 수 없다. [원본 감사](../data/diagnostics/20261014-allocation-hold-exit/source-audit.json).

실제 연구 입력 후보 두 디렉터리(`data/research/historical-development-inputs/monthly-2024-09-to-2026-01-clockfix-v2`, `multi-period-2024-2026-stocks-pairs-v4`)는 기본 샌드박스와 사용자 세션 읽기 전용 재확인 모두 Windows 접근 거부였다. 전체 역사 입력이 없다고 결론내리지 않으며 이 두 폴더 내용/적용 근거 존재 여부는 미검증이다. 읽기 ACL을 바꾸거나 자료를 현재 숫자로 합성하지 않았다. 확인된 DTO 오류와 이 자료 접근/모델 근거 문제를 별도로 유지한다.

다음은 접근 가능한 실제 원본 또는 읽기 권한이 회복된 입력에서 사전 cohort·공통 맥락·상위 입력 계보·native 보호/후속 지원을 확인/생산하는 작업이다. 상위00 Position·Risk·Capacity/Liquidity 자동 비중을 유지하고, 현재 R4 적용 및 사용자③ 전체/④ 실제 모의계좌00/04/REST·잔고·청산/운용 승인은 미완료다. 무관 proof/ML 단계를 자동 추가하지 않는다. main/HEAD232737d·누적 로컬 미커밋·미배포·build2026.10.14-allocation-hold-exit-v1. 전체 회귀·HTTP 전체 묶음·CI·원격main·실제PG/NAS/장중 수락 미실행, catalog의 무관 archive5개 보류 유지.

이하 설명은 해당 시점의 기록이다.

## 2026-10-14 마무리 묶음 3 — 주문 OFF 로컬 통합 검증 종료

고정한 세 묶음의 로컬 검증을 마무리했다. 현재 입력→수량/주문 OFF 계획과 고정 연구 입력→합성 브로커 원장 전체 흐름을 구분한다. 현재 R4는 EV_UNAVAILABLE/result=None/orderfalse이며 양의 현재 R4를 native 주문으로 연결한 성공을 주장하지 않는다. 사용자 순서③ 전체 개발 완료 또는④ 실제 모의계좌 통합/운용 승인 완료는 아니다.

| 로컬 묶음 | 종료 범위 |
|---|---|
| 1. 공통 입력 연결 | 기존 현재 관측과 원래 EV/출구/study 참조·cut·TTL 연결 검증 완료. |
| 2. 모델·보호 경계 | 과거 지원 범위와 현재 미지원 차단, native 보호/목적별 미체결 우선 검증 완료. 현재 모델 적용 근거는 보류. |
| 3. 주문 OFF 통합 | 고정 입력의 선택→상위 자동 비중→수량→단일 원장 미체결/청산, 현재 RAM 계획/차단·입력 소실·만료·종료 검증 완료. |

기존 시험 모듈 두 개에 통합 시험3건만 추가했다. 관련 고유59건 통과, compile·소비 감사delta0·기존 required Windows CI route/core fingerprint 유지. 생산 코드·호출 깊이·owner/collector/task/table/writer·배포 build는 변경하지 않았다. 현재 RAM8개 Shadow는 같은 선정 범위/상위 goal ref/비중 금액/하위 수량으로 주문 OFF 계획을 대사하고 DB 접근0·계좌borrow1을 확인했다. 현재 R4 미지원, account source 예외 후 이전 계획 제거, 원래 sizing TTL 만료/조회 제거 및 종료 후 게시 거절을 확인했다. 기존 게시 취소 drain과 selected 기록 회귀도 실행했다.

고정 입력 시나리오는 계좌 운용 한도10,000원과 EV/시장·테마·종목/유동성·위험 입력으로 목표를 자동 결정한다. 양의 EV 종목은10주, 음의 EV 종목은 신규0주로 선택되고 하위 수량 결과가 같은 시험 원장의 root quantity/decision ref로 전달된다. 종목별 투자금을 사용자가 직접 지정하는 최종 구조로 바꾸지 않았다. 이 경로는 supplied fixed-input 연구이며 현재 native 모델 적용 승인·성과 검증이 아니다.

같은 원장의 BUY3주 부분체결/취소 후1,010원 재평가에서는10,000원 목표가9주가 되어 잔여6주다. 이전7주 WAIT/REPRICE 근거를 거절하고 별도 명시한 고정6주 근거만 계산한다. UNKNOWN 응답과 재시작은 중복 제출을 만들지 않는다. 이후 익절 WAIT와 보호 CANCEL/REPRICE를 구분하고, 보호 SELL4주 부분체결/취소 확정 후 잔여5주를 다시 계산한다. 완전 체결+신선한 flat/reconciled 계좌일 때만 liquidation_confirmed이며 불완전 대사/미체결은 청산 완료로 인정하지 않는다. 수량/현금은 원장 상세 FILL을 읽는 시험 계좌 producer에서 계산하고 기존 Paper fee ROUND_DOWN을 재사용한다. gross-110원/모의 수수료·세금34원/net-144원·최종현금99,856원·보유0을 손계산과 대사했다. 원장4intent·가짜 submit4/cancel2·실제 브로커 전송0이다. 합성 응답/모의 비용으로 실제 체결·수수료·잔고 수락을 대체하지 않는다.

[검증 기록](../data/diagnostics/20261014-order-off-closeout/verification.json) · [원장 흐름/목표/최종 계좌](../data/diagnostics/20261014-order-off-closeout/offline-ledger-flow.json).

다음 남은 항목은 현재 R4에 필요한 exact 과거 맥락·상위 입력 독립성/source custody·native 보호/후속 모델 지원 근거 확보와 실제 모의계좌00/04/REST·부분체결/잔고/청산 검증 및 별도 운용 승인이다. 없는 과거 사실이나 미지원 모델을 승인 자료로 합성하지 않는다. 원래00 Position·Risk·Capacity/Liquidity 상위 자동 비중 정책을 유지한다. 무관 기능/새 ML 프레임워크/새 proof 단계는 자동 추가하지 않는다. main/HEAD232737d·누적 로컬 미커밋·미배포·build2026.10.14-allocation-input-bundle-v1 유지. 전체 회귀·CI·원격main·실제PG·NAS·장중 수락 미실행, catalog 무관 archive5개 보류 유지.

이하 설명은 해당 시점의 기록이다.

## 2026-10-14 마무리 묶음 2 — 지원·차단/보호 경계 검증, 현재 모델 적용은 보류

고정한 둘째 묶음의 **로컬 지원·차단/보호 경계 검증**을 마쳤다. 현재 R4 모델 적용을 승인한 것은 아니다. 새 생산 코드·엔진·수집·조회·기록·주문 경로 없이 기존 시험 모듈에 동시 실행 회귀 한 건을 추가했다. 8개 Shadow의 실제 공통 allocation 계산을 멈춘 동안 별도 native 계좌 owner의 실제 Collector→Supervisor→Runner 손절 cue와 첫 보호 처리가 진행됐다. 두 시험 owner의 계좌 cut·모델 승인을 혼용하지 않는다.

| 검증 대상 | 현재 판정 |
|---|---|
| 사전 동결한 native Paper HOLD·ADD/REDUCE 짝 | 과거 비교 COHORT_COMPARABLE. 음의 수수료 포함 결과도 유지. 관측된 범위의 경험 통계이며 현재 예측/자동 비중 전체 성능 승인이 아님. |
| 현재 계좌의 R4 | 원래 exact context·상위 입력 독립성/source custody·native 보호/후속 모델 지원 미증명. EV_UNAVAILABLE/result=None/orderfalse 유지. 누락·변형·범위 충돌·만료와 후행 표본 선택 차단 검증. |
| native Risk·손절·미체결 | Shadow/EV/다음 분봉 완료 전에 보호 cue·wake·첫 가짜 전송 확인. 취소 확정/체결 대사 전 재주문과 UNKNOWN 중복 제출 금지. 매수·익절·보호 정책 구분. |

신규1 포함 관련 고유 **90건** 통과. 새 동시 시험은 R4를 풀기 전에 첫 보호 전송이 1회 진행되는지, 최초 stop quote/요청·수량을 보존하는지, 제출 상태를 청산 완료로 오인하지 않는지 확인했다. 즉시 cue의 connection/SELECT/COMMIT/전송0, 기존 status100회·기한/중복cue100회의 DB0 및 반복10회 COMMIT0 회귀도 통과했다. 이는 불필요한 전체 preflight 반복을 차단하는 경계 검증이며 Runner의 기존 체결/운영 control 주기 조회가 사라졌다는 뜻은 아니다. 소비 감사delta0·compile·기존 required Windows CI route/core fingerprint 유지. [검증 산출물](../data/diagnostics/20261014-model-protection-closeout/verification.json).

다음은 셋째 **주문 OFF 전체 흐름 통합**이다. 현재 R4 미지원은 명시 차단 시나리오로 포함하며, 이를 지원된 현재 R4의 종목 선정/비중/성과 통합 성공으로 표시하지 않는다. 현재 모델 적용 근거 확보와 실제 모의계좌00/04/REST·잔고·청산/운용 승인은 보류 항목으로 유지한다. 원래00의 Position·Risk·Capacity/Liquidity 상위 자동 비중 정책과 수량 하위 기능 구분은 그대로다. bundle 2의 경계 검증 완료를 사용자 순서③ 전체 구현 완료로 표시하지 않는다.

이번 묶음은 시험/문서만 변경했다. main/HEAD232737d·누적 로컬 미커밋, build2026.10.14-allocation-input-bundle-v1 유지, 미배포. 전체 회귀·CI·원격main·실제계좌·실제PG·NAS·장중 수락 미실행. catalog의 기존 무관 archive5개 보류 유지. 기본 샌드박스 비동기 시작은 중단했고 사용자 세션의 완료된 격리 SQLite/가짜 계좌 시험만 통과로 집계했다.

이하 설명은 해당 시점의 기록이다.

## 2026-10-14 개발 마무리 — 입력 연결 묶음 완료·다음은 적용/보호 검증

사용자가 종료 시점을 요청한 뒤 현재 사용자 순서③의 개발 마무리를 다음 세 묶음으로 고정했다. 입력 연결·원본 근거 참조의 구현과 모델 적합성/독립성 검증, 실제 운용 승인을 구분한다. 범위 밖의 새로운 기능·광범위 source 수집/ML/통계 보정 요구를 이 묶음에 자동 추가하지 않는다. 현재 쓰는 paired empirical EV는 학습 예측기가 아니며, 실제 학습/선택/검증 모델을 사용하는 경우의 계보와 기존 후보 모델 지원 검증을 생략한다는 뜻은 아니다.

| 묶음 | 개발 상태 | 종료 기준 |
|---|---|---|
| 1. 현재 입력·원본 근거 연결 | **구현/관련 검증 완료** | 기존 현재가/시총·대금/변동성·시장/테마/종목·계좌Risk와 EV/출구/study 원래 참조를 동일 cut에 연결. 누락/불일치/만료는 현재 입력으로 인정하지 않고 공통 소비·기록 횟수를 보존. |
| 2. 현재 적용·보호 정책 검증 | **지원·차단/보호 경계 검증 완료 · 현재 모델 적용 보류** | 명시된 지원 범위의 판단 결과, 범위 밖/충돌/만료의 EV_UNAVAILABLE, 원래 sampling·학습/선택/검증 계보 및 native Risk/보호/미체결 후속 우선 정책을 함께 검증. 입력 연결 완료를 모델 승인으로 사용하지 않음. |
| 3. 주문 OFF 전체 흐름 통합 | **다음: 미지원 모델 차단 포함** | 종목 선정→상위 자동 비중→하위 수량→주문 계획→미체결/청산까지 공통 경로를 한 통합 시나리오로 대사하고 입력 부족/실패/종료를 검증. |

실제 모의계좌 활성/전송·00/04/REST/잔고/청산, NAS/장중 수락과 별도 운용 승인은 위 로컬 개발 완료와 구분한다. unsupported 입력/모델이 실제 운용 가능한 자료로 준비됐다고 주장하지 않는다. 전체 종료 시간이나 다음 사용자 메시지 수를 보장하지 않는다.

기존 current RAM allocation의 이미 계산된 facts/account·Risk refs와 typed EV/출구 모델 및 재검증한 native source/study refs를 dynamic-allocation-input-bundle/v1으로 한 번 동결했다. v2만 선택 field를 추가하고 v1 출력/수치/ID는 보존한다. R4 각 goal은 같은 bundle_ref를 공유한다. source refs는 원래 것 그대로이며 원래 account/quote/model availability와 expiry 최솟값을 갱신하지 않는다. 지원되지 않는 source/누락/차단된 cut은 INPUTS_UNAVAILABLE, 갖춰진 현재 cut/원래 보고 근거는 INPUTS_BOUND다. 두 경우 모두 model_application_verifiedFalse·orderfalse이며 R4 현재 적용은 계속 EV_UNAVAILABLE다.

새 engine/collector/DB read/table/writer/task나 모델 재생·시장 계산은 없다. 현재 EV 추정 방법을 paired_empirical_incremental_portfolio_delta/v1/trained_predictor_usedFalse로 기록하고 학습 모델 계보를 만들어내지 않는다. Native raw/custody와 독립성·조건별 모델 지원은 다음 묶음의 미검증으로 유지한다. raw facts/EV outcome arrays를 재복사하지 않고 현재 facts의 hash와 원래 모델/입력 refs hash/count를 사용한다.256KiB budget 초과 시 작은 unavailable 사유만 제공하고 이미 계산한 비중/목표/quality/lease를 바꾸지 않는다. 조회 만료 때 현재 bundle과 goal을 함께 제거한다.

신규6 포함 관련17건·기존 v1 전체 예제/decision ID 동일·소비 감사delta0·compile 및 자체 CI 등록/core fingerprint를 확인했다. 격리2/8 Shadow의 Collector/계좌/입력bundle/상위 계산 각1·현재시장1·DB/COMMIT/주문0, status/중복100회 추가 read/build/replay/evaluation0이다. bundle2236bytes·평균 생성0.1377/0.1795ms, cold frame10.647/9.301ms이며 운영 지연 수치가 아니다. 명시 선택 기록은 기존 upsert 한 번/COMMIT1·다른 입력변화 없는 반복0이며 새 저장 경로가 없다. 기록은 selected_latest_transition의 한 latest 진단이고 전체 역사 capture가 아니다. 전체회귀/새bundle HTTP/실계좌·PG/NAS·장중/CI/원격main은 미검증이다. catalog 기존 archive5 보류 유지. build2026.10.14-allocation-input-bundle-v1·main/HEAD232737d 로컬 미커밋·미배포다. [86단계](AI_TRADING_SYSTEM_REDESIGN.md#86-개발-마무리-범위-고정과-공통-입력원본-근거-묶음)

이하 단계 설명은 해당 시점의 기록이다.

## 2026-10-14 사용자 순서 ③ — 과거 순위·테마 맥락의 native 연구 연결

기존 FrozenResearchDataset의 D2 순위 관측과 D5 테마 스냅샷을 과거 case의 원래 decision cut에서 동결해 native Paper 연구 명세와 기존 POSITION_STUDY_CASE proof에 선택 연결했다. 현재 테마를 과거에 붙이지 않으며 future availability/effective time·다른 dataset/symbol/case·실제 입력과 다른 내용·변형된 theme hash를 거절한다. 기존 연구 실행·목표·체결·값 adapter·writer를 재사용하고 새 collector/TR/DB query/table/writer/task/Manager는 없다. 기본 None 연구 명세/7-key proof는 유지한다.

같은 case의 HOLD/행동 branch는 같은 historical context를 가져야 완결 짝으로 인정한다. 최초 v2 게시에서 기존 H1 market 판단과 D5 membership 변환을 사용하고 동일 context_ref는 한 번만 재생한다. 이후 공통 RAM goal에는 작은 historical_context_summary(원래 refs/관측 테마/시장 quality/맥락·case coverage)를 공유한다. 기존 기본 v1 및 context 없는 v2 packet/goal 모양은 유지한다. 시장/테마 공통 모듈 두 개를 explicit KRX scientific implementation hash의 dependency에 추가했으며 legacy None hash는 유지한다. 기존 패키지를 새 지문으로 자동 재표지하지 않는다.

이 연결은 기록 당시 관측된 순위·테마의 제한된 provenance다. 초 단위 테마 leadership·성숙 breakout·시총·상위 EV/출구/stress 조건, 학습·선택·검증 계보와 native 보호/실행 후속 정책의 모델 지원은 아직 없다. 현재 조건의 exact statistical support를 승인하지 않는다. current_context_support_verifiedFalse·R4 EV_UNAVAILABLE/result=None/order OFF이며 사용자 순서③ 전체 판단은 미완료다. 원본 DB/capture의 독립 인증과 historical theme/revision 완전성도 not_verified다.

context는 최신100 ranking/100 theme snapshot 및128KiB, 기존 case proof256KiB/전체packet4MiB/event10000/outcome2000/bar2000 예산 안에 둔다. row count로 잘린 이력은 명시 flag를 남기고 bytes 초과는 실행 전 실패한다. 잘린 테마의 이전 seed나 빈 자료를 복원·추정하지 않는다. 새 현재 quote/status 조회가 과거 시각·quality/TTL을 갱신하지 않는다. 보호 Risk/Runner 경로는 이 준비/게시/Shadow 평가를 기다리지 않는다.

신규8 포함 관련 고유53건·소비 감사delta0·기존 supplied v1 전체 예제/decision ID 동일을 확인했다. 과거 case2개의 동결은 DB/판단 계산0·1.439ms, 네 run 준비 전후 borrow4/SELECT16/COMMIT0·원본 bytes 동일이다. 명시 연구 packet163433→262097bytes로 커지고 source read21.380→26.437ms다. 기존 별도 case event 행/COMMIT을 추가하지 않고 기존 명세/metadata payload만 커진다. 격리2/8 최초 게시의 과거시장 계산은 각각2회(case별1회), hot Collector/계좌 각1·현재 시장1·상위1·DB/COMMIT/주문0, status/중복100회 추가read/replay/evaluation0이다. 전체회귀·새 context proof의 HTTP/실제계좌 활성·00/04/REST/잔고/청산·PG/NAS/장중/CI/원격main은 검증하지 않았다. 기존 catalog의 무관 archive 미등록5개는 보류다. build2026.10.14-position-historical-context-v1·main/HEAD232737d 로컬 미커밋·미배포다.

다음은 이번에 없는 종목/타점·EV/Capacity/stress 조건과 모델 학습·선택·검증 계보를 기존 생산자에서 연결하고, 실제 현재 맥락에 대한 모델 지원 및 native 보호/실행 후속 정책의 적합성을 증명하는 것이다. 공통 시장/테마 관측이 붙었다는 이유만으로③ 전체 판단이나 모의계좌 운용 승인을 완료로 표시하지 않는다. 원래00 Position·Risk·Capacity/Liquidity의 상위 자동 비중과②/④ 수락·운용 승인 분리는 유지한다. [85단계](AI_TRADING_SYSTEM_REDESIGN.md#85-기존-연구-입력의-과거-순위테마-맥락-연결)

이하 단계 설명은 해당 시점의 기록이다.

## 2026-10-14 사용자 순서 ③ — 상위 EV 원본 출처와 R4 중복 확인

기존 readonly EV 준비에서 원래 연구 명세/report·이벤트를 같은 transaction/borrow로 함께 전달하는 선택 include_source_records=True 경로를 추가했다. 기본 False 결과/원래 outcome·비교·정책 ID와 availability/TTL은 동일하다. 기존 CLI 입력의 명시 옵션으로 native_source_publication을 준비하고 기존 v2 allocation 게시/공통 RAM 경로에서만 원래 결과와 재생 대조한다. 새 DB 읽기·writer/table/collector/계좌/TR/task/Manager 없음.

같은 기존 native source 검증기를 EV와 R4 study가 재사용한다. run/input manifest hash·개발 role/범위·보고된 final 제외·이벤트 content/time/정수·원래 endpoints·outcome/source receipt 전체를 확인한다. 4MiB·전체 event10000/outcome2000/bar2000 예산은 유지한다. v2에서 원본 proof가 명시됐으면 null/변형/다른 receipt는 거절하고 기존 활성 슬롯/세대는 유지한다. proof가 없는 기존 v1/v2는 지원되지 않은 계보를 합성하지 않는다.

재검증한 보고 출처끼리 run·입력 revision hash·warmup을 포함한 선언 기간·outcome 공유를 확인해 현재 goal에 선택 allocation_lineage를 연결한다. 격리 시험의 서로 다른 run/input hash도 선언 기간16쌍이 겹쳐 REPORTED_OVERLAP으로 표시했다. 기간 끝 경계는 보수적으로 포함한다. REPORTED_DISJOINT도 원시 revision 목록·상위 모델 학습/선택/검증 계보·독립 source custody가 없으므로 independence_verified=False다. 보고된 중복 진단을 실제 통계적 독립성 승인으로 바꾸지 않는다. 과거 exact 시장/테마 context와 native 보호·실행 후속 정책의 모델 지원은 아직 미증명이므로 R4 EV_UNAVAILABLE/result=None/order OFF, 사용자 순서③ 전체 판단은 미완료다.

신규7 포함 관련 고유38건·소비 감사delta0·기존 supplied v1 전체 예제/decision ID 동일을 확인했다. 명시 네 run 준비는 변경 전후 SELECT16/COMMIT0·borrow4·원본DB bytes 동일이다. 격리2/8 Shadow는 Collector/계좌 각1·상위1·DB/COMMIT/주문0, status/중복100회 추가 read/replay/evaluation0이다. 재생은 최초 게시 때만 수행하고 실시간에서는 동결한 작은 계보 진단만 복사한다. 실제 CLI opt-in/default와 기존 v1 게시/해제/drain을 실행했다. 새로운 proof의 인증 HTTP→실제 broker 활성/전송·00/04/REST·잔고/청산·PG/NAS/장중/CI/원격main은 검증하지 않았다. 전수 회귀 없음, catalog의 기존 과거자료 미등록5개는 보류다. build2026.10.14-position-ev-lineage-v1·main/HEAD232737d 로컬 미커밋·미배포다.

다음은 기존 source가 관측 당시 공통 시장/테마/종목 context와 상위 EV 모델의 학습·선택·검증 계보를 실제로 생산할 수 있는 경로를 확인·연결하고 native 보호/실행 정책 지원을 증명하는 것이다. 현재 숫자나 서로 다른 run 이름만으로 과거 상태/독립성을 인정하지 않는다. 원래00의 상위 Position·Risk·Capacity/Liquidity 자동 비중 정책과② 모의계좌 수락/④ 전체 통합·운용 승인 분리는 유지한다. [84단계](AI_TRADING_SYSTEM_REDESIGN.md#84-상위-ev-원본-출처-재생과-r4-연구-중복-확인)

이하 단계 설명은 해당 시점의 기록이다.

## 2026-10-14 사용자 순서 ③ — native Runner 체결·보호 상태 RAM 연결

기존 Runner가 실제 반영 체결로 유지하는 가중 진입가·잔여 수량·최초 보유 시각, 등록 family/parameters/session/implementation identity, 원래 보호 요청·session clock·native Risk 한도를 기존 계좌 RAM 관측 한 번에 연결했다. 별도 원장 조회·TR·DB/COMMIT·저장/계산 엔진이나 task를 추가하지 않았다. 기존 기본 PositionAccountObservation 모양은 보존하며 현재 등록 owner의 정합성이 확인된 경우에만 선택 subtype의 native-position-runner-state/v1을 제공한다. 주문 권한은 false다.

82단계의 '원가·기간/전략 상태가 없다'는 표현을 바로잡는다. Risk의 owned_entry_prices와 Runner의 StrategyState/opened_at는 이미 존재했으며 당시 현재 비교 경로에 전달되지 않았던 것이다. 이번 연결은 그 사실의 관측을 해결한다. 보호·실행 후속 정책이 연구 모델과 동등하다는 증명, 과거 공통 시장 맥락과 상위 EV 독립 lineage는 아직 생산되지 않았다. 등록 family/parameters/session/implementation hash가 맞는지 확인하더라도 그 추가 정책 지원을 인정하지 않는다. 현재 R4는 계속 EV_UNAVAILABLE/result=None/order OFF이고 사용자 순서 ③ 전체 판단은 미완료다.

원장 high-watermark·Risk 원가/수량·계좌 cut·run/revision/path와 owner registry/계좌 epoch를 확인한다. 체결 반영 중·오류/동시 writer·복사 중 상태/보호/session 변경·복구/종료 시에는 추가 사실을 버린다. writer metadata lock은 I/O 밖의 짧은 경계에만 두고 getter는 기다리지 않는 lock 검사로 종료한다. 기존 native Risk 보호 callback은 이 lock이나 Shadow/LLM/다음 봉을 기다리지 않는다. 최초 보호 시각·최초 quote·예산·자식 주문 ID를 다시 만들지 않는다. 관측 조회와 미변경 fill read로 state availability를 갱신하지 않는다.

신규 11건 포함 관련 고유 98건·소비 감사 delta0·기존 supplied v1 전체 예제/decision ID 동일을 확인했다. 격리 native getter100회는 원장/DB/COMMIT/주문0, 평균0.1127ms이며 원래 상태/availability가 같다. 2/8 Shadow 모두 Collector/계좌 RAM 각1·native getter1·상위 계산1·원장/DB/COMMIT/주문0, status100회 추가 계좌 조회/계산0이다. 동일 시장 입력 cache 재사용 frame3.737/3.814ms이고 cold baseline11.244/9.499ms와 cache 조건이 다르므로 실운영 지연 개선으로 일반화하지 않는다. 실제 app 계좌 활성 HTTP→broker 전송, 실제 00/04/REST·부분체결/잔고/청산, PG/NAS/장중·CI/원격 main은 검증하지 않았다. 전체 회귀 없음, catalog의 기존 과거자료 미등록5개는 별도 보류다. build2026.10.14-native-position-state-v1·main/HEAD232737d 로컬 미커밋·미배포다.

다음은 기존 연구 source에서 과거 공통 context/상위 EV 입력·검증 lineage와 native 보호/실행 후속 지원을 실제 증명할 수 있는 경로를 확인·연결하는 것이다. 없는 과거 사실을 현재 숫자로 채우지 않는다. 원래 00의 상위 Position·Risk·Capacity/Liquidity 동적 자동 비중과 ② 모의계좌 수락/④ 전체 통합·운용 승인 분리는 유지한다. [83단계](AI_TRADING_SYSTEM_REDESIGN.md#83-native-runner-체결보호-상태의-동일-계좌-ram-관측)

이하 단계 설명은 해당 시점의 기록이다.

## 2026-10-14 사용자 순서 ③ — v2 공통 RAM 게시/현재 목표·Risk 결합

81단계 native cohort의 원래 run 명세/report·이벤트를 동일 readonly transaction에서 함께 준비하고, 기존 PC client·인증 allocation-models endpoint·selection lock/세대 fence/owned drain/한 RAM 슬롯에 명시 v2로 게시한다. 최초 게시에서 같은 native source/case/cohort 검증기로 재생하고 원본 ID/availability/TTL을 보존한다. 기존 v1 EV/출구 receipt는 그대로 두며 혼합/부분 종목·변형 source/통계·다른 revision은 원자적으로 거절한다. 4MiB/전체 10,000 event·2,000 outcome/bar 예산은 늘리지 않았다.

공통 RAM에서 기존 상위 자동 비중→하위 목표와 같은 계좌 관측/ACK Risk를 공유해 현재 goal/Risk binding을 추가했다. 계좌·run/revision/path·원래 신선도/기한과 정확한 수량·가격/비용·sizing 의미를 확인하고 native Risk가 우선한다. 만료한 하위 목표/현재 결합은 조회에서 제거하며 모델 원래 기한은 갱신하지 않는다. 현재 관측에는 원가·보유기간/등록 전략 보호 상태가 없고 81단계 source에는 과거 공통 context·상위 EV 독립 lineage 증명이 없으므로 EV_UNAVAILABLE/result=None/order OFF다. v2 게시 완료를 현재 R4 사용/③ 전체 완료로 보고하지 않는다.

신규 8건 포함 관련 고유 63건·소비 감사 delta 0·기존 supplied v1 예제/ID 동일을 확인했다. 명시 PC 준비 네 run SELECT 16/COMMIT 0·원본 DB 동일로 81단계와 SQL 횟수가 같다. 격리 2/8 Shadow의 Collector/계좌 RAM 각 1회·상위 계산 1회·DB/COMMIT/주문 0, 100회 중복/status의 추가 조회/계산 0이다. 인증 HTTP/ASGI v2 최초/중복/revision/해제와 기존 v1 게시·취소 drain/보호 우선 회귀를 실행했다. 전체 HTTP 72묶음/전수 회귀·실제 계좌 활성/전송·PG/NAS/장중·CI/원격 main 검증은 하지 않았다. 신규 CI 등록과 core fingerprint를 유지했고 catalog의 기존 과거자료 미등록 5개는 별도 보류다. build 2026.10.14-position-study-publication-v2·main/HEAD 232737d 로컬 미커밋·미배포다.

다음은 기존 native owner/연구 source에 필요한 보호·후속 상태 및 과거 공통 시장 맥락·상위 EV 입력/검증 lineage 증명을 연결하는 것이다. 없는 상태를 합성하거나 현재 숫자만 과거에 붙이지 않는다. 원래 00의 상위 Position·Risk·Capacity/Liquidity 자동 비중 정책과 ② actual 00/04/REST/잔고·청산 수락, ④ 전체 통합/운용 승인 분리는 유지한다. [82단계](AI_TRADING_SYSTEM_REDESIGN.md#82-v2-연구-근거의-공통-ram-게시와-현재-목표risk-결합)

이하 단계 설명은 해당 시점의 기록이다.

## 2026-10-14 사용자 순서 ③ — v2 native 짝 모집단/통계 구현

79단계의 두 번째 구현으로 사전 case/HOLD·행동 목록을 각 native run의 시작 명세에 고정하고, 실제 Paper의 HOLD+ADD/REDUCE 결과를 기존 readonly source guard·가치/수수료 adapter와 공통 짝 산술로 비교한다. 원래 outcome·정책/실행 ID와 당시 목표 TTL은 보존한다. 짝의 실제 포트폴리오/전략 상태·source bar/목표가 같아야 하며, case 창은 MARK 끝점을 공유하지 않는다. 선언한 case가 누락·미완료·미지원이면 성공 표본만으로 평균을 만들지 않고 전체 cohort EV를 미확인으로 반환한다.

지원된 완결 모집단의 연구 상태는 COHORT_COMPARABLE다. 이는 역사적 목표 규칙의 조건부 경험 통계이며 상위 자동 비중 정책 전체의 성과나 현재 타점 지원 증명은 아니다. exact context·상위 EV/검증 lineage 독립성·현재 계좌/Risk binding이 아직 없으므로 current_goal_status=EV_UNAVAILABLE/usable_for_current_goal=false/order_authorized=false다. v2 EXIT/flat REENTER와 partial/보호 개입 창의 확대, 명시 v2 인증 게시/공통 RAM 연결은 후속이다. 최종 비중 정책은 기존 00의 Position·Risk·Capacity/Liquidity를 따르는 상위 자동 결정이며 연구 목표금액은 하위 sizing 기능이다.

신규 12건 포함 관련 고유 81건·소비 감사 delta 0·기존 supplied v1 output/ID 동일을 확인했다. 명시 두 case/네 run 준비 SELECT 16·BEGIN/ROLLBACK 각 4·COMMIT 0·원본 SQLite bytes 동일이고 실시간 반복 호출을 추가하지 않았다. 신규 모듈은 기존 연구 profile/required Windows CI에 등록했으나 전체 catalog의 기존 과거자료 미등록 5개는 별도 보류로 남는다. 전수 회귀·HTTP 묶음·실제 주문/활성·PG/NAS/장중 수락·④ 통합/운용 승인·CI/원격 main 확인은 실행하지 않았다. build 2026.10.14-position-study-cohort-v2·main/HEAD 232737d 로컬 미커밋·미배포다. [81단계](AI_TRADING_SYSTEM_REDESIGN.md#81-v2-native-짝-모집단-동결과-공통-통계)

이하 단계 설명은 해당 시점의 기록이다.

## 2026-10-14 사용자 순서 ③ — v2 연구 규칙/case native 증명 구현

79단계 인계의 첫 구현을 완료했다. 기존 position_target.py의 typed v2 study/case 계약은 sizing 의미·등록 family/설정·실행/cost/session·실제 scientific hash를 안정적인 study ref에 묶고, 원래 순간별 요청/TTL은 별도로 유지한다. 기존 research_run_identity/execute_research의 명시 opt-in에서 같은 run spec에 study/case를 동결하고, 같은 Paper/원장 writer가 실제 source bar·포트폴리오/전략 상태·목표 계산의 POSITION_STUDY_CASE 증명 한 건을 기록한다. native readonly reader는 기존 source guard와 가치/비용 adapter를 재사용해 원래 outcome/정책 ID를 보존한다.

실제 native 연구 run에서 30초 목표의 180초 완결 결과를 원래 TTL 연장 없이 읽고, 독립 HOLD에는 목표 주문이 없는 것을 검증했다. 이것은 NATIVE_CASE_PROOF이며 짝 모집단/현재 exact context 지원 증명이 아직 없으므로 R4는 EV_UNAVAILABLE/주문 OFF다. 현재 공통 RAM에 v2를 게시하지 않았다. 다음은 자체 독립 native cohort/같은 짝 통계, 이어 기존 인증 게시/공통 RAM 연결이다.

관련 고유 149건(신규 10건)·기존 supplied v1 예제/ID 동일·소비 감사 delta 0을 확인했다. 기존 연구 101건/118.869초와 수량/R4/비중 호환 46건/0.110초, 최종 신규 경계 검증을 실행했고 전체 회귀/기존 HTTP 묶음은 반복하지 않았다. 단일 명시 native 준비는 run borrow 1·SELECT 4·BEGIN/ROLLBACK 각 1·COMMIT 0·원본 SQLite 동일이다. 이 비용은 실시간 반복 판단 비용이 아니다. source_durability=not_verified/order_authorized=false·실제 계좌/PG/NAS/장중 수락·④ 전체 모의 통합/운용 승인은 별도다. build 2026.10.14-position-study-native-v2·main/HEAD 232737d 로컬 미커밋·미배포다. [80단계](AI_TRADING_SYSTEM_REDESIGN.md#80-v2-연구-규칙순간별-case-계약과-native-증명-첫-구현)

이하 단계 설명은 해당 시점의 기록이다.

## 2026-10-14 사용자 순서 ③ — R4 연구 정책/판단 TTL 분리 설계 완료

Astra High에서 78단계 다음 연결의 계약을 확정했다. 안정적인 연구 규칙 ID와 원래 순간별 목표/계좌·Risk 증명, 완결 근거 ID를 분리한다. 과거 목표가 당시 유효했다면 TTL 뒤 완결된 outcome을 연구할 수 있지만, 현재 사용에는 새 목표의 원래 짧은 TTL·freshness·정확한 study/수량·가격/비용·실행·후속 정책/지원 상태 검증이 필요하다. 기존 v1 ID/비교 조건을 바꾸거나 기존 표본의 정책을 재표지하지 않고 명시 v2로 추가한다.

**설계만 완료했고 구현은 대기 중이다.** 현재 RAM의 새 동적 목표 R4는 계속 EV_UNAVAILABLE/주문 OFF다. 다음 Sol High 구현은 ① v2 식별·시간/native proof ② 독립 native cohort/공통 짝 비교 ③ 기존 인증 게시/공통 RAM 연결 순서다. 코드 경로와 고정 예제의 30초 TTL/180초 horizon 충돌을 재확인했으며 새 기능 테스트/전체 회귀는 실행하지 않았다. source build allocation-model-publication-v1·main/HEAD 232737d·누적 미커밋/미배포 상태는 유지한다. 실제 모의 수락·④ 전체 통합/운용 승인은 별도다. [79단계 설계·구현 인계](AI_TRADING_SYSTEM_REDESIGN.md#79-동적-목표의-연구-정책과-순간별-판단-유효기간-분리)

## 2026-10-14 사용자 순서 ③ — native 모델 공통 RAM 게시·목표 수량 연결

기존 PC readonly 연구 준비의 배분 EV·전체 출구 Capacity/스트레스 결과를 인증된 POST /api/v1/research/ram-shadow/allocation-models로 기존 공통 MarketThemeShadowRuntime의 RAM 슬롯 하나에 게시한다. 기존 선택 revision/계좌·run/binding/path·정책 내용/종목·가격/현재·검증 수량·동결 cost/horizon·원래 availability/expiry를 결합한다. native 짝 EV 통계와 같은 Paper 출구 시나리오를 최초 게시에서 재생 검증하고, 동일 패킷 재전송/status는 재생·계좌 조회·깨우기·TTL 갱신이 없다. 선택 교체/해제·재시작은 슬롯을 복구하지 않으며 이전 worker가 새 세대 결과를 덮어쓰지 못한다.

기존 Collector/공통 시장·테마/ACK된 계좌 Risk 사실이 유효하면 상위 자동 비중→기존 하위 목표 수량을 공통 frame에서 한 번 계산한다. 가격·계좌/Risk binding·source TTL이 바뀌거나 근거가 부족하면 BLOCKED/미확인으로 남기며 만료한 목표 결과는 조회에 노출하지 않는다. 새 동적 목표 정책의 R4 행동 비교는 자체 완결 모집단이 필요하므로 EV_UNAVAILABLE다. 기존 정책의 EV를 새 정책으로 재표지하지 않는다. PC_READONLY_REPLAY_REPORTED·source_durability=not_verified·order_authorized=false이며 DB 원본/시장 확률·운영 Risk/주문 승인을 뜻하지 않는다.

관련 고유 96건(신규 8건) 통과 후 최종 영향 15건 및 입력/종료 경계 2건을 확인했다. 격리 2/8 Shadow에서 게시 후 Collector/계좌 RAM 각 1회·상위 계산 1회, DB 연결/COMMIT/주문/구독 0이며 중복/status 100회 추가 계산/조회 0이다. 최초 시장 계산은 기존 cache에 공유하고 게시만으로 재계산하지 않는다. 원본 자료 준비의 SELECT 16(EV)/4(출구)·COMMIT 0은 앞 단계의 별도 비용이다. 전체 회귀/기존 HTTP 72시나리오는 반복하지 않았다. 소비 감사 delta 0·기존 supplied v1 예제/ID 동일, 관련 없는 과거자료 catalog 5개 보류 유지다. build 2026.10.14-allocation-model-publication-v1·main/HEAD 232737d 로컬 미커밋·미배포·실제 계좌 활성/주문 없음.

다음 개발은 생성된 동적 목표 정책에 일치하는 독립 native 모집단과 R4 행동 비교의 주문 없는 통합이다. actual 모의 00/04/REST·부분체결/취소·잔고/청산 수락과 ④ 전체 모의 통합·별도 운용 승인은 남는다. ② 운용 수락 대기는 ③ 개발의 선행 조건이 아니다. 아래 단계별 설명은 각 시점의 기록이며 현재 미완료 범위는 이 절을 따른다. [78단계](AI_TRADING_SYSTEM_REDESIGN.md#78-준비된-native-모델의-공통-ram-게시와-상위하위-목표-연결)

## 2026-10-14 사용자 순서 ③ — 전체 출구/스트레스 연구 근거

기존 PaperExecutionEngine의 부분 SELL 원가/잔고/수수료 경로로 전체 잠재 보유의 출구 Capacity·스트레스 연구 시나리오를 구현했다. 참여율·volume haircut·가격 충격·추가 슬리피지·체결 불가 구간/기간은 전부 명시 정책이다. 기존 guarded native readonly reader에서 시장 bar proof 창을 한 번 읽어 재사용하며 WAIT/REPRICE 성과를 청산 근거로 재표지하지 않는다. 모든 서로 다른 시나리오에서 전체 수량이 완료된 경우만 검증 수량까지 근거를 만들고, 부분/불가/누락/미래/비용 만료는 미확인이다.

상위 비중은 실제 보유+pending BUY 전체·가격·비용·출구 horizon·스트레스 값·원래 TTL을 묶으며 pending SELL로 위험 여유를 만들지 않는다. 기존 native EV와 두 출구 근거를 함께 소비하는 순수 주문 없는 계산도 검증했다. 이는 명시한 역사/충격 가정의 연구 결과이며 미래 유동성/확률·실제 청산 보장·Risk/주문 승인이 아니다. 세 producer의 공통 RAM 게시/실시간 계좌 상위→하위 통합은 다음 단계이며 현재 native RAM result=None/BLOCKED는 유지한다. actual 00/04/REST/잔고/청산 수락과 운용 승인은 별도다.

관련 고유 125건(신규 16건) 및 기존 supplied v1 봉인 예제/ID·소비 감사 delta 0 pass. 명시 준비 SELECT 4·COMMIT 0/1 run·원본 DB 동일, 준비된 native EV/출구·스트레스의 pure preview 101회 DB/주문 0이다. 전체 회귀/기존 HTTP 72시나리오는 반복하지 않았다. 관련 없는 과거자료 catalog 5개 보류 유지. build 2026.10.14-full-exit-stress-v1·main/HEAD 232737d 로컬 미커밋·미배포·실제 계좌 활성/주문 없음. [77단계](AI_TRADING_SYSTEM_REDESIGN.md#77-전체-보유-출구-capacity와-명시-스트레스-연구-근거)

## 2026-10-14 사용자 순서 ③ — 배분 EV native 연구 공급

기존 native 연구 원장 reader/PaperExecutionEngine/R4 paired 비교를 재사용해 ADD·REENTER 대 독립 HOLD의 배분 EV를 명시 readonly 준비하는 경로와 기존 preview CLI를 연결했다. 현재가·보유/목표 수량·비용·horizon·완결 TRAIN/VALIDATION 짝·원래 availability/expiry를 묶는다. 순 차이는 추가 매수 대금 기준 Fraction 평균 내림/표준오차 올림이며 calibrated 확률/신뢰구간이 아니다. 상위 비중 계산은 검증된 정확한 BUY 수량만 허용하고 더 큰 비중·부분 현금/위험 수량에 외삽하지 않는다. 불리한 ADD EV를 기존 보유의 EXIT 근거로 바꾸지 않는다.

이 producer는 실시간 루프 밖의 PC readonly 연구 준비다. 현재 공통 RAM에는 게시하지 않았고 전체 출구 Capacity·스트레스 모델도 미공급이므로 native 비중 결과는 계속 None/BLOCKED다. 순수 preview의 supplied Capacity/스트레스를 native 검증으로 보고하지 않는다. 다음은 기존 공통 실행 모델에 전체 청산/스트레스 연구 시나리오를 명시해 크기·시간·유동성·비용 범위를 검증하고, 세 근거의 RAM 게시와 계좌 상위→하위 통합을 연결하는 것이다. 실제 모의 00/04/REST 수락·운용 승인은 별도다.

관련 고유 118건(신규 12건)·최종 통계 영향 49건·기존 RAM/API 14건 및 봉인된 supplied v1 예제/decision ID 대조·소비 감사 delta 0 pass다. readonly 부하는 76단계 및 data/diagnostics/20261014-allocation-native-ev에 기록한다. SELECT 16·COMMIT 0/4 run을 명시 준비에서만 수행했고 pure preview 101회 DB/주문 0·원본 DB 동일이다. 전수 회귀·실제 주문·계좌 활성·배포·commit/push 없음. build 2026.10.14-allocation-native-ev-v1·main/HEAD 232737d 로컬 미커밋이다. [76단계](AI_TRADING_SYSTEM_REDESIGN.md#76-배분-ev의-native-연구-공급과-검증-크기-제한)

## 2026-10-14 사용자 순서 ③ — 공통 RAM 사실/native Risk 연결

2026-10-14 동적 비중의 실제 사실 입력을 기존 공통 Shadow runtime에 연결했다. 명시 position_allocation 선택에서 승인 KRX RAM 현재가/0B 시총·기존 창 대금/관측 종가 RMS·공통 TOP20/테마/종목 맥락과 ACK된 native 계좌/Risk를 한 번 빌린다. 기존 R4 비교와 같은 계좌 read를 공유하고 변환은 같은 owned drain에서 수행해 native 손절을 기다리게 하지 않는다. 조회/재시작으로 원래 source TTL을 갱신하지 않는다.

현재 배분용 독립 EV·전체 출구 Capacity·스트레스 모델 producer는 없으므로 result=None/BLOCKED다. 미체결 EV/leadership 점수·관측 대금을 필요한 근거로 재표지하지 않으며 목표 0을 합성하거나 새 주문을 승인하지 않는다. stage74의 native 전체 목표 계산이 완료된 것으로 보고하지 않는다. source_durability=not_verified/order_authorized=false 유지다.

기존 119건/최종 신규 14건=고유 133건·기존 HTTP 72시나리오/실제 lifespan·health/build·소비 감사 delta 0 pass. 2/8 Shadow 모두 Collector RAM 1·계좌 RAM 1·시장 계산 1·입력 변환 1, DB/COMMIT/추가 주문·구독 0; 조회 100회 추가 계산 0이다. 기존 전체 회귀는 반복하지 않았으며 과거자료 catalog 5개 보류 유지다. 다음은 세 모델의 크기/시점/비용/horizon 결합과 계좌 전체 상위→하위/R4 주문 없는 통합이다. 실제 모의 수락/운용 승인은 별도다. build dynamic-allocation-ram-v1·HEAD 232737d 미커밋, 이번 작업에서 배포/계좌 활성/실제 주문은 하지 않았다. [75단계](AI_TRADING_SYSTEM_REDESIGN.md#75-동적-비중의-공통-ram-사실과-native-계좌-risk-연결)

## 2026-10-14 사용자 순서 ③ — 동적 비중 계산 시작

2026-10-14 실제 모의 수락 준비를 인증 GET/선택 NAS 릴리즈 지문으로 확인했다. NAS recorder-shared-10m-v1은 관련 5파일이 개발 소스와 다르고 mock 계좌는 MANUAL·automatic control/Runner 없음·계좌별 주문 OFF·native 원장 0건이다. 실제 주문/취소·활성/설정·배포 변경은 하지 않았다. ② actual 00/04/REST·부분체결/잔고/청산 수락은 남긴다.

사용자 지시대로 운영 수락 대기와 개발을 분리해 ③ 동적 비중의 첫 주문 없는 계산을 구현했다. 기존 position_target.py에서 계좌 운용 한도/현재 현금·보유·미체결을 바탕으로 시장·테마·종목 강도/순 EV·불확실성·시총·거래대금·출구 수용량·변동성·스트레스 위험으로 자동 목표 비중/금액을 결정하고 기존 하위 수량/비용 정책과 preview CLI를 재사용한다. 미체결 매도에 현금/위험 여유를 주지 않고 겹치는 테마·현재/제안 포트폴리오를 모두 검사한다. 수동 종목 투자금 입력·임의 운용 계수/한도·신용 활성 없음. supplied 실험용 정책으로 native 입력/운영 승인은 아니다.

관련 고유 103건(신규 21건)·기존 HTTP 72시나리오/실제 lifespan·health/build·CLI 동일 결과·소비 감사 delta 0 pass. 200종목 controlled 계산 33.337ms·DB/COMMIT/주문 0, source TTL 갱신 없음. 전체 회귀/직전 보호 묶음은 반복하지 않았다. 다음은 실제 공통 RAM의 시장/테마·EV·Capacity·native Risk 입력 연결과 R4 계좌 전체 목표/행동 비교의 주문 없는 통합이다. ② 수락 준비 대기는 ③ 개발의 선행 조건이 아니다. build dynamic-allocation-shadow-v1·HEAD 232737d 미커밋·미배포다. [74단계](AI_TRADING_SYSTEM_REDESIGN.md#74-실제-모의계좌-수락-준비-확인과-상위-동적-비중-계산-시작)

## 2026-10-14 사용자 순서 ① 보완 — 즉시 종목 손절

2026-10-14 기존 동결 unfilled execution 정책을 선택한 자동 모의 Runner에 즉시 종목 손절 producer를 연결했다. 상세 FILL 기반 owned_entry_prices·기존 정수 이동평균과 승인 후보 stop_loss_bps를 사용하고, Collector 승인 KRX RAM→기존 Supervisor→Runner의 가격 비교/한 번 wake로 봉·Shadow·LLM/EV를 기다리지 않는다. 최초 손절 가격·시점은 고정하고 현재 계좌 ACK/상세 체결·owner/control/native QUEUED에서 원가·수량·candidate/hash/정규 한도를 재검사한다. 일반 cue는 review 전용, UNKNOWN/취소 미확정의 중복 제출 금지는 유지한다. 오래된 계좌는 기존 recovery queue로 대사하고 가격 만료는 BLOCKED/운영자 주의다.

관련 280건(신규 18건)/101.808초와 기존 HTTP 72시나리오·실제 ASGI lifespan/health/build pass. 정상/손절 중복 1,000틱 각각 SELECT·COMMIT·전송 0, 최초 RAM cue 0.395ms이다. 현재 계좌 ACK 후 첫 보호 승인/원장/가짜 전송 전체는 SELECT 80·COMMIT 18·13.587ms이며 보호 대기 10회 SELECT 20·COMMIT 0이다. 실제 REST/시장/NAS 지연은 별도다. 전체 회귀는 반복하지 않았고 소비 감사 delta 0, 과거자료 catalog 5개 보류 유지다.

실제 계좌 활성/전송·00/04/REST·PG/NAS/장중 수락과 운용 승인은 남는다. 다음은 실제 모의 부분체결/미체결/잔고 수락, 이어 원래 Position·Risk·Capacity/Liquidity의 상위 동적 비중·전체 통합 순서다. 운용 승인 대기는 동적 비중 개발을 막지 않는다. build 2026.10.14-immediate-native-stop-v1·main/HEAD 232737d 미커밋·미배포다. [73단계](AI_TRADING_SYSTEM_REDESIGN.md#73-승인된-collector-ram-가격의-즉시-종목-손절과-native-보호-exit)

## 2026-10-14 사용자 순서 ② — PC native 원장 결과의 인증 게시

PC CentralContentClient의 명시 prepare_native_unfilled_population으로 기존 readonly 연구 원장/공통 재생을 한 번 검증하고, 같은 클라이언트의 publish_native_unfilled_population이 준비된 패킷을 기존 인증 POST 경계로 보낸다. 기존 mock 라우트·Supervisor·Runner의 계좌 lock/owned drain/단일 RAM 접수를 재사용한다. 서버에 DB 경로를 보내거나 계좌를 활성화하지 않는다.

원래 native 결과의 최대 available_at과 내용 hash를 고정하며 HTTP 재전송/계좌 ACK/재시작으로 갱신하지 않는다. 계좌·profile/spec/run/control·원주문/known child·동결 비용/horizon/표본·같은 native 정책/모델·완결 짝/가치·1MiB/200창/10,000 event budget을 검사한다. 새 패킷에는 현재 native RUNNING control 한 번을 확인하고 같은 패킷의 inert 중복은 새 조회/깨우기/슬롯 교체가 없다. 등록 전환과 RAM 교체는 기존 input lock 안에서 검사한다. 실제 주문/취소는 계속 기존 현재 계좌/Risk/브로커·native owner/control transaction 검사를 통과해야 한다. 보호 요청은 source decode를 기다리지 않고 STOP 직후 아직 Runner poll 전인 새 패킷도 차단한다. 상태에는 원래 출처 요약만 optional 노출하며 raw outcomes/출처를 checkpoint로 복구하지 않는다.

관련 153건/145.093초 통과 후 최종 변경 영향 신규 15건/28.666초를 확인했다(고유 154건). 기존 start/stop/resume/status HTTP 72시나리오를 지원되는 httpx ASGITransport와 실제 lifespan에서 원래 baseline과 대조했고 health/build도 확인했다. 전수 회귀/기존 통과 묶음을 반복하지 않았고 httpx2를 설치하거나 import를 위장하지 않았다. 격리 SQLite/ASGI에서 첫 새 게시 SELECT 1·COMMIT 0·7.143ms, 중복 HTTP 100회 SELECT 0·COMMIT 0·257.041ms이다. 추가 취소/제출 0·한 RAM 슬롯/가용시각·source generation 유지이고 실제 공통 실행 결과 두 짝을 기존 계산에 연결해 순 차이 -12원을 확인했다. PC 자료 생성/읽기·계좌 Risk 생산·실제 네트워크/시장 지연은 이 측정 밖이다. 소비 감사는 367 reviewed/37 후보/107 계약/60 forwarding pass이며 기존 repository의 native control bound reference 1개만 검토·baseline에 추가했다. 별도 과거자료 catalog 5개 보류 유지다. [72단계](AI_TRADING_SYSTEM_REDESIGN.md#72-pc-native-원장-결과의-인증-게시와-기존-runner-ram-접수).

PC의 readonly/replay 검증 보고이며 서버의 시장 원본/호가/체결 확률/취소 지연 승인이 아니다. PC_READONLY_REPLAY_REPORTED·source_durability=not_verified·order_authorized=false를 유지한다. native v1은 ACCEPTED 분봉 자료만 지원하고 현재 PARTIALLY_FILLED/CANCELLED/UNKNOWN에서는 EV 미확인(native_unfilled_model_state_unsupported)이며 이전 cohort를 재표지하지 않는다. 실제 00/04/REST·PG/NAS/장중 수락·부분체결/CANCELLED 모집단/취소 지연 보정은 남는다. 프로그램 호출 경로를 구현했으며 UI 버튼/자동 연구 예약·게시 스케줄러는 추가하지 않았다. 새 writer/table/collector/Manager/독립 background task 없이 기존 입력/제어 소유자를 사용한다.

build `2026.10.14-unfilled-source-publication-v1`, main/HEAD `232737d` 미커밋·미배포/계좌 미활성이다. 다음 우선 보완은 종목별 즉시 native 손절 producer이며 기존 Risk→Runner 보호 경로를 재사용한다. 이후 실제 모의 00/04/REST 수락을 별도 다루고 ③ 원래 Position·Risk·Capacity/Liquidity 상위 동적 비중 → ④ 모의 통합/별도 운용 승인 순서를 유지한다. 운용 승인을 동적 비중 개발의 선행 조건으로 두지 않는다. 아래 71단계 이하의 publisher 미연결은 당시 상태다.

## 2026-10-14 사용자 순서 ② — 공통 지정가 연구 실행·native 결과 읽기

기존 PaperExecutionEngine에 명시 unfilled_action_plan의 SUBMIT→WAIT/REPRICE 지정가 연구 분기를 연결하고 같은 연구 원장 writer를 재사용했다. native readonly 개발 구간/최종평가·내용 hash/cut/예산 guard 뒤에 같은 실행기로 가격·현금·예약·비용·취소/재주문 순서를 재생해 UnfilledActionOutcome을 생성한다. 기본 next-open 실행과 기존 HOLD/EXIT/자동 목표 자료는 유지한다.

현재 v1은 KRX 완결 분봉의 이후 시가·슬리피지·전체 수량/거래량 조건만 사용하는 연구 모델이다. SUBMIT으로 실제 paper pending owner를 만들고 두 분기는 같은 초기 상태/기한·원래 가격·목표·입력/비용/코드 출처에 결합한다. 가격 접촉만으로 체결하지 않고 취소 확정 사건을 새 paper 주문 앞에 기록한다. 초기 평가는 분봉 종료시각, 취소/제출은 실제 입력 가용시각이므로 판단 시각을 소급하지 않는다. 매수·익절 매도 ACCEPTED 시나리오만 native 결과로 생성한다. 취소 지연 0/전체 체결은 명시 연구 가정이며 실제 broker 확인을 대체하지 않는다.

관련 6모듈 고유 266건의 근거를 대조했다. 초기 제한 실행의 성공 99건 뒤 중단하여 잔여 묶음 120건/135.554초와 미실행 64건/24.719초만 완료하고, 최종 변경 영향 신규 19건/43.142초를 확인했다. 중복은 고유 건수에서 제외하며 전체 회귀는 반복하지 않았다. 초기 신규 fixture는 폐쇄 거래/평일 요건을 충족하도록 수정했고 운영 guard를 완화하지 않았다. 격리 SQLite의 4개 source run/2개 짝 읽기는 SELECT 16·COMMIT 0·BEGIN/ROLLBACK 각 4회·12.657ms다. 이는 완료된 오프라인 연구 원장 읽기이며 실시간 사건/독립 계좌 producer/실제 시장 지연 측정이 아니다. Runner와 order_lifecycle 소스 SHA는 70단계와 동일하며 새 실시간 DB/TR/task는 없다. ASGI health/build·소비 감사 pass(367/36/107/60, delta 0). [71단계](AI_TRADING_SYSTEM_REDESIGN.md#71-기존-연구-실행기의-지정가-분기와-native-미체결-결과-읽기).

실시간 원장 입력 게시 caller·CANCELLED/부분체결 모집단 생성은 후속이며, 실제 호가 순서·취소 지연·부분체결 확률·시장 원본 lineage는 미검증이다. source_durability=not_verified와 주문 승인 false를 유지한다. 실제 00/04/REST·종목별 즉시 native 손절 producer/수락·PG/NAS/장중 운용 승인도 남는다. 프로그램 API의 opt-in 계획/명시 원장 선택이며 자동 연구 예약/실시간 공급은 없다. 모델 근거는 controlled 분봉을 기존 Runtime/SQLite에 실제 실행한 결과이고 실제 시장 모집단 수락은 아니다. 새 schema/table/writer/Manager/수집기 없이 오프라인 분기별 재생 proof를 기존 execution event JSON에 저장하므로 연구 기록 건수/저장량은 증가한다. 시장자료 수집을 재사용하며 오프라인 branch별 모델 입력 proof의 저장량 증가는 별도로 기록한다.

build `2026.10.14-unfilled-native-population-v1`, main/HEAD `232737d` 미커밋·미배포/계좌 미활성. 별도 과거자료 catalog 5개 보류는 유지한다. 다음은 검증한 원장 결과를 기존 bounded RAM 접수에 한 번 게시하는 연결과 미지원 상태의 명시 처리다. 이후 ③ 원래 Position·Risk·Capacity/Liquidity 동적 비중 → ④ 전체 모의 통합/별도 운용 승인 순서를 유지하며, 운용 승인을 비중 개발의 선행 조건으로 두지 않는다. 아래 70단계 이하의 생성기/reader 없음은 당시 상태다.

## 2026-10-14 사용자 순서 ② — WAIT/REPRICE 입력·비용·익절 판단 연결

기존 공통 짝 비교에 별도 UnfilledActionOutcome(WAIT/REPRICE) 입력 계약을 연결했다. HOLD/EXIT를 변환하지 않고 같은 초기 상태/시각·주문 상태·수량/가격/원래 목표·비용 모델·기간의 완결 자료만 비교한다. 비용 차이를 한 번만 차감한 평균/짝 표준오차를 기존 UnfilledRepriceEvidence로 전달한다. 선택된 동결 명세의 allow_supplied_paired_mock_evidence가 true인 경우만 하나의 bounded RAM 모집단을 받으며 1MiB/10,000행 상한·내용/순서/범위를 검사한다. **실제 모집단 생성기·native 출처 검증 reader는 아직 없고, 현재 명시 공급된 실험 자료는 source_durability=not_verified다.** 자료가 없으면 EV 미확인, 실제 미래 기대수익/운용 승인이 아니다.

현재 비용은 기존 동결 후보 execution_model의 유효기간/출처가 있는 모델을 재사용하며 실제 비용 완결 여부는 현재 계좌 Risk에서 계속 검증한다. 익절의 대기는 원래 승인 EXIT와 기존 등록 전략의 실제 새 bar HOLD/EXIT 판단을 사용한다. 별도 추세 엔진이나 틱별 재분석은 없고 실제 FILL 상태를 변경하지 않는다. 전략이 알려진 손절/기한 EXIT를 내면 기존 보호 요청으로 먼저 처리하며 계좌 native Risk의 빠른 경로는 분봉/EV를 기다리지 않는다. 모든 종목별 즉시 손절 trigger를 완성했다는 뜻은 아니다.

새 주문의 판단 cut은 실제 ACK Risk 관측에 맞추고 EV/가격/판단 발표가 뒤면 원래 입력/가격을 보존해 기존 복구 큐를 한 번 요청한다. 같은 업무 내용인 건강한 ACK도 이 대기만 깨운다. 보호 취소는 원래 현재 가격/REST 직전 재검증을 유지해 새 EV 정렬을 기다리지 않는다. 취소 전 모집단은 CANCELLED 이후 판단에 쓰지 않으며 현재 상태의 새 짝 입력이 필요하다. 원래 목표/기한/예산·UNKNOWN/취소 확정 전 중복 제출 금지를 유지한다.

관련 7모듈 458건/270.028초 중 456건 통과·기존 미확인 표시/보호 가격 준비의 회귀 2건을 수정한 뒤 영향 경계 62건/50.298초 통과(신규 26건·최종 고유 459건). 전수 회귀는 반복하지 않았다. 격리 SQLite·합성 짝 자료/가짜 REST·broker로 익절 WAIT, 취소 한 번, 취소 확정 후 별도 새 자료로 3주/990원 child 한 번과 보호 우선·ACK/wake/복구를 확인했다. 접수 101회 DB 0·첫 사건 짝 계산 1회/유휴 10회 SELECT 20·COMMIT 0이다. 사건 자체 취소 72/16(22.977ms), child 114/18(22.649ms)의 비용은 남는다. 독립 계좌 producer 비용/실제 시장 지연과는 다른 측정이다. [70단계](AI_TRADING_SYSTEM_REDESIGN.md#70-waitreprice-짝-입력동결-비용익절-판단의-공통-연결).

build `2026.10.14-unfilled-paired-inputs-v1`, main/HEAD `232737d` 미커밋·미배포/계좌 미활성. source/ASGI health/build·소비 감사 pass(367 reviewed/36 후보/107 계약/60 forwarding, 변화 0). DB schema/collection/writer/collector·별도 task/Manager 추가 없음. 별도 과거자료 5개 catalog 보류는 유지한다. 실제 계좌 00/04/REST·PG/NAS/장중·전체 회귀/CI·commit/push·활성화는 하지 않았다.

다음 ② 보완은 **실제 WAIT/REPRICE 모집단의 기존 연구 Runtime/원장 기반 생성·native 출처/개발 구간 검증**이다. 종목별 즉시 native 손절과 실제 계좌 수락도 남는다. 이후 ③ 원래 Position·Risk·Capacity/Liquidity 상위 동적 비중 → ④ 모의 통합·별도 운용 승인 순서를 유지하며 운용 승인을 동적 비중 개발의 선행 조건으로 두지 않는다. 아래 기록의 입력 미연결/다음은 당시 상태다.

## 2026-10-14 사용자 순서 ② — 취소 거절 이후 제한된 보호 대응

보호 매도만 마지막 native 취소 응답이 **CANCEL_REJECTED**이고 이후 실제 브로커 확인·전체 계좌/Risk·현재 가격/슬리피지가 검증되면 기존 취소 경로로 다시 요청한다. 새 모의 운용 명세의 maximum_protective_cancel_retries(0–3)·protective_cancel_retry_delay_seconds에 명시한 경우만 허용하며 **기본 0/OFF**와 기존 명세/정책 ID는 유지한다. 취소 응답 불명·승인/확인 대기·응답 유실은 재전송하지 않는다. 취소 확정 전 새 주문과 UNKNOWN 중복 제출도 금지한다.

원주문 계보 전체의 실제 CANCEL_STARTED가 재시도 예산이며 자식 주문·재시작·ACK 유실로 초기화하지 않는다. 같은 native transaction이 현재 거절 ID/시도·계보/동결 정책·현재 Risk/브로커 cut·owner/control을 검증하고 증거를 기존 event JSON에 저장한다. 원래 보호/관리 기한이나 한도 도달은 ESCALATE/운영자 확인이다. BUY/익절은 기존 정책이며 보호 판단은 EV/LLM/분봉을 기다리지 않는다. 기한의 복구 요청은 기존 Monitor 큐에 한 번 합친다.

관련 10모듈 348건/202.691초 중 347건 통과·신규 경합 fixture 입력 오류 1건을 수정한 뒤 신규 19건/17.685초와 마지막 실제 큐 ACK 경계 1건/1.014초, 최종 보호 기한 변조/정상 커서 2건/1.597초 통과(신규 고유 21건). 최종 고유 범위 351건이며 전수 회귀를 반복하지 않았다. default OFF 대비 명시 1회 정책의 첫 취소 거절은 SELECT 74/COMMIT 17로 같고, 이후 실제 재시도 사건은 78/16(19.778ms), idle 10회는 20/0이다. 격리 SQLite/가짜 REST·broker 값이며 독립 계좌 producer 비용과 실제 시장 지연은 별도다. [69단계](AI_TRADING_SYSTEM_REDESIGN.md#69-취소-거절불확실-이후-bounded-보호-대응).

build `2026.10.14-protective-cancel-recovery-v1`, main/HEAD `232737d` 미커밋·미배포/계좌 미활성. source/ASGI health/build·소비 감사 pass(367 reviewed/36 후보/107 계약/60 forwarding, 변경 0). PG는 격리 동일 cursor/row-lock 경로만 확인했으며 실제 PG·모의계좌 00/04/REST·NAS/장중·전체 회귀/CI·commit/push·주문 활성화는 하지 않았다. 별도 과거자료 catalog 미등록 5모듈은 유지한다.

다음 ② 개발은 WAIT-vs-REPRICE 짝 EV/현재 비용·익절 추세 입력이다. 종목별 즉시 손절 native Risk trigger와 실제 계좌 부분체결/잔고/운용 수락도 남는다. 이후 ③ 원래 Position·Risk·Capacity/Liquidity 상위 동적 비중 → ④ 전체 모의 통합·별도 운용 승인 순서이며 실제 운용 승인을 동적 비중 개발의 선행 조건으로 두지 않는다. 아래 68단계 이하의 다음/미연결은 당시 기록이다.

## 2026-10-14 사용자 순서 ② — 운영 Risk 보호 요청의 첫 EXIT 연결

ACK된 native Risk의 **일손실 한도 초과 EXIT_REQUIRED 요청**에서 최신 계좌 전체/상세 체결·run 보유 수량과 승인 KRX Collector 가격으로 첫 보호 EXIT를 생성한다. 일반 caller cue·UNKNOWN/불일치의 RECONCILE_REQUIRED는 검토/대사를 유지한다. 새 native 결정/원래 가격·수량·시각/만료를 기존 checkpoint에 먼저 ACK한 뒤 기존 ENTER/EXIT 승인→runtime→lifecycle→원장 경로로 한 번 제출한다. 비용 조회가 이후 실패해도 확인된 보유 청산은 EV/분봉/LLM/복구 bootstrap을 기다리지 않는다. 가격이 계좌 관측보다 새로우면 그 가격을 보존하고 기존 복구 queue를 한 번 요청한다.

실제 QUEUED transaction에서 고정 workflow/승인·현재 Risk/요청·owner/control·계좌 전체 미종결/UNKNOWN·TTL을 검사한다. proof는 기존 intent JSON의 optional mock_protection_claim에 원자 저장하며 이후 event에서도 유지한다. QUEUED/receipt ACK 유실은 같은 native root와 원래 보호 목적을 복구하고 새 제출을 하지 않는다. 청산 성공은 ACK가 아니라 실제 상세 FILL와 신선한 full account 잔고 0이며 재시작 후 재검증한다. 보호 latch/새 매수 차단은 유지한다.

관련 10모듈 **328건/202.974초** 통과 후 최종 native TTL/청산 신선도·close 경계 포함 신규 **16건/3.695초** 재검증 통과(고유 330건 범위). 코드가 동일한 Collector 34건 근거는 재사용했다. SQLite/가짜 REST·broker의 첫 보호 EXIT 1주 제출 1회와 UNKNOWN/ACK 유실·재시작/STOP·청산을 확인했다. 첫 보호 EXIT 전체 경로 SELECT 79/COMMIT 19(13.776ms), QUEUED/ACK 사건 cursor 처리 2/1, 이후 idle 10회 20/0, 동일 Risk 알림 100회 DB 0이다. 실제 REST/시장 지연 측정은 아니다. [68단계](AI_TRADING_SYSTEM_REDESIGN.md#68-운영-risk-보호-요청의-첫-exit-생성과-native-claim-확인).

build `2026.10.14-first-protection-exit-v1`·main/HEAD `232737d` 미커밋·미배포/미활성화. source/격리 httpx ASGI health/3개 build 표식·소비 감사 pass이며 보호 proof reader 1개와 같은 이름의 repository control 후보 1개만 검토해 baseline에 반영했다(367 reviewed/36 후보/107 계약/60 forwarding). 별도 과거자료 5모듈 catalog 보류다. 실제 PG는 미실행(placeholder/row-lock은 격리 cursor), 실제 계좌 00/04/REST·NAS/장중·전체 회귀/CI·commit/push/활성화는 하지 않았다. NAS recorder 배포는 아래 별도 기록이다.

다음 ② 개발은 취소 거절 후 bounded 보호 대응과 WAIT-vs-REPRICE 짝 EV/비용·익절 추세 공급이다. 이번 첫 EXIT의 자동 trigger는 계좌 일손실 한도이며 종목별 손절의 즉시 native Risk producer는 별도 남는다. 실제 모의계좌 부분체결/잔고/지연·운용 수락과 ③ 원래 Position·Risk·Capacity/Liquidity 동적 비중 → ④ 전체 모의 통합/별도 운용 승인 순서를 유지한다. 아래 67단계 이하의 미연결/다음 표현은 완료 당시 기록이다.

## 2026-10-14 사용자 순서 ② — 기존 입력 자동 공급/원래 주문 관리 등록

기존 승인 ENTER/EXIT의 제출 뒤 같은 owned 작업에서 원래 수량·가격·시각·기한과 목적을 기존 Runner checkpoint에 등록한다. Collector의 이미 승인된 KRX trade RAM 가격과 Monitor의 ACK된 Risk/계좌 전체 주문 관측·실제 broker confirmation을 같은 Runner에 공급한다. `offer_unfilled_execution_inputs` production caller는 0→1개다. BUY의 타점 유효기간은 원래 ENTER 결정에서 가져오며 갱신하지 않는다. WAIT-vs-REPRICE 짝 EV 공급자는 없으므로 일반 BUY/익절 재주문은 계속 차단한다.

첫 미체결 BUY의 native Risk가 상세 체결 원장 부재로 미완료인 경우 신규 위험/재주문 승인 조건을 유지한다. 전체 계좌가 flat이고 모든 미체결이 확인된 무체결 BUY이며 UNKNOWN/잔고 불일치/현재 상세 체결 누락이 없을 때만 취소 후 ABANDON을 허용한다. 보호 EXIT는 EV/비용/다음 분봉·bootstrap 복구를 기다리지 않고, 실제 CANCELLED/상세 부분체결 확인 뒤 남은 수량만 기존 fixed child 승인 경로로 제출한다. 취소 ACK/UNKNOWN/거절은 중복 제출 권한이 아니다.

선택 9모듈 **334건**의 첫 검사에서 기존 정책 OFF의 Monitor 없는 준비 경계 10건과 새 테스트 import 1건이 실패했다. OFF 가드/optional Monitor 및 테스트 import를 수정하고 입력 준비 중 ACK 경합 검사 1건을 추가한 뒤 신규 15건 포함 관련 **50건/23.126초**를 다시 통과했다. 직접 무관한 나머지 285건의 통과 근거는 재사용했다. 실제 로컬 Collector/Monitor/Runner·SQLite와 가짜 REST/broker로 보호 취소 1회→확정 전 새 주문 0→부분체결 1주 확인→잔여 2주 제출 1회를 확인했다. 유휴 전후 10회 각 SELECT 20/COMMIT 0, Collector 가격 읽기·정상 Risk 알림 각 100회 DB 0이다. 최초 등록/WAIT 41/8, 매수 기한 취소 68/15, 보호 취소 72/16, 보호 대체 제출 114/18(각 SELECT/COMMIT)이므로 사건 자체 비용은 남는다. [67단계](AI_TRADING_SYSTEM_REDESIGN.md#67-기존-collector계좌-입력의-자동-공급과-원래-주문-관리-등록).

build `2026.10.14-unfilled-native-source-v1`·main/HEAD `232737d` 미커밋·미배포/미활성화다. 실제 모의계좌 00/04/REST·부분체결/잔고/지연·PG/NAS/운용 수락은 하지 않았다. 기존 소비 감사/격리 httpx ASGI health/3개 build 표식은 통과했고 별도 과거자료 5모듈 catalog 미등록은 유지한다. 전체 회귀/CI·commit/push·NAS 배포·계좌 활성화는 없다. NAS의 recorder 배포 상태는 아래 별도 기록을 따른다.

남은 ② 개발은 WAIT-vs-REPRICE 짝 EV/비용·익절 추세 공급, 취소 거절 후 bounded 보호 대응과 기존 보호 요청으로 새 보호 EXIT를 처음 생성하는 연결이다. 원래 승인 EXIT가 복구됐지만 목적 등록이 유실된 경우 목적을 추측하지 않고 차단한다. 실제 운용 수락과 후속 ③ 원래 Position·Risk·Capacity/Liquidity 동적 비중 → ④ 전체 모의 통합/별도 운용 승인 순서를 유지한다. 아래 66단계 이하의 다음/미연결 표현은 각 단계 완료 당시 기록이다.

## 2026-10-14 사용자 순서 ② — 목적별 자동 취소/확정 대사 연결

같은 Runner의 입력 소비에서 WAIT/CANCEL/ABANDON/REPRICE를 분기하고 기존 gateway의 계좌 갱신→runtime CANCEL_STARTED→취소/대사를 자동 호출하도록 연결했다. WAIT/ABANDON을 fixed-child 제출로 넘기던 빈틈을 보완했으며 매수 위험 무효화는 취소 후 포기, 익절은 허용된 짝 EV/추세, 보호 EXIT는 EV/비용 미확인보다 위험 정책을 우선한다. 취소 ACK/UNKNOWN/거절은 새 제출·같은 취소 재시도 권한이 아니다.

취소 직전 기존 Monitor의 ACK된 계좌/Risk·계좌 전체 RAM 관측을 읽고, 실제 native broker confirmation/보유·현재 Risk/원래 타점·가격·EV 신선도를 runtime 잠금 안에서 재평가한다. REST로 새 Risk가 생겨도 원래 타점/가격/EV 시각을 갱신하지 않는다. 현재 선택과 달라지거나 입력이 만료되면 CANCEL_STARTED 전에 차단한다. 기존 취소 owner/control 및 exact native state/fill transaction 경계와 수동 API/명시 재시도는 유지한다. 보호 pending에서도 기존 BUY 위험을 취소할 수 있지만 새로운 BUY 제출은 차단한다.

신규 **20건 포함 관련 8모듈 286건(137.129초)** 통과. 가짜 broker에서 취소 1회→CANCEL_PENDING 재주문 0→상세 부분체결/취소 확정→BUY ADD 8주·익절 REDUCE 1주·보호 EXIT 2주를 연결했다. 실제 Monitor 코드/가짜 REST reader의 보호 취소, 타점/가격 만료·추가 체결/STOP·동결 EV 미허용·거절/close 전후 drain을 검증했다. 같은 격리 입력 idle 전후 10회는 각 SELECT 20/COMMIT 0, 알림 101회/중복 100회는 DB 0이다. 최초 취소 전체 경로 SELECT 71/COMMIT 15(14.461–16.151ms), 취소 ACK 사건 검토 25/3, 확정 후 대체 제출 104–106/15(16.864–18.123ms)로 사건 처리 자체의 비용은 남는다. 최초 취소 측정은 첫 anchor/계획 및 fixture-native Risk/recovery 쓰기를 포함하며 실제 REST 지연/장중 수락은 아니다. [66단계](AI_TRADING_SYSTEM_REDESIGN.md#66-목적별-자동-취소와-취소-직전-native-계좌risk-재검증).

**최초 가격/타점/짝 EV/target을 묶는 production 공급자와 원래 주문의 자동 관리 등록은 아직 미연결**이다. 취소 직전 계좌/Risk 공급은 기존 Monitor RAM으로 연결됐지만 최초 `offer_unfilled_execution_inputs` production caller는 0개다. 취소 거절 후 보호 검토/attention은 표시하되 자동 재시도/우회는 추가하지 않았다. 다음은 이 최초 공급/등록 연결이며 실제 모의계좌 00/04/REST 부분체결·잔고·지연 및 운용 승인 수락은 남는다. 이후 ③ 원래 Position·Risk·Capacity/Liquidity 동적 비중 → ④ 전체 모의 통합/별도 운용 승인 순서를 유지한다.

build `2026.10.14-unfilled-running-cancel-v1`, main/HEAD `232737d` 미커밋·미배포/미활성화다. source/격리 ASGI/build 3표식·소비 감사 pass(기존 baseline/366 reviewed/35 후보/107 계약/60 forwarding 변화 0). catalog 별도 과거자료 5모듈 미등록 유지. 실제 계좌/PG/NAS/장중·Recorder capture/Replay 수락, 전체 회귀/CI·commit/push·배포/주문 활성화 없음.

## 2026-10-14 사용자 순서 ② — running Runner 입력 소비 연결

기존 Runner의 단일 RAM 입력 슬롯→같은 loop wake→고정 child native 승인/제출을 연결했다. 명시 idle API는 계속 running 호출을 거절하며 같은 잠금 core를 재사용한다. 현재 typed 입력 한 건만 유지하고 시점/revision 역행·같은 cut의 다른 내용·1MiB 초과·계좌/run 불일치·오래된 입력을 차단한다. 가격 시각/정상 REST revision 갱신은 신선도 갱신이며, 주문 상태/상세 체결·위험·타점 조건/EV 허용 여부 변화 또는 관리 기한만 전체 검토한다. 누락 입력으로 기한이 도래하면 새 가격/EV를 만들지 않고 보호 기한은 기존 보호 요청을 한 번 arming한다. 독립 session/risk clock을 막지 않는다.

신규 **21건 포함 관련 8모듈 266건(122.762초)** 통과. 가짜 broker에서 running BUY ADD 7주·익절 REDUCE 1주·보호 EXIT 2주, 봉 없는 wake·UNKNOWN/STOP·신선도/ACK·close 전후 claim/drain을 검증했다. 같은 격리 입력 전후 idle 10회는 각각 SELECT 20/COMMIT 0, 동일 알림 101회·건강한 갱신 6회·입력 없는 보호 기한 100회는 추가 DB 0이다. 실제 제출 한 번은 SELECT 108–110/COMMIT 14·17.926–21.764ms로 native 승인/원장/기록 비용을 없앤 것은 아니다. 실제 제출 후 pending-sell 변화는 별도 사건(SELECT 10/lease COMMIT 1)이고 건강한 REST revision과 구분한다. 보호 요청 첫 poll은 COMMIT 1/반복 10회 0이다. [65단계](AI_TRADING_SYSTEM_REDESIGN.md#65-running-runner의-사건기한-소비와-고정-주문의-동일-native-실행).

**현재 완료는 running 소비 경계이며 production 현재 입력 producer·목적별 자동 취소 호출은 아직 없다.** Collector RAM 가격 원천/기존 계좌 관측은 존재하지만 타점·짝 EV·현재 broker confirmation을 묶는 실행용 공급자 연결이 남는다. 현재 artifact는 supplied mock typed input이며 예측/운용 수락이 아니다. 다음은 이 공급자와 기존 취소 primitive를 실제 사건/기한에 연결하고 실제 모의계좌 00/04·REST 부분체결/잔고를 대조하는 것이다. 이후 ③ 원래 Position·Risk·Capacity/Liquidity 동적 비중 → ④ 전체 모의 통합/별도 운용 승인 순서를 유지한다.

build `2026.10.14-unfilled-running-dispatch-v1`, main/HEAD `232737d` 미커밋·미배포/미활성화다. source/ASGI health/build 3표식·소비 감사 pass(두 기존 wrapper 후보의 잠금 core 이동만 검토, 실제 store 호출/계약/forwarding 변화 0). catalog의 별도 과거자료 5모듈 미등록은 남는다. 실제 계좌·PG/NAS/장중·Recorder capture/Replay 수락, 전체 회귀/CI·commit/push·주문 활성화·배포는 하지 않았다.

## 2026-10-14 사용자 순서 ② — 목적별 고정 대체 주문의 native 승인·제출

동결 운용 명세의 optional `unfilled_execution_policy`와 별도 v3 고정 child 승인을 추가했다. 기존 명세는 필드를 생략하고 기존 hash/실행 OFF를 유지한다. BUY/익절의 공급된 짝 EV 입력은 정책에서 모의용으로 명시 허용해야 하며 예측 검증/운용 승격으로 쓰지 않는다. 보호 EXIT는 일반 EV/비용 미확인보다 기존 보호 경로를 우선한다. ADD/REDUCE를 ENTER/EXIT로 바꾸지 않고 기존 native submit/lifecycle를 사용한다.

신규 **29건 포함 관련 12모듈 372건(167.737초)** 통과. SQLite 실제 spec/admission/Risk/승인·원장과 가짜 broker에서 BUY ADD 7주, 익절 REDUCE 1주, 보호 EXIT 2주를 제출했다. 부분체결 후 7→5주·QUEUE/승인/receipt ACK 유실·UNKNOWN/재시작·STOP/부모/Risk/고정 내용 경합·close/drain을 검증했다. QUEUED transaction에 실제 검사한 gate/Risk 참조를 같은 intent JSON에 저장하고 이후 대사에도 보존한다. native metadata로 복구하며 관리 child를 새 root로 삼아 예산을 초기화하지 않는다. 실제 PostgreSQL은 실행하지 않았으며 placeholder/row-lock 구조 검사는 격리 cursor다.

현재 연결은 **idle Runner의 명시 실행 dispatcher**다. running loop의 사건/기한 자동 호출·최신 가격/타점/짝 EV producer·목적별 자동 취소와 취소 거절 후 bounded 보호 대응은 다음 연결 범위다. 반복 polling으로 전체 검토하지 않으며 실제 계좌 `00`·`04`/REST·장중/PG/NAS 및 운영 승인 수락은 남는다. native 실행 한 번의 SELECT 106–108/COMMIT 14는 예약 재검증·최신 승인·실제 QUEUE/전송/receipt/checkpoint를 포함한다. 동일 명시 요청 5회는 각 SELECT 10/lease COMMIT 1·추가 제출 0이다. 실제 REST/Risk 생산 비용·장중 지연은 제외했고 개선을 주장하지 않는다. [64단계](AI_TRADING_SYSTEM_REDESIGN.md#64-목적별-고정-대체-주문의-native-승인과-제출).

build `2026.10.14-unfilled-native-dispatch-v1`, main/HEAD `232737d` 미커밋·미배포/미활성화 작업본이다. 소비 감사는 신규 native claim read 1개/기존 repository wrapper 후보 1개만 검토 후 pass이며 이전 baseline을 보존했다. catalog의 별도 과거자료 5모듈 미등록은 남는다. 전체 회귀/CI·commit/push·계좌/주문 활성화·NAS 배포는 하지 않았다. 다음 큰 개발 순서 ③ 원래 Position·Risk·Capacity/Liquidity 동적 비중·④ 전체 모의 통합/별도 운용 승인을 유지한다.

## 2026-10-14 사용자 순서 ② 시작 — native 자동 취소 실행 경계

기존 gateway/runtime/lifecycle에 명시 `cancel_automation_intent` 경로를 추가했다. 검토한 native 주문과 계좌 갱신 후의 주문·체결 상태가 같아야 하며, 상태 변화 없이 REST 확인 시각만 앞으로 갱신된 경우는 허용한다. CANCEL_STARTED 저장 transaction에서 owner·RUNNING/spec/run/control revision을 검사한다. 취소 시작 기록 뒤 STOP은 기존 응답/늦은 체결 대사를 막지 않는다. ACK 유실/UNKNOWN/거절·재시작 후 같은 주문의 자동 취소 재전송을 막고 수동 취소의 명시 재시도는 유지한다. 취소 ACK는 CANCEL_PENDING이며 대체 주문을 만들지 않는다.

신규 18건 포함 관련 6모듈 **212건(124.253초)**·소스 지문/로컬 ASGI health/build·소비 감사 delta 0 통과. 격리 SQLite 경계만 비교해 첫 취소 SELECT 11→14·COMMIT 3→3, 중복 요청당 SELECT 4·COMMIT 1(owner lease), 추가 계좌 갱신/취소 전송/Risk 무효화 0을 확인했다. 실제 REST/Risk 생산 비용과 장중 지연은 포함하지 않는다. build `2026.10.14-automation-cancel-fence-v1`, main/HEAD `232737d` 미커밋·미배포 작업본이다. **② 완료가 아니라 취소 기반 구현이며 production caller는 아직 없다.** 다음은 목적별 사건/기한 dispatcher와 고정 child의 native 실행 승인·제출 연결, 이후 실제 00/04/REST 수락이다. 계좌 활성화/주문·전체 회귀/CI·commit/push/NAS 배포는 하지 않았다. [63단계](AI_TRADING_SYSTEM_REDESIGN.md#63-모의계좌-자동-취소의-native-실행-경계).

## 2026-10-14 사용자 순서 ① 운영 Risk 자동 보호 요청 — 로컬 연결 완료

개발 순서를 **① 운영 Risk 자동 보호 요청 → ② BUY/익절/보호의 모의계좌 자동 취소·재주문·00/04/REST 대사 → ③ 동적 비중조절 → ④ 선정부터 청산까지 모의 통합 검증·별도 운용 승인**으로 변경했다. 기존 monitor의 ACK된 Risk/계좌만 RAM 전달하고 같은 Runner를 깨워 보호 우선 검토/일반 판단 차단을 시작한다. 새 수집/전체 주문 페이지 복제·TR/DB writer/독립 task는 없다. 동일 위험/누적 mismatch는 합치며 실제 노출 변화에 새 요청을 만든다. 현재 자동 요청/검토까지이며 실제 취소/재주문/보호 제출은 다음 ②다. [62단계](AI_TRADING_SYSTEM_REDESIGN.md#62-운영-risk의-자동-보호-요청-연결과-개발-순서-개정).

신규 **13건 포함 관련 9모듈 234건(108.710초)** 통과. 실제 기존 Risk/FIFO parser/writer·monitor/Runner와 격리 native SQLite/가짜 broker에서 당일 -360,000원·한도 -300,000원·보유 1주를 자동 요청으로 연결했고 봉 없이 idle loop를 깨웠다. ACK/STOP/UNKNOWN·scope/listener/초기 cut·반복/캐시/복구를 검사했다. recovery revision 6개→요청 1개·callback 추가 DB/TR/COMMIT/전송 0·반복 5회 checkpoint 0, ASGI/build/source·소비 감사 pass다. 실제 장중 청산 지연/모의계좌·PG/NAS 수락은 아니다.

build `2026.10.14-native-risk-protection-v1`, main/HEAD `232737d` 작업본·미커밋·미배포다. 별도 배포된 recorder-only 릴리즈와 구분한다. catalog의 별도 과거자료 신규 2모듈 미등록·recorder checkpoint 미지원은 보류 유지한다. 다음 ②에서 기존 취소/체결 대사·owner/control/현재 Risk와 목적별 자동 실행을 연결하고, 취소 확정 전 재주문/UNKNOWN 중복은 계속 금지한다. ③은 원래 Position·Risk·Capacity/Liquidity 자동 비중 정책으로 유지한다. 전체 회귀/CI·commit/push·계좌/주문 활성화·배포는 하지 않았다.

## 2026-10-14 주문 OFF 첫 연결 통합 수락 — 다음은 동적 비중조절

명시 native 미체결 검토·목적별 고정 child·caller의 OFF 보호 요청·기존 checkpoint 첫 연결 범위를 종료했다. 신규 통합 4건 포함 선택 **38건(10.994초)** 통과. 실행 소스 4개 SHA/기존 테스트 class AST 동일을 확인해 앞선 155건 근거를 재사용했고 전수검사를 반복하지 않았다. [61단계](AI_TRADING_SYSTEM_REDESIGN.md#61-주문-off-첫-연결의-통합-수락과-db-호출-범위).

실제 SQLite observation reader의 격리 idle polling 전후 각 5회는 회당 SELECT 4/COMMIT 0으로 같았다. 상태 100회·기한 표시/동일 보호 요청 100회는 DB 0, 첫 보호 처리 SELECT 2/COMMIT 1·이후 10회 FILL/control SELECT 20/COMMIT 0·전체 gate/Risk 조회 0이다. 현재 전체 검토는 명시 OFF 호출뿐이며 running runner에서는 I/O 전에 거부한다. 수동 반복의 SELECT 25/27·COMMIT 3/2 비용은 남고, 사건/기한 자동 dispatcher·운영 보호 producer는 아직 미구현이다. 미래 실행은 동일 사건 합치기/기한 한 번 깨우기·실제 제출 직전 native 재검증을 수락 조건으로 둔다.

**다음 큰 개발은 원래 00 통합 명세의 Position·Risk·Capacity/Liquidity 기반 상위 동적 비중조절이다.** 현재 순자산/출처·시장/테마/종목·시총/대금/유동성/변동성·EV/Risk의 입력 계약과 결측 차단부터 시작하고, 목표 금액/수량 계산은 하위 기능으로 유지한다. 실제 모의계좌 취소/재주문·청산/잔고/지연 수락과 운용 승격을 개발의 선행 조건으로 두지 않는다. 실행 소스/build `2026.10.14-unfilled-purpose-gate-v1`·main/HEAD `232737d` 작업본 유지, 미커밋·미배포·주문 OFF다. PG/NAS/장중·recorder checkpoint 미지원·catalog 보류는 별도이며 CI/전체 회귀·commit/push·활성화/배포는 하지 않았다.

## 2026-10-14 NAS 녹화 저장 최적화·10MiB/s 배포 완료

활성 NAS 릴리즈는 **`2026.10.14-recorder-shared-10m-v1-af0f8072f960415b`**, build `2026.10.14-recorder-shared-10m-v1`이다. 관리자 배포 결과 뒤 무비밀번호 status로 health ok·같은 build/소스 경로·diagnostics idle·trace off를 재확인했다. 실제 녹화 코드 SHA `9df9d3fe…`와 설치 helper SHA `c2846caf…`도 통과한 검증본과 같다. 공유 본문·완료 대기/metadata 묶음 개선과 **10MiB/s(10,485,760 bytes/s)** 기본 속도가 적용됐다. helper는 기존 설치 절차로 갱신했고 DB 컨테이너·runtime image·원본 녹화·sudoers·원래 revoke 백업·지난 예약 기록은 보존했다. 검증을 다시 실행하거나 새 녹화를 시작하지 않았다. [배포·직접 확인 근거](../artifacts/recorder-shared-deployed-result-20261014.json).

이 릴리즈는 base `232737d`의 NAS 소스에 검증한 녹화 파일만 적용한 **미커밋 recorder-only 수정본**이다. 아래 다른 개발 단계의 main 작업본·원격과 전체가 같다는 뜻이 아니다. 새 녹화 예약/장중 수신 무손실·전체 앱/Replay 성능 수락은 확인하지 않았고, 운영 DB 동시 I/O 영향 및 실제 10~20초 COMMIT P1은 계속 미해결이다. 다음은 이 P1의 WAL/I/O/잠금/Writer 경합 판별과 안전한 공통 저장 비용 개선이며, 새 입력을 임의로 복원하지 않는다.

## 2026-10-14 목적별 대체 주문·보호 우선 검토 — 전송 OFF

기존 owner/control·Risk gate를 공유해 원래 BUY 승인 계보·취소 확정·실제 체결/보유와 고정 child ID/payload·자본/자금·예산을 결합했다. 익절 REDUCE와 보호 EXIT를 구분하며 legacy ADD/REDUCE 차단은 유지한다. OFF 검토 결과는 실제 approved-gate/주문으로 저장하지 않는다. 기존 runner의 bounded RAM 보호 요청이 같은 loop를 깨우고 봉 조회/일반 평가·dispatch에 앞서 처리된다. 새 수집/초봉/LLM/Shadow·collection/writer/독립 task는 없다. [60단계](AI_TRADING_SYSTEM_REDESIGN.md#60-목적별-고정-대체-주문-검토와-보호-요청의-우선-처리).

신규 **20건 포함 관련 155건(60.004초)**·legacy gate 전후 360회 동일·기존 lifecycle/transport AST·별도 OS 복구·ASGI health/build·소비 감사 pass. SQLite BUY 원래 승인 조회로 SELECT 변경 24→25/반복 26→27이며 COMMIT 3/2·checkpoint 1/0·추가 전송/실행 원장 쓰기 0이다. 60초 idle poll의 요청 6회는 RAM 약 0.35–0.52ms/처리 약 1.27–1.74ms·요청 자체 DB 0이다. 실제 broker/PG/NAS/장중 지연 수락이 아니다. 기존 catalog 미등록·recorder checkpoint capture 미지원은 보류 유지한다.

build `2026.10.14-unfilled-purpose-gate-v1`, main/HEAD `232737d` 작업본·미커밋·미배포다. **주문 OFF 첫 실행 연결 종료까지 통합 수락 한 묶음이 남았다.** 실제 보호 Risk producer 자동 연결·이미 실행 중 native 작업의 지연/실제 청산·자동 취소/재주문·모의계좌/PG/NAS 수락은 후속이다. EV 운영 검증·상위 자동 비중조절은 원래 Position·Risk·Capacity/Liquidity 설계로 유지한다. 전체 회귀/CI·커밋/push·계좌/주문 활성화·배포는 하지 않았다.

## 2026-10-14 Native 미체결 시도·예산/owner gate 검토 — 전송 OFF

기존 idle runner/checkpoint에 native parent-child 기대 내용·계보·QUEUED claim/실제 SUBMISSION_STARTED 횟수를 연결했다. caller 횟수로 초기화하지 않고 원래 anchor/가격/기한과 결정적 root+ordinal ID를 유지한다. 기존 상세 체결 reader의 실제 보유·native Risk cut을 맞추고 실제 owner/control/binding/복구/Risk와 ACK 후 상태/시간을 재검증한다. [59단계](AI_TRADING_SYSTEM_REDESIGN.md#59-native-parent-child-시도-투영과-닫힌-owner-gate-검토).

신규 **14건 포함 관련 5모듈 135건(65.245초)** 통과. 10주 BUY의 3→2→1주 부분 체결 후 대체 검토량 7→5→4주·예산 소진 ABANDON·QUEUED/UNKNOWN 중복 금지·manifest/원장 손실·ACK/STOP/owner/느린 저장 경계를 확인했다. 실제 별도 OS 프로세스에서 시도 2회·보유 6주·원장에 없는 마지막 예약을 복구했다. adapter 추가 주문/취소·실행 원장 쓰기 0, 기존 class AST/ASGI health/build·소비 감사 pass다. catalog의 기존 미등록 과거자료 모듈 보류 유지. 근거 `data/diagnostics/20261014-unfilled-native-attempts/`.

기존 gate의 ADD/REDUCE는 NON_ACTION_DECISION으로 차단하며 ENTER/EXIT로 바꾸지 않는다. 보호 EXIT는 기존 native 평가를 사용하지만 raw gate 평가를 고정 child 승인으로 쓰지 않고 outer order_authorized=false/transport_state=OFF다. approved-gate/dispatch receipt/실제 주문 행은 저장하지 않는다. native guard의 owner lease 갱신 때문에 변경 검토 COMMIT 3/동일 cut 반복 2회이며 기존 checkpoint 자체는 1/0이다. 일반 Shadow/runner polling·새 수집/초봉/독립 writer/task에는 연결하지 않았다. 실제 운영 부하/지연 표본이 아니다.

build `2026.10.14-unfilled-native-attempts-v1`, main/HEAD `232737d` 작업본·미커밋·미배포다. 다음은 기존 gate의 미체결 대체 주문 목적별 승인 계약·고정 child 결합과 보호 우선 wake-up을 주문 OFF로 연결 검증한다. 실제 자동 취소/재주문·EV 운영 승격·모의계좌/PG/NAS/장중/지연/체결/잔고·recorder checkpoint capture 수락은 남는다. 상위 동적 비중은 원래 Position·Risk·Capacity/Liquidity의 후속 정책으로 유지한다. 전체 회귀/CI·커밋/push·계좌/주문 활성화·배포는 하지 않았다.

## 2026-10-14 미체결 검토 계획의 영속 복구 — 전송 OFF

기존 idle runner의 명시 검토·native parent reader·checkpoint writer·보호 시계 힌트를 연결했다. 취소 확정/현재 계좌 대사 후 첫 대체 preview 슬롯을 저장하며 실제 주문 원장 행을 만들지 않는다. 복구 뒤 항상 현재 입력 재검증이 필요하고 원래 anchor/기한/예산/control을 초기화하지 못한다. ACK/COMMIT 실패·취소 경쟁·STOP/중복 child·손상/크기 제한·실제 별도 OS 프로세스 복구와 수동 run_once 중 native 작업 대기를 검사했다. [58단계](AI_TRADING_SYSTEM_REDESIGN.md#58-미체결-검토-계획의-기존-checkpoint-복구와-실행-전-검증).

신규 **13건 포함 관련 4모듈 100건(42.650초)** 통과. 기존 lifecycle/transport class AST 동일, 검토의 추가 submit/cancel·실행 원장 쓰기 0회, 변경 checkpoint COMMIT 1회·동일 owner/입력 반복 0회, source SHA/AST·ASGI health/build 3곳·소비 감사 pass를 확인했다. native control은 공급 fixture, Risk는 연구 제약이며 owner lease/native 실행 승인·실제 보호 wake-up 수락은 아니다. sandbox socketpair 초기화 정체를 추적으로 확인한 뒤 사용자 환경에서 관련 검사를 통과했다. 초기 실패/중단·중복 묶음은 최종 통과 수에 합산하지 않았다. catalog는 기존 미등록 `tests.unit.test_prepared_historical_archive_inputs`로 실패 상태다. 근거 `data/diagnostics/20261014-unfilled-preflight/`.

로컬 build `2026.10.14-unfilled-preflight-v1`, main 작업본/HEAD `232737d`·미커밋·미배포다. 단일 root의 첫 preview 예약과 실제 전송 횟수를 구분하며 native child 존재/공급 횟수 증가에는 재시도 투영이 필요하다고 차단한다. 다음은 기존 owner/control fence의 native parent-child 전송 시도·다회 예산과 guarded adapter를 주문 OFF로 연결 검증한다. 실제 자동 재주문/보호 우선 wake-up·모의계좌/지연/체결/잔고·PG/NAS/장중 수락은 남는다. recorder의 runner checkpoint collection은 기존 미지원이며 capture/Replay 완료로 보고하지 않는다. 상위 동적 비중은 원래 Position·Risk·Capacity/Liquidity 후속 정책으로 유지한다. 전체 회귀/CI·커밋/push·계좌/주문 활성화·NAS 배포는 하지 않았다.

## 2026-10-14 녹화 저장 속도 선택 — 10MiB/s

**Linux 등록·실제 PostgreSQL 검사 통과/배포 준비 완료:** NAS jobs `f4faa9e3096ba59f94efe2af65ba6b9d`·`d2122e5ae81ee73f6602a1519d34a4e4`가 통과했다. 등록 복사본의 controlled 계좌/lease/VI/작은·큰 본문 Replay 두 번에서 19 native 호출·16 COMMIT·3 예상 rollback, 최종 원장·내용·순번 및 초기 상태 복원을 확인했다. 기존 실제 계좌 입력 첫 60초도 동일 초기 DB로 두 번(각 4호출) 재생했다. 운영 앱 복구·DB/원본/예약 보존과 실제 health ok/idle을 재확인했다. 기존 Linux 8개는 정확한 hash/통과 로그로 재사용했다. [NAS 근거](../artifacts/recorder-shared-linux-pg-nas-result-20261014.json).

검증한 파일만 기존 배포 기준에 얹은 `2026.10.14-recorder-shared-10m-v1-af0f8072f960415b`를 준비했다(1,072파일·11개 변경, base `232737d`·미커밋 recorder-only 수정본). 배포·rollback 12건과 실제 배포본의 ASGI 포장 검사가 통과했다. NAS helper와 후보의 실제 차이는 공유 본문 검증과 기존 account-context/v4의 동일 frontier 검증을 포함하는 승인 호환 두 함수이며 필요한 Linux helper 61건도 통과했다. 나머지 installer/worker·의존성·스키마는 동일하고 Dockerfile 계약은 build 표식만 바뀐다. NAS 파일 해시·지난 예약 12개 파일과 보이는 live waiter 없음도 확인했다. 관리자 실행은 아직 없으며 현재 운영 release/1MiB/s는 그대로다. 배포 후 capabilities의 10MiB/s·8GiB·500만 이벤트와 health/build/path를 확인하며 실패 시 기존 운영 소스로 복구한다. 과거 예약을 재실행하거나 새 녹화를 시작하지 않는다. [배포 준비](../artifacts/recorder-shared-deployment-preparation-20261014.json).

NAS job `82404f5dd4aa83cddd73bb364b5c9e84`는 Linux 선택 검사 이후 PG fixture의 “공유 root 두 개 이상” 전제에서 실패했다. 계좌 입력 대부분은 기존 일반 본문 경로이므로 fixture에 작은 large-profile 입력 두 건을 명시적으로 추가했다. PG의 독립 native 호출/COMMIT 기대값은 17/14→19/16, 세 Shadow 호출의 각각 COMMIT 1회·작은/큰 최종 내용과 원장/revision/sequence·초기 상태 복원 검사는 유지/보강했다. 운영 소스는 변경하지 않았고 실제 앱 복구·임시 자원 정리·health ok/idle을 확인했다. 정리된 worker 폴더를 읽던 진단 오류도 보존 보고서/로그만 읽도록 수정했다. 관련 진단 2건·fixture/실패 요약 3건 통과. v2는 이전 Linux 8개의 정확한 소스 hash/로그 통과 ID를 일시정지 전에 확인해 재사용하고, 변경 PG fixture와 별도 새 DB의 기존 실제 계좌 Replay만 실행한다. NAS SHA/문법/설치 인자는 확인했으며 v2 실행·배포·운영 10MiB/s 적용은 아직 없다. [실패·수정 근거](../artifacts/recorder-shared-linux-pg-fixture-fix-20261014.json).

10MiB/s를 실제 시작 상태·공개 capabilities·검사 계약에 반영한 로컬 수정본의 API/예약/지연 저장 회귀 **44건(16.155초)**이 통과했다. 현재 배포 기준 `232737d`에 녹화 수정만 적용한 불변 검증본에서도 필요한 **8건(6.318초)**이 통과했다(44건과 중복하므로 합산하지 않는다). NAS의 9개 파일 SHA·기준 소스·설치 인자를 읽기 전용으로 확인했다. 다음 고정 관리자 검사는 실제 Linux 등록 후 등록 복사본의 PostgreSQL 계좌/lease/VI/대형 입력 Replay 두 번과, 별도 새 DB에서 기존 실제 녹화본의 첫 60초 계좌 Replay 두 번이다. 아직 실행·helper 갱신·배포하지 않았으며 두 검사 후 운영 앱/기존 예약을 보존한다. [준비 근거](../artifacts/recorder-shared-linux-pg-preparation-20261014.json).

사용자 요청으로 다음 운영 적용 속도를 **10MiB/s(10,485,760 bytes/s)**로 정했다. 무제한은 성능 판별용 격리 시험 결과로만 남긴다. 공유 파일 수정의 Linux 등록·실제 PostgreSQL/account Replay와 기존 API·예약 계약 검증을 마친 뒤 배포 및 저장 예약의 `write_bytes_per_second`에 이 값을 반영한다. 현재 운영은 기존 1MiB/s이며 이번 선택만으로 릴리즈·예약을 변경하지 않았다. [결정 기록](../artifacts/recorder-operating-rate-choice-20261014.json).

## 2026-10-14 작은 본문 파일 공유 — NAS 저장 개선 확인·배포 전 검증 대기

1MiB 이하 단일 block만 명시적인 `blocks/shared-v1` root로 기존 16MiB 본문 묶음에 저장하는 최소 수정을 구현했다. 기존 logical digest/metadata/입력 profile·불변 reference·입력별 원래 native 호출·blocks/v1 읽기/큰 입력·메모리/큐/스토리지 한도를 유지한다. reader와 관리자 등록기는 숫자 파일명/offset/한 블록/root 및 물리 파일 checksum·크기를 함께 검증한다. 같은 본문/metadata sync가 완료되기 전 입력을 공개하거나 RAM 소유권을 해제하지 않는다. 등록기는 최대 한 공유 묶음만 cache하며 candidate 코드를 import하지 않는다. 새 모듈이나 native SQL/트랜잭션 변경은 없다.

관련 **81건+52건=133개 독립 검사**가 통과했다. 공유 입력의 native Replay 두 번·동일 arguments의 별도 호출·큰/구형 블록·shared corruption/다른 입력의 독립 읽기·경로/offset/bool/한도·bundle rollover·sync 실패 suffix·등록 후 재검증을 확인했다. 같은 실제 입력 16,384건/빈 PC 디렉터리/동일 무제한 속도에서 본문 sync **45→9**, 본문+metadata **51→15**, 저장/drain 시간 **2.837→2.370초(16.47%)**다. 49,944,996바이트·원문 metadata hash/checksum/순번·pending 4,096행·종료 charge/pending/drop/reject 0은 동일했다. payload sync p95/최대는 23.8/34.4→131.6/131.6ms로 증가해 큰 파일의 지연도 확인해야 한다. 검사 도구의 검증 포함 wall 시간은 3.914→5.124초로 증가했으므로 전체 검증/Replay 개선으로 보고하지 않는다.

NAS의 같은 실제 입력 16,384건·빈 출력 디렉터리·무제한 격리 A/B 두 순서에서 **10.455→6.912초(33.88%)**, **10.859→6.376초(41.28%)**로 줄었다. 본문 파일/sync 45→9·본문+metadata sync 51→15, bytes/내용/checksum/순번과 종료 pending/charge/drop/reject 0을 유지했다. 개별 본문 sync p95는 338/348→582/500ms로 커졌지만 총 본문 sync는 6.547/7.128→2.776/2.532초로 줄었다. RSS는 약 6.6~12.5MB 늘었다. 이 파일 저장 개선은 유지한다. 사용자 결과의 운영 앱 복구·기존 release/DB/원본/계획/helper/sudoers 보존을 확인했으며 후보는 미배포다. 현재 NAS helper는 새 형식을 모르는 이전 버전이므로 Linux 등록/reader·실제 PostgreSQL/account Replay와 승인된 helper 갱신이 남는다. 다음 운영 속도는 사용자 선택 **10MiB/s**이며 아직 기존 1MiB/s다. 운영 동시 I/O·COMMIT P1·전체 앱/Replay 수락은 미해결이다. [NAS 근거](../artifacts/recorder-shared-blocks-nas-result-20261014.json).

## 2026-10-14 본문 동기화 순서 후보 기각·코드 철회 완료

NAS 두 순서 비교는 9.669→9.196초(4.89% 개선), 9.453→9.936초(5.11% 악화)로 일관되지 않았다. 평균은 9.561→9.566초로 사실상 같고 51회 파일 fsync도 그대로다. byte/hash/checksum·메모리/순번/복원은 보존됐지만 성능 개선 조건을 충족하지 못해 이 가설의 반복을 종료했다.

이번 순서 변경만 SHA를 확인해 철회하고 기존 휴식·metadata 묶음 확대 수정본 **4db756a1…**로 되돌렸다. 후보 원본은 artifact에 보존하고 후보 전용 2개 검사만 제거했다. 나머지 테스트 파일은 Git 변경이 없으며 철회 후 블록·지연 회귀 **29건(19.326초)**이 통과했다. job `b24d92d1b479803430e8dfaa1d87a883`의 실제 NAS health ok·idle·운영 복구를 재확인했고 후보는 배포하지 않았다. 기존 release/DB/원본/계획/helper/sudoers와 운영 1MiB/s는 유지한다.

다음 수정은 실제 파일 수를 줄이는 작은 block 본문 공유다. 1MiB 이하 단일 block 입력만 기존 16MiB 본문 묶음에 함께 저장하는 명시적 새 descriptor를 검토하고, blocks/v1 구형 읽기·원문/입력별 checksum/offset·불변 reference/복원 메모리·실패 prefix를 유지한다. reader와 관리자 등록 검증을 함께 맞춰야 하며 파일/동기화 감소·같은 입력의 실제 개선을 확인해야 수락한다. 이번 단계에서 이 공유 형식의 구현/검증/배포를 했다고 주장하지 않는다. COMMIT P1과 파일 저장 병목 자체는 미해결이며 동기화 순서 가설만 기각했다. [결과/철회 근거](../artifacts/recorder-group-sync-nas-result-20261014.json).

## 2026-10-14 R4 후 미체결 목적별 정책 — 주문 없는 첫 연결

기존 lifecycle에 독립 상태 없는 목적별 정책을 두고 기존 preview CLI의 고정 입력으로 연결했다. 매수/익절/보호의 WAIT/CANCEL/REPRICE/ABANDON을 구분하고 취소 ACK/UNKNOWN에는 대체 수량을 만들지 않는다. 원래 상태 정규화·취소/체결 대사·현재 계좌 전체 pending/예약/기존 목표 계산을 재사용한다. 보호는 일반 EV/타점 분석을 기다리지 않으며 실제 청산/기한·미해결 위험을 분리한다. [57단계](AI_TRADING_SYSTEM_REDESIGN.md#57-r4-후-미체결-목적별-정책의-주문-없는-첫-검증).

신규 24건 포함 최종 관련 **191건(80.121초)** 통과. 기존 lifecycle/transport AST 동일·native SQLite/fake transport의 취소 중 부분/전량 체결·거절/ACK UNKNOWN/재시작/다른 run·수동 주문/중복·슬리피지/EV/시점/세션·보호 시계를 검사했다. 10주 원래 BUY 중 취소 경쟁 3주 체결 이후 7주, 보호 SELL 4주 체결 이후 잔여 6주를 같은 목표/비용 계산으로 제안했다. 정책 추가 SQL/submit/cancel 0·typed/JSON/CLI 결과/ID·source SHA/AST·ASGI health/build 3곳과 DB 소비 delta 0 확인. catalog는 별도 미등록 `tests.unit.test_prepared_historical_archive_inputs`로 실패했다. 근거 `data/diagnostics/20261014-unfilled-management/`.

로컬 build `2026.10.14-unfilled-management-shadow-v1`은 미커밋·미배포다. 현재 paired EV/횟수/기한·Risk/타점은 공급된 고정 연구 입력이며 native 실행 승인이 아니다. 다음은 기존 owner의 영속 결정/parent-child 계획·재시도 예산·취소 대사·보호 시계 연결의 실행 전 검증, 전송 OFF다. 상위 동적 비중은 원래 Position·Risk·Capacity/Liquidity의 후속 범위이며 현재 금액/수량은 하위 기능이다. 실제 모의계좌/재주문/보호 지연·PG/NAS/장중 수락·전체 회귀/CI·push/활성화/배포는 미실행이다.

## 2026-10-14 본문 쓰기·동기화 순서 최소 수정 — PC 개선 없음·NAS 판별 대기

입력별 파일을 합치려면 blocks/v1 reader와 관리자 등록 계약이 바뀌므로 먼저 파일 형식을 유지하는 작은 후보를 구현했다. 지연 저장의 같은 metadata chunk에 속한 block 파일을 먼저 쓰고 close/rename한 뒤 각 파일을 다시 열어 모두 fsync한다. 일반 본문도 먼저 flush하며 block/일반 본문 sync가 전부 끝난 뒤에만 metadata sync·공개·RAM 해제를 수행한다. streaming/단독 block 쓰기는 기존 즉시 sync를 유지하고 하나의 payload sync 함수로 기존 중복 계측을 공통화했다. 파일별 동기화 횟수·구형 reader·입력/native 호출/순번/hash·메모리/스토리지 한도는 변경하지 않는다. 동기화 호출 깊이는 같은 파일의 계측 함수 한 단계이며 새 모듈/프로세스/저장 포맷은 없다.

기존 68건에 새 순서/공개 전 sync·실패 suffix 보존/native Replay 2건을 추가해 **31건+39건=70개 독립 검사**가 통과했다. 같은 보존 입력 16,384건/빈 PC 출력 디렉터리/동일 무제한 속도의 실제 비교에서 3.215→3.649초로 **13.49% 느려졌다**. 49,944,996바이트·내용/metadata hash/checksum·51회 파일 sync·pending 상한/종료 상태는 동일했다. PC 개선으로 처리하지 않으며 PC 반복은 추가하지 않는다.

대상 NAS 파일시스템에서 함께 쓴 파일의 동기화 비용이 줄어드는지를 판별할 두 순서 A/B를 준비했고 origin→이전 수정본→새 후보 SHA·인자·보호 조건을 확인했다. 아직 NAS 비교/배포/운영 속도 변경은 없다. NAS에서도 효과가 없으면 이 순서 변경과 전용 검사를 철회하고 이전 두 개선을 유지한다. 물리적 파일 공유는 별도 writer/reader 계약 검증이 필요하며 현재 코드가 실제 파일 수를 줄였다고 주장하지 않는다. [근거](../artifacts/recorder-group-sync-review-20261014.json).

## 2026-10-14 속도 제한 계산과 남은 본문 파일 비용 확인

현재 `_write`는 64KiB마다 먼저 `len(block)/rate`만큼 sleep한 뒤 쓰며 파일 flush/fsync는 별도다. 디스크/동기화 대기로 전체 처리량이 5MiB/s 미만이어도 설정별 선불 휴식이 추가된다. 무제한에서는 기존 직접 write 경로로 분할/블록별 sleep 호출도 빠진다. 따라서 평균 전체 저장 처리량 4.60MiB/s가 장치나 개별 쓰기 구간의 속도 상한을 뜻하지 않는다. 실제 기록된 pacing/cooldown은 5/7/10MiB/s 평균 약 11.0/8.2/6.1초, 무제한 1.25초다. 100MiB/s는 실측하지 않았다.

같은 보존 16,384건의 metadata/descriptor만 읽어 unique payload 1,775개, block root/file 39개·31,887,342바이트를 확인했다. 그중 **36개는 1MiB 이하**, 39개 모두 8MiB 이하이고 일반 payload는 1,736개·7,701,063바이트다. 본문 45회 sync 중 block 전용 파일 39개가 큰 비중이며 기존 digest 기반 중복 제거는 이미 적용돼 있다. 현재 blocks/v1 reader가 digest별 파일명을 요구하므로 16MiB 상한만 올려서는 합쳐지지 않는다.

우선 검토는 작은 block 본문을 공유 저장 파일에 묶되 논리 입력/체크섬·구형 reader 호환·bounded 복원·실패 prefix를 보존하는 writer/reader 계약이다. 이후 봉인된 큐의 최소 휴식(이번 무제한 1.25초)과 직렬화/hash CPU(약 2초)를 검토한다. 이 단계는 원본을 읽기 전용으로 확인했으며 새 코드 수정·회귀 실행·배포·운영 속도 변경은 없다. [근거](../artifacts/recorder-remaining-costs-20261014.json).

## 2026-10-14 속도 범위 NAS 비교 완료 — 무제한이 격리 저장에서 가장 빠름

실제 보존 입력 16,384건·출력 49,944,996바이트·동일 Writer/빈 출력 디렉터리를 3·5·7·10MiB/s·byte pacing 없음 각 두 번 정순/역순으로 비교했다. 평균 전체 저장 시간은 **31.034 / 24.047 / 21.487 / 19.942 / 10.363초**다. 무제한의 두 실행은 10.219/10.506초, 전체 저장 기준 실제 처리량 4.66/4.53MiB/s였다. 이는 이번 약 47.6MiB 저장 사례의 처리량으로 NAS 장치의 최대 대역폭이나 전체 녹화 지속 처리량은 아니다. 모든 조건의 metadata 6개·본문/metadata fsync 51회·내용/순번/hash/checksum·pending 상한 4,096행·종료 charge/pending/drop/reject 0은 동일하다. CPU는 3MiB/s 약 2.84→무제한 약 2.00초, RSS는 약 305~312MB다.

속도 상한은 쓰기 구간의 pacing에만 적용되므로 전체 저장 속도와 비례하지 않는다. 3→10MiB/s는 설정 3.33배지만 전체 처리속도는 약 1.56배다. 무제한에서도 metadata/payload 동기화에 평균 **7.333초(전체 10.363초의 약 71%)**가 걸리고 직렬화/hash·최소 휴식도 남는다. 또한 기존 `_write`에서 제한 없음은 본문을 직접 쓰고, 제한 있음은 64KiB 블록별 write/sleep을 수행하므로 비교에는 블록 호출 비용 차이도 포함된다. 동기화 시간이 일부 줄어든 내부 원인이나 원래 장중/전체 녹화의 긴 대기는 이 결과로 확정하지 않는다.

job `b24d5610cae132a256b1aff033bf5734` 완료 뒤 운영 health ok·idle·기존 통합 release/복구를 확인했다. 원본·운영 DB·runtime/helper/sudoers·기존 계획은 유지하고 운영 속도는 **1MiB/s**, 수정/상향은 미배포다. 이번 속도 범위 비교는 완료하고 추가 속도 sweep을 반복하지 않는다. 무제한을 격리 파일 저장의 최선 후보로 두며 다음은 실제 native DB/동시 I/O 영향 확인과 기존 API/예약 계약을 보존하는 적용 준비다. 장중 COMMIT P1·전체 앱/Replay 수락은 여전히 미해결이다. [실측 근거](../artifacts/recorder-rate-range-nas-result-20261014.json).

## 2026-10-14 R4 native Risk·타점 입력 연결 — 주문 없는 첫 범위 종료

기존 Risk 저장본을 같은 계좌 RAM에 복사하고, 명시 선택 가설의 기존 R3 결과와 함께 공통 비교 한 벌에 전달한다. 원래 내용/계좌/as_of·가용/만료·타점 ref/quality를 검증한다. HOLD를 증액 허가로 바꾸지 않고 WAIT/모호함은 차단, 유효 EXIT는 EV/Risk 결손보다 보호 경로 필요를 우선 표시한다. 실제 Risk 승인·주문 권한은 없다. [56단계](AI_TRADING_SYSTEM_REDESIGN.md#56-r4-기존-타점과-native-risk의-공통-ram-입력-연결).

신규 15건 포함 최종 관련 **173건(61.455초)** 통과. 8개 getter/소비의 추가 SQL/계좌 조회 0·회차당 source/비교 각 1회·계좌 이벤트 H1/H2 추가 계산 0을 확인했다. native UNKNOWN/시점/범위/내용·기존 부분체결/예약·타점/복구와 이전 고정 결과/ID·CLI/ASGI/Qt·source SHA/AST/build를 검증했다. DB 소비 감사 delta 0, catalog는 별도 미등록 `tests.unit.test_prepared_historical_archive_inputs`로 실패했다. 최초 fixture/복구 검사 실패와 최종 성공은 `data/diagnostics/20261014-position-native-inputs/`에 구분 보존했다.

로컬 build `2026.10.14-position-native-inputs-v1`은 미커밋·미배포다. R4 주문 없는 첫 기능 범위를 종료하고 다음은 R4 후 미체결 실행 정책의 기존 취소/대사 계약 연결을 주문 없는 모의 검증으로 진행한다. 상위 동적 비중은 원래 Position·Risk·Capacity/Liquidity를 따라 후속 구현하며 현재 금액/수량은 하위 기능이다. 실제 계좌/운영 Risk·재주문/보호 지연·PG/NAS/장중 수락·전체 회귀/CI·push·활성화/배포는 미실행이다.

## 2026-10-14 3MiB/s NAS 효과 확인·요청한 속도 범위 시험 준비

동일 Writer/실제 입력 16,384건/빈 출력 디렉터리의 2→3MiB/s 비교는 **39.200→30.403초(22.44%)**, **39.218→29.527초(24.71%)**로 두 순서 모두 개선됐다. 49,944,996바이트·metadata 6개·본문/metadata fsync 51회·checksum/순번·pending 4,096행/종료 charge/pending/drop/reject 0은 유지한다. CPU 3.048/3.057→2.802/2.798초, RSS 약 304~309MB로 급증이 없었다. payload fsync p95는 582.5/553.6→411.5/383.8ms, 최대는 635.2/832.8→537.2/620.9ms로 이번 두 쌍에서 악화되지 않았다. 큐 p95는 36.755/36.139→27.746/27.201초, 최대는 39.040/39.045→30.278/29.403초다.

job `69078d394423ae966a44d8676aaf64f3` 완료 뒤 실제 NAS health ok·idle·기존 release·복구를 확인했다. 운영 속도 1MiB/s와 원본/DB/기존 계획/runtime/helper/sudoers는 유지하며 미배포다. 3MiB/s의 격리 저장 효과를 확인한 것이고 운영 DB 동시 I/O·native SQL/COMMIT P1·전체 앱 수락은 별도다.

사용자가 5·7·10MiB/s와 최대속도까지 요청해 **3·5·7·10MiB/s·쓰기 속도 제한 없음**을 각 두 번, 정순/역순으로 한 고정 시험에 묶었다. 무제한은 byte pacing만 해제하고 fsync·완료 공개·checksum·메모리/입력 상한은 유지한다. 최대 15분·기존 운영 일시정지/격리/복구 방식이며 원본/운영 제어는 변경하지 않는다. 같은 검증된 Worker/입력 결합·다섯 속도·인자·보호 조건을 NAS 읽기 전용으로 확인했고 실제 범위 시험은 아직 미실행이다. 속도별 처리량/동기화 p95·최대/큐/CPU/RSS를 보고 포화 지점과 적용 후보를 판단한다. [근거](../artifacts/recorder-rate3-nas-result-20261014.json).

## 2026-10-14 R4 주문 없는 행동 권고 연결

같은 HOLD 짝의 비용 차감 순차이·명시 표준오차 페널티·연구 위험 제약을 기존 비교 함수/CLI·공통 Runtime/PC 표에 연결했다. 제약의 계좌/종목/input cut·시각 guard, 증액 금지, 결손/동률 WAIT와 보호 EXIT 우선을 적용하되 실제 Risk 승인·주문 권한은 없다. 최종 목표 비중은 원래 Position·Risk·Capacity/Liquidity 설계에 따른 상위 동적 정책이 결정하며 현재 목표 금액·수량은 하위 계산으로 유지한다. [55단계](AI_TRADING_SYSTEM_REDESIGN.md#55-r4-비용불확실성위험-제약의-주문-없는-행동-권고).

신규 13건과 native 소비 검사 보완 포함 관련 **164건(58.152초)** 통과. 8개 소비의 회차당 source/비교 각 1회·GET 추가 계산/SQL 0회, 같은 권고의 추가 기록 0회·정책 만료/ACK 유실/재시작·legacy 결과/ID를 확인했다. CLI/ASGI health/선택·source/SHA/AST·한글 Qt 표시/build 3곳과 DB 소비 감사 delta 0을 확인했다. catalog는 별도 미등록 `tests.unit.test_prepared_historical_archive_inputs`로 실패했다. 근거 `data/diagnostics/20261014-position-action-selection/`.

현재 로컬 build `2026.10.14-position-action-selection-v1`은 미커밋·미배포다. 다음은 기존 타점·native Risk 관측의 좁은 입력/제약 연결 검증이다. 동적 비중조절·운영 승격·미체결 실행·실제 앱/계좌/PG/NAS/장중 수락은 후속이며 전체 회귀/CI·push·주문 활성화·배포는 수행하지 않았다.

## 2026-10-14 1/2MiB/s NAS 비교 완료 — 3MiB/s 추가 후보

같은 묶음/휴식 수정본과 실제 입력 16,384건의 두 순서 비교에서 1→2MiB/s는 **62.399→37.752초(39.50%)**, **61.318→37.314초(39.15%)** 단축했다. 49,944,996바이트·metadata 6개·본문/metadata fsync 51회·순번/내용/checksum·reader 한도·pending 4,096행/종료 charge/pending/drop/reject 0은 동일하다. CPU 3.67/3.647→3.062/3.007초, RSS 약 305~309MB로 급증이 없고 큐 p95는 59.153/57.715→35.319/35.414초, 최대는 62.123/61.041→37.592/37.144초다. payload fsync p95는 558.4/419.9→456.3/440.0ms, 최대는 678.1/456.2→664.3/615.1ms로 일부 증가했으므로 동기화 자체를 해결한 결과로 해석하지 않는다.

job `3a27cf3817faee7e13cd03970df202ef` 완료 뒤 운영 health ok·idle·기존 통합 release/복구를 재확인했다. 두 코드 수정과 속도 후보의 격리 NAS 비교는 여기까지 완료했으며 운영 속도는 1MiB/s, 미배포다. 운영 앱을 일시정지한 시험이므로 native SQL/COMMIT·동시 운영 DB I/O 영향·전체 앱 수락은 미검증이다. 기존 시작 capabilities와 예약기는 현재 1MiB/s에 묶여 있어 운영 상향 시 기존 계획을 보존하는 계약 검증도 필요하다.

사용자의 3MiB/s 질문에 따라 같은 입력/수정본에서 **2→3MiB/s**로 포화/동기화 비용을 구분하는 고정 두 순서 시험을 준비했다. 기존 1/2 시험의 검증된 worker를 기준으로 격리 session 속도만 바꿨고 NAS에서 코드 hash·동일 Writer·인자·저장 중 거부 조건을 확인했다. 3MiB/s 성능은 아직 미측정이며 기존 도구·원본·운영 제어를 바꾸지 않았다. 반복 1/2 재측정이나 3MiB/s 운영 설정 변경은 수행하지 않는다. [실측 근거](../artifacts/recorder-rate-nas-result-20261014.json).

## 2026-10-14 저장 묶음 확대 — NAS 실제 개선 확인

휴식 수정본 대비 지연 저장 metadata 한도 확대를 같은 실제 입력 16,384건/빈 출력 디렉터리/1MiB/s에서 비교해 **66.112→64.879초(1.86%)**, **64.335→61.577초(4.29%)** 단축했다. metadata 파일 10→6개, metadata/payload fsync 59→51회이며 49,944,996바이트와 원문 metadata hash·본문 checksum·순번이 동일하다. 새 최대 chunk 1,999,825바이트는 기존 reader 2,000,000바이트 안이고 pending 상한 4,096행, 종료 charge/pending/drop/reject 0을 유지했다. CPU 3.717/3.691→3.726/3.617초, RSS 약 304~308MB로 급증은 없었다.

큐 대기 p95는 65.778/64.002→60.502/58.286초, 최대는 65.795/64.021→64.584/61.288초로 줄었다. 다만 **평균은 34.766/33.872→36.365/34.300초(4.60%/1.27% 증가)**다. 모두 저장 시작 전 준비한 입력에서 큰 묶음의 공개 시점이 달라지는 교환관계로 기록하며 모든 지연이 개선됐다고 주장하지 않는다. 이 단계는 지연 파일 저장에만 적용되고 일반 녹화의 1MiB 한도·순서·본문 16MiB·동기화·완료 공개 계약은 유지한다. 동일 내용/감소한 동기화/두 쌍의 처리시간 개선과 메모리 상한 근거로 코드를 유지하고 이 prefix 범위 검증을 완료한다.

job `41a60351aac9e85f07505bf3e9efb55f` 완료 뒤 NAS health ok·idle·기존 통합 release와 운영 복구를 재확인했다. 원본·운영 DB·runtime/helper/sudoers·계획을 유지했으며 두 수정은 아직 미배포다. 장시간 fsync의 원인·장중 COMMIT P1·전체 앱 성능은 별도 미해결이다. 다음 같은 수정본의 1/2MiB/s 비교 실행기 hash·인자·보호 조건을 확인했다. [실측 근거](../artifacts/recorder-batch-nas-result-20261014.json).

## 2026-10-14 동기화 후 추가 휴식 제거 — NAS 실제 개선 확인

같은 실제 보존 입력 16,384건/빈 출력 디렉터리/1MiB/s의 두 순서 비교에서 **68.179→62.793초(7.90%)**, **70.120→63.788초(9.03%)** 단축했다. 완료된 fsync를 추가 휴식에 다시 포함하던 최소 수정은 유지한다. 각 실행의 49,944,996바이트, metadata 10개·payload 49회/합계 59회 fsync, 순번·metadata hash·본문 checksum이 동일하며 pending 상한 4,096행·종료 charge/pending/drop/reject 0이다. CPU 3.74/3.81→3.667/3.66초, RSS 약 304~311MB로 급증이 없었다. 큐 대기 p95는 67.887/69.303→62.282/63.486초로 줄었다. payload fsync p95는 357.0/399.8→510.1/477.6ms, 최대는 376.2/543.4→516.8/652.0ms이므로 동기화 자체나 전체 녹화의 17초 동기화 지연이 해결됐다는 결과는 아니다.

검사 job `ded92d8865afb8802b73c963cf34d8c2`는 완료됐고 이후 읽기 전용 조회에서 운영 health ok·idle·기존 통합 release와 운영 일시정지 복구를 재확인했다. 원본·운영 DB·runtime/helper/sudoers·기존 계획은 유지하고 배포하지 않았다. 이 수정의 저장 prefix 범위 성능 검증은 완료이며 코드 유지/배포 대기다. native SQL/COMMIT·revision·전체 앱 수락은 미검증이고 COMMIT P1은 계속 미해결이다. 다음 묶음 확대 NAS A/B의 hash·인자·보호 조건도 확인했다. [실측 근거](../artifacts/recorder-sync-wait-nas-result-20261014.json).

## 2026-10-14 R4 native ADD·REDUCE·REENTER 자료 연결

기존 연구 함수의 명시 typed 목표 계획→같은 PaperExecutionEngine/기존 writer·보고/manifest→readonly native proof→공통 비교/CLI·PC 선택을 연결했다. ADD 원가 누적·REDUCE 잔여 원가·REENTER 선행 청산/flat HOLD와 위험/전략 EXIT의 target 선점 취소를 검증했다. 기본 연구 event/state와 실제 broker 경로를 유지한다. KST 원장을 UTC 문자열로 거절하던 reader 원인은 실제 runner 왕복으로 확정해 저장 변경 없이 SQL 시각 비교·정밀 cut 검사를 보완했다. [54단계](AI_TRADING_SYSTEM_REDESIGN.md#54-r4-자동-목표의-native-addreducereenter-실행-자료-연결).

신규 13건 포함 관련 214건 뒤 최종 표시/경계 50건·native 29건 각각 통과·실패/오류/skip 0. 중복 묶음을 전수검사로 합산하지 않는다. 실제 준비된 격리 개발 입력의 runner/ELIGIBLE 보고/원장·reader 왕복, legacy event/state·typed/CLI/Qt 6/6창·3행 한글·현재 source/import/SHA/AST·감사 delta 0/catalog/build를 확인했다. 6 run native SELECT 24회/COMMIT 0회와 생성 schema SELECT 1회이며 GET/tick 새 SQL/TR은 없다. 근거 `data/diagnostics/20261013-position-action-native/`.

현재 로컬 build `2026.10.14-position-action-native-v1`은 미커밋·미배포다. 다음은 비용·불확실성·위험을 반영하는 R4 행동 선택의 주문 없는 연결이다. 계획 생성 폼/자동 예약·예측 보정/운영 승격·미체결 실행·실제 앱/운용 연구 데이터·broker/PG/NAS/장중 수락은 남는다. 전체 회귀/CI·push·계좌/주문 활성화·배포는 미실행이다.

## 2026-10-14 원본 녹화 저장 종료 확인 — NAS A/B 관리자 실행 대기

10/13 장중 원본 `20261012T235950Z-fb98b0ddcee9`의 **accepted=written=2,485,437**, 파일 쓰기 3,560,130,242바이트, metadata 1,557개·payload fsync 3,249회를 읽기 전용으로 확인했다. queued/pending/packed/packing/charge/copy reserve는 모두 0이다. NAS `health=ok`, 원래 통합 release 유지, operator의 `diagnostics_idle=true`도 확인했다. 최종 상태는 기존 입력 거부 4건(`unsupported_document_collection`) 때문에 `incomplete`이며 known_dropped는 0이다. 수락 입력의 durable drain과 전체 캡처/Replay 수락을 구분한다. 이번 확인에서 전체 파일 checksum 순회는 실행하지 않았다.

현재 소스·installed helper·고정 비교 worker hash와 selected 인자·persisting 중 거부 조건을 재확인했다. 첫 동기화 후 추가 휴식 제거 NAS A/B를 `sudo -n`으로 시도했지만 비밀번호 요구로 시작되지 않았다. 사용자에게 준비된 고정 명령의 관리자 실행을 요청했다. 시험 job 생성·운영 앱 일시정지·배포·예약·속도 변경은 없다. 동기화/묶음/1·2MiB/s NAS 실측과 운영 복구 확인은 아직 남는다. [근거](../artifacts/recorder-storage-finished-20261014.json).

## 2026-10-13 R4 자동 목표 수량 정책의 공통 계산 연결

명시한 원화 목표·보유 원화/주식 한도·남길 현금·가용/만료 구간의 bounded 정책에서 ADD/REDUCE/REENTER 목표를 생성한다. 기존 부분체결/예약·native 확인 잔량·비용 반올림/자금·상충/미확인 주문 차단을 재사용하고 행동을 자동 변경하지 않는다. 기존 HOLD/EXIT와 정책 없는 결과/ID 전체는 유지했다. 자체 정책 자료가 없으면 EV_UNAVAILABLE이며 주문 권한은 false다. 기존 JSON/CLI·선택 저장/복구·공통 RAM/관찰 표 연결이며 새 DB/API/수집 owner는 없다. [53단계](AI_TRADING_SYSTEM_REDESIGN.md#53-r4-자동-목표-수량-정책의-공통-shadow-계산-연결).

신규 13건 포함 최종 관련 **115건(27.457초)** 통과·실패/오류/skip 0. 8개 소비의 회차당 source read/비교 각 1회, 반복 GET 추가 계산/SQL 0회·legacy 결과 전체/ID·저장/ACK 유실·재시작·현재 import/SHA/AST·감사 delta 0/catalog·CLI/build/한글 offscreen 화면을 확인했다. 최초 실패는 테스트 fixture/시각 표기/QA 예제 생성에서 수정하고 기록을 보존했다. 근거 `data/diagnostics/20261013-position-sizing-policy/`.

당시 로컬 build는 `2026.10.13-position-sizing-policy-v1`이며 미배포였다. 자체 자료의 첫 native 연결은 위 54단계에서 로컬 검증했다. 자동 행동/정책 선택·비중/손절 기반 sizing·예측 검증/운영 승격·미체결 실행과 실제 운영 수락은 남는다.

## 2026-10-13 R4 완결 행동 자료의 PC 선택 연결

기존 관찰 탭에서 연구 DB·자료 창 JSON·TRAIN/VALIDATION을 명시 선택해 공통 readonly reader의 HOLD/EXIT 결과를 비교 입력에 전달했다. 기존 목표/정책/계좌/cut을 유지하고 outcomes만 변경한다. 가져오기는 편집 입력이며 저장/적용은 별도다. 저장/재시작/GET/tick은 원장을 재조회하지 않고 PC 파일 경로를 서버에 보내지 않는다. 선택 변경/닫기/종료의 늦은 결과를 차단하고 실제 SQLite 중단/worker 완료까지 기존 수명이 소유한다. [52단계](AI_TRADING_SYSTEM_REDESIGN.md#52-r4-완결-행동-자료의-기존-pc-관찰-선택-연결).

신규 6건 포함 관련 **122건(35.175초)** 통과·실패/오류/skip 0, 현재 import/SHA/AST·소비 감사 delta 0·catalog 438 unit/26 integration·build 3곳/diff 확인. 격리 native SQLite/Qt fixture와 820×850 offscreen 한글 화면 수락이며 실제 운용 연구 run/PC 앱·broker/PG/NAS·장중 성능 수락은 남는다. 4 run 가져오기에서 native SELECT 16회/COMMIT 0회, 별도 생성 schema SELECT 1회이며 새 서버 GET/tick SQL/TR은 없다. 근거 `data/diagnostics/20261013-position-outcome-selection/`.

당시 로컬 build는 `2026.10.13-position-outcome-selection-v1`이며 미배포였다. 아래 51단계의 PC 연결을 완료했고 자동 bounded 목표 정책은 위 53단계에서 연결했다. 다른 행동의 자체 자료·예측 검증/운영 승격·미체결 정책 실행은 남는다.

## 2026-10-13 R4 기존 연구 원장의 HOLD/EXIT 완결 자료 reader

기존 ResearchRepository와 시뮬레이션 MARK/FILL 기록으로 고정 기간의 실제 포트폴리오 변화·비용을 한 번씩 계산해 기존 비교 CLI에 연결했다. 새 DB/시뮬레이터·서버 실시간 조회는 없다. source의 원래 정책/실행/비용/시각·초기 상태를 유지하며 미지원 ADD/REDUCE/REENTER·gap/미확인 주문/미래·다른 role·final/OOS/등록 final overlap·중복/예산은 차단한다. 완결 원장의 선택은 아직 자동 실행 승인이나 미래 EV 보정이 아니다. [51단계](AI_TRADING_SYSTEM_REDESIGN.md#51-r4-기존-연구-원장의-완결-행동-자료-reader-연결).

신규 10건·관련 79건(18.898초) 뒤 추가 경계/최종 build/health 33건(10.456초) 각각 통과, 실패/오류/skip 0이다. 묶음을 독립 112건으로 합산하지 않는다. 같은 run의 여러 창은 native 연결 1회/SELECT 4회/COMMIT 0회이며 기존 GET/tick 추가 SQL/TR은 없다. 실제 import/SHA/AST·소비 감사 delta 0/catalog·diff 확인. 근거 `data/diagnostics/20261013-position-outcome-reader/`. 첫 검사의 테스트 연결 정리/sys import 오류는 수정하고 최초 실패 로그를 보존했다.

당시 로컬 build는 `2026.10.13-position-outcome-reader-v1`이며 미배포였다. 수락은 기존 엔진의 격리 SQLite 시뮬레이션 fixture이며 실제 연구 run/모의 broker·PG/NAS/장중 성능 수락은 남는다. 자료의 기존 PC 관찰 선택은 위 52단계에서 연결했다. 다른 행동의 자체 자료·자동 목표/예측 검증·R4 이후 미체결 정책 실행과 실제 운영 승격은 별도로 남는다.

## 2026-10-13 R4 이후 미체결 주문 관리 정책 설계

매수·일반 익절 매도·손절/위험 청산을 같은 실행 owner 안의 목적별 정책으로 구분했다. WAIT/CANCEL/REPRICE·REENTER/ABANDON을 현재 타점·슬리피지·관측 EV/미확인·위험/기한으로 판단하며, 강제 위험 제한은 R4 EV 추천보다 우선한다. 보호는 PC LLM·다음 1분봉·Shadow 완료를 기다리지 않는다. 기존 취소 ACK의 CANCEL_PENDING·broker 대사·실제 FILL/잔고 확인과 멱등/권한 경계를 재사용하고 취소 확정 전 재주문·UNKNOWN 중복 제출을 금지한다. [정본 7.6절](AI_TRADING_SYSTEM_REDESIGN.md#76-미체결-주문-관리-정책).

이번 변경은 설계/로드맵/잔여 범위 명시다. 실행 코드·API/DB·build·주문/운영 설정은 변경하지 않았고 자동 선택/재주문·보호 우선 깨우기/지연·실제 모의계좌 수락은 아직 미구현이다. 현재 지원 지정가/세션과 사용자 STOP 의미를 유지한다. 시장가/공격적 지정가도 체결을 보장하지 않으며 미지원 주문 유형은 별도 검증 전 차단한다. 문서 링크/현재 코드 계약 대조를 확인하며 실행 회귀는 이 문서 변경 때문에 반복하지 않는다.

## 2026-10-13 저장 속도 1/2MiB/s 비교 — PC 실측 통과, NAS 대기

휴식·묶음 수정본의 동일 Writer 소스에서 격리 session의 속도만 1/2MiB/s로 바꿔 실제 보존 입력 16,384건을 각 두 번 저장했다. 같은 빈 출력 디렉터리 조건의 순서 교차 비교에서 **52.912→29.361초(44.5%)**, **52.644→28.846초(45.2%)** 단축했다. 가상 시간 없이 PC 디스크의 실제 쓰기/fsync를 실행한 결과이며 NAS 성능 수락은 아니다. 두 쌍 모두 49,944,996바이트, metadata 6개·본문/metadata fsync 51회, 순번·metadata hash·본문 checksum이 동일하고 최대 pending 4,096행, 종료 charge/pending/drop/reject 0이었다. CPU는 0.938~1.016→0.703~0.797초, RSS는 약 288~290MB 범위다. 본문 fsync p95는 77.7/98.2→94.2/109.0ms, 최대는 103.8/122.9→149.3/152.2ms로 증가했다. 큐 대기 p95는 49.854/49.588→27.435/26.985초다. 이 큐 대기는 모든 선택 입력을 저장 시작 전에 준비한 조건이다.

고정 NAS 속도 비교 실행기의 동일 소스·입력 결합, selected 인자, 원본 persisting 중 시험 생성 전 거부를 읽기 전용으로 확인했다. 원본이 아직 저장 중이어서 NAS 시험/일시정지/속도 변경/배포는 수행하지 않았다. 21:43 KST 마지막 조회는 1,275,234/2,485,437건(51.3%) 저장, 확인된 drop 0·기존 입력 거부 4건 유지다. 다음은 durable idle 후 준비된 NAS 수정·속도 A/B와 운영 복구 확인이다. 운영 앱을 일시정지하는 격리 파일 시험만으로 운영 PostgreSQL과 동시 I/O 영향을 수락하지 않는다. 운영 속도는 1MiB/s이며 장중 COMMIT P1은 미해결이다. [근거](../artifacts/recorder-rate-review-20261013.json).

## 2026-10-13 지연 저장의 메타데이터 묶음 확대

동기화 대기 제외 수정본을 기준으로 지연 저장의 메타데이터 묶음만 기존 reader 한도인 **2,000,000바이트**로 확대했다. 일반 녹화·단일 metadata/RAM segment의 1MiB, 본문 묶음 16MiB, 1MiB/s, pending 4,096행/metadata 8MiB, 순서·checksum·파일별 fsync·최종 manifest 동기화 계약은 유지한다. 관련 7모듈 67건과 기본 큰 묶음의 두 번째 쓰기 실패/prefix·RAM suffix 보존 1건이 통과했다.

같은 실제 보존 입력 16,384건의 두 순서 비교에서 메타데이터 파일 **10→6**, 본문·메타데이터 fsync **59→51(13.6% 감소)**, 전체 49,944,996바이트와 내용/순번/체크섬은 동일하다. 가장 큰 새 metadata 파일은 1,999,825바이트로 기존 읽기 한도 안이다. **가상 2초 동기화 조건의 시간은 약 174.5→157.6초(추가 9.7% 감소)이며 NAS 실측 개선이 아니다.** 최대 pending은 4,096행, 비교 종료 후 pending/charge/drop/reject는 0이었다.

NAS 고정 A/B 실행기의 소스 hash 결합·selected 인자·저장 중 거부 조건은 확인했지만 현재 원본이 persisting이므로 시험/운영 일시정지/배포는 수행하지 않았다. NAS 실행 소스는 아래 통합 커밋이며 두 수정은 미커밋·미배포다. NAS 실제 큐/fsync p95·최대/CPU/RSS·운영 복구와 부하 영향을 확인해야 완료 판정한다. 다음 후보는 같은 입력의 1/2MiB/s 비교이며 운영 속도를 상향하지 않았다. 근거: `artifacts/recorder-batch-review-20261013.json`.

## 2026-10-13 녹화 저장의 동기화 대기 중복 산정 보완

운영 파일 저장에서 완료된 동기화 시간을 추가 휴식에 다시 포함하는 비용을 확인해 기존 `diagnostic_trace` 한 파일을 최소 수정했다. 관련 회귀 42건과 실제 보존 입력 16,384건의 순서 교차 비교 두 번이 통과했다. **가상 2초 동기화 조건에서 약 19.4% 감소이며 NAS 실제 성능 수락은 아니다.** 파일/본문 체크섬·출력 49,944,996바이트·payload/metadata fsync 59회·pending 4,096행 상한은 동일하다. 첫 비교기의 가상 시간 연결 누락 결과는 제외했고 연결 실행 검사를 보강했다. 아래 이미 배포된 통합 커밋과 별개의 미커밋 수정이며 NAS에 반영하지 않았다.

20:44 KST 읽기 확인에서 오늘 원본은 `persisting`, 280,267/2,485,437건(약 11.3%) 저장이고 확인된 드롭 0·입력 거부 4건을 유지했다. 따라서 기존 `trace_not_durably_idle` 조건을 지키며 NAS A/B를 아직 실행하지 않았다. 고정 검사 실행기와 입력/코드 hash 결합·selected 인자·저장 중 거부 확인은 준비 완료다. 원본 저장 완료 뒤 같은 보존 입력/빈 출력 디렉터리로 NAS 두 쌍을 비교하고 운영 앱 복구·실제 지연/처리량/큐·CPU/RSS를 확인해야 완료 판단할 수 있다. 파일 동기화가 느린 장치/RAID/다른 I/O 원인과 장중 10~20초 COMMIT P1은 미해결이다. 근거: `artifacts/recorder-sync-wait-review-20261013.json`.

## 2026-10-13 녹화 전 NAS 통합 커밋 배포 완료

08:42 KST에 검증한 커밋 `232737d33b3c1ac438a3e7dc3e3123817d19a868`을 NAS 릴리즈 `2026.10.13-integrated-recorder-v1-cfad9375c0c06f81`로 배포했다. 전체 4,241건 검사는 재실행하지 않고 기존 결과를 사용했으며, 같은 소스의 전용 PostgreSQL에서 TOP20 표시 녹화 native Replay 두 번·계좌 초기 상태 복원/봉인 rollback/native Replay 검사를 **2건 통과, skip 0**으로 확인했다(job `8efc113038cd9885857caab552a4ebc5`). 게시 소스 1,072개는 해당 커밋과 일치한다. 이 커밋 이후 다른 작업의 미커밋 R1/R2 변경은 배포에 포함하지 않았다.

배포 후 실제 `health=ok`, 새 build/source path, 운영 일시정지 복구·검사 정리를 확인했다. 예약 PID **5425**의 실행 인자와 새 릴리즈 계획이 일치하고, 읽기 전용 사전 검사가 통과했다. **오늘 10/13 08:59:50~09:59:50 녹화·20:10 저장**, 다섯 입력 옵션·8GiB·500만 이벤트·1MiB/s를 유지한다. 실제 계획은 `artifacts/nas-integrated-20261013/today.plan.json`이다. 확인 시 녹화 시작 전이며 NAS 재부팅 후 예약 생존은 지원하지 않는다. 운영 DB 컨테이너·런타임 이미지·sudoers는 유지했고 관리자 배포 계약의 빌드 표식만 갱신했다. 설치 helper 코드는 이번에 교체하지 않았다. 실제 장중 녹화/저장 결과와 전체 앱 성능 수락은 별도다.

## 2026-10-13 R4 계좌 전체 대사와 확인된 잔량 계산

명시 live 선택 계좌만 기존 recovery 전후에 전체 실행의 미종결 원장을 각각 한 번 읽는다. 기존 브로커 응답의 수동 주문·원래 수량/방향/가격과 확인된 잔량을 공통 RAM으로 전달한다. 다른 실행의 원장/체결은 쓰지 않는다. QUEUED/전송 미확인·취소 대기·누락/모호한 근거·예산/조회 실패·조회 중 실행 변경은 추가 수량을 차단한다. 완전한 근거만 주문 없이 목표 수량을 계산하고 원래 고정 입력 계약을 유지한다. [50단계](AI_TRADING_SYSTEM_REDESIGN.md#50-r4-계좌-전체-대사와-브로커-확인-잔량-계산).

신규 22건 포함 최종 관련 **188건(74.372초)**·정적 DB 계약 검사 **5건** 통과, 실패/오류/skip 0이다. native SQLite 고정 빈 주문의 24쌍 ON/OFF 비교는 연결/SELECT 각 1→3, COMMIT 1→1·기존 broker recovery 1→1이다. owner 회복 중앙값 2.288→6.700ms로 추가 확인 비용이 있으며 실제 장중 판단 지연 측정은 아니다. 8개 Shadow의 source read/비교 계산 각 1회·RAM/화면 getter DB 0회다. 현재 import/SHA·AST·추가 read 메서드/소비 각 1개와 기존 계약 보존·catalog 438 unit/26 integration을 확인했다. 근거 `data/diagnostics/20261013-position-account-reconciliation/`.

로컬 미커밋 build는 `2026.10.13-position-account-reconciliation-v1`이다. 실제 PG/NAS/모의계좌·장중 수락·주문/배포는 미실행이다. 조회는 원자적 브로커 계좌 cut이나 외부 프로세스의 연속 변경 검증이 아니며 주문 권한은 false다. 다음 기능은 행동별 실제 완결 자료의 기존 reader 연결이다. 자동 목표 정책·예측 검증·운영 승격은 별도로 남는다. 이전 49단계의 실행별 차단 설명은 당시 범위 기록이다.

## 2026-10-13 R4 기존 모의계좌 RAM 관측 전달

기존 계좌 owner가 이미 완료한 recovery의 account/주문·자동모드 위험 자료를 RAM에 게시하고 검증된 현재 mock binding으로 공통 Shadow에 전달한다. 명시 live 선택에서 계좌 수량·기준가격을 같은 Collector RAM cut으로 갱신하고 고정 목표·비용/행동 정책은 유지한다. 새 계좌 TR/DB 조회·owner 활성화·1초봉 저장은 없다. source 부재/알림·조회 실패·종료/임대 실패는 자료를 무효화하며 고정 입력으로 fallback하지 않는다.

현재 로컬 원장은 실행별 범위이므로 계좌 전체 미체결 완전성은 false다. 실제 보유/확인된 주문은 표시하지만 추가 수량·EV는 기존 규칙이 차단한다. 계좌 자료 시각/만료와 미확인 근거를 표시하고 주문/Replay 승인으로 승격하지 않는다. [49단계](AI_TRADING_SYSTEM_REDESIGN.md#49-기존-모의계좌-owner의-ram-관측을-공통-r4-입력으로-전달).

신규 총 11건·관련 **160건(93.093초)** 통과 뒤 최종 해제/종료·임대/선별 기록/표시 보완은 직접 관련 **18건**으로 재검증했다. 두 단계 각각 실패/오류/skip 0이며 최종 SHA/import/AST·소비 감사 delta 0/catalog·격리 Qt 픽셀/스크롤을 확인했다. 두 묶음을 178개 독립 검사 또는 최종 소스의 160건 단일 재실행으로 합산하지 않는다. 8개 관찰에서 RAM 계좌 read/R4 평가/Collector read 각 1회, 계좌 알림만 바뀔 때 H1/H2 추가 평가 0회·조회 추가 DB/read/계산 0회다. 첫 선별 COMMIT 1회·동일 상태의 관측 ref 갱신 추가 COMMIT 0회를 확인했다. 근거 `data/diagnostics/20261013-position-account-ram/`.

로컬 미배포 build는 `2026.10.13-position-account-ram-v1`다. 다음은 모든 실행·수동/QUEUED/전송 미확인 주문을 포함한 계좌 전체 대사 근거 연결이다. 실제 행동 자료 reader·자동 목표/예측 검증·실제 NAS/PG/모의계좌/장중 수락과 운영 승격은 남는다. 이번 단계의 커밋/push·배포·전체 회귀/CI·실제 주문/계좌 활성화는 없다.

## 2026-10-13 R4 목표·행동 비교의 공통 관찰 연결

기존 관찰 탭에서 고정 행동 비교 입력 JSON을 명시 가져와 같은 선택 API/Runtime·선별 기록/인증 조회로 전달한다. 입력 적용 때 기존 계산 한 번, 여러 테마/틱·조회는 공통 결과를 재사용한다. 수량/미체결·비용 차감 평균·HOLD 대비 차이·표본/표준오차·미확인을 표시하며 고정 자료의 원래 시각/ID와 실시간 계좌 자료 아님·주문 없음 표시를 유지한다. 입력/결과 각각 256 KiB·mock/KRX·관찰 테마 구성원 범위를 검증한다.

신규 6건 포함 관련 **105건 통과(32.300초), 실패/오류/skip 0**·현재 source SHA/AST·DB 소비 감사 delta 0·catalog·diff 확인. 8개 관찰에서 RAM 읽기 1회/R4 계산 1회/기존 기록 COMMIT 1회, 같은 상태 다음 cut의 R4 계산/추가 COMMIT 0회·반복 live 조회 DB 연결 0회를 확인했다. 기본 820×850 창의 한글 폰트 격리 Qt 픽셀/스크롤도 확인했다. 최초 검사 코드의 import/JSON·KST/UTC 문자열 비교 실패는 수정 후 최종 묶음으로 재검증했고 최초 로그를 보존했다. [48단계](AI_TRADING_SYSTEM_REDESIGN.md#48-r4-목표행동-비교의-기존-공통-shadow관찰-소비-연결) · 근거 `data/diagnostics/20261013-position-comparison-view/`.

로컬 미배포 build는 `2026.10.13-position-comparison-view-v1`다. 실제 계좌 owner 관측 자동 전달·행동별 실제 자료 reader·자동 목표/예측 검증과 운영 승격은 남는다. 현재 execution 원장은 실행별 범위이므로 계좌 전체 미체결로 간주하지 않는다. 다음은 기존 owner의 계좌·주문 관측 연결이다. 실제 NAS/PG/계좌·장중 수락·지연 개선·전체 회귀/CI·커밋/push·배포는 이번 단계에서 실행하지 않았다.

## 2026-10-13 R4 행동별 비용 차감 관측 EV/불확실성

명시 목표 계산을 재사용하고 연구 계산부에 같은 초기 상태·기간의 완결 행동 자료 비교를 연결했다. HOLD 대비 짝이 맞는 사례의 비용 차감 평균/차이·표본 수·표준편차/표준오차를 함께 반환한다. 관측 집단의 통계이며 보정된 미래 확률/운영 승격으로 쓰지 않는다. ADD 자료가 없어도 다른 행동의 비교는 유지하고, 부족한 행동·미래/불완전/비용 미확인·현재 차단/자금 부족 목표는 수치를 만들지 않는다.

신규 15건 포함 관련 **100건 통과(969.926초), 실패/오류/skip 0**·실제 다섯 import/SHA·AST/catalog/diff 확인. 고정 입력 typed/CLI 연결 범위다. 새 데이터 조회/원장/Collector·체결 시뮬레이터/주문·계좌 owner는 없다. 실제 행동별 자료 준비/reader·실시간 계좌/관찰 화면 소비·예측 검증/운영 승격·NAS/PG/장중 수락은 남는다. [계약/검증](AI_TRADING_SYSTEM_REDESIGN.md#47-r4-행동별-비용-차감-관측-기대값과-불확실성-비교).

## 2026-10-13 R4 첫 목표 수량 계산

R3의 운용 진단/실제 승격과 분리해 R4 개발을 시작했다. 명시 목표량·기존 AccountSnapshot/ExecutionRecord를 같은 순수 계산에 전달해 실제 수량 + 확인된 미체결 매수 - 확인된 미체결 매도를 목표와 비교한다. 부분체결은 잔량만 사용하고 예약 자금은 한 번 차감하며 기존 연구 비용/반올림을 공유해 추가 가능 수량을 계산한다. 미정 목표를 0으로 바꾸지 않으며 UNKNOWN·취소 대기/불완전 대사·미확인 비용은 새 수량 계산을 차단한다.

신규 15건 포함 관련 **114건 통과(29.324초), 실패/오류/skip 0**·현재 소스 네 SHA/import 경로·catalog/diff 확인. 고정 typed/CLI 입력 연결과 로컬 검증 범위다. 새 DB 조회/원장/Collector·계좌 owner·주문/체결은 없고 build/배포는 기존 상태다. 목표량 자동 정책·비용 차감 EV 비교·실시간 계좌/화면 소비와 운용 승인·NAS/PG/장중 수락은 남는다. [계약/검증](AI_TRADING_SYSTEM_REDESIGN.md#46-r4-명시-목표-수량과-기존-계좌주문-입력-연결).

## 2026-10-13 R3 RAM 출처의 녹화 입력 조회 힌트

기존 Collector Recorder high-water를 활성 trace/epoch와 대조해 같은 RAM snapshot의 capture_anchor로 고정하고 공통 출처·선별 한 행으로 전달했다. OFF/타 세션/재생·잘못된 ID는 새 snapshot에서 null, 거절된 최신 입력을 수락 ID로 만들지 않는다. 이전 판단은 당시 힌트를 유지한다. 힌트는 조회 위치이며 capture/native/운영 승인으로 쓰지 않는다. 현재 Recorder의 0g 제외도 미확인이다.

신규 **5건 포함 관련 108건 통과(39.902초), 실패/오류/skip 0**·아홉 import/SHA·소비 감사/catalog 통과. 실제 격리 Recorder의 봉인 reader row/input ID·component/time와 기존 SQLite 선택 행을 대조했다. RAM 조회 세 번은 녹화 payload를 늘리지 않았고 8 소비자는 token RAM 확인 1회·원본 writer/COMMIT 각 4회/동일 H1·H2 공유를 유지했다. [계약/검증](AI_TRADING_SYSTEM_REDESIGN.md#45-ram-출처의-기존-녹화-입력-조회-힌트) · 근거 data/diagnostics/20261013-shadow-capture-anchor/.

로컬 미배포 build는 `2026.10.13-shadow-capture-anchor-v1`다. 0g capture/전체 원본 값·lineage 대조·적격성/운영 승인과 실제 앱/PG/NAS/장중 수락은 남는다. 실제 녹화/활성화·계좌/주문·커밋/push·배포·전체 회귀/CI는 없다.

## 2026-10-13 R3 RAM 판단의 공통 출처/연속성 근거

기존 Collector RAM snapshot 한 번의 source/generation·승인/연속 시작·보존/gap·전달 누락을 회차 한 벌로 묶고 실제 context_ref/cut에 연결했다. 실패 slot은 이전 근거, 취소 publish도 실제 cut을 유지하고 이후 delivery 상태를 이전 입력에 섞지 않는다. optional map은 기존 live/선별 한 행에만 포함하며 기존 판단의 기록 예산을 넘으면 생략한다. raw 초봉 저장·틱 추가 read/COMMIT·원본 확정/Replay/운영 승인은 없다.

신규 **6건 포함 관련 142건 통과(91.717초), 실패/오류/skip 0**·현재 import/일곱 SHA·소비 감사/catalog 통과. 소비자 0/2/8의 원본 writer/COMMIT 각 4회·8테마 H1 1회/H2 8개와 기존 첫 선별 기록 1회가 유지됐다. 작은 8테마 입력 중앙값 7.070→7.174ms, 600 frame×20 종목의 8테마 입력 84.154→88.269ms. 이는 저장/장중 수락이 아니며 판단 ID는 전부 일치했다. [현재 계약/검증](AI_TRADING_SYSTEM_REDESIGN.md#44-ram-판단의-공통-출처와-연속성-근거-연결) · 근거 data/diagnostics/20261013-shadow-source-coverage/.

로컬 미배포 build는 `2026.10.13-shadow-source-coverage-v1`다. 실제 capture lineage/원본 값의 확정·적격성·운영 승인과 앱/PG/NAS·장시간/장중 수락은 남는다. 계좌/주문·활성화·커밋/push·배포·전체 회귀/CI는 없다.

## 2026-10-13 R3 관찰 화면의 수동 원본 대조

관찰 탭에서 저장된 연구 가설/revision과 1–60초 범위를 기존 Qt worker의 수동 source-check로 대조하고 일치·미확인·조회 비용/비승인 상태를 표시한다. 편집값 저장/PC DB 읽기·기본 live polling/틱 대조는 없다. 선택 reload/save에서 이전 결과 초기화, stale/승인 범위가 다른 응답 거절·실패/live 분리·중복/종료 join/늦은 결과 미표시를 확인했다.

신규 **5건 포함 관련 50건 통과(31.236초), 실패/오류/skip 0**·실제 import/SHA 여덟 파일·소비 감사/catalog·격리 native 8/8 대조 결과의 offscreen 픽셀 QA 통과. 서버/API/transaction/build 표식은 기존 `2026.10.13-shadow-source-check-v1` 그대로다. [현재 범위](AI_TRADING_SYSTEM_REDESIGN.md#43-관찰-화면의-수동-원본-대조와-결과-표시) · 근거 data/diagnostics/20261013-shadow-source-check-ui/.

원본 수집 연속성/이전 revision·lineage/적격성/운영 승인과 실제 앱/PG/NAS/장중 수락·판단 지연 전후 측정은 남는다. 실행 앱 조작·활성화/주문·커밋/push·배포·전체 회귀/CI는 없다.

## 2026-10-13 R3 연구 가설의 명시 native 원본 대조

인증 source-check의 expected_revision/1–60초 명시 창→기존 app lock/drain→native 테마 이력·최신 초봉 두 읽기를 연결했다. 정확 theme ID/내용 hash/구성원과 초봉 전체 값 hash를 대조하고 창 밖/이전 revision/연속성·factor/hypothesis lineage는 미확인으로 남긴다. 보고서 저장·tick 추가 read·운영/Replay 승인은 없다. 화면 버튼은 후속이다.

신규 10건 통과, 관련 최초 110건 중 109건 통과·기존 staging rename의 WinError 5 한 건만 새 임시 경로 재실행 통과. 제품 수정 없이 아홉 SHA 일치·소비 감사/catalog 통과. SQLite 명시 대조 SELECT 2회/COMMIT 0회·DB 바이트 무변경, live GET 추가 source read 0회. 소비자 0/2/8의 기존 원본 writer/COMMIT 각 4회, 8테마 H1 1회/H2 8개를 유지했다. [계약/검증](AI_TRADING_SYSTEM_REDESIGN.md#42-연구-가설의-native-원본을-명시-대조) · 근거 data/diagnostics/20261013-shadow-source-check/.

로컬 미배포 build는 `2026.10.13-shadow-source-check-v1`다. 실제 초봉 이력/수집 연속성·lineage/적격성/운영 승인·PG/NAS/장중 수락과 판단 지연 전후 실측은 남는다. 명시 대조 표시가 다음 연결 범위이며 계좌/주문·활성화·커밋/push·배포·전체 회귀/CI는 없다.

## 2026-10-13 R3 연구 가설·정책의 관찰 화면/명시 설정 연결

기존 관찰 탭에서 PC 연구 DB/run/가설 ID·네 정책을 명시 선택해 기존 Qt worker에서 read-only 조회하고 검증된 불변 문서 snapshot만 인증 selection API로 전달한다. 선택 가설은 한 테마의 행동 제안에 연결하며 다른 테마는 독립 context 관찰이다. private v1 설정의 optional stock_thesis/null 해제·revision/save/apply/ACK 유실·새 앱 재시작을 기존 owner/문서 한 행으로 처리한다. PC 경로/파일을 서버에서 열지 않으며 연구 문서를 시장 원본 확정/운영 승인으로 취급하지 않는다.

신규 **10건 포함 관련 116건 통과(74.115초), 실패/오류/skip 0**·현재 import/SHA 아홉 파일·소비 감사 delta 0/catalog·offscreen 픽셀 QA 통과. 새 PC 선택은 schema+근거 SELECT 각 1회/COMMIT 0회, 저장 snapshot 재사용·tick 가설 read 0회다. 기존 원본 writer/COMMIT·H1/H2 공유·source_durability=not_verified/order_authorized=false를 유지한다. [계약/검증](AI_TRADING_SYSTEM_REDESIGN.md#41-저장된-연구-가설정책의-관찰-화면과-명시-설정-연결) · 근거 `data/diagnostics/20261013-shadow-thesis-selection/`.

로컬 미배포 build는 `2026.10.13-shadow-thesis-selection-v1`다. 다음은 실제 앱/NAS 통합·원본 입력의 확정 provenance/적격성·운영 승인 및 PG/장시간/장중 수락이다. 이번 작업의 계좌/주문·운영 활성화·커밋/push·배포·전체 회귀/CI는 없다.

## 2026-10-13 R3 저장된 연구 진입 가설의 명시 복구/Shadow 전달

기존 ResearchRepository에서 지정 run/thesis ID·연결된 대장 근거를 단일 SELECT로 읽어 기존 typed stock-thesis 요청에 연결했다. 기존 CLI에 연구 DB/ID/정책 명시 옵션을 추가하고 read-only mode=ro로 열어 재시작 후 같은 저장 가설을 공통 Shadow에 전달한다. 불변 ID/본문·scope/거래소·COMPLETE/가용시각을 검증하며 실패 run·PARTIAL·RAM 임시 참조는 거절한다. 저장 연구 문서를 운영 승인이나 시장 원본 확정 수락으로 표시하지 않는다.

신규 **10건 포함 관련 103건 통과(30.274초), 실패/오류/skip 0**·actual CLI·DB 바이트 무변경·두 새 RAM binding·실제 import/SHA 일곱 파일·소비 감사 delta 0/catalog 통과. 정확한 가설 조회는 SELECT 1회/COMMIT 0회, CLI schema 확인은 별도 1회이며 틱마다 재조회하지 않는다. 네 정책의 기존 결과/ID와 주문 차단·현재 source_durability=not_verified를 유지하고 이전 H2 상태는 복원하지 않는다. [계약/사용/검증](AI_TRADING_SYSTEM_REDESIGN.md#40-기존-연구-db의-불변-진입-가설을-명시-복구해-shadow에-전달) · 근거 `data/diagnostics/20261013-shadow-persisted-thesis/`.

로컬 미배포 build는 `2026.10.13-shadow-persisted-thesis-v1`다. 실제 화면/운영 설정의 가설·정책 선택, 원본 확정 승인·적격성 및 PG/NAS/장중 수락은 남는다. 이번 단계의 계좌/주문·운영 활성화·커밋/push·배포·전체 회귀/CI는 없다.

## 2026-10-13 R3 기존 0g 상한가 참조의 RAM 전달

기존 Collector가 받는 종목정보 0g의 당일/거래소별 상한가 가격과 수신 가용시각을 공통 RAM 입력→기존 H2에 연결했다. 승인 source마다 최신 한 벌만 보존하며 삭제/reset/gap·전일/미래/거래소 혼합을 제외한다. 참조만 도착해도 갱신하고 미확인은 UNKNOWN으로 남긴다. 새 TR/REG·DB reader/writer·주문/전략 엔진은 없다. RAM 참조를 durable 승인으로 쓰지 않는다.

신규 **9건 포함 관련 152건 통과(33.699초), 실패/오류/skip 0**·현재 import/SHA·소비 감사 delta 0/catalog 통과. 수정 전후 80체결+0g 1건의 원본 봉/참조 원문 hash·연결/COMMIT은 같고 8개 테마도 RAM 읽기 1회/H1 1회/H2 8회다. 원본 5 COMMIT·기록 ON 첫 진단 포함 6 COMMIT은 전후 유지된다. 단독 합성 RAM 21쌍 도착→결과 중앙값 20.742→21.395ms로 지연 개선은 확인되지 않았다. [계약/검증](AI_TRADING_SYSTEM_REDESIGN.md#39-기존-0g-상한가-참조정보의-ram-shadow-전달) · 근거 `data/diagnostics/20261013-shadow-upper-limit/`.

로컬 build 표식은 `2026.10.13-ram-shadow-upper-limit-v1`로 갱신했고 health/소스 release 계약 별도 12건이 통과했다. 이 표식은 위 NAS 배포 기록과 별개인 미배포 로컬 소스다. 확정 승인/보호 정책/재시작 근거·실제 PG/NAS/장시간/장중 수락은 남는다. 이번 작업의 운영 활성화·주문·커밋/push·배포·전체 회귀/CI는 없다.

## 2026-10-13 R3 서로 다른 테마의 H1 공유·입력 복사 부하 개선

같은 H1 scope의 시장 판단을 테마 간 공유하고 검증된 불변 market frame 참조만 재사용한다. 테마별 H2 상태/결과·정책과 가변 snapshot 복사·결과 인코딩을 보존한다. H1만 줄인 중간 측정은 지연 개선이 없었으며 입력 deepcopy 계측 후 최소 수정했다. 8개 테마 H1 8→1/H2 8회 유지, 600 frame×20 시장 구성원의 로컬 합성 21쌍에서 준비→결과 중앙값 **677.394→88.058ms**, process CPU 671.875→93.750ms, 별도 추가 Python allocation peak 23,052,780→217,788 bytes다. 장중/RSS 측정으로 확대하지 않는다.

신규 **12건 포함 관련 184건 통과(63.626초), 실패/오류/skip 0**·현재 source/test SHA·소비 감사 delta 0/catalog 통과. Collector RAM/native SQLite 0/8 OFF의 원본 writer/COMMIT 4회·봉 내용은 같고 8개 RAM 읽기 1회/H1 1회/H2 8회다. 첫 선택 recording ON은 진단 COMMIT 1회를 더해 총 5회다. [계약/측정](AI_TRADING_SYSTEM_REDESIGN.md#38-서로-다른-테마의-h1-공유와-불변-시장-입력-재사용) · 근거 `data/diagnostics/20261013-shadow-h1-sharing/`. 확정 승인/보호 정책/상한가 원본·실제 PG/NAS/장시간/장중 수락은 남는다. 이번 단계의 활성화·주문·커밋/push·배포·전체 회귀/CI는 없다.

## 2026-10-13 R3 시장·테마 관찰 화면·명시 선택

기존 Shadow 후보 창에 시장·테마 관찰 탭을 연결했다. 테마 ID/KRX·NXT/기록 여부를 명시 선택하며 기본 OFF, 기존 Collector의 passive 공통 Runtime만 사용한다. 실시간 판단과 최근 저장 결과/만료·독립 정책 행동 제안을 표시한다. 보이는 live 탭에서만 5초 RAM 조회(DB 0), 저장분은 명시 요청만 읽고 편집 중 선택/후보 알림을 보존한다.

별도 인증 selection GET/PUT에서 필수 revision·native 테마 검증·저장/적용/복구·현재 Runtime 교체와 shutdown fence를 소유한다. config 재시작은 새 session/빈 상태이며 진단/thesis/주문 승인은 복원하지 않는다. 새 선택에 늦은 owner notification도 연결한다. 기동 실패의 두 번째 start 분기를 고장 주입으로 재현하고 최소 수정했다.

신규 **15건 포함 관련 166건 통과(69.706초), 실패/오류/skip 0**·현재 소스/테스트 11개 SHA·소비 감사/catalog·synthetic offscreen 픽셀 QA 완료. [계약/범위](AI_TRADING_SYSTEM_REDESIGN.md#37-시장테마-관찰-화면과-명시-실행-선택) · 근거 `data/diagnostics/20261013-shadow-selection-ui/`. 실제 여러 테마의 공통 H1 최적화/종단 부하, 확정 승인/보호 정책/상한가 원본 연결·PG/NAS/장중 수락은 후속이다. 운영 활성화·주문·커밋/push·배포·전체 회귀/CI는 없다.

## 2026-10-13 R3 RAM 결과 선별 저장·인증 조회

명시 recording ON에서 첫 결과/범주 판단 변화만 기존 문서 저장으로 기록한다. 최신 한 행·공통 context 한 번·정책별 결과를 보존하며 기본 30초 간격/최신 pending coalescing·독립 writer·종료 drain/실패를 소유한다. 기본 OFF/빈 binding은 유지한다. 인증 `/api/v1/research/ram-shadow`는 기본 RAM만 읽고 저장분은 명시 요청 때 고정 key 한 번 조회한다. 저장 결과는 현재 판단/Replay/주문 승인으로 복원하지 않는다.

8개 소비자의 첫 기록은 context 1개/result 8개·COMMIT 1회, 같은 판단 상태의 다음 cut은 추가 쓰기 0회였다. 신규 **7건(3.554초)**과 기존 관련 **156건**을 검증했다. 관련 실행의 신규 앱 검사 두 subcase는 기존 404 기대값을 잘못 적어 실패했고 assert 한 줄만 수정해 신규 묶음을 재검증했다. 동일 제품 소스·native DB/API 계약과 소비 감사/catalog를 확인했다. 163건 단일 clean run/전체 회귀로 보고하지 않는다. [계약·검증](AI_TRADING_SYSTEM_REDESIGN.md#36-ram-shadow-결과의-선별-저장과-인증-조회) · 근거 `data/diagnostics/20261013-shadow-selected-records/`.

실제 데스크톱 표시/명시 운영 선택·재시작 승인 근거, PG/NAS·장중·성능·전체 gate는 남는다. 운영 활성화·커밋/push·배포는 없다.

## 2026-10-13 R3 TOP20·테마 context 자동 갱신

기존 TOP20의 검증된 RAM 공개 알림과 Content 테마 쓰기 완료 신호를 공통 Runtime에 연결했다. 순위는 RAM으로 공유하고 테마는 startup/변경·불확실한 쓰기 완료 때 native snapshot을 한 번 확인한다. 8개 소비자도 최초 순위 읽기/변환·테마 읽기 각각 1회이며 중복 pending→persisted 알림은 추가 변환/평가가 없다. 과거 frame의 테마 근거·future cut·테마 삭제/빈 scope·실패 retry와 갱신 중 close를 보존한다.

COMMIT 뒤 ACK 유실 때 새 테마 알림이 빠지는 것을 실제 SQLite로 재현·최소 수정했다. HTTP 실패는 유지하고 실제 native snapshot만 반영한다. 새 6건 포함 관련 **255건 통과(71.291초), 실패/오류/skip 0건**·현재 소스 10개 해시 대조·소비 감사/catalog 통과. 기존 원본 writer/COMMIT·API/DB 계약은 유지한다. [범위/근거](AI_TRADING_SYSTEM_REDESIGN.md#35-top20테마-owner의-공통-context-자동-갱신) · 근거 `data/diagnostics/20261013-shadow-context-updates/`.

다음은 선별 기록/표시·재시작 근거와 명시 운영 선택이다. 현재 기본 binding은 비어 있으며 실제 PG/NAS·장중·새 pipeline 성능·전체 회귀/CI·커밋/push·배포는 미실행이다.

## 2026-10-13 R3 Collector RAM·앱 공통 수명 연결

기존 Hub 관측자 한 개가 Collector의 canonical 초 집계를 한 번 읽어 공통 typed 입력/계산으로 전달한다. 명시 Shadow binding에만 공통 Runtime을 조립했고 source generation/승인·연속 구간·RAM 보존·gap/소비 drop을 품질로 전달한다. H2에 필요한 clean RAM 구간만 기본 20초/상한 60초로 보존하며 late/dedup/dirty/원본 writer·native Candidate 기록/복구는 유지한다. 동일 Collector의 중복 수명 owner를 거절한다. 입력 없는 주기적 read/계산·새 종목 요청/TR/REG·매매법 엔진·원본 writer는 없다.

0/2/8개 소비자·80체결에서 집계 각각 80회, 네 writer 각각 1회/연결·COMMIT 각각 4회·봉 내용이 같았다. 8개 동일 입력의 RAM read/변환/H1/H2는 각각 1회·DB 0회다. 실제 app lifespan의 시작/상류 실패/취소·종료와 frozen 최종 cut, queue 포화/context·실패 retry/수명 소유권을 포함해 새 12건 포함 **183건 통과(42.017초), 실패/오류/skip 0건**. 새 오류 cleanup store.close 한 site만 검토·기준선 보존 후 소비 감사/catalog 통과다. [범위/검증/제약](AI_TRADING_SYSTEM_REDESIGN.md#34-기존-collector-ram-관측과-앱의-공통-수명-연결) · 근거 `data/diagnostics/20261013-shadow-ram-runtime/`.

현재 운영 기본 binding은 비어 있으며 NAS 활성화·배포는 없다. 시장/테마 context는 기존 owner가 공급·갱신하는 명시 입력이고 TOP20/theme의 자동 갱신 연결·운영 선택·선별 기록/표시·재시작 근거는 후속이다. RAM의 COMPLETE는 durable 승인 근거가 아니다. 실제 PG/NAS·장중 부하·종단 지연·전체 회귀/CI·커밋/push는 미실행이다.

## 2026-10-13 R3 공통 frame·H1/H2 계산 연결

같은 native safe-prefix 페이지의 불변 분봉/순위 변환을 회차 안에서 공유하고 동일 입력 전체/cut/venue/window/parameters/이전 상태의 H1/H2를 한 번 계산해 독립 정책에 전달한다. 별도 엔진/수집기/task/writer는 추가하지 않았다. 전략별 상태·판단/ID·평가 저장/checkpoint와 저장 ACK 이후 상태 공개를 유지한다. 실패한 요청만 재시도하며 취소/close는 기존 owned 계산과 최신 pending drain을 보존한다.

실제 이전 공통 페이지 소스와 native SQLite 31쌍 비교에서 분봉 변환 8→4회, read 1회/SELECT 2회/연결 11회·독립 evaluator/평가 저장 8회·COMMIT 10회는 유지됐다. 동일 H1/H2 두 context는 각각 2→1회, 네 정책+context는 각각 5→1회로 감소했다. RAM 결과 준비 중앙값은 1.81→1.22ms/4.83→2.29ms지만 SQLite 포함 판단 준비는 321.3→349.8ms로 개선되지 않았다. 고정 입력의 결과/ID는 동일하며 실제 장중 성능으로 확대하지 않는다.

새 9건 포함 관련 10모듈+RAM/DB 경계 2사례 **160건 통과(42.878초), 실패/오류/skip 0건**. 소비 감사 delta 0·catalog·실제 사용자 환경 소스 해시 전후 대조를 통과했다. [범위/측정/후속](AI_TRADING_SYSTEM_REDESIGN.md#33-같은-관측의-frame-변환과-h1h2-계산-공유) · 근거 `data/diagnostics/20261013-shadow-common-calculation/`. 다음은 Collector RAM 읽기 전용 발행/generation·gap과 app 공통 호출의 수명/선별 기록 연결이다. 운영 app/설정·계좌·NAS 배포·전체 회귀/CI·커밋/push는 수행하지 않았다.

## 2026-10-13 R3 Collector RAM·중복·지연 네 항목 점검

현재 소스와 격리된 Collector/Hub/native SQLite로 사용자의 네 항목을 확인했다. 구독자 0/2/8개·40체결에서 원래 Collector 1개/1분·1초 집계 각각 40회/원본 writer 네 호출 각각 1회/COMMIT 4회·봉 payload를 보존했다. 기존 Hub와 RAM peek를 재사용할 수 있지만 live-minute read는 DB+RAM이고 현재 Shadow의 safe-prefix 기록/복구는 확정 revision이 필요하다.

두 Family의 31쌍 비교는 read 2→1/관측 SELECT 4→2/연결 12→11이며 전략별 evaluator/저장 8회·COMMIT 10회는 유지한다. 공통 분봉 변환은 8→8회이고 동일 H1/H2 RAM monitor 두 개는 각각 2회 계산해 **공통 계산 중복 제거는 미완료**다. 최신 로컬 표본의 전체 판단 준비 중앙값 350.3→358.8ms/p95 643.0→573.0ms로 일관된 지연 개선은 확인하지 못했다. 봉 종료·source COMMIT/운영 PG 지연은 제외한 8판단 묶음이다. [네 항목 상세/제약](AI_TRADING_SYSTEM_REDESIGN.md#32-collector-ram-공유입력-권한중복지연의-네-항목-점검) · 근거 `data/diagnostics/20261013-shadow-collector-audit/`.

현재 평가 저장 실패 두 검사 네 subcase가 실패해 이전 111건 보고를 현재 소스 보증으로 쓰지 않는다. 실제 사용자 환경 소스에서 평가 저장 ACK 뒤에만 RAM 상태를 공개하는 3줄을 수정했고 기존 7모듈+RAM/DB 경계 2사례 **113건 통과(44.568초), 실패/오류/skip 0건**·수정 후 해시를 확인했다. 초기 실패/표본도 보존했다. 공통 frame/H1/H2 계산·Collector RAM 직접 발행·app 수명 연결은 남는다. 전체 회귀/CI·운영 설정·계좌·NAS 배포는 이번 단계에서 변경/수행하지 않았다.

## 2026-10-13 R3 공통 관측 페이지의 RAM 다중 전달

기존 native safe-prefix page를 한 번 읽어 돌파·눌림 Shadow의 각 cursor 이후 입력으로 전달하는 공통 호출을 구현했다. 같은 store/독립 ID·bounded page·안전 prefix와 per-monitor 직렬화를 검사하며 기존 기록/checkpoint/frames-only 복구와 owned drain을 유지한다. 개별 worker가 실행 중이면 공통 전달은 거절한다. 새 task/수집기/writer/DB API/주문 owner는 없다. 평가 저장 실패/ACK 유실 때 RAM 상태가 먼저 바뀌어 후보를 잃는 기존 경로도 재현해 해당 행의 상태 rollback/동일 문서 재시도로 최소 수정했다.

새 13건 포함 관련 7모듈 **111건 통과(42.556초), 실패/오류/skip 0건**. 같은 native SQLite fixture의 source read 2→1, 관측 SELECT 4→2, 연결 12→11, 쓰기 COMMIT 10→10이며 판단/후보/최종 상태는 같다. 실패/동시/취소·복구와 실제 SQLite 평가 저장 재시도를 확인했다. 추가 read site 1개 검토·이전 기준선 archive 후 소비 감사 통과다. 전체 회귀는 반복하지 않았다. [납품/제약](AI_TRADING_SYSTEM_REDESIGN.md#31-공통-관측-페이지의-ram-다중-전달) · 근거 `data/diagnostics/20261013-shadow-shared-page/`.

source read 자체는 남으며 collector RAM 직접 발행·H1/H2 공통 맥락·app 공통 worker/수명·장중/PG/NAS 수락은 미완료다. 운영/app 선택·NAS/배포는 변경하지 않았다. 이번 미커밋 소스는 위 NAS 배포와 구분하며 커밋/push 전 all-local·지문 gate·새 CI는 별도다.

## 2026-10-13 R3 전략별 입력과 공통 Shadow 실행부

기존 CandidateMonitor의 돌파 고정 선택을 등록 config/Family 선택으로 바꿔 돌파·눌림이 동일 실행부의 1분봉·순위 reader/저장/checkpoint/복구를 재사용한다. 각 전략의 규칙·monitor ID·상태·결과를 유지하고 1초봉을 필수 입력에 추가하지 않았다. 명시 `family_id`로 코드에서 눌림을 선택할 수 있으며 기존 JSON/기본 돌파·app/운영 설정은 유지한다. 다른 해상도 계약은 초기 저장 접근 전에 거절한다.

새 6건 포함 관련 7모듈 **98건 통과(38.233초), 실패/오류/skip 0건**. 두 전략의 직접 evaluator와 판단/상태 동등성, 실제 SQLite checkpoint/후보 저장과 독립 재시작·중복 방지, 1초 read/기존 주문 dispatch 0회를 확인했다. 관측 reader는 고정 fixture이며 실제 PG/NAS·장중 부하는 미실행이다. 기존 소비 감사의 delta 0·catalog·diff를 통과했다. [범위/제약/후속](AI_TRADING_SYSTEM_REDESIGN.md#30-전략-입력-해상도와-기존-shadow-실행부-재사용) · 근거 `data/diagnostics/20261013-shadow-family-inputs/`.

RAM 단일 발행/다중 소비·H1/H2 공통 맥락/선별 기록·app 수명/운영 선택은 남는다. 전체 회귀를 반복하지 않았고 이번 단계는 로컬 미커밋·미배포다. 위 NAS 배포와 구분하며 커밋/push 전 all-local·지문 gate/새 CI는 별도다.

## 2026-10-13 R3 RAM 입력 수명과 반복 DB 조회 제거

사용자의 후속 지시에 따라 Shadow는 공통 판단 엔진의 주문 없는 검증 실행으로 통일한다. 전략별 규칙·상태·결과를 보존하고 기존 수집·시장 분석·기록·복구를 재사용한다. 직전 준비하던 전용 ranking/1초봉 listener·peek API·publisher scope는 제거해 producer 세 파일의 변경을 원복했다. 기존 공유 evaluator·H1/H2/진입 가설 계산과 검증된 RAM 수명은 보존했다. 기록/복구/조립까지의 공통 실행 연결은 아직 완료되지 않았다. [현행 계약/다음 범위](AI_TRADING_SYSTEM_REDESIGN.md#29-shadow를-공통-판단의-검증-실행으로-통일).

계약 반영/전용 hooks 제거 후 관련 6모듈 **77건 통과(28.622초), 실패/오류/skip 0건**. producer 세 파일의 HEAD 대조와 diff 검사도 통과했다. 근거 `data/diagnostics/20261013-shadow-common-contract/`. 전체 회귀·CI·NAS 배포는 이번 단계에서 실행하지 않았다.

사용자의 Shadow 역할/부하 지적에 따라 준비하던 2초 DB 재조회·app/settings/compose 연결을 제거했다. 대신 store/네트워크 없이 RAM snapshot 도착 때만 계산하는 실행부를 구현했다. 중복 입력은 생략하고 대기 snapshot은 1개로 제한하며 coalesced 횟수와 최신값 관찰 범위·durable=false를 명시한다. 실패/취소/종료에서 마지막 상태 보존·진행 계산 join·최종 대기 snapshot drain을 확인했다.

새 6건 포함 관련 8모듈 **91건 통과(29.643초), 실패/오류/skip 0건**. 같은 입력 100회에서 평가 1회·SQLite/PG 연결 0회이며 기존 소비 계약·catalog·AST·diff도 통과했다. 전체 회귀는 반복하지 않았다. [역할/수명/부하 범위](AI_TRADING_SYSTEM_REDESIGN.md#28-r3-ram-입력-shadow-수명과-부하-경계) · 근거 `data/diagnostics/20261013-r3-ram-shadow/`.

producer RAM 발행·app 연결·source generation/continuity·실제 장중 CPU/메모리/부하·결과 저장/표시는 남는다. 기존 돌파 CandidateMonitor의 조회/저장은 그대로이며 이번 신규 실행부의 I/O 0회를 전체 앱 부하 0으로 확대하지 않는다. 이번 변경은 로컬 미커밋/미배포이며 운영 설정/NAS는 변경하지 않았다.

## 2026-10-13 R3 native 입력 reader 연결

기존 순위/테마 이력과 새 bounded `load_second_trade_bars`를 같은 native backend에서 읽어 공통 Shadow 입력으로 변환했다. 날짜·KRX/NXT·종목·최대 600초/10,000행·가용시각을 제한하고 예산 초과를 거절한다. writer/스키마/HTTP/주문 owner는 유지했다. mutable 행의 이전 버전과 연속성을 복원/확정하지 않으며 UNAVAILABLE/H2 PARTIAL로 남긴다. read별 snapshot은 별도다.

새 6건 포함 관련 10모듈 **200건 통과(38.079초), 실패/오류/skip 0건**. SQLite index·연결/SELECT 1회·조건/예산/덮인 과거 행 제외·native fixture→실제 CLI 동등성을 확인했다. PG는 가짜 연결+실제 psycopg exit 수명 검증이며 실제 SQL/plan/부하는 미실행이다. API 1개/소비 3지점의 정확한 delta 검토·기존 기준선 아카이브 후 감사·catalog·AST·diff를 통과했다. 전체 회귀는 반복하지 않았다. [범위/제약](AI_TRADING_SYSTEM_REDESIGN.md#27-r3-native-입력-조회-연결) · 근거 `data/diagnostics/20261013-r3-native-inputs/`.

실제 source cut/continuity·live task/종료 수명·저장/표시·새 reader 녹화/Replay·신규 진입/수량·PG/NAS/운영 수락은 남는다. 이번 R3 소스는 미커밋/미배포이며 위 NAS의 기존 커밋 배포와 구분한다. 운영 DB/계좌/NAS는 이 작업에서 변경하지 않았다. 커밋·push 전 all-local/지문 gate·새 CI는 별도다.

## 2026-10-13 R3 종목 행동/무효화 Shadow 연결

기존 진입 근거 `EntryThesis`와 명시한 thesis policy를 H1/H2 공통 평가에 연결했다. VALID/AT_RISK/INVALIDATED와 기존 HOLD/WAIT/REDUCE/EXIT 제안·근거·무효화 조건·원본 결정/내용 ID·H2 유효기간을 반환한다. H1 누락은 기존 H2 보호 제안을 막지 않고 H2 누락은 UNKNOWN/NO_TRADE·HOLD이다. 정책 자동 선택/새 임계값·주문 권한·수량·실행 경로는 추가하지 않았다. 기존 맥락 v1 출력/revision을 보존하고 CLI에 별도 `stock-thesis-shadow/v1` 입력을 연결했다.

새 8건 포함 관련 6모듈 **64건 통과(10.558초), 실패/오류/skip 0건**. 실제 CLI 다섯 사례와 typed 결과, 기존 맥락 출력이 일치하며 catalog·AST·diff도 통과했다. 전체 회귀는 반복하지 않았다. [연결 계약/납품 범위/후속](AI_TRADING_SYSTEM_REDESIGN.md#26-r3-진입-근거의-종목-행동무효화-shadow-연결) · 실행 근거 `data/diagnostics/20261013-r3-stock-thesis/`.

신규 ENTER/ADD/REENTER·목표 수량/EV·시장 유형별 새 진입 규칙·실시간 source reader/수명·저장/표시·운영 수락은 남는다. 현재 로컬 미커밋/미배포이며 운영 DB/계좌/NAS 변경은 없다. 커밋·push 전 all-local/지문 gate와 새 CI는 별도다.

## 2026-10-13 R3 첫 시장/테마 Shadow 평가

같은 입력 시각의 기존 H1 시장/H2 대장 공통 평가와 JSON 미리보기 CLI를 구현했다. 미래/타 venue·window 제외, 품질·근거·유효기간·내용 revision/다음 H2 상태를 반환하며 중복 입력·참조 충돌·미래 상태를 검사한다. 주문 권한은 false이고 기존 매매/주문/live 감시 경로는 유지했다. 새 12건 포함 관련 5모듈 **52건 통과(9.459초), 실패/오류/skip 0건**과 실제 CLI·AST·catalog·diff를 확인했다. [첫 기능/입력 계약/후속](AI_TRADING_SYSTEM_REDESIGN.md#25-r3-시장테마-shadow-입력-연결) · 근거 `data/diagnostics/20261013-r3-market-theme/`.

앞서 R1/R2 all-local은 소스 반영 전에 중단했고 완료 33 worker/1,830건·미실행 257 worker와 전용 프로세스 종료를 기록했다. 전체/현재 지문 통과가 아니다. 실제 source cut/실시간 소비·종목 행동/무효화·운영 수락·커밋/push 전 전체 검사·CI는 남는다. 현재 로컬 미커밋/미배포이며 NAS/운영 DB 변경은 없다.

## 2026-10-13 R2 checkpoint 실패 재시도·no-op·종료 순서

현재 main `232737d` 위 미커밋에서 native SQLite COMMIT 두 번 실패 후 idle worker가 저장을 재시도하지 않아 RAM cursor 1/DB cursor 0이 남는 결함과, 저장 ACK 전 close가 끝나는 경로를 재현했다. 같은 ACK 내용의 저장도 revision/saved_at 때문에 두 번 COMMIT했다. 기존 runner에 pending/ACK 내용 비교를 두고 ACK 이후에만 해제하며, fills/control 확인 후 후속 입력 전에 retry하고 기존 owned drain으로 저장·종료 순서를 보존했다. 필수 최종 저장은 강제하고 bootstrap/부분 recovery guard·v1 JSON·attempt generation·DB transaction·중지/주문 권한은 유지했다.

수정 전 4검사/5 assertion 실패→같은 수정 후 4건 통과. ACK 유실·후속 입력 차단·주문 제출 후 실패, 간접 bootstrap 저장·FILL 상태 읽기 drain까지 새 9건 포함 관련 15모듈 **278건 통과(78.119초), 실패/오류/skip 0건**이다. 동일 내용 두 저장의 native COMMIT은 **2→0**, 필수 최종 COMMIT은 **1→1**이며 정상 매매의 결과 hash·연결 153/SELECT 161/쓰기 COMMIT 44/변경 행 54는 이전 R2와 동일하다. 소스 소비 감사/catalog/구문/diff도 통과했다. [변경·재현·완료 범위](AI_TRADING_SYSTEM_REDESIGN.md#24-r2-checkpoint-저장-실패noop종료-순서) · [실행 원장](../data/diagnostics/20261013-r2-checkpoint/verification.json).

실제 PG/NAS 장애·취소/부하·broker 전체 재시작·전체 R2/P1 지연 수락·all-local/새 CI는 미실행이며 커밋/push/배포하지 않았다. 이번 NAS 조회나 운영 DB/계좌 변경은 없다.

## 2026-10-13 R2 첫 불변 승인 근거 조회 최적화

main HEAD `232737d` 위 미커밋으로 복구/gate 저장 전 불변 spec·admission·lease·recovery의 좁은 `load_mock_automation_lineage` 조회를 SQLite/PostgreSQL 기존 실행 DB 모듈에 추가했다. 고정 가짜 broker 매매 한 사이클의 중앙 native SQLite 연결/SELECT는 **163→153 / 171→161**, 실제 쓰기 COMMIT **44→44**, 변경 행 **54→54**, fetch 행 **149→149**다. 같은 매매·체결·현금·비용·수량·cursor 결과와 일지 검사를 보존했고 control/binding/최신 risk 재검사·writer transaction을 합치지 않았다.

관련 15모듈 **268건 통과(71.939초), 실패/오류/skip 0건**과 후속 새 SQLite 동시 게시/snapshot **1건 별도 통과**다. PostgreSQL은 가짜 연결+실제 psycopg native exit의 수명/계측 검증이며 실제 PG SQL·planner·동시성·부하는 미실행이다. 소비 계약 감사는 새 API/2개 소비 지점만 검토해 이전 기준선을 아카이브하고 통과했으며 catalog도 통과했다. 전체 R2 성능·P1 COMMIT 원인/개선·NAS·실제 broker·all-local/새 CI·커밋/push/배포는 남는다. 이번 NAS 재조회/운영 변경은 없다. [범위와 실측](AI_TRADING_SYSTEM_REDESIGN.md#23-r2-불변-승인-근거의-좁은-조회) · [실행 원장](../data/diagnostics/20261013-r2-breakout/verification.json).

## 2026-10-13 R1 첫 돌파 판단 소비 연결

main HEAD `232737d` 위 로컬 미커밋 작업으로 Shadow의 돌파 직접 호출을 기존 Family 등록 evaluator에 연결하고 결과 타입을 기존 `StrategyEvaluation`으로 명시했다. 기존 미커밋 문서/검사와 원본 자료를 보존했다. 동일 입력의 두 소비자 판단과 FILL 소유 경계의 새 1건 포함 관련 8모듈 **95건 통과(46.412초), 실패/오류/skip 0건**이다. Shadow 6사례와 가짜 broker 매매/체결/재시작/일지의 고정 결과 묶음은 전후 JSON·SHA-256·45,919바이트 크기가 일치했고 FakeStore 호출 수가 유지됐다. 실제 DB COMMIT/성능 개선 실측은 아니다.

전체 R1 목표 구조, 좁은 업무 저장 API·native SQL/COMMIT 검토, R2/R3와 실제 운영 수락은 남는다. 새 all-local·CI·NAS/PG·실제 broker 검사는 미실행이며 커밋/push/배포하지 않았다. 이 작업에서 NAS 상태를 재조회하지 않았으므로 아래 NAS 기록은 해당 시점의 근거로 유지한다. [변경·계약·검증 범위](AI_TRADING_SYSTEM_REDESIGN.md#22-r1-첫-돌파-판단-소비-연결) · [실행 원장](../data/diagnostics/20261013-r1-breakout/verification.json).

## 2026-10-13 전체 설계·재조립/신규 개발 비교

| 구분 | 이번 결과 |
| --- | --- |
| 사용자 요청·소스 | 전체 설계를 다시 구성해 재조립과 전체 신규 개발을 판단할 근거 작성. 로컬 main HEAD `232737d33b3c1ac438a3e7dc3e3123817d19a868`, 통합 소스 `3180f04`. 이전 검사 2파일·문서 3파일의 미커밋 변경을 보존했다. |
| 실제 조사 | 로컬 Python 380파일 정적 AST·지문 목록, 핵심 26모듈의 import/정의, 연구/Shadow/모의/수집/주문/자료 소유권의 대표 경로와 원래 명세 대조. 동작/성능 전수 감사나 NAS 재조회가 아니다. |
| 설계·권고 | [전체 설계와 선택 근거](AI_TRADING_SYSTEM_REDESIGN.md)를 작성했다. 기존 기반의 선택적 재조립을 우선하며 필요한 기능과 연결을 남긴다. 작은 신규 구조는 사용자 참고안이며 새 프로젝트를 결정하지 않았다. DB/메인 결합 근거, 목적별 저장·좁은 소비 계약, NAS/독립 PC 모드, DB Replay의 별도 진단 도구 분리, 기능 추가의 변경 국소화와 전환/복귀를 명시했다. |
| 개발 순서 | R0 코드 수정 없는 설계 → R1 기존 핵심 경로 하나의 유지/정리/교체 → R2 해당 저장 경로 최적화 → R3 새 판단 기능 → R4 포지션/EV → R5 학습/운용 확장으로 정리했다. 구조 정리와 새 기능은 분리하며 전체 표를 자동 실행하지 않는다. 이전 로드맵 전문은 [아카이브](archive/2026-10-13/README.md)에 보존했다. |
| 검증·미실행 | 새 설계는 문서/정적 연결 검토이며 새 정책·prototype·성능 측정·실제 주문·NAS 변경/배포는 미실행이다. 과거 회귀 통과를 새 설계의 구현 합격으로 사용하지 않는다. 방식 선택과 실제 코드 재구성은 남는다. |

정적 근거는 `artifacts/architecture-review-20261013/source-inventory.json`이다. 실제 모델 기록은 GPT-6.1 Sol High이며 전환하지 않았다. 아래 예약/검사 기록은 과거 확인 시점으로 유지하고 해당 차단을 전체 설계·정책 개발의 선행 조건으로 확대하지 않는다.

## 2026-10-13 모의운용 NAS 차단 확인·시계 복원 검사 보완

| 구분 | 현재 확인 결과 |
| --- | --- |
| 작업 소스 | 로컬 main HEAD `232737d33b3c1ac438a3e7dc3e3123817d19a868`, 통합 소스 커밋 `3180f04`. 이번 변경은 기존 SQLite·PostgreSQL 검사 보완과 문서이며 미커밋·미배포다. 아래 4,241건은 커밋 전 고정 소스의 기존 결과로 보존한다. |
| 운영 NAS | 05:32~05:37 KST 읽기 확인: health=ok·trace=off·diagnostics_idle=true, 활성 release `2026.10.12-recorder-account-index-v3-875421f5e5e3fb26`. 서비스·예약·계좌·주문 설정 변경과 operational pause는 요청하지 않았다. |
| 격리 검사 차단 | 어제 고정 후보 `2026.10.12-mock-session-clock-v1-3442fec908ef9878`의 133건 요청 1회가 `operation_busy`로 실행 전 거부됐다. 새 job·실행 시험 0건이다. 오늘 녹화 예약 PID 31632의 fd 3에서 예약 `.status.json.lock` inode 829529의 WRITE FLOCK을 확인했다. 이는 operator fence를 통과할 수 없게 하는 충분한 차단 조건이다. private operator.lock의 다른 소유자 여부는 미확인이다. |
| 로컬 검증 | 시계·계좌 입력·초기 복원·모의 세션 관련 4모듈 66건 통과, 실패/오류/skip 0건(45.804초). 동일 초기 상태에서 두 번 Replay하고 중복 저장 전후 문서·updated_at과 결과 digest가 같은지 확인했다. PostgreSQL 함수 1건 로딩·문법, 기존 CI catalog(unit 438/integration 26) 연결 확인은 실행 합격과 구분한다. |
| PostgreSQL 준비 | 기존 전용 account/VI 검사에 account-context/v4의 초기 STOPPED·미확인 주문·취소 이력, 시계 읽기/저장/재시도 5호출, 두 번 Replay의 최종 내용·중복 저장·각 native transaction 및 초기 상태 복원을 추가했다. 총 22호출·19 commit·3 rollback/회는 검사 요구 조건이며 실제 PostgreSQL 검증은 미실행이다. |
| 다음 조건 | 녹화 예약 실행기의 종료와 trace durable idle·operator 잠금 사용 가능을 모두 확인한다. 기존 133건은 어제 후보의 검증으로 유지하며, v4 검사와 최신 통합 소스의 검증에는 해당 소스를 담은 별도 후보·gate가 필요하다. 운영 배포·실제 모의계좌 운용·앱 전체 인과 Replay 수락은 남는다. |

근거는 `data/diagnostics/20261013-053236-mock-nas-isolation/`의 `attempt.json`, `reservation-process.json`, `clock-v4-verification.json`이다. `/proc/locks`만으로 소유자를 확인하지 못했던 관측과 실제 프로세스의 fdinfo 잠금 확인을 구분한다. 예약(08:59:50~09:59:50 녹화·20:10 저장)은 유지했다. 모델은 현재 실행 기록의 GPT-6.1 Sol High이며 전환하지 않았다.

## 2026-10-13 통합 소스 커밋·녹화 범위 보완

| 구분 | 현재 확인 결과 |
| --- | --- |
| 로컬 소스 | main 소스 커밋 `3180f04e4672a953cd1c18afde9372324c6b43fd`. 기존 미커밋분과 녹화 보완 169파일을 함께 커밋했다. 아래 미커밋 표현은 각 기록 시점의 상태다. |
| 녹화 지원 | 모의 시계 초기 상태와 저장/조회는 account-context/v4 및 account 옵션으로 연결했다. TOP20 표시 계산본·통계·세 native reader와 실제 날짜/캐시 환경·결과 해시를 기록/검증한다. 구형 v1/v2/v3 복원 범위와 raw/표시 provenance를 유지한다. |
| Windows 검증 | 고정 소스의 all-local 4,241건·290 worker 통과, 실패/오류/skip 0건·소스 변경 없음. Windows-only local 증거 gate 통과. Git에 저장할 1,084개 실행/검사/배포 파일의 LF 지문 `8396888a9b1c0ca7fcc52387e0f2e542e2e213dacd12f7f6e9d98f7025659dc4` 일치. |
| 검사 보완 | 차등 REG 송신에 맞춘 재연결 fixture, 새 그룹 생성 함수를 읽는 저장 경로 감사, source/Replay 동일 25초와 20종목·미소비 입력 0건을 요구하는 TOP20 반복 fixture를 보완했다. 앞선 세 실패를 보존하고 최종 전체 실행에서 통과했다. |
| NAS 실행·예약 | 05:15 KST 커밋 후 읽기 확인에서 활성 `2026.10.12-recorder-account-index-v3-875421f5e5e3fb26`, health=ok·trace=off. 10/13 08:59:50~09:59:50 녹화·20:10 저장·다섯 옵션·8GiB·500만 이벤트·1MiB/s 예약 PID 31632 생존. 이번 소스 커밋으로 NAS를 배포/재시작하거나 예약을 변경하지 않았다. 예약은 NAS 재부팅 생존을 보장하지 않는다. |
| 외부 검증·잔여 | 새 계약의 실제 PostgreSQL/봉인·배포·실녹화 수락은 남는다. 과거뉴스 v2는 별도 불변 SQLite 파일의 버전·해시가 필요한 외부 읽기 범위다. 앱 전체 인과 Replay·전체 성능 수락은 완료하지 않았으며 실제 장중 10~20초 COMMIT은 P1 미해결이다. |

이전 181건 NAS PostgreSQL 결과와 과거 prefix 저장 A/B를 새 통합 소스의 운영 수락으로 재사용하지 않는다. 로컬 근거는 `artifacts/commit-all-20261013/new-feature-capture-audit-v4.json`, 최종 Windows 보고서는 `C:/Users/pc-1/AppData/Local/Temp/kiwoom-commit-all-20261013-frozen-v6/run.json`에 보존했다. 이번 구현·검증에서 GPT-6.1 Sol High를 사용했다.

**과거뉴스 원본 직접 입력으로 전환(2026-10-13 03:44 KST):** 사용자 요청에 따라 사본 입력 실행기를 정지하고 기존 `prepared_news.sqlite3`와 `prepared-market-news.sqlite3`를 직접 읽는 새 세대로 시작했다. 원본 SHA/WAL 전후 검사와 읽기 전용 transaction을 유지하며 시황의 legacy scope는 TEMP view에서만 정규화한다. 중복 key 거절·원래 scope/hash·failed 입력·batch rollback 의미를 보존했다. 추가 2건 포함 관련 **33건 통과(23.221초)**, 구문·대상 diff 검사 통과. 538파일 고정 코드의 실제 import/전수 SHA/construction digest `3df2cb20a3d2…`를 확인했다. 현재 원본 hash 확인 중이며 `archive-direct.building.sqlite3`에서 기존 NAS ID seed와 뉴스 결과를 구성한다. 봉인·전체 입력/관계/무결성 검사를 통과해야 `X:/kiwoom-monitor/deploy/synology/server-data/historical-news-archives`의 새 버전으로 자동 복사·검증한다. NAS 파일 경로와 활성 서버 연결은 별개이며 현재 NAS 복사·활성화는 미완료다. 기존 사본·원본·이전 building 파일을 삭제하지 않았다. 08:50 정지→12:00 재개 예약은 동일 실행기 이름으로 유지한다.

**과거뉴스 v2 입력 범위 보완·재개(2026-10-13 02:41 KST):** 시황 준비본의 기존 `historical_market` 555,835행과 `historical_market_backfill` 사이 중복 key 0개를 읽기 확인했다. 원본은 유지하고 새 고정 사본에서만 명시 alias 정규화를 한다. 원래 scope/input hash는 ready·failed·제외 행 모두 별도 원장에 보존하고 출처 수·관계 검사를 봉인 gate에 포함한다. 중복 key는 사본 게시를 거절한다. 최신 관련 31건 회귀 통과(기본 sandbox에서 멈춘 이전 검사는 종료하고 사용자 실행 환경에서 검증), 구문·대상 diff 검사 통과. 537파일 고정 코드 `b583fcf49461…`의 전수 hash/실제 import digest를 확인해 새 `archive-v2.building.sqlite3`에서 기존 seed를 물질화하는 중이다. 기존 seed 작업본·검색 사본·원본 DB는 보존했다. 08:50~12:00 KST 녹화 시간은 phase 시작 대기와 두 일회성 자동화로 정지/재개한다. 전체 준비/봉인/NAS 복사·활성화는 아직 완료되지 않았다.

**과거뉴스 전체 이관 v2 구현·진행(2026-10-13, main `941347e` 작업본·미커밋/미배포):** 검색/시황의 기사·본문·여러 대상 판정·사건·compact 목록을 PC에서 완료하고 NAS는 읽기만 하는 경로를 구현했다. 기존 ID/sequence/참조를 보존하며 기존 v1은 별도로 유지한다. 원본 누락·실패 근거·입력/본문 hash·target/참조·seed 전수 비교·full integrity 검사를 통과해야 봉인된다. OS builder 잠금·batch rollback/재개·2GiB 저용량 중단·불변 버전 복사/전체 hash 검사도 포함한다. 관련 40건 묶음 통과 뒤 canonical 손상/입력 snapshot을 보강한 최신 source/추출/snapshot/진도 재개 묶음 27건도 통과했다. 실제 PostgreSQL 전용 검사 2건도 통과했다. 대용량 사본 생성과 겹친 확대 묶음은 60초 제한으로 중단됐고 성공 처리하지 않았다. 현재 코드의 전체 운영 자료 검증/게시가 완료됐다는 뜻은 아니다. 검색/시황 준비본 총 1,024행 표본은 예상 신규 archive 약 160.0GB, 고정 준비본 57.0GB, 가정 여유 48.0GB다. 원본 5테이블 320행 표본(파싱 항목 포함)으로 원본 이력 약 44.5GB 및 인덱스 여유 포함 66.8GB를 추가 추정해 약 332GB에 요청 실패 원장 추가 여유 6.3GB를 더한 약 338GB+최신 NAS seed가 필요하다. 통계 표본 보장은 아니며 실제 peak는 미측정이다. C: 여유 약 359GB를 확인해 검색 고정 사본 생성을 시작했다. 최신 NAS seed 추출 311,488행을 완료하고 PC 사본의 전체 SHA/무결성을 확인했다. frozen builder 536파일로 기존 ID 물질화를 시작했다. 전체 준비 입력 최종화·봉인·NAS 복사/활성화·원본 삭제는 아직 하지 않았다. [구현/실행 경계](PREPARED_NEWS_ARCHIVE_DESIGN_REVIEW.md#v2-구현과-실행-상태-2026-10-13).

<a id="current-summary"></a>

정리 기준: **2026-10-12 · 제품 2.1.0**. 아래 제품 소스·배포·예약·CI 표는 10/11 확인 기록이며, 10/12 후속 개발 검증은 별도 작업본 항목에 기록한다.

**새 프로세스의 실행기 소유권·영속 중지 복구 검증(2026-10-12 21:07 KST, 로컬 미커밋):** NAS의 녹화 저장이 진행 중이므로 같은 검사 요청을 반복하지 않고 독립 로컬 복구 회귀 1건을 추가했다. 격리 native SQLite 저장본에서 부모 실행기가 중지·lease 해제 후 별도 Python 프로세스의 새 ExecutionRuntime이 소유권을 얻는다. 경쟁 소유권 획득·오래된 복구로 재개는 거절하고, 새 가짜 broker 잔고/비용을 실제 위험·복구 경로로 기록해도 영속 STOPPED와 control revision·실제 보유 2주를 유지한다. 실제 runner는 STOPPED로 주문 없이 반환하고 native EXIT gate도 `AUTOMATION_CONTROL_NOT_RUNNING`으로 막는다. 명시 재개에서만 RUNNING/revision+1로 바뀌며 직접 주문 전송은 계속 차단돼 intent·broker 제출/취소 0건이다. 자식 종료 뒤 lease 0개도 확인했다. 새 1건 포함 시계·명세·runtime barrier·supervisor 4모듈 51건 통과, 해당 핵심 운영 소스 8파일은 고정 후보와 byte 일치다. 이번 운영 코드 수정·NAS/계좌/주문 변경은 없다. 전체 서비스/supervisor·실제 broker/WebSocket 재시작·PG·실계좌 acceptance는 미검증이다. 새 시험은 로컬에만 추가했고 NAS에 게시한 불변 후보의 시험 파일/133건 계획은 변경하지 않았다. [회귀·범위·NAS 읽기 기록](../data/diagnostics/20261012-mock-runtime-restart-v1/verification.json) · [실행 로그](../data/diagnostics/20261012-mock-runtime-restart-v1/regression.log). 최신 NAS status는 health 정상·동일 운영 source/build이며 `trace_not_durably_idle`, written 284,286/queued 753,953건이다. 녹화 저장 완료와 운영 잠금 사용 가능 확인 뒤 고정 후보의 NAS 격리 검증을 진행한다.

**NAS 격리 검사 준비·실행 차단(2026-10-12 20:44 KST):** 같은 후보 `2026.10.12-mock-session-clock-v1-3442fec908ef9878`의 1,054파일을 NAS의 새 비활성 release 경로에 hash 검증 후 원자 게시했다. 기존 release·active.json·runtime.json을 덮어쓰지 않았고 `.env`·postgres-data·server-data에는 쓰지 않았다. 설치된 제한 운영 도구의 `selected` profile로 Linux 단위 124건+PostgreSQL 9건, 총 133건의 검사 이름을 고정했다. PC의 후보 경로에서 로딩 오류 0개를 확인했지만 이는 발견 검사이며 NAS 실행 성공이 아니다. 실제 두 요청은 모두 `operation_busy`로 거부됐고 신규 operator job·시험·임시 PG 작업은 시작되지 않았다(실행 0건). 별도 status는 `trace_not_durably_idle`·녹화 `persisting`이며 마지막 written 176,128/queued 862,111건이다. 이전 읽기의 written 131,072보다 증가했으나 저장 완료는 아니다. 일반 계정에서 두 알려진 공유 잠금의 inode와 `/proc/locks` 일치는 관측되지 않았고 private operator.lock은 접근 거부라 잠금 소유자·대상은 미확정이다. 운영 잠금과 녹화 저장 상태의 인과관계를 단정하지 않는다. 잠금 해제·프로세스 중지·권한 변경·차단 우회·운영 배포·계좌/주문 변경은 하지 않았다. 다음은 녹화가 durable idle이고 운영 잠금이 사용 가능해진 뒤 같은 후보/133건의 격리 검사를 다시 진행하는 것이다. [검사 계획](../data/diagnostics/20261012-mock-nas-isolation-v1/plan.json) · [실제 거부·운영 상태](../data/diagnostics/20261012-mock-nas-isolation-v1/attempt.json) · [잠금 메타데이터](../data/diagnostics/20261012-mock-nas-isolation-v1/lock-metadata.json).

**모의운용 후보의 배포 전 로컬 검증(2026-10-12, 운영 미반영):** 기존 NAS 활성 release를 기준으로 자동매매 관련 서버/공통 전략 8파일과 app 빌드 표식만 반영한 PC 후보 `2026.10.12-mock-session-clock-v1-3442fec908ef9878`을 준비했다. 나머지 운영 소스·의존성/스키마 계약은 NAS 기준을 유지하고 PC의 화면 보완은 기존 로컬 경로에 유지한다. 1,054파일·15.53MiB의 후보와 manifest를 PC에 보존했으며 NAS 게시·선택·재시작은 하지 않았다. 새 파일 DB 복구 시험은 부분 청산의 checkpoint 미반영 체결을 별도 프로세스에서 한 번 복구하고 잔여 intent·시계 문서·일지를 대조한다. 마지막 체결 이후 별도 프로세스에서도 cooldown·활성 주문 없음, 일지 새 체결 2→1→0건·상세 3건·매수/매도 주문 각 1개를 확인했다. SQLite backup 저장본·체결 replay·일지 검증이며 실제 supervisor/broker 런타임 전체 재시작은 아니다. 시험 작성 중 checkpoint의 미반영 체결과 JSON tuple/list 비교, 시험용 SQLite 연결 종료 조건을 바로잡았고 운영 코드는 이번 턴에 수정하지 않았다. [소스 비교](../data/diagnostics/20261012-mock-predeployment-v1/source-inventory.json) · [후보 범위](../data/diagnostics/20261012-mock-predeployment-v1/candidate.json) · [실행·범위 원장](../data/diagnostics/20261012-mock-predeployment-v1/verification.json).

**모의운용 준비 점검·계좌 설정 차단 보완(2026-10-12):** 인증된 GET 7회와 NAS 활성 release를 읽기 전용으로 확인했다. READY 명세만으로 감시/모의주문 허용이 꺼져 있어도 시작 버튼이 켜지는 로컬 결함을 재현했다. 기존 화면에서 계좌 scope·활성 프로필·설정 버전·감시/주문 허용을 확인해 시작·재개를 차단하고 한글 이유를 표시한다. 실행 중 중지 버튼은 유지한다.

| 구분 | 이번 확인 결과 |
| --- | --- |
| 로컬 소스 | main HEAD `941347e30ce9ff55603fced66e90fd75de094672` 위 미커밋 작업본. 이번 추가는 새 프로세스의 소유권/영속 중지 복구 회귀·현재 문서이며 운영 코드는 수정하지 않았다. 기존 변경과 고정한 후보를 보존했다. 작업본 전체의 운영 소스 차이 52개와 Dockerfile 계약 차이를 그대로 배포하지 않았다. |
| NAS/API | 19:59 KST health 정상. API 빌드/활성 manifest `2026.10.12-recorder-account-index-v3`, 활성 release `2026.10.12-recorder-account-index-v3-875421f5e5e3fb26`. 후속으로 활성 release 1,044파일 hash·manifest·허용된 runtime 계약을 전수 확인했고 최종 읽기에서도 같은 release다. 관련 19파일 중 로컬 차이 8개·독립 시계 청산 미배포는 유지한다. 실행 중 컨테이너 mount의 독립 감사는 아니다. |
| 서버 후보 | `2026.10.12-mock-session-clock-v1-3442fec908ef9878`, 운영 소스 변경 9개(8파일+app 표식). PC 보존본과 NAS 비활성 게시본이며 운영 선택/배포는 하지 않았다. 기존 NAS 계약 유지, PC/후보의 공통 구현 hash `0d2393656b3b…` 일치. 실제 PC import가 후보 안의 경로임을 확인했고 검사 뒤 전수 hash도 유지됐다. |
| 모의 준비 상태 | 구성된 모의 프로필 1개, binding·계좌 scope 일치. 감시 켜짐·모의주문 허용 꺼짐. 후보/명세/READY 각 0개. MANUAL·미시작·runner/복원 오류 없음. |
| 로컬 검증 | 최신 새 소유권/중지 복구 1건 포함 관련 4모듈 51건 통과. 이전 작업본 10모듈 162건·고정 서버 후보의 대상 169건·PC 도구 30건은 각 기록 시점 검사다. 서로 겹치며 합산하지 않는다. 기존 사용자 Python·가짜 브로커·격리 native SQLite이고 의존성은 유지했다. 새 시험은 고정 후보에 아직 포함하지 않았다. |
| NAS 격리 검사 | 고정 후보 133건 계획/로딩 오류 0개, 이전 두 요청은 `operation_busy`로 실행 전 거부·실행 0건. 이번 턴은 status만 읽고 재요청하지 않았다. 21:07 KST 녹화 queued 753,953·persisting이며 잠금 소유자는 미확정이다. NAS Linux·PG 합격으로 처리하지 않는다. 운영 build·source path·active release·기존 job은 동일하고 health 정상이다. |
| 후보 검사 범위 | PC 일별 준비 CLI에 의존하는 `test_strategy_session_exit`와 PC 진단 CLI를 호출하는 sparse-history 1건은 보존된 NAS 도구에서 로딩되지 않았다. 실패를 보존하고 후보의 제외/미검증 범위로 명시했으며 PC의 전체 해당 2모듈 30건으로 별도 확인했다. 테스트 보조 import 경로 누락도 실패 기록 뒤 바로잡았다. 후보 전체 모듈/전체 제품 gate 성공으로 처리하지 않는다. 전체 catalog는 기존 별도 뉴스 `test_seed_news_jobs_from_nas_rankings` 미등록으로 실패한다. |
| 변경하지 않은 운영 상태 | 외부 broker 인증/주문, 계좌 설정, 후보/명세 게시, NAS 배포/예약. 전체 회귀·CI·실계좌 주문·프로세스 재시작·장시간 acceptance는 미실행. |
| 다음 확인 | 녹화 durable idle·운영 잠금 사용 가능 확인 뒤 고정한 후보의 NAS 격리 Linux/의존성 이미지·PostgreSQL 검사. 실제 supervisor/runtime 재시작·운영 acceptance도 남는다. 후보/명세 준비와 주문 허용은 별도 기존 gate를 유지하고 추가 25bp 연구는 보류한다. 비활성 후보 게시는 운영 배포 승인/완료가 아니다. |

[조회 당시 근거](../data/diagnostics/20261012-195912-mock-operational-readiness/readiness.json) · [로컬 수정 검증](../data/diagnostics/20261012-195912-mock-operational-readiness/local-verification.json). 아래 정리 기록의 준비 점검 예고는 이번 조회로 완료했으며 실제 모의운용 준비 완료와 구분한다.

**완료 연구 데이터 정리·모의 기능 검증(2026-10-12, 미커밋·미배포):** 사용자 결정으로 추가 타점 연구/25bp 비교를 보류하고 모의 주문·체결·청산·복구 기능 확인을 우선한다. 완료 연구 상세 DB·중복 입력 124묶음·599파일·51.12GiB를 NAS 아카이브에 5.17GiB로 검증 보관한 뒤 PC 사본을 제거했다. 실패 0개, 확인 시 C: 여유 93.35GiB다. 뉴스·원본 수집·운영 DB, 현재 16일 기준 DB와 월별 화면 비교 DB는 유지했다. 작은 완료 결과/요청/manifest·239일 체결/타점 진단은 PC에 남고, 원본 재생·상세 DB 감사·새 비교 실행은 복원 후 source/hash 재검증이 필요하다. 입력 결합 714개는 PC 238개와 보관 원본 SHA 476개로 대조했고 완료 DB 119개의 hash는 진단 당시 원장과 일치했다. 접근 거부 과거 폴더는 제외했다. 원본 파일 합계와 전체 여유 변화는 구분한다. 기능 개발의 첫 후속으로 기존 실제 gate·위험 조회·주문 수명·시계 청산·checkpoint·일지 경로의 매수 2주→부분 청산→runner 재생성→잔여 체결→재생성/반복 조회·일지 재투영을 연결 검증했다. 주문 매수/매도 각 1개·상세 체결 3개·최종 포지션/활성 주문 0개·일지 재투영 새 체결 0개이며 새 1건 포함 관련 3모듈 47건 통과다. 회귀만 추가했고 운영 코드는 수정하지 않았다. 격리 브로커·같은 프로세스의 native 인메모리 SQLite/runner 재생성 검증이며 실제 프로세스·NAS 재시작·실제 모의계좌·장시간 운용은 미검증이다. 현재 코드/배포와 모의계좌 binding·후보·운영 명세의 읽기 전용 준비 점검이 다음 확인이다. 실제 주문은 별도 명시 범위와 기존 적격 gate를 유지하며 기능 시험 때문에 부적격 후보나 미정 기준을 승격하지 않는다. [정리·복원/기능 검증 근거](../data/storage-cleanup/20261012-192538-completed-research/CLEANUP_REVIEW.md). 아래 연구의 다음 단계 표현은 당시 기록이며 현재 25bp 비교는 별도 disposition의 DEFERRED다. 공통 계산 자료 재사용에 대한 질문은 아이디어 검토이며 새 지표 캐시 구현/성능 검증은 하지 않았다. 운영 NAS 배포/예약·외부 주문·뉴스 설정·전체 회귀·CI·PG는 변경/실행하지 않았다. 기존 뉴스 회귀 미등록 catalog 보류는 남는다. 실행 기록 gpt-6.1-sol / High, 모델 변경 없음.

**TRAIN 타점 실패 사례·25bp 비교 준비(2026-10-12, 미커밋·미배포):** TRAIN 239일·7,848거래의 실제 보유 경로와 가격 분해를 완료했다. 보유 만료 7,333건·순손익 -1,737,232원, 돌파 강도/개장 후 시간 네 구간 모두 순손실이다. 관측창 완료 7,409건·공백/늦은 가용 시각 400건·전체 봉 미관측 39건, 양수 종가 뒤 최종 손실 3,558건이며 가상 청산 성과로 사용하지 않는다. 진입 다음 봉 시가 +138,995원·진입 모델 슬리피지 -271,096원, 일반 청산 시가 +3,350원·모델 슬리피지 -266,961원을 대조했다. 위험 청산 515건의 미확인 EXIT와 잔여를 보존했다. 기존 원장·입력·기준 보고서·과학적 구현을 유지했다. 새 7건 포함 관련 46건과 첫 2일/전체 239일 실제 진단을 통과했다. 새 회귀는 기존 Windows 경로에 등록했고 전체 catalog는 기존 뉴스 회귀 미등록 1개로 실패한 상태다. [한국어 진단](../data/research/historical-development-results/expanded-training-diagnostics-20261012-v1/TRAINING_FAILURE_REVIEW.md). 돌파 여유만 25bp로 바꾸는 전체 TRAIN 239일·119개 요청을 사전 고정했으며 현재 미실행이다. 현재 C: 여유 42.71GiB 대비 새 결과 추정과 여유를 합친 약 59.44GiB가 필요해 공간 요건은 미통과다. 비교 실행 전 저장 공간을 확보·재검증하고 모든 날짜를 기존 native runner로 비교해야 한다. 25bp는 개발 가설이며 운영 타점·후보 기준에 채택하지 않았다. 순수 가격봉 타점/실행 연구이며 실시간 종목 선정·뉴스/테마·시장 상황·비중 배분을 포함한 전체 전략 수익성 검증과 구분한다. VALIDATION/OOS 성과·주문·NAS 쓰기/배포·예약·전체 회귀·CI·PG·실계좌·장시간 검증은 수행하지 않았다. 실행 기록 gpt-6.1-sol / High, 모델 변경 없음.

**TRAIN 239일 돌파 기준 평가 완료(2026-10-12, 미커밋·미배포):** 사전 고정 TRAIN 239일·9,952종목·일·119요청의 기존 돌파 기준 평가를 전부 완료했다. 첫 2일은 native 완료 cache, 나머지 237일은 누적 새 독립 실행이다. 중단 전 168일은 보존·재대조하고 이번 재개에서 남은 71일을 실행했다. 닫힌 거래 7,848건·순손익 합계 -1,801,369원·수익일 16/239일, 연속장 밖 체결 0건·종료 미청산 0일이다. 전략·체결/비용·평가·세션·독립 분할·과학적 ID/출력 manifest와 FIFO 금액을 대조했다. 기존 TRAIN 기준과 겹치는 12일의 거래/체결가 손익/수수료/세금/순손익은 모두 일치하며 기존 DB·714개 입력/요청 파일·과학적 구현 `0d2393656b3b…`를 보존했다. 독립 실행/분할/비교/자원 한도 관련 4모듈 70건 통과다. 일별 닫힌 거래 합계를 연속 계좌 수익률·낙폭으로 해석하지 않는다. [한국어 결과 보고서](../data/research/historical-development-results/expanded-training-baseline-20261012-v1/BASELINE_EVALUATION_REVIEW.md)에 전체 월/날짜·비용·청산 사유·검열을 보존했다. 다음은 전체 TRAIN 기준의 손익·비용·미청산을 근거로 실패 사례를 좁히고 소수 사전 고정 개선안을 비교하는 단계다. 실제 계좌/매체 비용·종일 완전성·후보 적격 기준 채택·독립 검증/final·같은 설정 Shadow는 남는다. TRAIN만으로 후보 승격이나 미정 기준 동결을 하지 않는다. 운영 소스는 이번 단계에서 수정하지 않았다. VALIDATION 성과/OOS 접근·주문·NAS 쓰기/배포·예약 변경은 없으며 전체 회귀·CI·PG·실계좌·장시간 운영 acceptance는 미실행이다. 실행 기록 `gpt-6.1-sol / High`, 모델 변경 없음이다.

**뉴스 제외 PC 용량 정리 완료(2026-10-12):** 구형 배포/설치 18.39GiB·빌드 1.09GiB·과거 실험 15.86GiB, 총 86묶음·87,827파일·35.34GiB를 NAS `archives/pc-storage-cleanup/20261012-113531`에 11.97GiB의 ZIP으로 보존하고 PC 사본을 정리했다. 전체 ZIP/파일 SHA·삭제 직전 원본 SHA 대조를 완료했고 정리 실패는 0개다. 확인 시 C: 여유 58.33GiB이며 뉴스 원본/가공/수집/로그와 기존 수집 프로세스는 유지했다. 현재 연구 입력/요청 714개·실행 결합·과학적 구현·비교 기준 DB의 SHA와 완료 168일을 보존했다. 정리 완료 시점에는 평가를 중단한 상태였으며, 정리 단계의 새 평가 실행·주문·운영 NAS 배포/예약·소스 변경은 없었다. [정리·복원 내역](../data/storage-cleanup/20261012-113531/CLEANUP_REVIEW.md)을 기준으로 과거 실험은 필요 시 원래 경로에 복원한다. 진행 표시의 Windows 교체 거부 1건과 재개 import 경로 누락은 완료 자료를 보존해 재개했고 직접 거부 소유자/필터는 미확정이다. 파일 크기 합계와 전체 여유 변화는 구분한다.



**TRAIN 확대 입력·첫 묶음 시험(2026-10-12, 미커밋·미배포):** 사전 고정 TRAIN 239일·9,952종목·일의 복원/전략 입력과 기존 native 요청 119묶음을 모두 준비했다. 전략 관측 3,679,853개, 결합 파일 714개의 완료 후 SHA-256 재대조와 모든 요청의 기존 전략·체결/비용·세션·평가 정책 일치를 확인했다. 기존 exporter의 선택 `--strategy-outcomes-only`와 요청 생성기의 `--train-expansion`을 사용하며 2~20일 한도·독립 상태·claim/완료 캐시를 유지한다. 첫 고정 2일 native 시험은 67거래·닫힌 거래 순손익 -15,485원, 연속장 밖 체결 0건·종료 미청산 0일이다. 첫날의 기존 기준 거래/손익 일치와 재실행 CACHED 2개, 기존 연구 DB hash 보존을 확인했다. 관련 기존 5모듈 133건 통과, 과학적 구현 `0d2393656b3b…` 유지다. Windows 교체 접근 거부로 세 차례 중단됐으나 완료 입력을 검증·보존해 재개했다. 성공/실패 부모 경로 DACL·소유자 동일, 잔여 파일/Defender 차단 이벤트 없음은 확인했지만 실패 순간 소유자/필터 원인은 미확정이며 저장 규칙을 변경하지 않았다. [한국어 보고서](../data/research/historical-development-results/expanded-training-baseline-20261012-v1/PREPARATION_REVIEW.md)에 전체 준비와 첫 시험을 구분했다. 239일 전체 손익 평가는 아직 실행하지 않았으며 다음은 사전 고정 119요청의 순차 기준 평가다. 실제 비용·종일 완전성·후보 기준 채택·독립 검증/final·Shadow는 남는다. VALIDATION/OOS 성과 조회·후보 승격·주문·NAS 쓰기/배포·예약 변경은 없으며 전체 회귀·CI·PG·실계좌·장시간 acceptance는 미실행이다. 실행 기록 `gpt-6.1-sol / High`, 모델 변경 없음이다.

**TRAIN 표본 확대 준비·저장 비용 근거(2026-10-12, 미커밋·미배포):** 기존 날짜 계획 CLI에 `--expand-train-selection`을 추가했다. 기존 TRAIN 12개월의 모든 후보일을 가격·성과와 무관하게 고정해 12→239일·9,952종목·일로 확대했으며, 8/29 선택→9/1 결과 한 사례는 첫 VALIDATION 경계에 닿아 제외 원장에 남겼다. 기존 후보 DB와 NAS 게시 원본의 후보·봉 시각을 읽기 전용 대조해 239일 모두 최소 연속쌍 제외 원장과 일치, 불일치 0일·제외 전 10,147종목·일/3,681,639봉을 확인했다. 최소 입력 `READY`는 종일 완전성·불변 가격 입력·수익성 승인과 다르며 NAS 157GB 전체 파일 hash는 미검증이다. 로컬 정산 427건 중 계좌 지정 실전 131건과 미배정 296건을 분리했고 익명 실전 관측 수수료는 매수 약 0.918bp·매도 약 0.913bp다. 2026년 저장 정산 비율이므로 2024~2025년 실제 비용·후보 계좌/REST 매체·할인·슬리피지로 채택하지 않았다. 기존 월별 CLI 호환성·hash/역할/날짜/제외 검증 관련 **10건 통과**. [한국어 검토 보고서](../data/research/historical-train-expansion-20261012-v1/EXPANSION_REVIEW.md)에 계획·현재 원본·비용·한계를 결합했다. 최초 감사의 코드 식별자는 legacy 상수였으므로 원본을 보존하고 별도 보완 원장에서 실제 `krx-regular/v1` 과학적 hash `0d2393656b3b…`가 직전 연구와 같음을 감사 뒤 확인했다. 새 손익 실행/요청·VALIDATION/OOS/final 조회·후보 승격·주문·NAS 쓰기/배포·예약은 없고 전체 회귀·CI·실제 PG·장시간 검증은 미실행이다. 다음은 고정 전체 날짜의 불변 입력 생성/바이트·투영 gate와 처리량을 제한한 순차 TRAIN 연결이다. 기존 월별 요청의 2~20사례 계약은 유지한다. 실행 기록 `gpt-6.1-sol / High`, 모델 변경 없음이다.

**사전 고정 TRAIN 돌파 가설 비교(2026-10-12, 미커밋·미배포):** 가격 경로 진단 뒤 기존 10분 보유/0bp 대비 5분 보유와 10bp 돌파 여유 두 요청을 실행 전 불변 고정했다. 같은 TRAIN 12일 전체·자료·비용·수량·정규장 시간 정책으로 새 격리 연구 DB에 24회 실행을 완료했고 기존 기준 12회는 native 원장을 읽기 전용 재사용했다. [비교 보고서](../data/research/historical-development-results/breakout-training-hypotheses-20261012-v1/TRAINING_COMPARISON.md)의 닫힌 거래 합계는 기준 396건·순손익 -82,706원·수익 2/12일, 5분 692건·-188,900원·1/12일, 10bp 392건·-62,750원·0/12일이다. 두 변경 모두 종료 미청산 0일이다. 5분은 거래/비용과 체결가 손실이 함께 늘었으며 10bp는 기준 대비 손실 19,956원을 줄였지만 수익성을 확인하지 못했다. 전체 날짜와 두 가설을 모두 보존했으며 사후 후보 선택·조건 추가·운영 승격은 없다. 보유시간 변경은 진입 마감 예산·재진입/점유도 바꾸므로 보유시간 단독 가격 경로 효과와 구분한다. native 전략·비용·평가·구현·날짜별 dataset/revision·거래/손익을 대조했고 기존 기준 DB·요청·결과·준비/진단 JSON의 SHA-256 및 과학적 `0d2393656b3b…`를 유지했다. VALIDATION 성과·OOS/final·실제 주문·NAS 쓰기/배포·예약 변경은 없으며 새 CLI 관련 144건 통과와 실제 연구 실행 24회는 별도 검증이다. 실제 비용·자료 완전성/대표성·적격 기준 채택·미사용 final·같은 설정 Shadow는 남는다. 다음은 가설을 무제한 늘리기보다 실제 비용 근거와 더 넓은 고정 개발 표본을 확인해 진입 조건을 검증하는 단계다. 전체 회귀·CI·PG·실계좌·장시간 검증은 미실행이며 실제 런타임 `gpt-6.1-sol / High`, 모델 변경 없음이다.

**TRAIN 돌파 보유 경로 진단(2026-10-12, 미커밋·미배포):** 기존 CLI에 `--inspect-training-paths`를 추가했다. 고정 개발 전체 원본 바이트의 무결성/범위는 기존 로더로 확인하며 native 성과 조회는 TRAIN 돌파만으로 제한한다. 기존 분할 hash·완료봉 replay·돌파 Factor·정수 슬리피지 계산을 재사용해 판단/체결 참조와 1/3/5/10분 관측을 확인했다. 위험 청산 봉·실제 종료 뒤 가격은 사용하지 않으며 공백·일찍 종료·미확인은 별도 상태/`null`로 남긴다. TRAIN 12일·396거래 중 관측창 봉 수가 맞는 379건, 양수 종가 관측 뒤 최종 순손실 179건이다. 시점별 관측 종가 변화 중앙값은 -6/-6/-12/-13bp지만 실제 보유 표본이 달라 짧은 보유의 실행 손익으로 해석하지 않는다. 다음 봉 시가 기여는 진입 +8,956원·정상 청산 +266원, 5bp 모델 슬리피지 기여는 진입 -15,483원·정상 청산 -15,188원이다. 실제 broker 측정값이나 다음 봉 이동이 손실의 단독 원인이라는 증명이 아니다. [한국어 경로 보고서](../data/research/historical-development-results/daily-2024-09-to-2025-12-session-exit-60s-v2/TRAINING_BREAKOUT_PATHS.md)와 불변 JSON을 보존하고 기존 원장·요청·결과·준비/가격 분해 보고서 SHA-256 및 과학적 구현 `0d2393656b3b…`를 유지했다. 새 7건 포함 관련 4모듈 **144건 통과**. 초기 fixture의 비용 모델 version 누락과 Factor 참조 tuple/JSON array 직접 비교는 기존 계약에 맞게 수정 후 재검증했다. `httpx2`는 추가하지 않았다. 실제 비용·입력 완전성·후보 기준 채택·final/Shadow·실환경 검증은 남으며 운영 전략·기준·OOS·주문·NAS 쓰기/배포·예약·전체 회귀·CI·PG·장시간 검증은 변경/수행하지 않았다. 실행 기록 `gpt-6.1-sol / High`, 모델 전환 없음이다.

**돌파 개발 손실 가격 분해(2026-10-12, 미커밋·미배포):** 기존 일별 원장 조회의 선택 진단 `--inspect-breakout-losses`를 구현했다. 판단/체결 가격·수량·시각과 FIFO 손익을 대조하고 사전 고정 청산 사유/진입 시간 기여를 집계한다. 기존 손실 조회 기본 출력은 유지하며 위험 청산의 없는 EXIT 신호는 `null`과 미분해 잔여로 남긴다. 실제 TRAIN 12일·396거래·순손익 -82,706원, VALIDATION 4일·132거래·체결가 손익 -19,109원·수수료 4,056원·세금 27,806원·순손익 -50,971원을 확인했다. 검증의 보유 만료 125건 순손익 -48,779원, 판단 사이 가격 이동 -9,195원·청산 판단→체결 -8,177원은 진단 근거이며 보유시간·슬리피지·지연 단독 원인을 증명하지 않는다. 다음 봉 가격 차이와 모델 슬리피지는 분리하지 못했다. 09–10시의 검증 양수/학습 음수도 함께 보존해 사후 시간 선별을 하지 않았다. [한국어 보고서](../data/research/historical-development-results/daily-2024-09-to-2025-12-session-exit-60s-v2/BREAKOUT_PRICE_ATTRIBUTION.md)와 불변 JSON에 기존 DB·요청·결과·준비 보고서 SHA-256 보존을 기록했으며 과학적 구현 `0d2393656b3b…`를 유지했다. 새 7건 포함 관련 4모듈 **137건 통과**. 최초 샌드박스 hard link 접근 거부는 사용자 환경 재검증으로 구분했고, 추가 CLI 검사의 Python tuple/JSON array 직접 비교 실패는 출력 계약에 맞춰 수정 후 같은 137건을 재검증했다. `httpx2`를 추가하지 않았다. 다음은 TRAIN의 보유 중 가격 경로·돌파 강도·다음 봉 체결을 확인하고 소수 사전 고정 가설을 비교하는 단계다. 실제 비용·입력 완전성·기준 채택·final/Shadow·실환경은 남는다. 전략·기준·final/OOS·후보 승격·주문·NAS 쓰기/배포·예약·전체 회귀·CI·PG·장시간 검증은 수행하지 않았고 실행 기록 `gpt-6.1-sol / High`, 모델 전환 없음이다.

**후보·final 동결 준비(2026-10-12, 미커밋·미배포):** 기존 후보 준비 CLI의 `inspect-preparation`과 PC 준비 점검을 연결했다. 현재 개발 전략·비용·세션·구현 hash를 미리 보여 주고 기준 타입·시간대·개발 실제 끝 시각 이후 범위·비용 유효기간을 확인한다. 선택한 고정 연구 요청은 자료·DB를 읽지 않고 후보 설정만 대조한다. 미정 기준·시작/끝·전략 이름을 기본값으로 채우지 않으며 검토·실패·입력 변경은 이전 후보 게시를 무효화한다. 실제 16일 자료의 미동결 `final-preparation-review.json`과 한국어 보고서를 보존했고 기존 64회 결과·요청·연구 DB·앞선 점검의 SHA-256 및 과학적 구현 `0d2393656b3b…`를 유지했다. 관련 5모듈 **137건 통과**, 추가 고정 요청 대조 뒤 준비·Qt 화면 **45건 재검증 통과**. 최초 경계 테스트는 실제 fold 끝의 마이크로초를 고려하지 않았고, 추가 요청 fixture는 기존 파서가 요구하는 디렉터리 존재 조건을 빠뜨렸다. 실제 종료시각·빈 입력 폴더로 바로잡아 통과했으며 입력 접근·동결로딩 검사를 생략하지 않았다. 사용자 요청으로 [기준 초안](MOCK_CANDIDATE_CRITERIA_PROPOSAL.md)과 별도 제안 JSON에 100거래·20운용일·비용 차감 후 양수·5% 낙폭·미사용 연속 40거래일을 기록했다. 실제 policy는 아니다. native 돌파 VALIDATION 4일·132거래·수익일 0일·닫힌 거래 순손익 -50,971원·수수료/세금 이전 체결가 손익 -19,109원을 재대조했으며 독립 날짜의 계좌 곡선을 합치지 않았다. 실제 비용·입력 완전성·개발 손실 원인/후보 개선·기준 채택·구체 final 자료/범위·같은 설정 Shadow는 남는다. 실제 기준 동결·OOS 접근·final 실행·후보 승격·주문·NAS 쓰기/배포·예약·전체 회귀·CI·PG·장시간 검증은 없으며 실행 기록은 `gpt-6.1-sol / High`, 모델 전환 없음이다.

**비용·공백 근거 대조(2026-10-12, 미커밋·미배포):** 기존 PC/CLI 점검에 고정 dataset/revision/관측 바이트와 날짜 전체에 묶인 비용 참고 JSON·과거 원응답 감사 선택 입력을 추가했다. 당시 매도세 참고값은 2024년 4일 18bp·2025년 12일 15bp로 기존 현재 비용 시나리오 20bp와 16일 모두 다르며 실제 계좌·매체·할인·슬리피지는 미검증이다. 긴 내부 공백 18구간은 현재 봉의 실제 종료시각과 과거 감사가 모두 일치하지만 9종목·일의 보관 원응답은 없어 현재 바이트 검증은 0건이다. 과거 일치 판정을 현재 무거래·종일 완전성으로 승계하지 않는다. 새 `daily-input-evidence-review.json`과 한국어 `INPUT_EVIDENCE_REVIEW.md`를 기존 시간 정책 결과 폴더에 보존했다. 기존 결과·요청·DB·원래 점검 JSON의 SHA-256과 정규장 과학적 구현 hash `0d2393656b3b…`는 유지됐다. 관련 5모듈 **95건 통과**. 테스트는 프로젝트 TEMP와 사용자 환경을 사용했으며 `httpx2`를 추가하지 않았다. 전체 catalog의 별도 뉴스 모듈 미등록은 기존 보류와 구분한다. 실제 비용·원본 접근/입력 완전성·후보 조건/기준 4개·final/같은 설정 Shadow·실제 broker 마감 체결 검증은 남는다. OOS·후보 승격·주문·NAS 쓰기/배포·예약 변경·전체 회귀·CI·PG·장시간 검증은 없으며 현재 실행 기록 `gpt-6.1-sol / High`, 모델 전환 없음이다.

| 항목 | 현재 결론 |
|---|---|
| 현재 소스 SHA | 제품·배포 소스 `44af48de387e93ed6d0885e8fbad3f4a04515fea`. 후속 문서 커밋은 같은 실행 소스를 유지한다. 빌드 `2026.10.11-mock-state-api-v1` 3곳 일치. |
| 실제 배포 release | NAS `2026.10.11-mock-state-api-v1-6f6782b2c18171af`, health `ok`·실제 source path·불변 1,042개 파일 확인. 예약 PID 7076 `armed`·잠금·preflight 통과: 10/12 **08:59:50부터 60분 / 20:10 저장**, 기존 5개 capture flag 보존. NAS 재부팅 후 예약은 유지되지 않는다. |
| 통과한 검증 | [제품 소스 Actions](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/38137039403) **9개 job 성공**: Windows 3,914건·279 worker, Linux 65건, PG required 163건·extended 24건, sealed v1 4+6/v2 15건·이미지 소스·operator·종합 gate. 로컬 전체 범위 3,914건은 최초 파일 교체 오류를 보존하고 동일 소스 worker 549건 재검증·현재 소스 대조 통과. NAS 최종 storage 114건·배포/health/예약 확인. |
| 미실행·미확인 검증 | PC 수동·장시간·다른 PC, 실제 장중 누락/복구·10/12 실제 녹화와 저장, 등록 baseline의 public operator Replay는 별도 운영 잔여다. |
| 남은 문제 | Windows 파일 교체 접근 거부의 직접 소유자/필터는 미확정. 외부 registry 일시 장애는 로컬 전수로 보증할 수 없다. 최초 enqueue 대기·다른 writer/모든 판단 입력의 종합 관측, P1 COMMIT 대기·성능/보존 및 제품 잔여는 [원장](OPEN_ITEMS.md)을 따른다. 구형 서버·불충분한 빈 분 근거는 복구 대기를 유지한다. |

**자동매매 첫 보완(2026-10-11, 미커밋·미배포):** 기존 돌파 판단→모의 주문→체결→청산→일지와 재시작·미체결·응답 유실·영속 중지를 격리 통합으로 확인했다. 비용 요청 실패 시 계좌 복구까지 중단하던 경로를 수정해 미확인 손익·신규 진입 차단을 유지하고, 기존 검증된 보유 청산 검사를 적용한다. 새 검사 9건을 포함한 관련 17모듈 **149건 통과**. 이 작업본의 전체 회귀·hosted CI·PG·실제 모의계좌·NAS 검증은 미실행이며 위 표의 직전 제품 소스 검증과 구분한다. NAS release·10/12 예약·주문 설정은 이 보완 작업에서 변경하지 않았다.

**자동매매 준비 확인·화면 보완(2026-10-11, 미커밋·미배포):** 인증된 NAS 조회 API로 health `ok`, 사용할 수 있는 모의계좌 1개·binding 존재, 게시 후보 0개·운용 명세 0개·Shadow 후보 사건 0개, 수동 모드·자동 control/runner 없음·복원 오류 없음을 확인했다. Shadow 설정은 활성·운영 revision 7 `ACTIVE`다. 일요일의 사건 0건만으로 감시 장애를 판단하지 않는다. 화면이 서버 `AUTOMATIC`을 소문자로 비교해 중지 버튼을 막던 오류, 상태 조회 실패와 이전 화면 상태로 시작이 가능해 보이던 경로를 재현·수정했다. 기존 운용의 복원 오류가 있어도 확인된 중지 제어는 유지한다. 화면·서버·후보 명세·계좌 위험·중앙 클라이언트 관련 6모듈 **53건 통과**(화면 8건 포함). 후보·명세 게시, 모의 주문, NAS 배포·예약·설정 변경은 실행하지 않았다. 다음은 적격 돌파 후보의 final 계보와 같은 설정의 Shadow 근거를 준비하는 단계다.

**자동매매 후보 파일 준비(2026-10-11, 미커밋·미배포):** [CLI](../scripts/prepare_mock_automation_candidate.py)로 기존 고정 연구 요청의 사전 적격 기준 파일 동결과 완료 final 원장·run·보고서의 읽기 전용 게시 파일 준비를 연결했다. 미정 기준·사후 동결·기준 미달은 `BLOCKED`로 보존하며, 전략·비용·과학적 구현 hash 변경과 기존 파일 덮어쓰기를 거절한다. 새 10건을 포함한 기존 final 준비·실행·후보 계약 관련 **69건 통과**. 샌드박스 hard link 접근 거부로 첫 실행 7건 오류를 보존했고 동일 검사를 사용자 환경에서 통과한 뒤 최종 69건을 확인했다. 이는 실제 후보 동결·OOS 실행·NAS 게시·주문 결과가 아니다. `historical-development-results`의 21개 DB를 메타데이터만 조회했고 읽힌 20개에는 final 실행 기록이 없었으며 1개는 `OperationalError`로 미확인이다. 앱 연구 디렉터리에 저장된 final 요청도 없다. 기존 다기간 clockfix 요약은 잠정 파라미터·개발용 비용·OOS 제외·희소 표본 선택 금지를 명시한다. 따라서 이 개발 결과를 자동매매 적격 후보로 승격하지 않았고, 실제 기준 4개와 final 후보·기간 결정 및 완료 근거 확보가 남는다. 일부 요청 폴더의 읽기 접근 거부는 제한 밖 조회에서도 동일했으며 권한·봉인을 변경하지 않았다.

**PC 후보 준비·게시 화면(2026-10-11, 미커밋·미배포):** `모의 자동운용 → 후보 준비·게시`에 기준 동결·완료 final 근거의 파일 준비·기존 후보 파일 검토·명시 NAS 게시를 연결했다. 미정 기준은 유지하고 적격 후보만 게시한다. 검토 파일·현재 구현 hash·재계산 판정·선택 계좌·최신 mock binding revision을 게시 직전에 대조하며 게시 완료는 부모 목록 조회만 요청한다. 입력 변경·실패는 이전 검토를 무효화하고 진행 중 창 닫기를 막으며 종료는 interruption 요청 뒤 실제 worker를 drain한다. 기존 판정·DB·인증 API·주문 경계는 유지했다. 화면 26건을 포함한 관련 6모듈 **126건 통과**. 최초 통합 검사의 존재하지 않는 입력 폴더 fixture 1건 실패는 기존 경로 검증을 유지한 채 빈 폴더 fixture로 바로잡았다. 실제 Qt 작업 스레드의 기준 동결과 native final 원장 읽기, 계좌·파일·binding 변경·판정 위조·게시 응답 불명·중단을 확인했고 한국어 offscreen 렌더링도 확인했다. 실제 앱 수동 조작·후보 선택·final 평가·NAS 게시·주문·배포·10/12 예약 변경은 실행하지 않았다.

**개발 후보 근거 확인(2026-10-11, 미커밋·미배포):** 후보 화면에 고정 네 TRAIN·VALIDATION 실행의 읽기 전용 개발 비교를 연결했다. native 원장·요청 조건·Family별 기존 비교를 사용하며 OOS 참조를 거절하고 현재 코드 차이·완료 거래일·검열·자료 제약을 표시한다. 기준 자동 작성·후보 승격은 없다. 관련 4모듈 **64건 통과**와 한국어 화면 렌더링을 확인했다. 실제 월별 clockfix-v2 원장에서 돌파 TRAIN 33건/1일/-10,744원, VALIDATION 33건/1일/-9,862원, 눌림 TRAIN 30건/8일/-1,683원, VALIDATION 3건/1일/-400원이었다. 네 실행 모두 현재 과학적 구현 hash와 다르다. 종료 manifest에서 돌파 두 실행·눌림 VALIDATION은 첫 거래일에 연 포지션이 종료까지 남아 있음을 확인했다. 월별 표본의 후속 보유종목 자료와 계속 보유 정책이 거래 집중에 미친 영향은 아직 미확인이며 오류로 단정하거나 임의 청산 규칙을 넣지 않았다. 다음은 이 개발 입력·보유 경로 재현과 현 코드 재검증이며 실제 후보·기준·final 기간은 미확정이다. OOS·NAS 게시·주문·배포·예약 변경은 실행하지 않았다.

**미청산 경로 추적·재현(2026-10-11, 미커밋·미배포):** 위 세 미청산 실행은 모두 첫 거래일 15:30에 `max_hold_reached` EXIT를 판단했고, 원장에 이후 보유종목 판단·관측은 없으며 SELL은 종료까지 체결되지 않았다. 다음 월 첫 다른 종목의 판단도 `open` 상태의 `different_symbol_position_managed_elsewhere` HOLD임을 확인했다. 현재 엔진에서도 늦은 진입→마감 청산 요청→다음 평가일에 다른 종목만 관측되면 SELL 검열·보유 유지·다른 종목 진입 차단을 재현했다. 실제 보유종목 후속 연속 봉을 넣으면 확인된 다음 봉에서만 청산된다. 현재 코드는 새 세션 첫 다른 종목 봉에서도 기존 SELL을 검열하며, 구 실행의 종료 검열 시점과 차이를 보존했다. 실제 runner→원장 통합과 진단·화면을 포함한 새 8건 및 관련 6모듈 **96건 통과**, 한국어 렌더링 확인. 개발 화면에 종료 미청산·마지막 보유종목 관측·청산 요청·검열 이유를 추가했으며 과학적 구현·체결·보유 정책·적격 판정은 변경하지 않았다. 원본 TRAIN 입력의 목록·manifest 읽기는 사용자 환경에서도 접근 거부여서 원본 봉 확인·동결 전체 재평가는 미실행이다. 확인된 실행 메커니즘과 원본 공백의 발생 원인은 구분한다. 다음은 접근 가능한 OOS 제외 개발 입력을 확보하고 기존 독립 개발 분할로 일별 평가기간을 명시·재검증하는 단계이며, 임의 청산·상태 초기화로 현재 결과를 보정하지 않는다. final·NAS 게시·주문·배포·예약 변경은 없다.


**독립 일별 개발 평가 준비(2026-10-11, 미커밋·미배포):** 기존 월별 선정의 16개 결과일(TRAIN 12·VALIDATION 4, 657종목·일)을 `data/research/historical-development-results/daily-2024-09-to-2025-12-current-v1/daily-plan.json`에 고정했다. 기존 전략·비용을 보존하고 자료 확인 뒤 기존 순차 독립 runner의 두 Family 요청을 원자 생성한다. 합성 자료의 두 날짜·두 Family 실행, 미청산 검열·다음 날짜 신규 진입·완료 캐시·불충분 표본, OOS/누락일/제외 코드/지연 개장/경쟁 게시 거절을 포함한 관련 3모듈 **58건 통과**. 실제 월별 개발 전용 manifest는 사용자 환경에서도 접근 거부여서 당시 저장 상태는 `source_required`였고 실제 요청·재평가 결과는 없었다. 후속 입력 연결·평가 완료는 아래 10/12 기록을 따른다. 봉인·권한·주문·배포·예약은 변경하지 않았다.

**초기 개발 CSV 제거(2026-10-11, 미커밋·미배포):** 사용자 요청에 따라 분봉 합산/일봉 거래대금 비교 CSV의 작성 함수, 전용 DTO 필드·타이머·pending·writer queue·신호·종료 flush를 제거했다. 분봉·일봉·가격 저장과 실패 복구·최종 drain의 관련 6모듈 **114건 통과**. 최초 합동 검사는 QCoreApplication이 먼저 만들어진 환경에서 native 종료했고, QApplication을 먼저 생성한 사용자 환경의 실제 회귀는 통과했다. DB 스키마·자료 수집·기존 통계 비교와 자동매매 연구 원장은 유지한다.

**독립 일별 개발 입력 연결(2026-10-12, 미커밋·미배포):** 접근 가능한 NAS 게시 스냅샷 `20260923T125937Z-58f3f4d08f5a`에서 고정 개발 후보일 16개와 각 다음 결과일만 다시 추출했다. 후보 800건·봉 577,207건·뉴스 근거 6,549건의 새 원본을 보존하고 기존 어댑터에서 개별 주식·연속쌍을 적용한 244,925개 관측을 생성했다. 기존 계획의 657종목·일과 제외 15건이 일치하며 16일 모두 최소 연속쌍 `READY`다. 실제 파일 크기 222,929,201바이트를 기존 8배 사전검사로 대조해 2GB 메모리·CPU 50%의 두 Family 순차 독립 요청을 생성했다. 기존 봉인 입력의 접근 권한은 변경하지 않았다. 로컬 `historical_intelligence.sqlite3`의 `market_bars`는 0건이었으며 원인은 미확인이다. NAS 조회의 UNC 파일 URI 오류를 인코딩으로 수정했고 지원 주기 60/300초를 날짜 검색에도 적용해 기존 추출 결과를 보존했다. 최초 일반 읽기 연결이 만든 빈 WAL·SHM는 생성 시각·크기·단독 소유를 확인해 로컬 백업 후 정리했으며 스냅샷 본체의 크기·수정 시각은 유지됐다. 이후 명시적 불변 읽기로 추출했고 종료 시 보조 파일 부재를 확인했다. 전체 157GB DB의 SHA를 재계산한 결과는 아니다. 새 5건과 결과 동일성 검사를 포함한 관련 2모듈 **37건 통과**. 현재 과학적 구현 `cad98fc9bd41…`로 두 Family 각 16일, 총 **32회 모두 완료**했고 고정 요청과 native 원장의 전략·비용·기간·구현 일치를 대조했다. 돌파는 완료 거래 530건·종료 미청산 15일·VALIDATION 4일 모두 손실(일별 중앙값 -10,601.5원), 눌림은 70건·1일·VALIDATION 수익일 2/4(중앙값 -104원)이다. 닫힌 거래 손익이며 미청산을 flat으로 보정하거나 독립 자금의 계좌 곡선을 합치지 않았다. `data/research/historical-development-results/daily-2024-09-to-2025-12-current-v1/DAILY_NATIVE_REVIEW.md`와 `daily-native-review.json`에 한국어 요약·native 비교·검열 원장을 저장했다. 후속 손실·미청산 검토와 시간 정책 비교는 아래 기록을 따른다. 실제 기준 4개·후보·final 기간은 미정이다. OOS 실행·후보 승격·NAS 게시·주문·배포·예약 변경은 없다. 전체 회귀·hosted CI·PG·실제 모의계좌 검증은 이 후속 작업에서 실행하지 않았다.

현재 완료·잔여 판정은 위 표와 위의 새 작업본 구분을 정본으로 사용한다. [기능 보존 검증 계획](DEPENDENCY_REFACTOR_VERIFICATION_PLAN.md)은 검증 절차와 범위를 관리한다. 소스 변경·배포·검증 시 이 표를 갱신하고, 단계별 증거는 날짜별 아카이브에 기록한다.

**일별 손실·마감 미청산 검토(2026-10-12, 미커밋·미배포):** 고정 요청과 native 연구 원장의 전략·비용·평가 정책 전체·세션·일별 입력을 읽기 전용 대조하는 `--inspect-daily-losses`를 추가했다. 기존 FIFO로 닫힌 거래 600건의 진입 판단·체결 비용·청산 사유를 추적했다. 돌파 VALIDATION 132건 중 보유시간 만료 125건의 순손실은 48,779원, 눌림 VALIDATION 손절 10건의 순손실은 9,107원이다. 수수료·세금 이전 체결가 손익도 두 전략·두 구간 모두 음수이며 슬리피지는 체결가에 이미 포함된다. 종료 미청산 16건은 모두 15:10~15:19 진입으로 보유한도 10분의 만료가 15:20 이상이고, 단일가 봉 종료 후 청산 판단·요청에 다음 연속 체결 근거가 없었다. 원본 종일 완전성·시장/뉴스별 손실 원인은 이번 조회로 확정하지 않았다. 한국어 `DAILY_LOSS_REVIEW.md`와 거래 참조가 있는 `daily-loss-review.json`을 같은 일별 결과 폴더에 보존했다. 새 12건 포함 관련 4모듈 **59건 통과**, 과학적 구현 hash `cad98fc9bd41…`와 32회 결과 일치는 유지됐다. 새 테스트는 회귀 profile에 등록했다. 전체 등록 검사는 기존 별도 뉴스 작업의 미등록 `tests.unit.test_seed_news_jobs_from_nas_rankings` 한 개로 실패했으며 기존 보류 항목을 유지한다. 사용자 요구를 [시간대별 단타 운영 계약](FORWARD_EVALUATION_CONTRACT.md#시간대별-단타-운영-요구--2026-10-12)에 반영했다: 일반 거래일 정규장 단타는 15:20:00 이전 청산 체결, NXT 프리 08:00~08:50·애프터 15:40~20:00, 현재 KRX 애프터 16:00~20:00·프리 준비 중이다. 후속 정규장 단타 시간 정책 구현·같은 입력 대조는 아래 완료 기록을 따른다. 실제 기준 4개·후보·final 범위는 미정이다. OOS·주문·NAS·예약·배포 변경, 전체 회귀·CI·PG·실제 계좌 검증은 없다.

**정규장 단타 시간 정책(2026-10-12, 미커밋·미배포 / 개발 대조 완료):** `krx_regular_before_auction/v1`와 명시적인 60~3600초 청산 여유를 두 Family의 공통 판단·연구 체결에 추가했다. 기존 기본 설정의 직렬화·판단은 유지한다. 진입은 신호 유효시간·보유한도·청산 여유가 연속장 끝보다 빠른 경우만 허용하며, 기존 보유는 진입 거래일의 경계 전 청산을 판단한다. 일반 거래일 보유 10분·신호 60초·청산 여유 60초 조건은 15:08 새 진입 차단·15:19 청산 판단이다. 실제 체결은 개장 전·주말·15:20 이상을 거절하고 경계 뒤 가격·관측 없는 청산을 만들지 않는다. 새 경계 18건 포함 관련 12모듈 **179건 통과**. 새 테스트는 회귀 profile에 등록했으며 전체 등록 검사는 기존 별도 뉴스 테스트 한 개의 미등록으로 실패했다. 같은 고정 16일·657종목·일·제외 15건을 사용해 현재 구현 `0d2393656b3b…`의 기존 정책 32회와 시간 정책 32회, 총 **64회 완료**했다. 현재 기존 정책 재실행은 직전 원장의 닫힌 거래 수·손익·비용·종료 미청산 근거와 32/32회 일치했다. 새 정책은 돌파 완료 거래 528건·종료 미청산 0일, 눌림 70건·0일이다. VALIDATION 닫힌 거래 순손익은 돌파 -50,971원·눌림 -9,160원, 적격 일별 중앙값은 각각 -10601.5원·-104.0원이다. 정규 연속장 밖 체결은 0건이며 마감 청산 판단은 0건이다. 늦은 진입 차단으로 마감 미청산을 줄인 결과이며 실계좌 청산 보장이나 수익성 개선으로 해석하지 않는다. `data/research/historical-development-results/daily-2024-09-to-2025-12-session-exit-60s-v2/SESSION_POLICY_REVIEW.md`와 `session-policy-comparison.json`에 같은 입력·정책·구현·native 체결과 손실 원장을 대조한 한국어 보고서를 저장했다. 초기 집계 스크립트의 필드 조회 오류와 개장 전 체결 경계 재현은 로그로 보존했고 수정 뒤 별도 v2 폴더에서 64회를 다시 완료했다. 봉 없는 구간의 독립 시계 청산 판단·실제 주문/체결·미체결 취소/중지·복구·Shadow 운영 검증, 실제 비용·후보 기준 4개·final은 남아 있다. 운영/NAS/예약/OOS·후보 승격은 변경하지 않았다.

**TOP20 수집 중단·오전 표시 조사(2026-10-12, 미커밋·미배포):** NAS health와 active release를 읽기 전용 확인하고 TOP20 서비스·집계기·통계의 세 파일이 로컬과 동일함을 SHA-256으로 대조했다. 10/1·2·6·7은 08:00~10:59의 180분이 모두 저장됐지만 `partial`이며 첫 `realtime_complete`는 각각 11:31·11:20·11:46·11:16이다. 10/8은 같은 오전 구간 178분 partial·2분 complete이며 정규장 첫 complete는 11:14다. NAS 차트는 partial을 제외하고 남은 분 사이 간격을 수집 중단으로 표시하며 일별 통계도 complete만 합산한다. 지연 순위 응답 뒤 요청 시작 시각으로 collector를 되감는 결함을 실제 호출 경로에서 재현했다. 0.25초 연속 관측·응답 3.25초 지연·동일 거래대금 1,200억원에서 수정 전 partial, 현재 시각 사용 후 complete다. collector lock 획득 뒤 현재 시각으로 advance/prepare하도록 최소 수정했고 순위 회차·membership 시각은 보존했다. 새 재현 2건을 포함한 TOP20·실제 누락·저장 수명·녹화 계보 4모듈 **107건 통과**. 최초 묶음의 과거 순위/실제 오늘 시각을 섞던 fixture 3건과 잘못 지정한 테스트 모듈 1건의 실패는 구분하며 fixture 시계를 정정했다. 기록은 `data/diagnostics/top20-capture-rootcause.json`과 `top20-regression.log`에 남겼다. 실제 program/index 저장 대기도 로그에 있으나 개별 분의 실제 누락·대기 소유자 원인은 미확정이다. 기존 partial 원장·통계·그래프는 소급 변경하지 않았고, NAS 배포·전체 회귀·hosted CI·PG·장중 수정 후 acceptance는 미실행이다. O03/O12 후속에 유지한다.

**TOP20 장후 재계산(2026-10-12, 로컬 구현):** [설계·검증 범위](TOP20_AFTER_CLOSE_RECONCILIATION.md)에 따라 장중 실시간 표시·장후 분봉 재계산·30초 편입/이탈 50%를 구현했다. 실시간 원장·연구 revision을 보존하고 날짜별 파생 문서를 원자 게시한다. 입력은 같은 DB snapshot에서 읽고 표시 통계는 별도 캐시를 사용한다. 조회 분봉도 OHLCV 추정이며 구성 배분도 추정이므로 확정값으로 표시하지 않는다. 미확인 분과 부분 일별 합계의 비율을 숨기고 축은 08:00부터 유지한다. 최종 기능·종료·녹화·계좌 경계 회귀 395건이 통과했고 마지막 화면/클라이언트 수정 후 10건을 재검증했다. 게시 취소 drain·raw recorder 제외·일별 늦은 응답/조회 실패·구 서버 통계 품질을 확인했다. PostgreSQL 직접 연결 감사는 통과했으나 전체 소비자 감사는 별도 mock runner 변경 4곳의 미검토 delta로 실패했다. 최초 실행의 잘못된 모듈 이름 로딩 오류는 성공으로 집계하지 않았고 존재 확인 후 다시 실행했다. 10/1·2·6·7·8의 저장 자료 읽기 전용 대조에서 정규장 302/326/326/322/316분을 계산할 수 있었지만 전체일 완료는 아니며 게시하지 않았다. 비활성 NAS 후보를 준비했으나 실제 PostgreSQL gate는 기존 월요일 녹화 예약 잠금의 `operation_busy`로 미실행이다. NAS 배포·과거 운영 DB 게시·전체 CI 성공은 아직 확인하지 않았다.

**TOP20 수급 조회 시간·프로그램 장후 보완 분리(2026-10-12, 로컬 수정·미배포):** 사용자 지시에 따라 후보 외국인·기관 수급 `ka10045`를 같은 KST 날짜의 평일 08:00~20:00(프리·정규·애프터장)에만 준비한다. 장 외에서는 flow stage를 성공으로 표시하지 않으며 다른 준비를 막지 않고, 08시 이후 대상에 남거나 재등장하면 미완료 flow를 예약한다. marker 조회와 fallback 사이 장 종료를 넘으면 새 요청·성공 표식을 건너뛴다. 장후 `_backfill_candidate_flows`는 프로그램매매 `ka90008`만 요청하며 기존 완료 문서는 재사용하고 새 `candidate_flow_finalization`의 scope는 `program_after_close`다. 프로그램 응답 원본 저장 확인·행 형식·날짜(행에 존재할 때)·완료 문서 저장을 검사하며, 빈 응답은 요청일 당일에만 완료로 허용한다. 이 표식은 0W 전체와 비교한 무누락 증거가 아니다. 새 경계·08시 재개·marker 대기 중 20시 전환 검사 4건 포함 TOP20·수급 입력 녹화/재생·순위 입력 3모듈 **93건 통과**, 변경 파일 diff 공백 검사 통과. 최초 실행의 구 정책 기대값 실패 3건은 새 정책에 맞춰 수정 후 같은 묶음으로 재검증했다. NAS 운영·PostgreSQL gate·전체 회귀·CI는 미검증이며 앞서 준비한 비활성 TOP20 릴리즈에는 이 추가 변경이 포함되지 않는다.

**한국 자정 로그 회전·프로그램 보완 로그 확인(2026-10-12, 로컬 수정·운영 반영 대기):** NAS `server.log.2026-10-09`, `.2026-10-10`, 현재 파일을 읽기 전용 조회했다. 현재 파일에서 `ka90008`은 00:00:35.446·00:00:43.434 KST 두 건이며 이후 06:37 조회 끝까지 추가 호출이나 장후 수급 보완 실패 기록은 없다. 요청 대상일·종목별 완료 표식과 작업 시작/끝 정보는 해당 로그에 없어 전날부터 지속했는지 새벽에 다시 시작했는지·미완료 원인은 확정하지 않았다. 기존 handler는 회전을 UTC 15:00(한국 00:00)에 예약하지만 보관 suffix는 UTC 전날로 만들어 기존 파일과 충돌하면 Python 3.13의 회전이 건너뛰어진다. `KSTDailyFileHandler`에서 보관명을 완료한 한국 날짜로 계산하고 같은 이름은 `.1`, `.2` part에 보존하며 보관 기간은 날짜 수로 제한했다. 기존 상세 파일/콘솔 분리와 1~365일 범위는 유지한다. 자정·재시작·기존 UTC/KST 보관명 충돌·날짜별 보존을 포함한 **6건 통과**, diff 공백 검사 통과. 최초 재현의 Windows 파일 정리 오류와 검사 LogRecord의 실제 시각 혼입은 구분해 보완했다. 현재 운영 `2026.10.11-mock-state-api-v1-6f6782b2c18171af`에 로그 소스·기존 검사 파일·build marker만 바꾼 비활성 후보 `2026.10.12-kst-log-rotation-v1-898cf390bc2068b1`을 게시하고 **1,042개 해시 대조**를 완료했다. 다른 미배포 TOP20·수급 시간 정책·mock/research 변경은 이 후보에 포함하지 않는다. NAS selected 검사 시도는 운영 도구의 `operation_busy` 거부로 미실행이며 활성 서버·기존 로그를 유지했다. NAS 검사·배포·실제 다음 자정 회전·전체 회귀·CI는 아직 미확인이다. 근거는 `data/diagnostics/nas-program-backfill-log-review.json`, `kst-log-rotation-before.log`, `kst-log-rotation-after.log`, `kst-log-rotation-release.json`에 남겼다.

**PC NXT 미확인 응답의 불가 캐시 수정(2026-10-12, 로컬 수정):** 엔켐 `348370`은 NAS 문서의 08:23:35 KST 관측이 `nxtEnable=Y`이고 중앙 WebSocket에서 NXT 체결이 전달됐지만, PC에는 당일 `nxt_enabled=0`이 저장돼 있었다. NXT 전용 시간대의 PC 구독은 가능 종목만 포함하므로 이 캐시는 실시간 수신 대상을 제외한다. 서비스가 빈 NAS 문서·누락된 필드도 False로 반환하고 worker가 정상 결과로 전달하는 경로를 재현했다. 이제 명시적 Y/N만 정상 결과로 인정하며 미확인 응답은 실패로 전달해 당일 불가 캐시를 만들지 않고 재시도한다. 당시 엔켐 NXT 조회 원문은 로그에 없어 최초 빈 응답 여부는 미확정이다. 빈 응답 후 Y 재시도·정상 N·잘못된 필드와 관련 구독/원격 클라이언트/컨트롤러 **60건 통과**. NAS 값을 재검증하고 엔켐 PC 캐시 한 행의 기존 값을 진단 파일에 보존한 뒤 08:37:01 KST 가능으로 복원했다. 08:41:30 순위에서 엔켐은 TOP20 밖이며 복원 이후 PC 신규 분봉은 아직 없어 화면 수신 복구를 완료로 보고하지 않는다. 실행 중 앱은 재시작 전까지 기존 서비스 코드를 사용한다. 소스는 미커밋이며 NAS 운영 소스·배포는 변경하지 않았다. 근거는 `data/diagnostics/enchem-central-delivery-review.json`, `enchem-nxt-before.log`, `enchem-nxt-after.log`, `enchem-nxt-cache-repair.json`, `enchem-post-repair-observation.json`, `enchem-post-repair-ranking.json`이다.

## 단계별 근거

- [2026-10-11까지의 구현·검증·배포 이력](archive/2026-10-11/history/CURRENT_STATUS_through_20261011.md)
- [문서별 분류·이동 목록](archive/2026-10-11/README.md)

제품 소스의 실제 Actions·배포 결과를 위 표에 반영했다. 후속 문서 커밋의 main 상태와 Actions 결과는 최종 전달에 별도로 표시하며 제품 소스 SHA와 혼합하지 않는다. 단계별 실패·후속 통과는 아래 이력에 보존한다.


**독립 시계 청산·미체결 관리(2026-10-12, 미커밋·미배포):** 선택 정규장 시간 정책의 runner는 입력이 없는 회차와 bootstrap/legacy 복구 중에도 서버 시계로 보유시간·진입 거래일 마감·주문 유효시간을 검사한다. 신선한 실제 보유종목 완료봉과 새 broker 위험 근거가 있을 때만 별도 clock Decision을 기존 O1 gate로 연결한다. 과거/미래 가격·복구 불확실·경계 초과·주문 만료는 영속 중지하며, 새 broker가 확인한 미체결만 기존 gateway로 단발 취소한다. clock 문서가 원본 가격·Decision·취소 시도·미해결 intent를 보존하고 실제 FILL만 보유·cooldown을 바꾼다. UNKNOWN·부분/늦은 체결·취소 직전 체결·동시 사용자 중지·교체된 run·명시 재개·실제 두 번째 포지션·종료 drain을 포함한 새 19건과 관련 10모듈 **117건 통과**. 첫 샌드박스 검사는 테스트 본문 전 Windows asyncio socket 초기화에서 대기했고 스택 확인 후 해당 두 검사 프로세스만 종료했다. 사용자 환경으로 재검증했으며, 추가 합동 실행의 기존 fixture import 경로 누락은 tests/unit 경로를 포함해 해결했다. 두 거래 검사의 가짜 체결 ID 중복도 독립 intent별 실제 ID로 바로잡았다. 전체 catalog는 기존 별도 뉴스 모듈 미등록 한 개로 실패했다. 과학적 구현 hash `0d2393656b3b…`는 유지되며 기존 64회 평가를 시계 경로 검증으로 재사용하지 않는다. 실제 계좌 주문·NAS·PG·장시간·전체 회귀·CI·OOS·후보 게시·배포·예약 변경은 없다. 실제 broker 조회 지연에서 가격 신선도와 마감 전 체결 성공을 확인해야 하며, 실제 비용·입력 완전성·적격 기준 4개·final/Shadow 근거는 남는다. 이번 단계는 사용자 예외 허용에 따라 기록상 `gpt-6.1-sol / High`로 진행했고 모델 전환은 없었다.

**일별 개발 비용·입력 점검(2026-10-12, 미커밋·미배포):** 후보 준비 PC 화면과 기존 요약 CLI의 `--inspect-daily-inputs`를 연결했다. 고정 계획·두 Family 요청을 먼저 대조하고 요청 생성과 같은 개발 자료 범위 검사, 기존 파일 hash, 요청 dataset/revision 식별자를 확인한다. 기존 비용은 수수료 매수·매도 각 1.5bp·매도세 20bp·추정 슬리피지 5bp이며 모델 유효기간이 모든 개발 구간을 포함한다. 출처 문자열을 실제 브로커 증거로 승인하지 않는다. 실제 16일·657종목·일의 연속장 고유 분봉을 점검한 결과 270건에 미관측 분, 내부 공백 3,277구간이 있었으며 미관측 분 10분 이상 내부 구간은 18건이다. 단일가 봉은 연속장 슬롯에서 제외하고 양끝 공백·revision 중복을 구분했다. 기존 최소 연속쌍 16일 READY를 유지하지만 종일 완전성·무거래/공급자 누락 구분은 미확인이다. `data/research/historical-development-results/daily-2024-09-to-2025-12-session-exit-60s-v2/daily-input-review.json`에 불변 점검 근거를 보존했다. CLI·PC 조회 자체는 파일·원장·NAS를 갱신하지 않고 후보 게시 검토를 무효화하며 긴 자료 읽기·투영 중 interruption을 확인한다. 새 12건 포함 관련 4모듈 **82건 통과**. 최초 회귀는 존재하지 않는 프로젝트 TEMP 때문에 시스템 TEMP로 fallback하여 샌드박스 hard-link 생성에서 실패했다. 프로젝트 TEMP 생성·사용자 권한 재검증으로 통과했다. 전체 catalog는 기존 별도 뉴스 모듈 미등록으로 실패했으며 이 단계의 새 테스트는 기존 등록 모듈에 포함됐다. 과학적 구현 `0d2393656b3b…`와 기존 64회 결과는 변경하지 않았다. 실제 비용·입력 완전성 승인·후보 기준 4개·final 범위·같은 설정 Shadow는 남아 있다. OOS·후보 승격·실제 주문·NAS 배포·예약·전체 회귀·CI·PG·장시간 검증은 수행하지 않았다. 현재 실행 기록은 `gpt-6.1-sol / High`이며 모델 전환은 없었다.

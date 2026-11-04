# R3 기능별 책임 계약

> **기능별 후속 적용.** 아래 F 계약은 관련 호출자와 상태 소유자를 찾는 데 사용한다. R3 판정은 검증한 판본·실행 범위별 근거이며, 실제 전환의 새 입력·저장·권한은 AR 정본에서 수락한다. [관련 계약](app-target-20261023.md#0-현재-기준과-진행-현황).

**이번 전체 재설계의 기준·최신 현황:** [정본 현재 현황](app-target-20261023.md#0-현재-기준과-진행-현황)을 따른다. 현재 문서의 구현/F 계약·R3 완료/실패·운영 제한은 해당 근거로 유지하며 이번 조사·설계 승인·구현·검증·운영 적용과 자동 합산하지 않는다.

**2026-10-23 이전 NAS 국소 검증 범위:** 기존 경로의 불필요한 반복/전달·책임 결합만 하나씩 정리한다. 현재 공개계약 소스28b64573 관련149·정적3종 PASS; 이전 뉴스claim89/PC146은각 source근거다. 이번에는 F14/F17/F24 공개 관측/소유와 간접F16/F18/F23·F25 증거를 갱신했다. 이전 F19 claim89·F22/146 검증은 각 source로 보존한다. 과거NAS8·로컬998은source별보존하며 현재전체수락으로합산하지 않는다. 새녹화/리플레이·진단시스템은제외한다. [현재 근거/잔여](../../REFACTOR_BACKLOG.md#nas-실행-경로-개선과-잔여--2026-10-23).

기준: 2026-10-18, 제품 2.1.0. 기존 owner를 보존하면서 기능 변경의 진입점과 영향 범위를 찾기 위한 문서다. 새로운 service·relay·queue·저장소를 정의하지 않는다.

현재 구조는 [ARCHITECTURE_CURRENT](../../ARCHITECTURE_CURRENT.md)와 [MODULE_MAP](../../MODULE_MAP.md), 불변 규칙은 [DEVELOPMENT_GUARDRAILS](../../DEVELOPMENT_GUARDRAILS.md)를 따른다. **R3의 진행·미해결·F01~F25 운영 상태는 [REFACTOR_BACKLOG](../../REFACTOR_BACKLOG.md)가 정본**이다. 394개 모듈의 코드 감사 완료는 기능 전체의 운영 성공이나 아래 미해결 문제의 수정 완료를 뜻하지 않는다. 함수별 근거는 [모듈 원장](../workstreams/refactor-module-coverage-20261018.json)에 있다.

## 필요한 기능군부터 읽기

| 변경하려는 기능 | 선택할 계약 |
| --- | --- |
| F01 기동, F23 설정/API 교체, F24 종료 | [조립·시계·수명](#composition) |
| F02 PC 순위, F03 NAS 순위/TR, F10 기본정보/NXT/고가 | [순위·기준 자료](#ranking) |
| F04 0B, F05 로컬 봉, F06 PC 가격, F07 NAS 저장, F08 봉/복구, F09 TOP20 | [실시간·저장·복구](#market-data) |
| F11 완료봉 후보, F12 0B 후보, F13 시장·테마 | [공유 판단과 Shadow](#shadow) |
| F14 포지션, F16 주문/취소, F17 자동 모의, F18 계좌 사건 | [계좌·Risk·Execution](#execution) |
| F15 연구→판단, F21 연구 실행/재시작 | [연구](#research) |
| F19 뉴스, F20 장전 입력 | [뉴스·장전](#news) |
| F22 콘텐츠/일지/테마 동기화, F18 일지 소비 | [일지·편집·동기화](#journal-sync) |
| F25 진단/capture/replay | [진단](#diagnostics) |

여러 기능군을 지나는 변경은 해당 경계만 함께 읽는다. 전체 모듈 재감사를 반복하지 않고, 변경으로 근거가 무효화된 caller·입력·판단·저장·API/UI·복원 범위만 추적한다.

## 모든 기능군의 공통 경계

**연구 입력의 시각·권한·종료 현재 계약273(F07의로컬저장/F15간접/F21/F24).** 저장codec의공통exact key는순간표현만비교하고원시각/hash를바꾸지않는다. metadata의legacy-naive와연구의aware필수정책을구분하며native export owner가원자manifest/member수락·fixed membership을소유한다. 실제HTTP→client→frozen-input과auth/retry·15연결/4thread종료를수락했으나연구NAS자동준비의broad bearer분리·최종source/T0·실제stock/운영은미수락이다. [정본현재·원실패·소유자](app-target-20261023.md#ar04-ar07-time-query270).

**보관사본실소비265(F01/F07의local full 저장/F22/F23/F24).** 같은source267의당시고유30은입력29+실제사본1이다. 기존monitor/current설정3977·중앙rank·journal3/pending을새full root의기존3owner에연결해공개native조회/재열기/원시각·판본/firstreceipt/noop·24연결종료를확인했고monitor/2.3GB중앙전체bytes는원사본과같았다. 파일배치는data_source연결선택/실제GUI·역사연구HTTP소비나sourcequiet/ACK/T0가아니다. 기존연구consumer268은정적연결돼있으므로새Manager/화면대신명시연결/선정과기존HTTP/frozen-input을269에서검증한다. admin export의target metadata쓰기는주문권한이아니며권한/원본/계좌안전을그대로지킨다. [현재수락·잔여](app-target-20261023.md#ar05-joint-native-copy265).



**현재 소유경계264(F01/F07의local full 저장/F22/F23/F24).** explicit full의중앙native 수집/실행사실은central.sqlite3,화면설정·선별관계/원receipt는monitor.sqlite3,일지3종은journal.sqlite3의기존JournalRepository가소유한다. bootstrap은경로선택·기존프로세스수명을,기존one-time CLI는source-free검증/full lease와owner검증을,JournalRepository는raw/시각/판본·native 원자receipt/COMMIT·close불명복구를담당한다. 같은retry는재저장/시계0이며foreign/changedraw는보존거절한다. default legacy/Query·최신Risk/control/주문/계좌권한은바꾸지않는다. 관련206은합성native/caller/재열기·종료수락이고actual target/설치물/운영수락은아니다. 포장258은원257소스다. [현재계약·비용·잔여](app-target-20261023.md#ar04-ar05-owner-integration264).



**명시full root의소유·연결257(F01/F10/F13/F22/F23/F24, 관련fixture355 로컬정상/실제인계·설치부분).** bootstrap/AppPaths는명시root/role만선택하고기존restore owner가CURRENT manifest/입력·대상과lease/rollback을소유한다. Database는원seed/첫receipt·strict 설정을,실제Window는그root의news/journal/research/자산/업데이트·기존Controller종료를사용한다. helper재기동도같은full pair만전달한다. catalog는선택별칭을보존하고관측rename/KIND는기존native transaction을따른다. 새중계/상태/작업0·매틱조회0,기본/query/legacy의기존공개계약은유지한다. 실제source/T0·owner quiet/finalACK·다른최소자료/설치물/운영은미수락이고원255합본native원인은UNKNOWN이다. [현재수락과미확인](app-target-20261023.md#ar05-full-root257).





**자료 선정의 원본 보관 책임(F01/F10/F22/F23):** 기존 current-state source-free selector는 TRANSFER/RETAIN_SOURCE/UNRESOLVED를 구분한다. captured query 후보222는 내부5+옛공통2의 원본보관을 확정해3977이전/7보관/27미확정이며 선택 원값/관계는 같다. 이 후보의 실제 새223 격리target에서초기image6→승인9편집→재열기→설치replay/쓰기0·최초receipt보존을수락했다. 최종source/T0·나머지27·다른실행역할/운영은남는다. 원raw·원선정34·원target/첫receipt/219수락을 새선정hash로 재결속하지 않는다. 실제 source/T0·owner ACK·다른 역할·운영 이전은 추가 수락하지 않는다. [현재222](../../tmp/ar05-actual-selection-validation190-20261101/pc-query-actual001/unresolved-review/image-source-selection222/ROOT_ACCEPTANCE222.json).

**이미지 제외 설정의 실제 새설치·편집·재시도(F01/F13/F22/F23/F24):** public current-state installer와기존SettingsRepository의값/판본transaction·commit뒤cache공개·같은값no-op을재사용한다. 새223격리target을선정222로직접seed해3977/image6/새receipt를확인하고사용자승인9를편집해native2행만바뀌었다. freshgetter/filter·재열기·setternoop0·동일설치replay의데이터/DDL쓰기0와9·최초receipt/다른모든행/판본/schema보존을실제1건으로수락했다. 12연결/worker/tree/TEMP/HKCU정리와원source/target·219/222/firstreceipt불변을확인했다. 실제UI·전체NAS/최종source/T0·인계/운영은추가수락하지않는다. [현재223](../../tmp/ar05-actual-selection-validation190-20261101/pc-query-actual001/unresolved-review/selection-install223/execution001/ROOT_ACCEPTANCE223.json).

**필수 순위 추출의 DB·파일 수명(F03/F07/F22/F24):** source218의RO/RR→3stream→rollback/close→fsync→fullverify/manifest-last/ACK와원시각/hash/관계는유지한다.237 완전사본566962행/2393775559B·연결1/SELECT6(data3)/fetch1116/rollback1/COMMIT0·503.157초,242 PC4파일44.284초/remote4ACK·SHA/size/fsync·최종manifest·Job/SSH종료,243 PC전체검증77.088초를수락했다.244 첫전체반입/원자COMPLETE receipt/durable재열기+245 같은target replay/native전체raw·publicreader를결합수락했다.원244 전체FAIL/계측원인과실패자료는보존한다.245는337.147초/1PASS·최초receipt exact·DML0·receipt재열기1연결/SELECT2,replay1연결/SELECT7/필수readCOMMIT1,consumer10연결/SELECT16·12연결close/계측복원·원4파일/원27증거/제품15지문불변·scratch/Job종료다.실제metadata시간형태1개만검증했고나머지3/v3ref·ACK손실주입을새로통과했다고하지않는다.제품owner/Collector/Hub·실시간/주문권한변경0이며중앙rankSQLite24와PCquery2를구분한다.최종source/T0/27선정·필수실행상태/ownerquiet/finalACK/인계·실제설치소비/운영은미수락,09:30이후추가NAS사용0이다. [237](../../tmp/ar05-transfer-readiness190-20261101/planning/capture-candidate237/ROOT_ACCEPTANCE237.json)·[242](../../tmp/ar05-transfer-readiness190-20261101/planning/capture-candidate237/post-capture242/ROOT_ACCEPTANCE242.json)·[243](../../tmp/ar05-transfer-readiness190-20261101/planning/capture-candidate237/post-copy-verification243/ROOT_ACCEPTANCE243.json)·[244 원실패](../../tmp/ar05-transfer-readiness190-20261101/planning/capture-candidate237/post-import244/FAILURE_ACCEPTANCE244.json)·[245 결합수락](../../tmp/ar05-transfer-readiness190-20261101/planning/capture-candidate237/post-import245-continuation/ROOT_ACCEPTANCE245.json).

**순위 캐시/별칭 owner212(F01/F02/F10/F22/F23/F24):** RankingService는기존StockRepository의cache_ranking_stocks만호출한다. 명시catalog의known과거명확인정책과순위캐시쓰기를구분하며같은native본문/clock/transaction·관측rename/market·실패close를재사용한다. 새순위입력때무관한선택alias를pending으로재분류/삭제하지않는다. 기존catalog single/batch의미승인None→사용자확인정책과single timestamp계약은보존했다. source212관련26 PASS/실제SQLite 성공·trigger rollback/retry/기존조회조립·Job/UUID/TEMP를수락했다. 원205 actualfirst alias282→274 FAIL과원본/firstreceipt/pending은보존하며source212 full213은actual15 code/5000파일·원본/자원/Job103종료를기술수락했지만실제214에서는3977행사본의두기동/정상종료/재시작·별칭282/보호theme/seed/firstreceipt/pending을53.112초에수락했고boot2 NXTHTTP0·모든소유프로세스/UUID/TEMP정리도확인했다. 최종NAS/34선정/T0·전체기능/운영으로승격하지않는다. [실제214](../../tmp/ar08-c18-selected-target214-20261101/ACCEPTANCE214.json). [213기술수락](../../tmp/ar08-c18-artifact213-20261101/preparation/root-actual-build-acceptance213.json). [현재26](../../tmp/ar01-ranking-cache-alias212-20261101/ACCEPTANCE212.json)·[원실패](../../tmp/ar08-c18-selected-target205-20261101/runtime-run001/root-failure-acceptance205.json).

**query 자동 catalog206(F01/F10/F23/F24):** 역할은MainWindow의고정query표식으로선택한다. 자동KRX/KIND worker·로컬성공날짜조회/상태쓰기는query에서시작하지않고장중필요historical공통조회는기존owner로이어준다. full/명시수동/lookup/remote/close·priorityguard는보존한다. 원fixture의메서드우회를제거한실제query child16/관련28로source206연결과수집시도0을확인했고구조/원장184도수락했다. 실제stockfrozen/3977/T0·운영은미수락이며사본검사설정제어와원자료이전을구분한다. [현재206검증](../../tmp/ar08-c18-query-autocatalog206-20261101/ACCEPTANCE206.json). 원source206 full207은당시actual13코드/원본·자원불변/Job103종료까지기술수락했다. 원205(source206/stock207) 실제첫기동/표20·기본정보20/NXT20/정상종료는확인했지만사본별칭282→274보존assert에서1FAIL했다. 그당시재기동은미실행이었고후속212/214에서원인확정·수정·동일oracle재검증을완료했다. 원본/firstreceipt/pending과모든소유프로세스종료근거는보존했다. 전체연결을정상으로승격하지않는다. [현재실패](../../tmp/ar08-c18-selected-target205-20261101/runtime-run001/root-failure-acceptance205.json). [현재207](../../tmp/ar08-c18-artifact207-20261101/preparation/root-actual-build-acceptance207.json).







**선별 설치의 부재 기본값199(F01/F10/F23/F24):** 저장소는 부재 행의 명시 default만 반환하고 원자료·cache·판본·시각을 만들지 않는다. 네 cache/catalog 성공 표식의 fresh 여부는 bootstrap/MainWindow/KRX 각 소비 owner가 정하며, 실제 성공 worker가 저장한다. strict 형식·SQL 오류·필수키 누락은 숨기지 않는다. 선별 원11관계/receipt/noop·기존 native 저장·실패/재시작은 관련70 최종002와 구조/증분 검증으로 확인했다. 실제3977/UI/frozen/T0·운영 연결의 완료 상태는 승격하지 않는다. [현재199 검증](../../tmp/ar08-c18-artifact188-20261101/selected-target-next194/default-boundary199/ACCEPTANCE199-run002.json). source199 full203은actual13 PYZ/원본·자원/Job종료까지기술수락했지만실제앱은미실행이다. query 자동KRX producer연결은205에서별도확인돼206보완전정상으로승격하지않는다. 정상Windows trust/proxy읽기는원설정격리와분리해보안경계로보존한다. [현재빌드/한계](../../tmp/ar08-c18-artifact203-20261101/preparation/root-actual-build-acceptance203.json).





**MAIN170 조회 업데이트 연결(F01/F19/F22/F23/F24):** 조회 전용 구성의 settings-role·명시 data-root를 bootstrap→MainWindow→기존 updater/helper→재기동까지 보존한다. 전용 update·로그 경로는 해당 root/updates이며 일반 full 모드의 기본 경로·1인자 worker 계약은 유지한다. 비활성 뉴스 창은 geometry를 위해 native QSettings를 만들지 않는다. 실제 main의 관련34 고유PASS·10 Job/tree/정확UUID/성공TEMP 정리와 구조1295site/증분170기록을 확인했다. 실제 frozen/UAC/지원 A·DB/외부ACK 복구·운영 수락은 미검증이고 C19 부분 상태를 유지한다. C20은 현재 MAIN171의 별도 수락/잔여를 위 계약과 정본에서 확인한다. [현재수락/한계](../../tmp/ar-v1-wave53-20261030/c19-integration170/root-acceptance170.json). source170 실제 frozen의 독립 A 정상종료 기준값→typed helper→B 재소비/정상종료 후 같은 role·Qt logical816×743 exact1 PASS(48.472초), Job/HANDLE/UUID/TEMP 정리를 확인했다. source171 query 영향 경계만 불변 재사용했으며 whole171 frozen/새코드upgrade/OS배포 수락은 아니다. 사용자 기준은 새 구조 새 설치이고 필수자료 이전·복구는 유지한다. [실제결과](../../tmp/ar08-c19-main170-frozen-next-20261030/run003-result/receipt.json). MAIN171 full 빌드1회/243.657초에서 변경15·제품397·raw20/public132/helper606과 source/venv/Job 보존을 확인했다. 실제 A170→B171 코드업데이트1은EXE교체WinError5/B미기동 FAIL1이며 target5000파일이A와정확히같아rollback을확인했다. 원인미확정이고 원ZIP/실패TEMP·Job/HANDLE/UUID보존/정리완료다. 별도 help HANDLE 관측에서는4교체가 모두 성공했으나 원 query 업데이트 해결은 아니다. [관측](../../tmp/ar08-c19-exe-handle-probe-20261030/probe-result/receipt.json). 새 B171 소비는 미수락이다. [원실패](../../tmp/ar08-c19-newstructure-update171-20261030/runtime-run001/run.json). [빌드근거](../../tmp/ar08-c19-main171-build-next-20261030/full-run001/build-result.json).

**MAIN171 C20 계약 통합(F14/F16/F17/F18/F23, 원 국소 조건/현재 관련42 수락):** 같은 계좌 identity와 키 세대를 구분하고 기존 owner가 원 run/주문·부분FILL/UNKNOWN을 유지한다. 관리 인계와 신규 진입의 명시 재개를 분리하며 UI도 재개 대기를 표시한다. 짧은 native 읽기 owner가 typed decode/오류 순서·가변 Recovery 재조회 및 후반 fresh Risk/STOP/control·최종 claim을 보존한다. UTC/KST는 동일 순간으로 비교하고 naive/invalid·실제9시간 차이를 거절하며 원시각/hash/판본은 유지한다. 현재 MAIN171 unit36+실제 WSL PG6=고유42 PASS(두job), source/guard/ledger·Job/tree/UUID/TEMP/전용DB/컨테이너 정리를 확인했다. 추가 Windows native PG 원 Protection1은 현재171에서도 첫취소3초 Timeout(ERROR1/0PASS/skip0)이며 AR08-C24/11.72 환경 수락 제약으로 남는다. Job/tree/UUID/전용DB 정리는 완료했고 Linux 통과로 대체하지 않는다. [현재실패](../../tmp/ar07-c20-main171-windows-pg1-prepare-20261030/results/direct.json). C19 frozen 관측 실패·실NAS/운영 수락과 구분한다. [단일 정본](app-target-20261023.md#0-현재-기준과-진행-현황)·[현재 수락/범위](../../tmp/ar-v1-wave53-20261030/c20-integration171/acceptance171.json).

**MAIN169 복원 연결(F01/F19/F22/F24):** source168 CDB진단29의4ERROR는v4필수필드없는수제fixture로 stage에서거절된것이다. 실제currentexporter를별도sourceDB에서사용하는helper로만고쳤고기존복원/rollback20은source169 normal문맥에서20PASS였다. 원assert·fault injection/target존재·미존재/lease/시한·169기록/기존168JSONLprefix를보존했고제품코드/뉴스정책/원시각/hash/운영자료는변경0이다. C16원Abort UNKNOWN과진단scope는그대로다. [검증/잔여](../../tmp/ar-v1-wave53-20261030/strict-restore169/verification169.json).

**MAIN168 관련 연결 검증(현재):** F01/F19/F24/F25의 원 역할별 새설치·빈DB/소비·실패/재시작 C17 계약을 로컬 수락했다. cold166 baseline1+RULE/AI/별도checkpoint4 실제5PASS 및 현재 제품414/raw20/wheel/installed35 불변·source168 관련133 실제133PASS를 source별로 구분한다. 진단 코드 부재 자식의 실제 registry 합류·앱 저장/조회/실패/close, Job/tree/UUID/성공TEMP 부재를 확인했다. 최초 실패/격리 URI 오판/TEMP는 보존했다. F02/F03/F07/F09/F15의 기존6 latest-slot/늦은 정정·원 revision/hash/history on/off 계약은 현재133에 포함되지만 live writer→reader/API 전환·중복 저장 제거는 남는다. F14/F16/F17/F18/F23 키 교체 후보는 격리 검증 중·공유 제품 미통합이다. 실제3OS/CLI/TCP/full AUTO/NAS 운영 수락은 별도다. [최종 수락/한계](../../tmp/ar-v1-wave53-20261030/verification168-c17-final-role-acceptance.json)·[정본](app-target-20261023.md#v1-detailed-checklist).

**MAIN167 C17 현재 보완 범위:** 원 -I source-pruned 자식은 같은 native registry owner에 명시 합류하도록 검사만 보강했다(기존 업무 assertion/시한/제품414 불변). [관련127 첫 결과](../../tmp/ar-v1-wave53-20261030/verification167-c17-first-failure.json)는124PASS/1FAIL/2ERROR·skip0이고 원자료/TEMP·Job active0/UUID 제거를 보존했다. F01/F22/F24/F25의 script 공급/기존 API schema 기대값 범위가 후속이며 source-pruned 자식은 공급누락으로 본문 실행 전 중단됐다. F19 모델의 새 판본 입력과 F25 operator API가 실시간 snapshot 기대값에 미치는 차이는 별도 정확 대조 중이다. source166 실제5와 현재 전체수락을 혼합하지 않는다.

**MAIN166 C17 국소 재검증:** [실제5건](../../tmp/ar-v1-wave53-20261030/verification166-c17.json)은 F01/F22/F24의 cold HTTP/native 저장 계약, F19의 RULE/AI 판본·실패/attempt, F17/F21의 별도 process checkpoint 읽기·무신규주문을 기존 installed35에서 확인했다. 부모/자식 실제 설치 origin·Job tree 종료·native HKCU/UUID 제거와 TEMP 부재를 확인했고 제품414/raw20은165와 같다. F25 전용도구 제거 검사의 -I child registry 보완과 관련127은 미실행이다. 기존 정상/부분 연결·전체 C17/AR 운영 판정을 올리지 않고 원실패를 보존한다.

**2026-10-30 OSS 후보의 F 계약 적용:** [A단계 보고서](opensource-assessment-20261030.md)는 F01/F21/F24/F25의 OS 관측/경로, F19/F22의 전송·입력/파서·파일 reader, F23의 설정/비밀 경계를 후속 비교 후보로만 연결한다. 기존 F의 정상/부분 연결과 source별 검증은 그대로이며 새 의존성·교체의 구현/검증 완료를 뜻하지 않는다. native CAS/ACK·주문/체결·PIT/시계·원본/hash·세대/drain은 기존 owner 소유를 유지한다. 비용 기준/guard/검사 원장 변경0, 성능 비교 미실행이다.
**같은 날짜의 데이터 스택 보완(F03/F04/F08/F09/F15/F19/F21~F25):** [추가읽기감사](opensource-assessment-20261030.md#data-stack-supplement)는Parquet/PyArrow파일형식·DuckDB/Polars조회·파일운반의후보경계만연결한다. 기존수신/RAM/분봉native·PIT/revision·원hash·첫receipt/미전송·계좌권한/종료는앱의기존책임이며라이브러리/OS실수락이아니다. 기존F연결상태·AR완료조건·guard/비용기준변경0이다.



**전체 역할의 OSS 보완(조사만):** [기능별 종합 비교](opensource-assessment-20261030.md#system-wide-supplement)는 F03~F09/13의 실시간·native/시계열, F15/21의 고정 연구/계산, F19의 RULE·identity/검색, F22의 archive/백업, F01/23/24의 OS 설치/수명에 후속 후보를 연결한다. F14/16~18의 주문·Risk/계좌 권한과 모든 F의 PIT/시계·판본/첫 receipt·종료/복구는 기존 owner 소유다. 새 broker/catalog/연구 엔진을 추가하지 않았고 F 실제 연결/AR 완료/원실패·guard/비용기준은 변경0이다.

**2026-10-30 채택한 v1 배치(F01~F25 공통, 설계 반영):** 같은 핵심 엔진을 선택한 Windows/macOS/Linux 실행 컴퓨터에 조립하고 장중 입력/RAM·로컬 SSD/native·뉴스 수집/RULE·주문/복구 상태는 해당 실행 owner가 소유한다. NAS는 장후 archive 수락·과거 조회·연구 결과·백업이며 사본/lease 수신이 실행 권한이 아니다. 기존 수집/판단(F02~F13), 계좌/주문(F14/F16~F18), 뉴스(F19), 연구(F15/F21), 동기화·설정/수명(F22~F24), 선택도구(F25)의 기존 공개 계약·고유 owner를 재사용한다. OS별 엔진 복제/공유SQLite 직접변경/매틱NAS조회·자동failover를 추가하지 않는다. 기존 정상 연결은 원소스/환경 범위로 유지하고 새OS/로컬배치/장후전송은 구현·연결·수락 미완료다. [단일 목표·단계/완료조건](app-target-20261023.md#portable-execution-target)을 따른다.


**MAIN124 최소로그/선별복구(2026-10-27):** 실패기록은기존handler,common getter는safe tuple의보고된기록만읽고health는additive projection이다. logging상태는reported_only/sticky/unknown이며현재I/O건강probe·operational/order gate를대체하지않는다. 선별미완료테마의publication/pin/첫receipt는기존native sync owner가소유하고query seed/marker가권한owner가되지않는다. [133실제검증·한계·승인된보관후속](app-target-20261023.md#1153-기본-로그-상태와-선별-미완료-테마-복구-연결--2026-10-27)을따른다.


**MAIN121 국소 경계(2026-10-27):** source119 관련468/새설치6쌍, source120 도구부재 생산본문179, source121 processor부재 실제TCP7을 각 원소스에서 검증했다. 최초186실행185PASS/1ERROR를 보존하며 전체AR/운영수락은 별도다. 정상 producer/Collector/Hub/QueryStore/계좌의 업무 상태·트랜잭션·종료는 기존 owner가 소유한다. 상세 녹화/관측은 도구의 명시 조립·per-Hub recorder와 기존 diagnostic_metrics가 소유하고 native는 선택 observer 참조만 보관한다. 기본 저장소 오류/자원 조회는 정상 제품 경로에 남긴다.

연구 raw20 recipe/digest는 `domain.research_contract` 하나가 소유하고 producer resource reader·기존 build CLI·frozen spec이 소비한다. 운영자가 선택한 metadata는 config가 기동 시 한 번 읽어 immutable expected provider로 공개 수락 경계에 전달한다. 선택/철회는 신규 후보 수락에 적용하며 동일 durable3 확인·일반 결과·기존 활성 실행/주문 권한과 분리한다. 명시 재시작 전 snapshot은 바뀌지 않으며 자동 전역 철회나 실제 archive 실행 attestation을 주장하지 않는다. 요청당 source hash/file polling을 추가하지 않았다. [현재 소스별 검증/미확인 경계](app-target-20261023.md#1151-ar09-선택-관측-분리의-실행-검증과-ar07-정책--2026-10-27)를 따른다.


**2026-10-24 기능교체·처리위치 기준.** 자동매매/연구는공식입력·결과/artifact와공통수집/native저장·실제주문권한을구분한다. 뉴스NAS처리기는향후PC처리로교체/제거가능하게하고NAS결과수락/조회는독립시킨다. 실제교체/물리부재·진행작업drain/재시작검증은필수잔여이며새mode/API구현완료로보지않는다. [정본11.16](app-target-20261023.md#1116-자동매매연구-교체와-뉴스-처리-위치-경계).

**네 역할의 실행 경계(F01~F25 공통, 2026-10-24 후속).** 조회 PC는 표·차트/공개 입력·결과 조회만으로 기동하고 연구·자동매매·뉴스 처리기의 설치/기동을 요구하지 않는 것을 목표로 한다. 처리기는 화면 없이 자기 작업의 입장·중단·실제 native ACK·완료/종료를 소유한다. 조회 화면 종료는 다른 실행 주체의 작업 중단 명령이 아니다. 연구/뉴스 결과 소비자는 처리기 내부가 아닌 수락 결과/출처·판본·가용시점 계약을 사용하며, 데이터 필수인 전략의 부족/오래됨과 무관한 기능의 생존을 구분한다. 실주문 권한/계좌 비밀·lease/FILL/복구는 단일 실행 owner만 보유하고 통신 단절 자동 인계는 추가하지 않는다. 역할별 현재 진입점·남은 코드/의존성 부재·headless/실통신 검증은 [단일 정본 11.17](app-target-20261023.md#1117-네-역할의-실행상태데이터권한-경계)을 따른다. 문서 반영을 역할 독립 실행 완료로 계산하지 않는다.

**2026-10-24 최소 이전 기준(F01~F25 공통).** 필수 순위 이력+해석 최소정보와 사용자 확정 예외만 새 구조로 이전한다. 미사용 과거 자료는 원본 DB에 보관하고 T0부터 다시 수집/계산할 수 있으며 동일 재생성 증명은 이전 생략 조건이 아니다. 현재 설정/사용자 편집·미확정 주문/미완료 저장/복구의 최소 예외 이전은 사용자 확정이다. 새 자료의 의미/관계/출처/판본·권한/안전/복구와 실제 외부 호환을 유지하며 physical schema/내부 API/설정은 고정하지 않는다. 빈DB 새 설치와 선별 이전을 별도 검증하고 불필요한 옛 호환은 제거한다. [정본/담당AR 조건](app-target-20261023.md#1112-데이터-보존과-db-구조-재설계--2026-10-24). 자료 삭제·운영 전환은 별도 승인이다.

- **상태와 transaction:** UI의 선택/표시, application의 판단, worker/runtime의 진행 상태, repository/store의 영속 상태를 구분한다. DB 연결·transaction·commit/rollback은 기존 native 저장소가 소유한다. 라우터나 UI에 같은 업무 SQL을 복제하거나 native ACK를 RAM 반영으로 대신하지 않는다. 로컬 DB의 사용자 편집/오프라인 원본과 중앙 DB의 역할도 구분한다.
- **시계와 자료:** [AGENTS 시간 기준](../../AGENTS.md#앱의-시간-기준--기존-키움-보정-시계kst)에 따라 같은 사건의 순간·출처/판본과 판단 cut의 당시 가용성을 보존한다. 원 체결·봉·기사 발행·저장 수락·사후 보완 시각은 각 경계에서 구분한다. 보정된 업무 현재시각은 기존 now_provider/server_now를 재사용하되, 원본 시각 변환·canonical UTC·동결 연구 비교·경과 타이머에 키움 client를 일괄 연결하지 않는다. 공유 수락/lease에는 일관된 절대시각이 필요하지만 기능별 직접 키움 의존이 필수인 것은 아니다. 정상 연결은 소비/복구 검증 없이 제거하지 않는다. 과거 R3-023 등록 완료와 변환 영수증은 보존하고, 이번 감사 잔여/담당 AR·우선순위는 [정본11.54](app-target-20261023.md#time-audit-followup)를 따른다. unknown/partial을 정상·주문 승인으로 바꾸지 않는다. 새 목표 구조에서 실제 이전/유지하는 책임에만 적용하고, 교체될 운영 코드의 전수 정상화나 미사용 과거6건 상세복구를 완료 조건에 추가하지 않는다. 사용자 KST 표시·동일 순간·당시 가용성은 새 구조의 필수 계약이다.
- **실패와 수명:** task/thread/queue를 만든 기존 owner가 실패 전달·retry·stop·join/drain을 소유한다. 새 caller는 기존 결과/실패 신호와 ACK 의미를 소비한다. 안전시점이 다른 Risk/control read를 단순 중복으로 제거하지 않는다. 새 owner는 독립 상태·정책·수명·저장 경계가 실제 필요한 경우에만 계약 변경으로 검토한다.
- **공식 경계:** 아래의 현재 public 메서드·typed DTO·Protocol·인증 API를 사용한다. private helper를 다른 기능의 새 API로 삼거나 단순 전달 클래스를 추가하지 않는다. 기존 호환 API/저장 설정은 실제 소비자를 확인한 뒤 변경한다.

<a id="composition"></a>

**F01/F22/F23/F24 실제자료289 현재 근거(2026-11-03):** source287/full288의 실제265 새 사본1건/93.696초 PASS에서 두 main·일지 설정 표시, 공개 서버 과거 순위의 같은 watermark/canonical 본문과 role/3owner/firstreceipt/pending 보존·재시작, 두 정상 종료/supplier drain을 확인했다. 원 ready15/close15/Job3/worker180과 실제 source/origin·native9tree/UUID/성공 TEMP·VM 정리를 유지했다. 실제 표/시장/계좌·최종 sourcequiet/ACK/T0·운영 수락은 아니다. 임시 도구의 두 VOID 선언만 바로잡았고 참 HRESULT 오류는 유지했다. 성공 중 실제 창 무효화 재선택은0회이며 이전 E_FAIL 전체 원인·원 Abort/PG3초/누적 gate는 미해결로 보존한다. 기존 native7·구조/증분은 같은 source의 별도 근거다. [현재 계약/근거/잔여](app-target-20261023.md#actual-retained289). 이전 현재 문구는 [갱신 전 원문](../../tmp/ar08-retained289-execution-20261103/before-current289/docs/architecture/r3-ownership.md)에 보존한다.

**F01/F23/F24 현재290(국소 통합·관련31 수락):** parent의 LocalCentralServerProcess가 자기 start event/child를 terminal까지 소유하고 child의 기존 watch→Uvicorn lifespan 실제 저장/완료 ACK→exit0을 확인한다. 실패·강제종료/interrupt는 성공으로 숨기지 않고 원5+2초/외부 lease·외부 재사용 서버를 보존한다. stdlib WindowsShutdownEvent는 설정/DPAPI와 분리한 native HANDLE 자원만 소유한다. 원 업무 DB/시계/주문 계약 변경0·새poll/서비스0. 현재31/native22·구조 위반0에 source290 포장Analysis/PYZ34 코드 일치·build120 terminal/venv불변을 추가 수락했다. 실제292 닫힌 clone cut/현재 설치물303 두 기동은 한정 수락했다. 같은 source290 POSIX 실제 종료2의 final COMMIT/정상0·주입실패1·소유PID/컨테이너 종료를 추가 수락했다(기존31에 새 고유2로 합산하지 않음). C24 원 Windows PG 오류·관리형 전체 기동·최종 운영은 미수락이다. [POSIX 실제 수락308](app-target-20261023.md#posix-owned-stop308). [후속 실제 수락](app-target-20261023.md#actual-recheck303). [포장/한계](app-target-20261023.md#current-packaging290). 이전289/source287과 현재source290을 구분한다. [근거/제한](app-target-20261023.md#owned-stop-acceptance290).

**F01/F16/F23/F24 C16/C24 현재329(분류·계약 유지):** 원139 당시55초 Application 조회324의 후보0과 기록 순서 양끝의 시각327을 확인했다. 양끝 시각은 원 구간을 포함하지만 연속 보존·당시 기록 여부를 입증하지 않는다. 원 Abort/child PID UNKNOWN이며 새 동일 실패 또는 직접 owner 증거가 필요하다. 기존 모듈 실행기 1,800초 기준의 SQLite 관련 검사: 49/49 PASS (204.985초). 개별 3·5초와 원 PG worker 180초는 유지했다. 앞선 모듈 180초 미완료 기록은 보존한다. PG 임시 위치만 옮긴 325는 첫 3초 3003.0089ms ERROR/0PASS로 미채택이다. 누적 실행 증거: 미수락 (KiwoomMonitor.spec 관련 현 소스 실행 증거 미제출이 첫 확인된 항목). PG 원인·지원 범위와 C24 전체 수락은 미결이다. 기존 fresh Risk/control·native claim과 저장·복구·종료·업무시계는 유지한다. [현재 근거](app-target-20261023.md#c24-related-module329).

**F01/F15/F21 패키징 검증 330.** 패키징 모듈 19/19 PASS (9.133초). 동일 소스 SQLite 49건과 함께 총 68개 실행 ID를 확인했다. F01의 배포물 자원 포함과 F15/F21의 연구 입력 hash 일치·누락 거부를 확인했다. 실제 설치·운영 실행의 수락은 기존 조건을 따른다. [근거](app-target-20261023.md#c24-packaging-ledger330).

**F01/F08/F15/F19/F20/F21/F23/F24 관련 실행 331.** 현재 원장 기준 관련 단위 모듈 8개에서 235/235건이 통과했다. 아카이브 원문, 패키징, 뉴스 저장·종료, 장전 시황, 연구 결과와 역할 설정을 단위 fixture에서 확인했다. 기능 분류와 운영 수락은 각 원래 조건을 따른다. [근거](app-target-20261023.md#c24-ledger-corrections331).

**F19/F25 PostgreSQL 접근 감사 332.** 두 뉴스 권한 통합 fixture의 진단용 연결 네 건을 정확한 함수·호출 수로 등록했고 현재 접근 감사 단위 검사 6건을 확인했다. 실제 PG 실행은 별도 검증 범위다. [근거](app-target-20261023.md#c24-full-module-evidence332).

**F01/F08/F15/F16/F19/F21/F23/F24/F25 관련 실행 333.** SQLite 보호, 패키징, 아카이브, 역할 설정과 저장 경계의 현 소스 검사를 확인했다. 뉴스 진단 PG 두 건은 이전 source164/165의 실제 통과 근거를 연결했다. [근거](app-target-20261023.md#c24-current-related333).

**F01/F02/F03/F04/F05/F07/F09/F10/F13/F15/F19/F21/F22/F23/F24/F25 관련 검증 365.** 순위·업데이트, 시세 수신과 실시간 전환, 연구 입력·복구·화면 관련 원 검사를 확인했다. 359 소스 합성 관리형 기동·인증 조회·정상 종료를 확인했다. 실제 설치물의 관리형 기동과 최종 운영 전환은 미수락이다. [365 근거](app-target-20261023.md#c24-last-local-runtime-evidence-365).

**F25 관측 도구 확인 366.** 검사 전용 Windows Job에서 합성 자식의 시작 통지·생성 시각과 Job 정상 종료를 확인했다. 보호주문 3초 원검사와 실제 PostgreSQL 관측의 판정은 미결이다. [366 근거](app-target-20261023.md#c24-owned-job-fixture-and-tmp-cleanup-366).

**F16/F17 보호주문 검증과 F25 관측 367.** 현재 359 판본의 원검사에서 첫 취소 확인 대기가 3초에 실패했다. 소유 PostgreSQL 프로세스 생성 시각과 시한을 넘긴 세션을 대조하고 검사 종료·정리를 확인했다. 지연 원인과 기능 수락은 미결이다. [367 근거](app-target-20261023.md#c24-owned-postgres-observer-prepared-367).

**F16/F17/F25 복구 입력 연결 368·369.** 첫 최신 Risk 조회를 기존 복구 읽기 연결에 포함했다. 저장 직전의 최신 Risk 재조회와 실행권·저장 경계는 유지한다. 369 격리 SQLite에서 저장 문서 동일·연결 8→7·두 stale Risk 거절과 잘못된 자료형의 I/O 전 거절을 확인했다. 369 등록 관련 97건이 통과했다. 격리 PostgreSQL의 입력 생산 연결은 11→10이며 보호주문 원검사는 첫 3초 취소 확인에서 실패했다. 정리는 확인했고 기능 수락은 대기다. [368·369 근거](app-target-20261023.md#c24-recovery-read-scope-368).

**F16/F17/F25 취소 원주문 조회 370.** 같은 원주문을 관리할 때 각 단계의 동일 ID 조회를 공유한다. 교체 자식의 원주문 조회와 최신 Risk·실행권·native 취소 저장을 유지한다. 등록 10건과 SQLite 첫 취소·재시도 조회 8→4를 확인했다. 370 Windows PostgreSQL의 gateway 전 해당 조회 2→1을 확인했다. 원검사는 첫 3초 대기에서 재실패했고 정리를 확인했다. 기능 수락은 미결이다. [370 근거](app-target-20261023.md#c24-same-intent-cancel-370).

**F16/F17/F25 체크포인트 이후 최신 조회 371.** 저장 확인 후 control·Risk가 연결 하나를 공유한다. 불일치 시 후속 판단을 중단하고, 정상 경로의 원장·실행권·시한 검사를 유지한다. 관련 8 PASS, SQLite 6→5·격리 PostgreSQL 4→3을 확인했다. 보호주문 원검사 3초 실패와 정리를 보존했다. [371 근거](app-target-20261023.md#c24-post-ack-scope-371).

**F16/F17/F25 판단·대사 조회 372.** 판단 입력 연결을 통합하면서 최신 복구·STOP을 별도 문장으로 읽는다. REST 계좌 대사의 실행권 갱신 전 중복 주문 조회를 제거하고 갱신 이후 최신 원장을 읽는다. 관련 11 PASS·체결 보존·실행권 상실 차단·격리 PostgreSQL 비용 감소를 확인했다. 보호주문 원검사의 3초 재실패와 정리는 보존한다. [372 근거](app-target-20261023.md#c24-gate-reconciliation-372).

**F16/F17/F25 최신 Risk 게시 373.** 기존 Risk 저장소가 불변 이력을 먼저 커밋하고 관측한 현재값에 대해서만 최신 판본을 게시한다. 경합한 낮은 판본은 현재값을 덮지 못하며 재시도·미확인 RAM 게시 차단·native 최신 Risk 재검사는 유지한다. Windows 20건과 격리 PostgreSQL 1건을 확인했다. 보호주문 원검사의 3초 재실패와 운영 수락 미결을 보존한다. [373 근거](app-target-20261023.md#c24-current-risk-cas-373).

**F13/F19/F22 검사 연결 374.** 테마 현재 조회·생성 게시와 뉴스 BODY 저장·재시도의 공유 정의를 SQLite·PostgreSQL 실제 실행 ID에 연결했다. 관련 Windows 54건·격리 PostgreSQL 27건에서 정합성과 기존 경계의 회귀를 확인했다. 기능 책임과 연결 상태, 운영 수락 범위는 기존 판정을 따른다. [374 근거](app-target-20261023.md#c24-runtime-catalog-374).

**F16/F17/F25 저장 확인 뒤 주문 원장 조회 375.** 같은 저장소의 현재 control·Risk·원주문·이력을 기존 읽기 연결로 확인한다. 새 커밋과 거절 순서가 유지되고 읽기 종료 뒤 실행권을 갱신한다. Windows 47건과 격리 PostgreSQL 1건을 확인했다. 보호주문 원검사의 3초 재실패와 운영 수락 미결을 보존한다. [375 근거](app-target-20261023.md#c24-post-ack-ledger-375).

**F16/F17/F25 취소 전후 원장 조회 376.** 계좌 새로고침 전과 실행권 확인 후의 두 주문·이력 쌍이 각자의 읽기 연결을 공유한다. 관련 Windows 69건과 실제 격리 PostgreSQL 연결 4→2를 확인했다. 원검사의 3초 초과 및 보호주문 절차 정상 종료 시점은 미결이다. [376 근거](app-target-20261023.md#c24-cancel-gateway-read-376).

**F16/F17/F25 보호주문 사전 판단·종결·실행기 종료 400.** 현재 소스의 Windows PostgreSQL 원검사와 별도 PG 계약 5건, 연결된 표준 Windows 105건을 확인했다. 최신 변경분 실행 ID 충족과 공식 누적 미수락을 분리한다. 운영 전환과 최신 NAS 수락은 미결이다. [400 근거](app-target-20261023.md#c24-local-contracts-and-cumulative-evidence-400).

**F16/F17/F25 보호주문 연결과 실패 복구 402.** 같은 native read-only owner를 재사용해 주문 계획의 연결이 9→7회로 줄었다. 실제 PostgreSQL 이력 조회 오류에서 동일 reader 롤백·종료, 영속 체결 유지, 후속 gate·checkpoint·취소 미진입과 같은 입력의 중복 없는 재시도를 확인했다. 최신 Risk·STOP·lease와 저장 ACK 경계를 유지한다. 403 최종 작업본의 실제 Windows 격리 PostgreSQL 원검사에서도 계획 연결 7회와 두 3초 대기 통과를 확인했다. Windows 개별 연결의 내부 원인과 운영 수락은 미확정이다. 404의 동일 작업본 계좌·주문 안전 표준 프로필 20모듈 589건은 통과했고, 공식 누적 판정은 부분 근거다. [404 표준 근거](app-target-20261023.md#current-standard-and-nas-readiness-404) · [403 PG](app-target-20261023.md#current-native-and-cutover-403) · [402 비교](app-target-20261023.md#c18-managed-frozen-and-c24-attribution-402).

**F16/F17/F25 QueryStore 정적 계약 406.** 앞선 Risk 조건부 게시와 읽기 범위 재사용의 참조·서명을 기존 소비자 기준선과 대조해 정확한 항목만 반영했다. 기존 수신자 승인과 미분류 구분을 보존했고, 동일 소스의 표준 12모듈 161건에서 소비자 감사 4 ID와 직접 PostgreSQL 연결 감사 2 ID를 확인했다. 원격 운영 연결과 Windows 개별 연결의 내부 원인은 미확정이다. [406 표준 근거](app-target-20261023.md#current-querystore-and-archive-standard-406).

**F16/F17/F25 보호주문 최신 연결 관측 407.** 같은 Windows 격리 PostgreSQL 원검사의 3초 대기 두 건과 취소 거절 후 재시도를 확인했다. 두 대기의 57/37개 연결에서 PostgreSQL 백엔드 시작 구간이 각 연결 첫 읽기의 과반을 차지했다. 초기화의 내부 원인·운영 분포·실계좌 수락은 미확정이다. 이 검사의 제품 동작 변경은 0건이다. [407 원검사와 정리](app-target-20261023.md#c24-current-connection-stages-407).

**F16/F17/F25 보호주문 시작 구간과 활성 주문 조회 408.** 기존 Windows 원검사의 두 3초 대기와 정리를 확인했다. 시작 구간 내부의 유효 관측 간격 321개에서 CPU 계수 증가를 확인했으며 정확한 내부 원인은 미확정이다. 실제 활성 주문 별도 연결은 대기당 1회였다. 초기 후보의 격리 239건은 통과했으며 수정 후보의 PG 최신성·연결 감소·종료 실패는 미검증이다. 현행 연결과 안전 계약의 변경은 없다. [408 연결 및 검사 근거](app-target-20261023.md#c24-owned-cpu-and-active-reader-408).

**F16/F17/F25 활성 주문 조회 연결 재사용 409.** 실행·평가 소유자가 같은 native 저장소를 쓸 때 계좌·run 범위의 최신 조회를 기존 읽기 연결에서 수행한다. PostgreSQL 원검사의 두 대기에서 별도 연결 각각 1→0회와 같은 연결의 SELECT 각각 1회, 실제 peer commit 가시성, SQLite 읽기 및 종료 오류의 gate·checkpoint·claim·전송 차단을 확인했다. 기존 fresh Risk·STOP·lease·저장 ACK와 다른 저장소의 독립 조회를 유지한다. 검증한 제품·테스트 바이트를 통합했고 통합 소스의 보호주문 관련 240건을 통과했다. 운영 수락·공식 누적은 별도다. [409 검증 범위](app-target-20261023.md#c24-active-intent-native-409).

**F16/F17/F25 현행 계좌 안전·DB 감사 410.** 409 통합 판본의 계좌·주문 안전 표준 20모듈 591건과 PostgreSQL 직접 연결·QueryStore 소비자 및 소스 계약을 확인했다. 같은 소스로 만든 NAS 검사 후보는 로컬 매니페스트까지 확인했다. 실제 NAS와 CI 수락은 미확인이다. [410 판본별 근거](app-target-20261023.md#c24-current-official-and-nas-freeze-410).

**F04/F09/F19/F25 causal capture 관련 근거 411.** 409 통합 소스에서 공식 요구의 누락 38모듈을 기존 등록 검사로 확인했다. TOP20·시장/뉴스의 최초 실패와 원 조건의 단독 재검증 통과를 각각 보존한다. 현재 근거는 격리 입력·저장·재생·종료 검사이며 최초 두 실패 원인과 실제 NAS·봉인 환경 수락은 별도다. [411 검증 범위](app-target-20261023.md#c24-causal-regression-and-receipts-411).

**F02/F03/F07/F15/F19/F21/F22/F24/F25 본문·자료 연결 근거 412.** 409 통합 판본의 본문 마감 관련 Windows 100과 격리 PostgreSQL 34 ID, 역사 뉴스 가져오기 PG 5 ID를 확인했다. 합성 순위 원본·연구 결과·뉴스 BODY/RULE의 저장·재시도·시계 계약을 포함한다. 조회 전용 조립은 같은 소스의 독립 Windows 결과다. 실제 선별자료 T0·운영 수락은 해당 AR에서 별도로 확인한다. [412 검증 근거](app-target-20261023.md#c24-historical-body-and-pg-412).

**F23/F24/F25 운영 도구 검증 근거 413.** 409 통합 소스의 Linux 격리 설치·복구·소스 입장 25건과 Windows 운영자·소스 도구 등록 15건을 확인했다. 운영 도구가 시험 작업의 설치·입장·정리를 맡는 경계를 확인했으며, 실제 NAS 게시·관리자 수락과 운영 기동 검증은 별도다. [413 검증 근거](app-target-20261023.md#c24-linux-operator-current-413).

**F16/F17/F18/F25 보호주문 근거 414.** 409 격리 PG 원검사 기록의 대기별 연결 비용과 백엔드 시작 구간을 다시 계산했다. 실제 주문을 대신한 합성 응답과 원장 조회를 구분했다. 신규 연결 감소 후보는 기존 fresh 계좌·Risk/control·lease, 불변 이력 확정 뒤의 현재값 CAS, 취소 원장·WATCH 체크포인트와 오류 전파를 보존해야 한다. Windows 시작 지연의 내부 원인과 운영 수락은 미확정이다. [414 원인·검증 범위](app-target-20261023.md#c24-pg-latency-and-linux-stdlib-414).

**F24/F25 Linux 진단·배포 근거 414.** 현행 소스의 Linux 진단 제어 16건과 소스 런타임 배포 fixture 8건을 격리 실행했다. 실제 NAS의 실행·관리 수락은 별도다. [414 검증 근거](app-target-20261023.md#c24-pg-latency-and-linux-stdlib-414).

**F01/F07/F08/F15/F19/F21/F23/F24/F25 Windows 관련 근거 415.** 409 통합 소스의 10모듈 206건에서 기동·작업 수명·시장/진입 자료 저장·재시도·스키마·AI 기록·연구 자료 소비를 확인했다. F16/F17의 Risk 연결 재사용은 격리 후보이며 현행 제품 계약과 운영 판정은 유지한다. [415 검증 근거](app-target-20261023.md#c24-risk-candidate-and-historical-windows-415).

**F02/F07/F09/F15/F16/F17/F19/F21/F24/F25 교차 검증 416.** 현행 소스의 Windows 118건과 격리 PostgreSQL 112건에서 연구 입력·저장·조회, AI 권한/기록, TOP20 대사와 DB 계약을 확인했다. Risk 연결 공유는 진단 요약과 입력 기록의 재현에 따라 격리 상태를 유지한다. [416 근거](app-target-20261023.md#c24-historical-pg-and-risk-diagnostics-416).

**F01/F03/F15/F18/F21/F22/F24 Windows 관련 검증 417.** 현행 13모듈 238개 단언을 확인했다. F01/F18/F24는 일지 작업자 대기 목록의 삭제된 객체로 인한 종료 예외를 미해결로 기록한다. 네이티브 join, DB/lease 정리의 기존 단언 통과와 종료 콜백 예외를 구분한다. [417 근거](app-target-20261023.md#c24-related-windows-and-journal-drain-417).

**F01/F18/F22/F24 일지 설정 작업자 종료 418.** 일지 창은 네이티브 설정 요청을 실제로 기다린 뒤 기존 메서드로 종료 대기 목록에서 해제한다. 동기화·클라이언트·PID·사용권 정리 책임은 유지한다. 격리 소스에서 세 경로와 일지 55건, 통합 소스에서 관련 73건과 종료 로그를 확인했다. 이전 소스의 운영 미검증 조건과 전체 AR 수락 조건은 유지한다. [418 근거](app-target-20261023.md#c24-journal-settings-release-418).

**F15/F16/F17/F18/F21/F24/F25 Risk·패키징 근거 419–422.** 현행 패키징·역사 자료 검사 11건과 Risk 격리 후보의 실제 PG·관련 12건을 구분한다. 보호주문 후보와 현행은 첫 거절 확인 3초 검사에서 실패했고 앱·DB·Job 정리는 확인했다. 후보의 COMMIT·재시도·관측 검증은 국소 근거이며 제품 미통합 상태다. [422 근거](app-target-20261023.md#c24-risk-commit-guard-and-original-timing-422).

**F15/F16/F17/F18/F21/F24/F25 단계 비교 423–425.** 현행 감사 7건을 통과했다. 보호주문은 현행·후보의 계측과 후보 최소 계측이 원래 시한에서 통과했다. 이전 시간 초과는 보존하며 서로 다른 WATCH 복원 상태, 실제 커밋·권한 경계를 유지한다. 시간 여유와 Risk 제품 통합은 별도다. [425 근거](app-target-20261023.md#c24-protection-stage-comparison-425).

**F15/F16/F17/F18/F21/F24/F25 연결 절감 426–429.** 격리 후보에서 주문 대사의 리스 커밋·최신 조회와 FILL 뒤 fresh control을 유지하며 물리 연결을 줄였다. 원 보호주문은 4/4 통과했고 가장 느린 첫 대기 여유는 146ms였다. 현행 F 연결 판정은 유지하며 진단 캡처 호환과 제품 통합은 별도로 검증한다. [429 근거](app-target-20261023.md#c24-protection-headroom-429).

**F15/F16/F17/F18/F21/F24/F25 연결 절감 통합 437.** Risk·재대사·FILL/control·복구 영수증이 기존 PostgreSQL 세션 범위를 재사용한다. 안전 확인과 각 커밋, 오류 복구, 진단의 업무 호출 기록은 유지한다. 통합 지문에서 PostgreSQL 7건·관련 24건과 원 보호주문 1건을 통과했다. 후보 계측의 첫 확인 연결은 56→45회였다. F 연결 상태와 운영 제한은 유지한다. [437 근거](app-target-20261023.md#c24-protection-recovery-reuse-435).

**F15/F16/F17/F18/F21/F24/F25 누적 검증 444.** 436 판본의 실제 PostgreSQL 접근 원 모듈 115건을 확인했다. 442 지문의 위임 검사는 기존 8개와 승인된 새 위임 6개의 소유·바인딩을 구분한다. 현재 구조·위임 110건, 인과 진단·격리 도구 155건, 실제 봉인 재생 11건을 통과했다. 제품 경로와 F 연결 상태는 유지한다. [444 근거](app-target-20261023.md#c24-query-delegates-and-pg-module-442).

**F25 진단 응답 호환 검증 451.** 공개 DB 진단과 생성 보고서의 요약 형식을 복구했다. 연결 수명은 원시 관측 행으로 제공하고 읽기·쓰기 및 트랜잭션 집계와 분리한다. 중앙 서버 원 모듈 97건·실제 PostgreSQL 7건·위임 4건에서 공개 계약과 연결 관측을 확인했다. F25 연결 판정과 운영 수락 조건을 유지한다. [451 근거](app-target-20261023.md#c24-diagnostic-http-shape-450).

**F03/F04/F16/F17/F18/F25 현행 봉인·보호주문 458.** 핵심 7모듈·뉴스/관측 5모듈과 실제 PostgreSQL 접근 115건을 현행 지문으로 검증했다. 봉인 재생 원 경로에서 실제 ACK 유실, 기준선 복원과 격리 자원 종료를 확인했다. 보호주문 원 경로는 거절과 수락의 두 확인을 각각 2.354초·1.665초에 통과했다. 필수 로컬 봉인 9/9를 충족했고 F 연결 판정과 운영 수락 조건을 유지한다. [458 근거](app-target-20261023.md#c24-current-causal-and-sealed-458).

**F03/F04/F09/F16/F17/F18/F19/F21/F23/F25 누적 검증 471.** 현행 TOP20 입력·출처·수명, 계좌/주문·역할/자격증명, 뉴스·기록/재생 및 Linux 격리 계약을 검증했다. 연구 내보내기 45건과 실제 격리 PostgreSQL의 순위·연구 결과·준비 뉴스 16건을 확인했다. 운영 수락과 현재 F 연결 판정은 유지한다. [471 근거](app-target-20261023.md#c24-causal-linux-and-export-471).

**F01/F02/F03/F04/F09/F19/F22/F23/F25 누적 검증 477.** 현행 Windows에서 조회 전용 앱·역할 전환·열 설정, TOP20 재생/세션 계획과 외부시장 입력, 시장·과거 뉴스 API 및 본문 마감의 정확 ID를 검증했다. 인과 진단 59/59와 요구 전체 모듈 65/65의 로컬 실행 근거를 확보했다. 운영 수락과 F 연결 판정은 유지한다. [477 근거](app-target-20261023.md#c24-causal-and-local-whole-477).

**F16/F17/F18/F23/F24 보호주문 반복 478.** 현행 Windows 격리 PostgreSQL에서 동일 계좌 키 교체의 원 취소 거절·복구·재시도 수락과 앱·DB 종료를 같은 ID로 5회 확인했다. 원 3초의 최소 여유는 394ms였다. 저장 책임과 F 연결 판정은 기존 계약을 따른다. 운영 부하와 실계좌는 별도 수락 조건이다. [478 근거](app-target-20261023.md#c24-protection-repeat-478).

**F19/F25 뉴스 반입·작업 도구 검증 482.** 임시 저장소에서 스냅샷 게시, 인증된 반입 후 조회, BODY/RULE의 시계·재시도와 실패 기록을 확인했다. 로컬 합성 자료에서 작업 도구의 경로 제한·원본 보존·진단 종료를 확인했다. 운영 NAS 수락과 F 연결 판정은 기존 조건을 따른다. [482 근거](app-target-20261023.md#c24-news-import-and-tooling-482).

**F01/F02/F03/F07/F19/F21/F24/F25 과거 연구 요청 검증 487.** 현행 판본에서 PC 화면과 작업의 기동·종료, 순위·분봉 저장, 연구 자식과 결과 발행을 확인했다. 격리 PostgreSQL에서 TOP20 대사의 원본 보존 및 실패 원복, 뉴스 AI 요청 권한의 저장·ACK 복구와 종료를 검증했다. 운영 수락과 F 연결 판정은 기존 조건을 따른다. [487 근거](app-target-20261023.md#c24-historical-baseline-487).

**F01/F02/F04/F05/F07/F10/F13/F15/F17/F18/F19/F21/F22/F23/F24/F25 실행기·기능 연결 검증 500.** 현행 판본에서 자식·Qt 종료, 순위·일봉·분봉 자료, 연구 실행·발행, 일지 동기화·복원, 계좌 바인딩과 뉴스 분석을 확인했다. 관측·테마·프로그램 자료의 저장은 격리 PostgreSQL에서 확인했다. F 연결 판정과 운영 수락은 기존 조건을 따른다. [500 근거](app-target-20261023.md#c24-runner-and-feature-evidence-500).

**F01/F03/F04/F07/F09/F19/F21/F24/F25 서버 조립·수신·조회 검증 507.** 실제 로컬 제어·작업자 종료, 연구 역할과 입력 조회, TOP20·실시간 수신·시장 조회의 합성 응답과 수명 계약을 확인했다. 뉴스 권한, 순위 원본·데이터셋의 실제 저장과 재시도는 격리 PostgreSQL에서 확인했다. 운영 수락과 F 연결 판정은 기존 조건을 따른다. [507 근거](app-target-20261023.md#c24-server-inputs-and-routes-507).

**F04/F05/F11/F12/F13/F14/F15/F19/F21/F24 시장·테마 Shadow 입력 검증 508.** 합성 0B·시장·계좌 입력과 임시 자료에서 자산 배분·진입 근거, 0B와 분봉 경계, 포지션 상태·연구 결과 발행 및 뉴스 저장을 확인했다. 미리보기 자식과 RAM 작업의 종료를 확인했으며 F 연결 판정과 운영 수락은 기존 조건을 따른다. [508 근거](app-target-20261023.md#c24-shadow-inputs-508).

**F01/F03/F04/F07/F08/F13/F18/F19/F22/F23/F24/F25 실시간·동기화 검증 516.** 로컬 격리에서 실시간 수신·등록·재생, 뉴스와 설정·테마 동기화, 복원과 일지 저장·재시도, 작업 종료를 확인했다. 테마·순위·기록 캡처의 저장과 경합은 격리 PostgreSQL에서 확인했다. F 연결 판정과 운영 수락은 기존 조건을 따른다. [516 근거](app-target-20261023.md#c24-realtime-sync-and-pg-516).

**F01/F10/F14/F15/F16/F18/F19/F21/F22/F23/F24/F25 로컬 정확 검증 524.** PC·Qt 작업자 종료, 후보와 주문 안전, 연구 실행·화면, 뉴스 처리, 설정 복원·업데이트를 현행 소스에서 확인했다. 실행 시 모듈 발견 보고서 100개와 실제 NAS operator·CI 근거를 별도로 관리하며 F 연결 판정과 운영 수락은 기존 조건을 따른다. [524 근거](app-target-20261023.md#c24-local-exact-and-discovery-524).

**F03/F07/F13/F19/F21/F24/F25 로컬 발견 근거 525.** 현행 Windows 87모듈의 기존 작업자 발견 기록을 대조했고, 격리 PostgreSQL 12모듈에서 뉴스 권한·테마·순위·자료 보존·기록 캡처의 모듈별 발견과 실제 실행을 확인했다. 고유 ID와 F 연결 판정은 기존 증거와 같으며 실제 NAS operator·CI 및 운영 수락 조건은 별도다. [525 근거](app-target-20261023.md#c24-local-discovery-525).

**F14/F16/F18/F24/F25 NAS 실제 격리 근거 526.** 526 비활성 소스에서 실제 NAS operator가 소유한 임시 PostgreSQL과 유효 replay lease로 계좌 기록 원자성·봉인·반복 재생·복원 1건을 검증했다. 임시 작업 정리와 운영 릴리스·컨테이너 불변, 같은 판본의 누적 검증기 통과를 확인했다. [526 근거](app-target-20261023.md#c24-nas-native-and-ci-candidate-526).

**F03/F24/F25 TOP20 저장·복원 검사 527.** 제품의 독립 재시도 및 close가 시작된 native 쓰기를 기다리는 소유권을 유지한다. PG 재시작 검사에서만 독립 루프를 격리해 최초 ACK 유실 → pending 보존 → 다음 시작 저장을 확인하고 실패 시에도 소유 서비스 drain에 합류한다. 격리 CI 후보 16/16에서 v1/v2 복원과 PostgreSQL 정리를 확인했다. [527 근거](app-target-20261023.md#c24-ci-v2-native-top20-527).

**F25 GitHub 회귀·NAS 수락 528.** 호스팅 코드 회귀는 기존 CI가 소유하며 실제 NAS 수락은 기존 operator가 소유한다. 526에서 확인한 NAS 실행 경로에 이번 CI 변경의 영향이 없음을 확인했다. GitHub 실제 실행과 운영 수락은 별도다. [528 근거](app-target-20261023.md#c24-hosted-regression-policy-528).

**F25 실제 호스팅 회귀 530.** 검사용 `24d2fcc`의 봉인·Linux operator·Windows 파티션 1은 통과했고 전체 GitHub 회귀는 실패했다. 실제 NAS 수락 526과 운영 수락 조건은 별도로 유지한다. [530 근거](app-target-20261023.md#c24-hosted-run-530).

**F25 PC 파일 로그·호스팅 회귀 532.** PC 파일 처리기의 재귀 오류 전달과 기존 일반 오류 기록을 유지했다. Windows 회귀는 기존 범위를 8개 시간균형 파티션으로 나누며, 실제 GitHub 재실행 수락은 남는다. [532 근거](app-target-20261023.md#c24-hosted-fixes-and-partitions-532).

**F07/F08/F14/F16/F17/F18/F25 회귀 근거 533~534.** Windows 8개 파티션 중 7개 통과와 3번의 보호주문 재시도 2건 실패를 확인했다. 최신 Risk 판본 확인을 유지하며 갱신 경합 원인을 확인해야 한다. 관측·분봉·Shadow의 PostgreSQL 검사 fixture 보완은 격리 26건에서 검증했다. 제품 연결 판정과 526의 NAS 수락은 기존 근거를 유지한다. [533](app-target-20261023.md#c24-hosted-balanced-run-533) · [534](app-target-20261023.md#c24-postgres-fixtures-534).

**F14/F16/F17/F18 보호주문 Risk 갱신 536.** 계좌 모니터는 최신 Risk와 ACK를 소유한다. 같은 실행 소유자의 새 Risk가 이전 재시도 평가를 무효화하면 episode는 기존 기한 내에서 새 ACK로 재평가한다. 주문 기록 직전 최신 Risk 검증, 취소 횟수, ACK 손실과 종료 안전을 유지한다. Windows 57·격리 PG 26 국소 검증이며 C24 hosted 수락과 운영 연결 판정은 별도다. [536](app-target-20261023.md#c24-protection-risk-supersession-536).

**F14/F16/F17/F18 보호주문 검증 537.** 536의 실행 책임·안전 조건을 유지했다. 기존 3초 측정 검사는 기존 부하를 쓰고, 강제 Risk 갱신은 독립된 회귀 검사에서 실행한다. Windows 58·격리 PG 26건 국소 통과이며 운영 연결 판정은 기존 조건을 따른다. [537](app-target-20261023.md#c24-distinct-timing-and-race-537).

**F14/F16/F17/F18/F22/F25 GitHub 검증 538.** 보호주문 3건과 기존 PG·봉인 재생은 해당 커밋에서 통과했다. Windows Drive의 늦은 ACK 처리 검사는 첫 가짜 업로드 진입 대기에서 실패했고 최종 필수 CI도 실패했다. F22의 이번 호스팅 경계는 미수락이다. [538](app-target-20261023.md#c24-hosted-537-run-538).

**F22/F25 Drive 검증 539.** 원검사 대기 문제를 이 PC의 통제 환경에서 재현하고 기존 Qt/SQLite·늦은 ACK·종료 단언을 보존해 Windows 118건에서 확인했다. 제품 연결 판정과 539 GitHub 수락은 별도다. [539](app-target-20261023.md#c24-drive-qt-event-yield-539).

**F14/F16/F17/F18/F22/F25 GitHub 검증 540.** 동일 커밋에서 Windows Drive 늦은 ACK·보호주문·필수 PG/봉인·Linux 검사와 최종 필수 판정이 통과했다. 526 NAS 선택 경로의 수락은 별도 근거로 유지한다. 운영 전환은 기존 조건을 따른다. [540](app-target-20261023.md#c24-hosted-539-run-540).

**F01/F23/F24 C16 재발 시 후속 이관 541.** 원139의 최초 Abort 원인은 미확인으로 보존한다. 동일 오류가 재발하면 실패 자식 PID·첫 C++ 호출 스택을 작업·종료 수명에 결속해 조사한다. 이번 판정은 확인된 기동·종료 근거와 기능 연결 상태를 변경하지 않는다. [541](app-target-20261023.md#c16-recurrence-handoff-541).

**F01/F21/F22/F23/F24 실제292/303·종료305(부분 연결 유지):** Cut5/native33와 현재 설치물 exact1/56.087초·두 자연 기동/일지/고정 과거HTTP/설정11·전체rank 보존을 수락했다. native9tree/UUID/TEMP·실패자료 보존/재구성·소유guest 종료를 확인했다. 최초 실패/준비 오류와 운영 미수락은 유지한다. [현재 실행/한계](app-target-20261023.md#actual-recheck303).

<details>
<summary>이전292 준비 근거</summary>

**F23/F24 검사 준비292(기능 상태 변경0):** runtime/product/sealed 원점을 RO로 재사용하고 실제 target·실패/변경 파일과 재구성 근거를 보존한다. native Cut 부모도 기존 Job/HANDLE/remaining180·cleanup3을 소유하고 host/guest Run에 실제 exit/receipt/Job SHA를 연결했다. 최종코드 CURRENT3 정상/nonzero/timeout의 owner 수락이며 실제자료 cut/새GUI/운영은 미실행이다. 제품 source290 관련31·포장34·F의 부분 연결/기존 제한은 그대로다. [현재 준비/한계](app-target-20261023.md#lowcopy-preparation292).

</details>

**MAIN165 C21 검증(F25, 관련F01/F23/F24):** 사용자승인으로기존PC WSL2/Podman의읽기전용·networknone·CPU2/mem256M/PID64·private32MiB 실제Linux FS에서원4검사를실행해4PASS/skip0·출처/hash·종료정리를확인했다. 최초namespace증거수집오류는보존하고같은4/180초/본문으로재검증했다. 실제NAS helper/ACL/sudo/ownedjob/lease/post-fence와운영연결은C20미검증이며이4로대체하지않는다. [현재수락/정리/한계](../../tmp/ar08-linux4-source165-run001/REPORT.md).

## 조립·시계·수명 — F01/F23/F24

**시험용 빌드의 관리형 서버 수명 402(F01/F23/F24).** Windows 격리 환경에서 `local_server` 설정을 읽은 앱이 동일 EXE의 서버 자식을 기동했다. 인증 401/200, 앱 종료 이벤트, 서버→앱 종료 0, 포트·잠금·작업 정리를 확인했다. 303의 실제 자료 소비와 365의 소스 실행은 각각 별도 근거로 보존한다. 최종 운영 인계와 배치는 미수락이다. [402 검증 범위](app-target-20261023.md#c18-managed-frozen-and-c24-attribution-402).

**AR05 246r2 — Drive pending 시각 판정의 단일 책임(F01/F22/F23/F24).** 기존Drive 모듈의`has_pending_google_drive_changes`가순수한완료표식비교를소유하고bootstrap과MainWindow getter가같은결과를쓴다.기동metadata선택은bootstrap/activate_startup,dirty/수동충돌선택/재시도·정상종료는기존Window/worker/service가그대로소유한다.현재aware UTC/KST는기존`aware_iso_kst`로동일순간을비교하며explicitdirty·missing/invalid/naive legacyfallback을유지한다.현재시계생성/원시각·DB/hash/판본재작성·새state/clock/Manager/worker/I/Osite0이다.원실제bootstrap1건/3subcase실패→현재같은ID와ACK/복원/worker등관련27/27PASS·35.116초를수락했고원실패와v4 fixture오류2는보존한다.실제Job/HKCU/성공TEMP정리·현재source246r2/188기록/6900정의/원미결49·구조1303site/위반0을확인했다.원본3getter call은유지하며실제Drive지연/SQL·성능향상이나최종소비/운영·C16/C24/CI를주장하지않는다. [246r2 수락](../../tmp/ar05-drive-time246-20261102/ROOT_ACCEPTANCE246.json).

**AR05 검증subset 실제 소비와 NAS 실패(F07/F23/F24, record194/195):** PC3977행은기존nativeinstaller/queryrole/getters·원SQL NOCASE/FK의미로확인했고최종raw/receipt/원시각/준비본/ACK없음·marker재시작을보존했다. 원보조실패2건은제품오류가아니라독립검증가정오류로진단후미완료범위만완료했다. source추가조회/반입중복0·native154연결종료/TEMP4정리다. 미해결34/全필수자료와T0·실제UI·주문권한은미수락이다. NAS195는실제첫순위revision cursor30초timeout/rows0/rollback1로HOLD,동일backendPG로그가취소원인을보강했다. source/config/app무변경·소유container제거와사본미완료를구분하고승인1회한도소진뒤자동retry하지않는다. [PC](../../tmp/ar05-actual-selection-validation190-20261101/pc-query-actual001/target-validation001/ACCEPTANCE194_SAFE.json)·[NAS실패](../../tmp/ar05-transfer-readiness190-20261101/planning/capture-candidate195/execution/failure-acceptance195.json). 실제198의 계획 비교는 데이터 조회0·신규container0·동일RO/RR·종료/불변 관측이며,197의 넓어진 Sort/실측 부재 때문에 제품 미채택이다. F07/F23/F24의 전체 이전·실제 앱 연결 상태는 승격하지 않는다. [198 수락 범위](../../tmp/ar05-transfer-readiness190-20261101/planning/capture-candidate195/execution/failure-analysis/projection-plans198/acceptance198.json). 추가단회202는240work 만료/HOLD·revision부분159,936,767bytes/fetch54이며 완전manifest/rollback ACK 미확인이다. source12/config/app/후보input/SSH 불변·container 제거/소유프로세스종료만 수락한다. rows metric0은수신0행이아니며 C08/F07/F23/F24/F25 전체성공으로승격하지않는다. 새NAS추출은추가승인전중단한다. [202 결과](../../tmp/ar05-transfer-readiness190-20261101/planning/capture-candidate202/execution/failure-acceptance202.json). 후속읽기리뷰에서fetch54는metadata/빈배치를포함한누계·rows0은완료합산미도달·rollback0은ACK미확보임을명시했다. outer PowerShell1과실제controller childUNKNOWN을구분하며전체이전/PG종료수락은그대로미확인이다. [관측층/원문보존](../../tmp/ar05-transfer-readiness190-20261101/planning/capture-candidate202/review202/failure-boundaries-review202.json).

**AR05 실제PC 사본 검증(F07/F23/F24, source182):** 사용자 승인한 readonlyPC journal3 실제3행을기존snapshot/codec으로보존하고새rawtarget 원자반입·재열기/동일입력no-op·별도consumer getter3개/정상종료로확인했다. 원본문/type/updated_at·최초receipt·원rawtarget은유지했고 runtimeOFFLINE_UNBOUND/주문권한false다. raw11 실제4011행은source-free검증·9375참조/차단0까지이며최종선정/target은0이다. marker/prepared/ACK는각source관측으로보존하고동일metadata만으로writer drain/coherent cut/T0를수락하지않는다. NAS추출은미실행이며권한연장미적용과자료검증승인을분리한다. [실제3 수락](../../tmp/ar05-actual-selection-validation190-20261101/pc-journal3-actual001/receipts/acceptance001.json)·[raw11](../../tmp/ar05-actual-selection-validation190-20261101/pc-query-actual001/SAFE_SUMMARY.json).

**AR05 실제자료 검증 준비(F07/F23/F24, source182 유지):** journal fixed3는 기존 readonly snapshot/primitive codec·target schema를 재사용하고 raw 보존target과 public getter 소비target을 분리한다. commit뒤연결손실·원자receipt/재열기·다른입력충돌/원문·취소경계를 합성14개로 확인했으며 실제업무값은읽지않았다. NAS 단회추출후보는 기존보호 source/고정image·원native exporter/RO RR/namedcursor를재사용하고 source읽기·소유container/정리/원만료·DSN RAM 경계를합성6개/정적으로검토했다. 이 후보의실제NAS/DB/data실행은0이며 별도사본범위승인·실제종료/자료소비를수락해야한다. 원시각/hash/판본·실제 source owner/주문/복구권한은이전하지않는다. [PC 현재검토](../../tmp/ar05-transfer-readiness190-20261101/pc-minimum-exceptions/review/review004-final.json)·[NAS 후보검토](../../tmp/ar05-transfer-readiness190-20261101/planning/capture-candidate193/review/owner-review193.json).

**AR05 최신 최소자료 읽기(F07/F23/F24, 제품source182 유지):** PC task 표본321/partial최소201은 일지 보완 후보로서 원주문/복구 권한 원장·자동 이전 목록이 아니다. NAS catalog8관계/78컬럼/19index와 실제186/187/189의 한정읽기·RO RR·rollback/close·원격/로컬종료·원본유지를 수락했다. 187의 지정 native intents 관계 존재0은 그 관계/조회시점만 증명하며 broker/다른원장의 미체결 없음으로 확대하지 않는다. available_at 양끝과189의 ranking/membership NULL 존재0은 서로 다른 RR 관측이며 최종 coherent cut이 아니다. 기존 source/target/T0·권한·미완료 주문/저장/복구 owner와 원시각/hash/판본을 보존한다. 현재 named server cursor·기능별 선별 exporter를 재사용하되 실제 자료 추출/이전은 별도 승인이다. [187](../../tmp/ar05-native-closure187-20261101/execution/acceptance187.json)·[189](../../tmp/ar05-native-null189-20261101/execution/acceptance189.json)·[이전 준비](../../tmp/ar05-transfer-readiness190-20261101/planning/approval-scope190.json).

**AR05 원본의 실제 조립 대조(F01/F14/F16/F17/F18/F23/F24, source175와active분리):** 기존공식status를1회읽어active Oct17 release·health ok·running source path 일치를현재시점에확인했다. 이public정보로정확release의app/supervisor 선택본문2파일을일반SSH로읽어13.521초/10361B·파일/디렉터리전후fence·Job종료·SSH설정불변을확인했다. private pointer의이전PermissionError는보존하며그경로를다시읽거나권한을우회하지않았다. app은mock_owner가있을때supervisor를조립하고별도router factory에전달하며, status owner는control조회1호출과runner/bundle의요약을조립한다. factory·native leaf·active runner.status는미확보여서bounded GET/전이부작용0·SQL횟수·완전한미확정/복구목록은확정하지않는다. 로컬175의runner.episode.status를active의runner.status근거로차용하지않았다. loaded code SHA·status/source간atomicity·DB identity/기간/행수/safe cut·계좌/profile/run/T0/target·허용사본은미확정이다. [공개상태](../../tmp/ar05-nas-public-status175-20261101/receipt.json)·[선택source2](../../tmp/ar05-account-source-publicstatus175-20261101-prepare002/active-source2-receipt001.json)·[실제연결/한계](../../tmp/ar05-active-status-source2-review175-20261101-analysis001/REPORT.md). 첫reader의미지원문자열함수오류는137B/기록SHA와정확일치하는고정오류로확인했고, 접두사slice·HOLD보고2행만보완해같은2파일/20초/60KiB로재확인했다. 원실패·proof는보존하고NAS정확Python판본을추정확정하지않는다. 관리자updater인증은여전히미확보이며helper2적용/필수PG2job·7건/실제자료export·이전·운영변경0이다.

**AR05 실제 인계 목록 경계(F07/F23/F24, 미완료 유지):** 현재 로컬175 코드의 계좌 이벤트 GET는 bounded SELECT이지만 event 없는 QUEUED를 빠뜨리며, 자동운용 GET의 control1행/RAM·cursor 요약이나 단일intent의 무제한event 조회도 전체 미확정/복구 목록을 대신하지 못한다. native active-intent bounded401 reader는 확인했지만 확보한 공식 GET의 전체 목록 연결은 없다. 이는 로컬 정적 실행본문의 한계이며 실제 NAS에 같은 route/상태가 존재한다고 확정하지 않는다. 허용한 일반 SSH source읽기1회는 첫 active.json metadata PermissionError로 HOLD, transport exit0과 조사 실패를 구분했다. 활성 pointer/현재 loadedSHA·readiness는 UNKNOWN, .py 본문/API/DB/비밀config/sudo/우회/재시도0이다. 과거 Oct30의 active Oct17/health ok는 그 시점의 근거로만 유지한다. 미완료 상태 없음으로 추정하거나 event projection만으로 이전하지 않으며, 인증된 원본 owner의 일관된 native snapshot/선별 검증이 필요하다. [읽기/owner 근거](../../tmp/ar05-account-source-readonly-boundary175-20261101-analysis001/REPORT.md). 로컬 product/설정·주문 권한·원본 자료/원시각·hash 변경0이며, 이 조사로 새 API/중계·호환 계층을 만들지 않는다.

**C16 현재 일반 원조건(F01/F23/F24 일부):** source175의 strict20/target7/news2는 실제29/3worker PASS, 원전체 AST33과 실행선정29·원180/35/12/15초·기존fixture/검사본문을 구분·유지했다. 원기반공급원/늦은실제원점·모듈임의강제import0, worker/자식 Job 종료와 일반사용자 HKCU 소유UUID·성공TEMP 실제부재를 확인했다. [원29 실제 근거](../../tmp/ar08-c16-normal-cohort175-scope-20261101/root-acceptance175-normal29.json). 보완169뒤 일반묶음 공백의 검증이며 원 Abort 원인 UNKNOWN/부분 완료·전체PC운영 미수락은 유지한다. 해당경계 코드 변경·구체 실패/dump·owner 증거 없이는 같은묶음을 반복하지 않는다. 원인 근거의 한정 대조에서 원139 직접9파일 사본·현재 핵심본문 동일성과 기존535관측을 재사용했다. 알려진 출력5범위에는 matching dump가 없으며 실패순간 native stack/owner·소멸순서는 미확보다. 전체PC dump 검색이나 원소스 재구성은 하지 않았다. [원인 HOLD·재개조건](../../tmp/ar08-c16-cause-evidence175-hold-20261101/REPORT.txt).

**MAIN175 C19 로컬 기술조건 수락(F01/F23/F24):** 기존 frozen 소비/B171·원 pristine 정상 코드 전환/source173·Win5 실패A5000복귀와 현재 helper15를 실제 소스별로 대조했다. 현재15는 실제 코드복귀 뒤 내부 SQLite UNKNOWN/원identity·첫receipt/event 보존과 같은 입력 재시작 submit/cancel0을 확인한다. 코드/DB/외부ACK 복구 owner를 섞지 않으며 이전 lease/승인·주문 권한을 복원하지 않는다. [현재 helper15/종료 정리와 완료조건 대조](../../tmp/ar-v1-wave56-20261101/c19-recovery175/acceptance175.json). 원 Win5 원인UNKNOWN·실제 선별이전/설치 소비·단일compiledACK/일반schema downgrade·실계좌/운영은 별도 미수락이다.

**C19권한관측(source172 F01/F23/F24 부분):** 원A170→B171/helper606/40assert시나리오는PASS1/62.510초·자연A/B종료/설정exact·소스/Job/차용HANDLE/UUID/TEMP보존/정리였다. 선별token3읽기는성공했지만Win5미재현으로posterror file reader는미실행이어서wrapper1/normalC19false다. 원실패당시권한/effectiveaccess원인해결로바꾸지않고pristine정상control과업데이트/DBACK잔여를유지한다. [진단근거](../../tmp/ar08-c19-original-query-access-observe-prepare172-20261030/probe-result/receipt.json).

**MAIN172 실제 frozen query 연결(F01/F22/F23/F24):** 실제B171을 분리된 한글경로에서1회기동해query API/20행적용·자연종료·고정role/816×743의fresh RO exact를 확인했다(40.552초/13checks). 제품src420은ROOT172와동일하고source fixture171·실제EXE171을구분한다. Job/HANDLE/fixtureUUID/TEMP정리,직전실제PC파일29/선택설정키18 메타불변을확인했다. stock frozen NativeHKCU/HKLM no-access 전체추적은미검증이다. 새코드업데이트 WinError5·원WindowsPG3초ERROR·source162연구3를이검사로수락하지않는다. [현재범위](../../tmp/ar-v1-wave54-20261030/nas-receipt172/acceptance172-frozen-query1.json).

**현재162 검증 범위(F01/F23/F24/F25):** 기존 App108전체108/설치BODY3/frozen연구3에이어 helper 원 unit12·native5 경계를수락했다. native5는run0023case 원문별도판정+run003후속2/19.242초PASS이며원run001/002FAILED와원파일을보존한다. 정상2archive교체/삭제후합성재시작,손상ZIP전체사전검증,late교체/삭제실패의원상태full복원,ZIP정리실패의new/recovery보존과재시작0·각Job/handle종료·5TEMP실제empty를확인했다. 합성waiter/restart는실제앱graceful종료/oldrelease/DB·외부ACK복구가아니며C19는부분이다. C16은별도UNKNOWN, 서버의존성31은제품runtime0이다. [helper 실제범위](../../tmp/ar-v1-wave51-20261029/integration162/acceptance162-c19-native-helper5.json)·[정본](app-target-20261023.md#v1-detailed-checklist).

**기존 실행 환경 대조(F01/F23/F24/F25, 2026-10-29):** Windows프로젝트검사/portablePG17.11·core4·서버의존성31은현재파일/해시로재사용확인했다. 사용자가WSL설치후읽기전용재확인에서WSL3.0.3/커널6.18.54.1-1·Ubuntu/WSL2/Stopped를확인했다. Linux내부도구/컨테이너엔진은미확인이며Linux4 정확ID는확정했지만실행0이다. 당시설치확인후사용자명시재개로Ubuntu26.04.1/기존Python3를실기동확인했다. 표준PATH의container도구/기본socket은미확인·추가설치0이며Linux4 미실행이다. 해당4는PC Linux container의실제파일권한검증이고외부sudo/ACL응답은모의다. 실제NAS 설치권한/source/mount·그host의ownedjob/lease/admission/post-fence는별도증거가필요하다. NAS6 exact선정은미확정이다. 사용자요청으로일시중지했고환경/권한변경0이다. [정본/보고서](app-target-20261023.md#0-현재-기준과-진행-현황).

**MAIN162 기능 import 책임(F01, 간접 F02/F05/F21):** application package는 서비스를 eager 재수출하지 않으며 bootstrap은 동일 RankingService의 실제 owner에서 직접 가져온다. 공통 MinuteOhlcv/집계기는 기존 위치·계산을 유지하고 TradeTick의 타입표기만 TYPE_CHECKING으로 분리했다. 신규 계층/상태/DB·시계·작업은0이다. 현재 query13과 정확 불변161의75로 연결을 수락하며 C12 원160128은 정의/owner 동일의 범위만 재사용한다. 원161 fixture4ERROR와 원160 설치실패는 보존했다. [관련 근거](../../tmp/ar-v1-wave51-20261029/integration162/acceptance162-related88.json).

**현재 MAIN160 C12 수명 연결(F01/F23/F24, F25 선택 관측):** 현재 관련128(App108/Main문맥12/Query8) PASS로 C12 범위의 실제 연결을 확인했다. 저장/native/observer 완료 뒤 outer 자원을 해제하고 최종 설정 크기 worker는 같은 owner가 합류한다. 공개 finish의 대기 중에만 기존 ready 신호로 동일 dispatcher 대기를 해제하며 정확한 임시 연결은 finally에서 제거한다. 첫 오류·명시 재시도·원 assert/시한은 유지했다. 17Job/tree·UUID 실제부재·성공TEMP17 제거와 source/metadata 불변을 확인했다. 전체 F 수명·설치·자료 인계·NAS/CI/운영 수락은 남는다. [현재 수락](../../tmp/ar-v1-wave51-20261029/integration160/acceptance160-c12.json)·[정본 잔여](app-target-20261023.md#v1-detailed-checklist).

**MAIN150 Recorder class(F01/F23/F24):** 기존matching lock/queue를 재사용해 입장/cut/actualthread와 원오류/RAM을 class에 귀속했고 shared4fact는 한 정의다. 기존clock/row정책을 유지한 관련23 PASS이며 runtime owner receiver는 아직 연결되지 않았다. [class 검증·실제 인계 잔여](app-target-20261023.md#main150-recorder-class).

**MAIN149 REST A/B2/C(F01/F23/F24):** Controller가 정확한 detached/current client·UNKNOWN과 정리 task/result/native tail을 소유한다. 기존 worker의 opt-in retain/public wait0 release를 사용하고 필수writer drain/pending0 뒤 current를 한 번 인계한다. actual source/Qt/SQLite ACK-loss·retry/원error/fact와 default3을 포함한120 PASS다. Recorder/outer setup/운영은 별도다. [현재 연결·증거](app-target-20261023.md#main149-rest-main-lifetime).

**MAIN148 factory 소유(F01/F23/F24):** 정상 runtime 반환 전 fresh REST 비교client는 factory가 소유한다. 후속 조립 실패 때만 같은 객체를 실제 종료하고 원예외 identity와 cleanup cause를 전달한다. 기존 current/정상 조립은 유지, 관련97 PASS다. 기존 cause chain 전체 보존이나 별도 Recorder/B2/C/outer setup 완료로 확대하지 않는다. [전후·미수락](app-target-20261023.md#main148-factory-rest-client).

**MAIN147 지수 작업 수명(F01/F23/F24):** Window는 path/기존 clock callable과 UI/native 수명만 연결하고, 지수 worker는 기존 monitor 원자 저장 후 성공을 소유한다. 실제 join/lease 전에 저장 결과가 보이며 같은 소스 관련77 PASS다. [현재 검증과 범위](app-target-20261023.md#main147-index-native-result).

**MAIN146 source 수명(F01/F23/F24):** 기존writer의공개release receipt가실제3 whole-loader/후속TR/rollback후재교체의해제상태를소유한다. caller는privatecache를읽지않고후속B에서소비할예정이며현재B/C 실제client종료는미연결이다. 현재관련29PASS·원145 receipt부재4ID/5하위FAIL을분리보존한다. [현재연결/미수락](app-target-20261023.md#main146-source-release).

**MAIN145 관측 시각(F01/F23/F24·직접F22):** 조회cut와결과관측을구분하고기존RAM시계두번째관측값을native저장/UI에동일전달한다. 뒤로조정된clock는시작하한을유지하며원시각을수정하지않는다. 현재관련98PASS·원144 3FAIL/worker-only정밀도1FAIL을분리보존한다. [현재근거/한계](app-target-20261023.md#main145-observed-availability).

**MAIN144 선택 비교client 수명(F01/F22/F23/F24/F25):**client가비교입장/FIFO/진행중요청/실thread/원오류를소유하고Journal은획득직후예외와native borrower종료뒤양쪽close를소유한다. report/log I/O와수명잠금은분리했고원143 8ID/10assertion실패·미통합V1 negative1FAIL→현재관련87PASS를확인했다. 원report오류/RAMremaining은실패로보존하며durable복구/전체main client/0B/운영종료로확대하지않는다. [현재소스·전후·한계](app-target-20261023.md#main144-validation-lifetime).

**MAIN143 Confirm 저장·시계 계약(F01/F22/F23/F24):**4caller의유일조립점이기존upsert_bars/server_now를필수주입하고worker가필터/원source·native저장/rollback후신호를보낸다. UI의같은결과직접저장은제거했다. 현재관련49+9+2+1=61PASS·원142같은2FAIL·동일순간KST/UTC/host±9h6subcase를확인했다. legacy naiveKST/metadata 순간·원hash/자료/거래안전은유지하며다른workflow·liveoverlay저장/선택daemon·전체source인계/queryAbort는별도미완료다. [현재전후/한계](app-target-20261023.md#main143-confirm-native).

**MAIN142 초기화 실패 계약(F01/F22/F23/F24):** constructor history가 시작한 창소유 native 작업은 constructor 자체가 실패해도 기존 finish_shutdown에서 실제 종료한다. 성공 반환 뒤 icon/PID/command timer 준비도 entry의 기존 finally로 보호하고 native→sync→own PID→outer lease 순서를 유지한다. 원141 동일2FAIL→현재 관련47+2+1=50PASS, 원45/poll/finally·원예외/고정row/시계/권한 불변이다. 선택client Python daemon·queued최종저장/전체source인계/원조회Abort는 미완료다. [전후·현재범위](app-target-20261023.md#main142-journal-setup).

**MAIN141 일지 종료 계약(F01/F22/F23/F24):** `JournalWindow.finish_shutdown()`이 창의 실제 QThread descendants와 계좌요청 parent·admission 차단·native join·기존 sync close를 소유한다. entry는 공개 완료를 기다린 뒤 PID, outer restore gate는 source lease를 정리한다. UI shutdown3000ms/review 저장·원 exec-error 객체·시계/데이터/주문 권한은 유지한다. 원140 동일4검사에서 native 저장 중 PID/lease가 먼저 풀리는 실제4FAIL을 보존했고 현재 관련48PASS로 실제종료 전 exclusive restore 차단을 확인했다. queued최종결과저장/setup-before-try/force-kill/전체source 인계와 원조회QtAbort는 별도 미완료다. [현재 소스·전후·한계](app-target-20261023.md#1161-main141-일지-창-소유-native-종료-통합--2026-10-27).

**조회 종료 관측(F01/F23/F24, 2026-10-28):** 같은 MAIN141의 기존 조회 검사1ID/child5를 원본문·기대값·시한 그대로 native 도구 하에 관측했다. 실제 프로세스/UUID HKCU/TEMP 정리는 확인했으나 Abort는 비재현이며 원139 원인UNKNOWN·정상기능 수락 추가0을 유지한다. 제품/저장/시계/권한 변경은 없고 반복비재현을 원인 해결로 삼지 않는다. [소스별 관측 근거](../../tmp/ar-v1-wave48-20261028/integration/query-observation-acceptance.json).

**MAIN140 현재 연결 검증:** 현재 wheel414의 조회 전용 새 설치와 처리기/도구40모듈 부재 설치에서 기존 ROLE3을 각각 실행해6환경쌍PASS다. 실제 설치 origin/hash·native 설정/cache·표시/ACK·다음 process 소비·재시작과 전용HKCU/TEMP 정리를 확인했다. 공급 fixture의 source와 실제 업무 자식의 설치 origin을 구분한다. 뉴스 결과는 같은7항 callback으로 metadata까지 전달하며 저장/동기화 소비는 해당 owner에 남긴다. 원139 조회 bootstrap QtAbort1은 UNKNOWN으로 남고 설치6쌍·관측 비재현은 이를 해결한 증거가 아니다. 실제 미완료작업 인계·source/T0·다중PC/운영은 미수락이다. [단일 정본·소스별 근거](app-target-20261023.md#1160-main140-뉴스출처저장operator설치-국소-검증--2026-10-27).

**MAIN134 국소 갱신:** query/disabled PC기동은validation 전용모듈의필수import 없이공통factory/worker를조립한다. source strictrestore는실제종결·lease해제뒤선별settings/원version을새query native원장에설치/재열기하며옛ledger/assets는source에남긴다. query전체13·도구/worker/child/failover43와restore25를포함한관련68 PASS, native정리확인. fullenabled desktop통합·실제source/T0/Runtime/account인계·원Abort/운영은별도미수락이다. [현재근거/계약](app-target-20261023.md#1157-main134-통합-검증--2026-10-27).

**MAIN130 entry 종료 연결 국소 검증:** 기존 AppController→EntrySnapshotWriter public close는 actual enriched COMMIT 후 ACK가 없으면 닫기를 막는다. 동일writer의 원payload 재시도/ACK·실제종료/pending0 후에만 ready이며 provider detach 뒤에도 재조회하지 않는다(관련9, investor/news 각1). 새제품 계층/작업/저장방식은 없고 settings-only seed는 별도미준비다. [현재근거·실제인계 잔여](app-target-20261023.md#0-현재-기준과-진행-현황).

**2026-10-27 구현·검증 국소 갱신:** 현재 MAIN128 PC configure_logging 기동계약을 유지하면서 새로그14 KST calendar dates/독립PID파일을118관련+실제2process1로 국소 검증했다. 기존main/news 호출은동일하며정상종료·회전close·busy보존은로그경계에한정한다. 업무clock/source교체·전체F24완료로확대하지않는다. [소스별 근거·남은 조건](app-target-20261023.md#time-audit-followup).

**2026-10-27 시간 감사 보충(수정·새 PASS 아님):** F01/F23의 동적 보정시계·교체/rollback은 유지한다. NAS wrapper의 로컬 시계 선택과 헤더 전 fallback을 분리하고 PC/NAS/독립뉴스의 clock 필요성·소유자를 검증한다. F24의 기존 종료 수락을 새 실패로 확대하지 않는다. [담당 AR·우선순위·완료 기준](app-target-20261023.md#time-audit-followup).

**AR08설치 검증(2026-10-27):** supplier의선택자료와설치nativewriter/설치consumer의책임을분리검증한다. 현재MAIN116의원형선별seed/첫receipt·실제UI/ACK/두process/선택코드38개부재를기존3ID×2구성으로확인했다. bridge고정시각검증/원실패와미완료수명·운영조건은[현재정본](app-target-20261023.md#1150-ar08-현재-wheel의-새-설치와-선별-이전-소비--2026-10-27)을따른다.

**MAIN116 연결 계약(2026-10-26):** per-Hub관측epoch/readyreceipt는기존lifecycle의명시observer,raw선별seed/typedreadback/첫receipt/rollback은기존Databaseinitializer가소유한다. sourceCLI/조회UI는권한을생성하거나편집을재설치하지않는다. native수집/저장/transport/codec와정상consumer의owner는유지한다. 현재고유656 중 656 PASS/0 FAIL·worker2/원실패·미실행/운영제한은[단일현황](app-target-20261023.md#1149-hub-관측-소유와-current-state-native-이전-통합--2026-10-26)을따른다.

**AR04 source 원본 계약(2026-10-26):** 기존 source writer는 한 readonly snapshot을 빌려 settings4/theme7 원문·출처/판본을 보존하고 명시 selection만 반환한다. 원 settingsv1 소비를 유지하며 source capture/참조 CLOSED를 target seed·적용/consumer/주문권한으로 승격하지 않는다. delete/fresh active/fallback/version은 같은native 저장owner의 한transaction으로 반영하고 UI가 coherent 선택을 한 번 게시한다. 현재core74/guard14 PASS와query1 FAIL·종료UNKNOWN 및 미통합seed/consumer 제한은 [단일 정본](app-target-20261023.md#1148-s2-delete-현재-통합과-조회-실패-분리--2026-10-26)을 따른다.

**AR09/07 국소 계약(2026-10-26):** native 수신/RAM/writer/재시도·종료는 기존 owner, optional input/flush/legacy 관측 상태는 명시 lifecycle recorder다. 후보·명세 게시2곳은 기존 native 합류 도구를 재사용하며 주문권한을 생성하지 않는다. 동일본문526·EOF정리후11 PASS/guard34·전체 도구부재/PG/이전·기존 UNKNOWN/운영은 구분한다. [현재 근거·잔여](app-target-20261023.md#1146-ar09-collector-관측-소유권과-ar07-게시-수명--2026-10-26).

**AR09 구독 관측 국소 계약(2026-10-26):** native RegistrationProgress/approved atoms·send/ACK·source approval/ready/종료는 Collector, recorder.complete는 명시 주입된 전용 도구 owner다. 현재77 PASS/구독 파일1개 실제 부재를 검증했으며 legacy/upstream/flush·전체 app/actualNAS5는 미완료다. [현재 근거·잔여](app-target-20261023.md#1145-ar09-실시간-구독-관측-상태의-소유권--2026-10-26).

**AR09 REST 국소 계약(2026-10-26):** native transport/ingest/cache/inflight/credential/drain은 기존 broker, tool ordinal/lineage와 기존 scope 관측은 명시 optional module이 소유한다. 관측자가 native 실행·재시도/저장 ACK를 대신하지 않는다. 실제 본류138·capacity caller11 복구·원 seed/실패/취소/종료를 확인했고 전체 producer/app/actualNAS5는 미완료다. [현재 근거·잔여](app-target-20261023.md#1144-ar09-rest-요청관측과-native-실행-소유권--2026-10-26).

**S2 현재 선택 국소 계약(2026-10-26):** Theme business SQL→같은 Settings nativeTx/ACK/close/cache→MainWindow accepted/queued owner와 모든 same-owner editor 게시다. 현재110 관련89 PASS이며 본문·시한·원 실패와 미검증 정책/운영은 보존한다. generic view는 현재 선택 저장과 구분하고 같은 raw filter는 호출 내1회 재사용한다. [현재 범위·근거·잔여](app-target-20261023.md#1143-s2-현재-프로필-선택의-native-소유권과-accepted-write-보호--2026-10-26).

**AR09 Hub:** 기존 accepted-native/Runtime/Tape 소유권을 유지하며 Hub 발행시각은 제품 owner와 기존 corrected/virtual provider가 맡는다. 실제 app 조립은 기존 lifecycle module을 명시 주입한다. 새 per-tick HTTP/poll/DB가 없고 wire·기존 종료 계약을 보존했다. [본류57·잔여](app-target-20261023.md#1142-ar09-hub-공개-관측과-발행시각-소유권--2026-10-26).

**AR09 공통 수명:** accepted-native wait2/원 cause·ordinary cancellation 의미를 유지한다. normal5의 owned helper import는 async_lifetime을 사용하고 Runtime/Tape의 admission/credit/lane/drain은 원 owner에 둔다. ContextVar는 scope submission callback만 보유하고 중첩/오류 뒤 token을 reset한다. 모든 ordinary thread가 native join한다고 확대하지 않는다. [55/본류45·범위 정정·잔여](app-target-20261023.md#1141-ar09-공통-수명-경계-통합--2026-10-26).

**S1 설치 국소 검증:** MAIN104 query12 및 동일 제품413 wheel의 새 core+query/전용 코드38개 부재 사본에서 기존 owner·실제 저장·두 process 재시작/종료를 검증했다. 역할 권한이나 DB/시계 owner를 검사 코드로 옮기지 않는다. namespace 수집/naive KST fixture 입력만 좁게 보완했으며 assertion/시한은 유지했다. [설치 범위·원실패·잔여](app-target-20261023.md#1139-조회-설치선택-코드-부재와-시계-fixture-검증--2026-10-26). 전체 AR/운영·S2 이후 배포물 수락은 별도다.

**S1 명시 query 역할:** 기존 AppPaths/lease/StrictRestore가 독립 root를 준비하고 기존 Database/Settings/Columns/stock/bar/theme이 단일 query-role DB를 소유한다. UI/worker·공개 보정 시계·종료를 재사용하며 API/sound/log/INI도 같은 root를 쓴다. FRESH와 SELECTED를 구분하고 raw 불완전값을 편집 기본값으로 자동 저장하지 않는다. 기본/full·추가 인자 없는 query는 유지한다. 현재 검증/잔여는 [정본 S1](app-target-20261023.md#1138-s1-조회-역할-native-소유권-통합--2026-10-26)을 따른다.

**AR v1 현재 계약.** accepted-native wait leaf는 이미 입장한 실제 task/thread를 취소와 분리해 합류하고 원 오류cause를 보존한다. 일반task/context·도구 전체 역전은 아직 미수락이며 NEWS v5는 미검증이다. [판본/검증·중지 현황](app-target-20261023.md#0-현재-기준과-진행-현황).

**AR v1 19차 검사자원 계약:** 실제Native QSettings를쓰는검사는controller소유HKCU UUID를Qt import전에각자식에연결하고,전체Job종료후정확삭제한다. INI marker/기본format만으로격리판정하지않으며HKLMfallback읽기는범위밖이다. 제품설정owner를검사helper로이전하지않는다. 현재Z476중475PASS/1querynativeAbortFAIL이며원인UNKNOWN/초기사용자영향UNKNOWN을보존한다. [현재정본](app-target-20261023.md#0-현재-기준과-진행-현황)·[현재근거](../../data/diagnostics/ar-v1-wave19-20261024/verification.json).

**현재 테스트 환경 제한(17차 후속).** QSettings의 조직/앱 생성자는 Native HKCU를 소비하므로 INI default/setPath marker만으로 격리를 인정하지 않는다. 446 기능 PASS와 사용자설정 격리 미수락을 분리하고 native test-hive proof 후 필요한 관련 검증을 수행한다. 원registry 영향은 미확정/임의복원0. [현재 정정](../../tmp/ar-v1-wave17-20261024/qsettings-isolation-followup.json).

**AR v1 17차 계약.** AppPaths getter는 path만 선택하고 개인자료를 복사하지 않는다. 실제 bootstrap이 기존 root 복원 owner에 역할별 준비를 연결한다. valid restore pointer가 우선하며 copy/publish 오류는 원예외로 기동을 막고 absent final 재시도를 보존한다. 새수명/관리계층0. [현재 국소 근거](../../data/diagnostics/ar-v1-wave17-20261024/verification.json). actualwheel/선별이전/운영은 별도 미검증.

**AR v1 16차 계약.** 선택config의단일정의와Session/Monitor의원상태·compute수명을구분한다. GUI NASsignal은명시QObject receiver/actual owned sender로받고기존native/drain/restorelease·시계를유지한다. 정상close/currentW515수락과C++내부원인/forceddestroy미검증은다르다. [현재국소검증](../../data/diagnostics/ar-v1-wave16-20261024/verification.json).

**AR v1 15차 계약.** app은기존NewsRunner를직접start/close하고source→acceptednative drain→AI/store순서를소유한다. 기존body/KST·계좌안전·restorelease유지. query는oldqueuedcallback/창close Abort가남아원시한단독통과만으로전체수명수락안함. [소스별국소검증/미해결](../../data/diagnostics/ar-v1-wave15-20261024/verification.json).

**AR v1 14차 계약.** 공통값은실제domain단일정의,root는원full evaluator/owner만조립한다. producer없는기동/GET/별도process재시작에서native설정/UNKNOWN/receipt와기존시계를유지한다. [현재국소근거](../../data/diagnostics/ar-v1-wave14-20261024/verification.json).

**AR v1 13차 계약.** query-only는명시선택이고기존full/공통restorelease를유지한다. DataSourceConfig가query파일선택을한곳에서소유하며새client조립/명시test에서만cap=query를확인한다. 저장/적용·오류·native drain과query-only own-close를구분한다. 설치/backup/최소이전은잔여다. [현재국소검증](../../data/diagnostics/ar-v1-wave13-20261024/verification.json).

**AR v1 12차 계약.** 선택 query bearer는 기존 server owner가 검증하고 read allowlist/WS scope만 전달한다. 기본off/admin동작 보존; PCquery 전용설정·headless전체/실제설치는 아직 미통합/미검증이다. [현재국소근거](../../data/diagnostics/ar-v1-wave12-20261024/verification.json).

**AR v1 11차 계약.** 선택producer는기존실제enabled factory/교체/Runner생성지점에서만구현을요구한다. role패키지extra는실행role/권한이아니고clean/frozen설치미수락이다. [현재국소근거](../../data/diagnostics/ar-v1-wave11-20261024/verification.json).

**AR v1 9차 계약.** server_now를 app→selection session→Runtime으로 같은provider로 주입한다. 뉴스 process/window/settings의 실제accepted작업이 끝난 뒤 lease를 반납하고 cleanup 예외도 나머지drain을 생략하지 않는다. [현재소스736근거](../../data/diagnostics/ar-v1-wave9-20261024/verification.json). 최신단계/잔여는정본0절이다.

**AR v1 8차 계약.** PC 선택 RAM/source/coverage/history/recovery/entry는 PcMarketSession, 순위/cohort는 coordinator, native ACK/drain은 AppController가 소유한다. 장후 task/stop/join은 Top20AfterCloseWorkflow다. 네 역할 조립·권한/외부 수명은11.17의 미완료 조건이다. [소스별 검증](../../data/diagnostics/ar-v1-wave8-20261024/verification.json); 현재 판정은 정본0절만 참조한다.

**AR v1 7차 당시 계약/근거.** 실제news child accepted-thread 입장/종료·lease반납과HTTP ingress/send/parent reset을통합했다. sourceJ41+변경없는I812 소스별범위, 원Ifixture오류와실제종료/정리보존. 최초window획득/native QThread drain은격리후속이다. [현재근거](../../data/diagnostics/ar-v1-wave7-20261024/verification.json).

**AR v1 6차 현재 국소 계약(F23/F24).** 실제 Supervisor 조립이 같은 Runner prepare/clock native 수명을 소유한다. clock load/validation 뒤 기존 decoder를 호출하고 native 종료 뒤 반복취소/close가 끝난다. 현재H497/9모듈과 native 비용·최초실패를 보존한다. 사용자 요청 일시정지이며 격리 child/HTTP/Broker 결과는 아직 main통합 전이다.

**AR v1 5차 현재 계약.** bootstrap/news-process의 실제 content client는 current-theme 공개읽기를 사용한다. 실제 인증ASGI 취소는 이미 시작된 native읽기/연결close까지 합류한다. 저수준 borrowed/retained는 기존 의미, J2 acquisition만 명시owner scope를 사용한다. [현재 소스/최초실패/잔여](app-target-20261023.md#1113-5차-국소-검증--current-테마-조회획득-연결-수명).

**AR02 필수 기술범위 검증 완료.** saved/applied·실제 owner/fence·공개allocation/오류·교체/close 11기준, current34본문과 실제source별ID를 검증했다. 전역누적gate/새설치·자료이전/운영은별도이며 불필요한 새AR02구현을 만들지 않는다. [완료근거](../../tmp/ar-v1-wave4-20261023/ar02-technical-verification.json).

**AR v1 4차 당시 계약(최신5차와 구분).** PC 관측 session 조립·동적 corrected clock과 기존 writer/child 수명을 유지한다. 공통 문맥은 단일 domain ContextVar pair이며 기존 postgres 이름도 동일 객체다. driver commit 실패 후 close 순서는 바꾸지 않았다. [현재 근거/미확인](app-target-20261023.md#1111-4차-구현--실제-상태workflow-소유권과-불변-수락).

**AR v1 3차 구현 계약(현재 통합 검증 중).** Session은 자체 mutation fence를 만들며 일반 설정 lock 조립에 의존하지 않는다. app.create_app는 initialize부터 return까지 실제 vault→store 실패 정리를 소유한다. Clock bind는 기존 provider/동적 Runtime 경로이고 성공 수명은 기존 lifespan이다. cleanup 실패/운영 권한·중간 강제종료는 별도 수락이다. [근거/현재 잔여](app-target-20261023.md#1110-3차-진행--승인된-ar의-연속-실행).

**AR v1 2차 계약.** Session은 기존 fence/revision/Runtime identity·closing 확인, Runtime은 allocation 공개 prepare/decoder/native 작업 합류·원래 offer/slot을 소유한다. PC API source 교체/복원은 EntrySnapshotWriter.replace_sources 공개 연산으로 연결하며 controller는 producer/signals·view와 자기 자원 수명이다. 기본 process stop은 kill 뒤 실제 wait(None)/명시 wait가 끝나야 handle 해제, 실패는 기존 같은 handle 보존이다. live clear/start+operation 자료·선택 fence 분리는 잔여다. [현재 국소 검증·남은 조건](app-target-20261023.md#119-2차-구현--allocation-공개-계약오류writer-source실제-process-exit).

**AR v1 1차 연결·소유 계약(2026-10-23).** NAS root는 RamShadowSelectionSession을 조립하고 app.state는 호환 투영으로만 게시한다. 선택 saved/applied/revision/error·계좌 관측 선택·native mutation/close task는 session이 소유하고 기존 operational_state.lock은 재진입/교체/종료 fence로 유지한다. mock bundle 철회는 공개 account context 무효화, 새 bundle은 선택 계좌 필터로 연결한다. PC CentralRealtimeWorker는 AppController의 실제 requestInterruption/wait 경로에서 child 실제 종료까지 refs/stop 결과를 보유하고 현재 child FILL은 queued drain으로 전달한다. Qt 후속 receiver는 bound Slot과 worker별 문맥이다. [현재 국소 검증/잔여](app-target-20261023.md#118-1차-구현--ci-원인-보완ar02-선택-상태); 전체 기동/종료·운영 수락이나 typed source 전환 완료가 아니다.

**이전 종료 보완 계약(PC6 보강·NAS 범위 정정 전, 당시 로컬).** 15%전용 ENV2/UI·API3설정과 session 종료 relay를 제거했다. old 설정3키는 RAM에서만 제외하며 저장원문·공통 clock/KST/worker/close 책임은 유지한다. 새 수명 계층이 없다. [근거](../../data/diagnostics/r3-050-final-20261023/verification.json).

**이전 집중 검증·이관 계약(당시 2026-10-23, 로컬).** 집중 개선 중단/기능 이관은 전체기동/설치/운영 수락이 아니다. 032·053 및085 credential/order 이행 조건은 기존 기능 원장에 남기고 native 반환 전 owned dependency close/lease 해제를 금지하는 기존 계약은 유지한다. [검증](../../data/diagnostics/r3-closeout-20261023/verification.json).

**이전 스물두번째 추가 계약(2026-10-23, 미커밋 당시).** 050 STOP의 fresh native 조회가 끝나기 전 native/store 소유 수명을 해제하지 않는다. RAM runner/bundle은 accepted read 뒤 루프에서 현재값을 소비한다. local server build3marker만 정합화했고 source-mode dependency pin/실제 NAS release는 불변이다. 근거 [통합 검증](../../data/diagnostics/r3-parallel-20261023/wave22/verification.json).

**이전 스물한번째 추가 계약(2026-10-23, 미커밋 당시).** 017 등록 조립·relay·중복 요청 비용은 로컬 종결한다. dict partial-apply/queued old worker/rollback 상태, 서버 binding 게시·awaited withdrawal 및 RAM lock/revision·old-close/new-start, 연구 provenance/claim/lease/게시의 내부 책임은 유지한다. 실제 source/factory/설정 복원·교체/close 계약과 relay 깊이·파일 감소 지표를 대조했다.050 mode-switch3개 native settings read 모두 기존 drain을 사용한다. 마지막2 await의 outer account lock·inner lock/pending/context 소유와 accepted native return 후 종료를 검증했다. 새 rollback/게시/owner 정책0. 생성자·listener·status는 미완료. 근거 [통합 검증](../../data/diagnostics/r3-parallel-20261023/wave21/verification.json).

**이전 스무번째 추가 계약(2026-10-23, 미커밋 당시).** 017 한 build의 불변 profile snapshot을 factory/realtime에 재사용하고 기존 client token/clock·rollback owner를 유지한다.033 older explicit 요청에서는 applied fence 후 정확 원cutoff purge,050 첫mode-switch read는 기존drain으로 native 반환까지 outer owner 수명을 유지한다.후속 await/legacy 미래clock/운영 수락은 별도다. 근거 [통합 검증](../../data/diagnostics/r3-parallel-20261023/wave20/verification.json).

**이전 열아홉번째 계약(2026-10-23, 미커밋 당시).** R3-032: bootstrap의 기존 중앙 설정 SettingsRequestWorker에 exact objectName을 부여하고, app.exec direct 반환/예외 finally가 기존 retention worker와 같은 parent의 두 exact name을 실제 wait한다. 새 worker/registry/timeout/정책은 없고 기존 보정 시계 변경도 보존한다. 부모의 기존2초 terminate/kill, dirty-review 거절/창 유지, 선택 외 constructor/setup·앞선 cleanup 실패와 실제 OS child/운영 기동 전체는 별도다. 해당 정책이나 일지 수명은 이번에 바꾸지 않았다. R3-033: repo-only AppController가 성공한 applied cutoff를 RAM 한 값으로 보유하고, 명시 retention 요청 없는 후속 stock/index/gap 및 MainWindow 직접 history/empty 저장의 원 success/failure 뒤 같은 explicit-minute fence를 적용한다. 기존 native purge/failure owner를 재사용하며 원 ACK·원 실패·최신 pending·타이머/close gate는 유지한다. older explicit cutoff와 같은 flush 입력, 미래 cutoff/clock 역행, legacy market:() 무날짜 확장, 영구 queued 신호 미소비/process kill은 잔여다. applied 값은 기존 Controller 수명 RAM이며 재시작 영속화가 아니다. 요청 cutoff를 native max로 강화하거나 원본 payload/ACK를 필터링하는 새 정책은 추가하지 않았다. 표준 bootstrap의 FIFO writer는 변경0, 기존023/075/076 완료를 재개하지 않는다. root관련283/17모듈PASS, 원본/진단/신규CAS측정교정과실제운영 수락을분리한다.


**이전 열여덟번째 R3-033 계약(2026-10-23, 미커밋).** bootstrap 30초 retention worker가 실행 시점의 기존 AppController.ranking_now→현재 RankingService/client.server_now를 한 번 읽고 as_kst로 날짜를 정규화하여 30일 cutoff를 만든다. 이미 적용된 API 교체를 따르며 새 clock owner·TR·HTTP·poll·relay는 없다. 기동 retention native 연결2/SELECT2/DELETE6/BEGIN2/COMMIT2 전후 동일, 보존30일/일봉250·실패 처리·worker/thread·timer·종료 합류/lease 계약을 유지한다. 새 clock 읽기1회/fixture HTTP0이며 원본 비교용 별도 diagnostic SELECT1/연결1은 생산 비용에서 제외한다. 공개 no-writer fallback의 지속 applied-cutoff/history·empty fence, legacy market:() 무날짜/미래 cutoff·clock 역행, 영구 queued 신호 미소비/process kill은 잔여다. 이 변경은 현재 clock 샘플 경계이며 API 적용/rollback의 동시 atomic 보장·실제 HTTP Date 동기화·NAS/운영 수락을 증명하지 않는다. R3-033 전체 진행, 기존023 완료를 재개하지 않는다. 관련128/7모듈·guard/소비/실행 gate 로컬 PASS; 실제 시계 동기화/배포 수락은 별도다.


**이전 열일곱번째 계약(2026-10-23, `066d2de1f71e52f1b3507a42e6a80153a092cd53`).** R3-032: CentralJournalSyncRunner가 기존 단일 실행의 실제 Thread 참조·closed admission·Lock을 소유하고 state lock 밖에서 같은 Thread를 합류한다. 자식 _run_journal_process의 app.exec finally는 matching-PID 정리와 outer lease 반환 전에 runner.close를 호출한다. 정상 dirty review 저장 실패 때 창을 유지하는 Window.shutdown에는 close를 추가하지 않았다. 부모의 기존 2초 terminate/kill이 child join을 끊을 수 있는 정책·선택 밖 constructor/setup/cleanup 실패·강제 종료는 별도다. 기존 Window.shutdown의 dirty review 거절/창 유지, 500ms/60초 schedule, 071 merge/전송·시계는 변경0이다. child join을 전체 부모 종료/실제 OS 자식·운영 수락으로 확대하지 않는다. R3-033: 표준 bootstrap은 항상 writer를 조립하며 repo-only 공개 DI 호환 경로에서 AppController.flush_minute_cache가 기존 on_minute_cache_failed를 재사용하고 _complete_shutdown의 failure+pending 조건은 writer 없이도 기존 close gate를 지킨다. 새 writer·cutoff·retry 정책은 없다. 별도 bootstrap._run_desktop은 정상 close의 기존 Qt child 추적을 유지하고 direct exec 반환/예외 finally에서 같은 parent·exact objectName retention worker를 실제 wait한다(제품 +7줄). repo-only open 실패에도 기존 1초 single-shot 재시도를 예약하도록 실패 owner를 재사용한다. fallback은 표준 제품 운영 조립의 관측 회귀가 아니라 공개 DI 호환 범위다. repository._path로 writer를 새로 추정하지 않는다. fallback의 지속 applied-cutoff/history·empty 전체 fence, legacy market:()의 자동 현재일·미래 cutoff/clock 역행, 영구 queued 신호 미소비/process kill은 잔여다. 기존 bootstrap retention kst_wall 시계를 키움 보정 시계에 연결하지 않은 잔여는 R033으로 남기고 R023 등록 완료를 재개하지 않는다. 전체033·운영 재발/lock latency는 완료로 확대하지 않는다. root642/35PASS, WT·원본 진단과 운영 수락은 별도다.


**이전 열여섯번째 계약(2026-10-21, `7d0b8e108893c649e1e56e127eb06f29151779a5`).** R3-032: bootstrap._run_desktop의 기존 try를 theme_store.all_by_name() 바로 앞으로 옮기고 central_settings_timer=None 초기화 위치만 앞당겼다. 정상 실행 본문을 풀어 비교한 AST와 기존 finally/handler·종료 순서가 같고 새 cleanup helper/ExitStack/timeout은 없다. 실제 ThemeRepository all_by_name SQLite authorizer AUTH 오류와 content news BEGIN 대기로 원본 1FAIL을 보존했다. 원본은 content가 살아 있는 동안 server.stop 미도달·lease 해제·독점 복구 진입을 관측했다(false,true,true). 보완한 현재4모듈96ID PASS(120.458 worker초), 이전95+신규1/기존 검사 method 변경0. 대기 중 세 관측 false, setup.failed→content.done→server.stop→lease.released, 원 AUTH 오류 identity/code·거부 read1·window 생성0을 확인했다. 해당 read의 connection은 finally로 닫힌 뒤 content를 기다려 native lock 대기 cycle을 추가하지 않는다. 비교 조건 HTTP11/연결5/SQL27 동일이며 I/O 절감 주장은 없다. 문법상 보호 범위는 MainWindow 생성/show·타이머 설정을 포함한 약89줄로 넓어지지만 실행 수락은 window 생성 전 실제 read 실패1과 기존95 검사에 한정한다. 모든 새 lexical 실패 분기를 수락하지 않는다. 앞선 setup/Thread.start 실패·cleanup 자체 실패의 오류 우선순위·journal 수명·강제 종료는 잔여다. app.exec/외부HTTP/server.stop은 통제 경계이며 전체 프로세스/운영 기동 수락은 아니다. root279/13PASS와WT비용/원본대조는별도이며새owner/계층/schema0,전체R3진행유지·075/076기존완료보존.


**이전 열다섯번째 계약(2026-10-21, `7fe20f325d462242de3390c50d678c585e6f0161`).** R3-032: bootstrap._run_desktop이 기존 startup central-content daemon Thread 참조를 보관하고 app.exec finally에서 theme.close 뒤/local_server.stop 앞에 실제 join한다. 기동시점·name/daemon·sync 본문은 동일하며 새worker/timeout/주기는 없다. app.exec try 이전 setup/start 실패·앞선 theme.close 예외·journal sync 수명/강제종료는 별도다. MainWindow/AppController/SQLite/lease는 실제이며 app.exec·외부HTTP·server.stop은 통제 경계다. 전체프로세스/운영 종료 수락은 아니다. root369/18모듈과WT비용/원본대조는구분한다.075등록범위로컬완료,나머지R3전체/PG5/운영미검증은백로그를따른다.


**이전 열네번째 계약(2026-10-21, `fbbb96a0cdbab65d476dd352a90745ba30a1eae4`).** R3-032: CentralThemeSyncDispatcher의 기존 상태 잠금에 active-run 수명만 더했다. close는 새 실행을 막고 revision 조회 전부터 HTTP·CAS 저장·pending marker 정리까지 들어온 본문이 끝나기를 기다린다. root448/25모듈은 통과했고 WT 비용·원본 대조는 별도다. 등록범위076만 완료, 나머지 잔여와 실제PG5/운영 미검증은 각 항목을 따른다.

**이전 열세번째 계약(2026-10-21, `b16c386d107b08218b2f97dc4d305f3d55ebc632`).** R3-017: daily 완료의 마지막 relay를 기존 AppController.start_fundamentals_phase로 직접 연결하고 coordinator 전용 인자·필드·메서드·sole injection을 제거했다. 기존 finalize/program backfill/RAM·settings cleanup 뒤 호출, normal/forced False·closing/reload/priority 및 NXT/catalog 순서는 유지한다. AppController의 조립 dict·서버/연구 조립 책임은 잔여다. R3-032: bootstrap retention의 기존 30초 callback에 closing guard를 두고 raw daemon을 기존 SettingsRequestWorker(parent=AppController)에 편입했다. purge와 daily250 본문·시계·실패 처리는 동일하다. 직접 quit/exec 예외/start 실패와 content/theme/journal 수명은 잔여다. 좁은 진단 통과를 최초95 전체통과로 바꾸지 않는다. R3-053: 제품·등록검사0. 현재 helper 소스를 WT의 별도 onefile/windowed EXE로 빌드하고 실제 Win32 parent wait·CLI→archive 적용→temp target 재시작을 진단했다. 기존 resources EXE/설치/runas/UAC는 미접촉이다. CLI 진단 exit109 의미와 실제 UAC/설치·강제종료 복구는 미수락. 성공 TEMP 삭제/실패 TEMP 보존과 소스·바이너리 대응을 별도 manifest로 남긴다. root1301 PC관련검증과실제PG5/053진단실패는구분한다.

**2026-10-21 `7e4fb607ceb09479f6b9c9ed2c03cbf6cdc42bbb` 국소 계약 갱신.** R3-013: 소비 없는 worker getter 11개와 전용 import 이름5개를 제거했다. 유지31개 property/동적 owner/Qt parent·종료는 보존했다. 관련17ID WT 통과와 AST 비교를 보존한다. 새 계층·실제 호출 깊이·DB 비용 변화0이며 속도 개선을 주장하지 않는다. R3-017: minute history 완료의 stateless coordinator relay만 기존 AppController owner로 통합했다. normal False→fundamentals, forced False→종료와 실패·close/reload 계약을 유지한다. 실제 QThread normal frame6→5/forced5→4, 경로 파일4→3. 관련58+단계진단1+비용1 WT 통과. 최초 비동기 close fixture 오류와 기존 provider2s 실패는 보존했고 후자는 재현되지 않아 원인 미확정이다. daily relay/조립 dict·서버·연구 조립 책임은 잔여다. R3-032: bootstrap 설정 sync만 기존 SettingsRequestWorker(parent=AppController)와 producer drain에 편입하고 기존 종료 timer 목록에 등록했다. 원본003은 HTTP 중 ready→server.stop→outer restore lease 해제/실제 exclusive 획득을 재현했다. 원본001/002는 초기 DB 준비에 가린 timer만의 증거로 구분했다. 수정후65 WT 통과, 중복/실패·재시도/늦은 callback 차단을 검증했다. 직접 quit/exec 예외·start 실패 및 content/retention/theme/journal plain thread는 잔여다. R3-053: update helper apply_archives에 전 ZIP 사전검증·union 원본1회 보존·동일폴더 atomic replace·ordinary copy/delete 실패 전체 old rollback을 적용했다. 복원 오류에도 나머지 복원을 계속하고 원본/설명자료를 보존한다. postcommit ZIP정리 실패는 new전체·남은ZIP/자료·오류·재시작0이다. 원본 copy/delete 반례와 WT18+비용1을 보존한다. backup0→2/11bytes·atomicreplace0→4, 설치copy4/12bytes유지, staging표본peak106→208bytes(전체diskpeak아님). 강제종료 복구·실제 잠긴Windows/UAC/helper EXE 재빌드/재기동은 잔여다. 현재root1060/62모듈·원본비용/WT검사는구분한다.

**2026-10-20 `8cc2d19` 추가계약.** 두 저장소의 비참조 datetime/UTC import 각1줄만 제거했다. 본문/SQL/시그니처/type hints·기존 kst_now/as_kst·native값/버전/rollback/동기화는 불변이다. 등록 소비자의 incidental module.datetime/UTC 사용은 발견하지 못했으며 외부 미등록 소비자는 미검증이다. 실행비용 개선을 주장하지 않는다. SettingsDialog 생성자의 unconditional return을 보존하고 그 뒤 도달 불가 구형31줄만 제거했다. 실제 grouped layout/연결버튼/save worker·geometry 및 다른 메서드/도달 AST·resolved 명령1790은 같다. bytecode index 재배치는 있으며 co_names173→153, 제품 실행비용 절감은0이다. 새검사/계층/설정/시계/DB 경로를 추가하지 않았다.

**2026-10-20 `0a54c35` 추가계약.** R3-030 검증 완료: 기존 EntrySnapshotWriter에 짧은 cache lock 하나로 loader/cache 동시 포착·setter의 dict 교체·현재dict/loader 확인후게시를 묶었다. provider/native I/O·signal·job/backfill은 lock 밖이다. 동일callable/None/A→B→A도 옛 in-flight 결과가 새cache를 채우지 못하고 원래job의 provenance는 보존한다. 실제 AppController의 기존 public setter 적용/rollback을 재사용(제품0). A→B old1/new0→old1/new1은 필요한 새조회 복구이며 이후cache hit 추가조회0, TTL60초/code-only/얕은copy·오류uncached 유지. Controller의 별도 private cache backup/update 전체를 새lock으로 원자화하거나 cross-date/global singleflight를 해결한 것은 아니다. R3-066 검증 완료: 기존 variadic drain을 모든context에 한번적용해 producer를멈추고 monitor/writer/read를실제합류한뒤에만 dependency를닫는다. closing예외조건을제거해 미저장pending/실패를정상종료로보고하지않는다. 이미끝난failed close에대한새명시close만 완료된noncancelled eventwriter로원본pending/queue를drain한다. closing/admission/binding/settings/order gate는닫힌채유지,자동retry/REST재조회/새queue없음. 실제native precommit/ACKloss 두실패+두event복구 save4/행2/dependency close0→1,추가REST0. waiter취소/동시close는같은살아있는owner를합류한다. native event readback/diagnostic archive까지검증했고 genericcontent route는이collection을노출하지않아실제일지/API end-to-end는확인하지못했다. processkill RAM유실·직접ownertask취소·socket자동복구는해결범위밖이다.

R3-070 완료: 제외종목찾기의 SimilarStockDialog 호출에 parent=self를명시해 QWidget이themes로전달되던TypeError를복구했다. 실제 MainWindow→Settings→modal검색/선택·중복/취소/없음→기존worker/native저장→테마총140억/제외40억·정렬40→재오픈을검증했다. 올바른themes 명시import caller·원래dialog시그니처는유지했다. F22는일반설정저장소간접공유만확인했고원격sync를새로수락하지않는다.

**현재 책임.** `bootstrap`은 실행 모드, client와 worker factory를 조립한다. MainWindow는 UI 선택과 신호 수신/표시를, AppController는 주입된 action·worker controller·pending buffer와 API 교체/종료 순서를 소유한다. 서버는 `app.create_app`/lifespan이 store·broker·collector·각 runtime owner를 조립하고 닫는다. 작은 worker controller의 중복 실행 방지·신호·결과 신선도·수명 책임은 유지한다.

**상태·실패·공식 경계.** PC는 `configure_runtime/configure_ranking/configure_shutdown`, `request_close` 등 기존 controller 경계를 쓴다. producer 정지 → 도착 중인 결과 처리 → pending flush → 실제 writer 종료와 queued ACK 확인 순서를 보존한다. 실패 pending이 남은 종료와 정상 종료를 구분하며 API 교체도 구 producer의 결과/loader 수명을 함께 처리한다. 서버 설정 저장 revision과 실제 적용 revision은 구분하고, 요청 시작에 선택한 runtime/gateway를 응답 중 임의 교체하지 않는다. transaction은 settings/native store에 남긴다.

**남은 수명 범위.** 030/031 entry writer와066 real close의 등록경계는 로컬검증 완료했다. 032의 별도thread/042 owner-Runner교체와각완료항목의운영·강제중단잔여는개별계약을따르며전체worker종료수락으로확대하지않는다.

**002·023 후속 계약.** 가격 실패도 같은 Controller에서 원 batch context와 최신 dispatch 순서를 관리한다. 실패 retry가 남으면 writer 유무와 무관하게 ready를 반환하지 않고 기존 명시적 close 재시도를 사용한다. 별도 ACK 신호/저장 owner를 추가하지 않는다. 다른 worker 종료 수락과 이 좁은 검증을 구분한다.

**관련 검사.** [AppController](../../tests/unit/test_app_controller.py), [모의 runtime 교체 장벽](../../tests/unit/test_mock_runtime_barrier.py), [strict restore](../../tests/unit/test_strict_restore.py). 변경한 worker의 late signal·실패·교체·재시작도 해당 검사에 연결한다.

<a id="ranking"></a>
## 순위·기준 자료 — F02/F03/F10

**새full 소비의 후속253(F02/F03/F10·F22/F24).** 새native target는선택catalog/alias를보존하지만full자동KRX 수집의기존legacy alias seed는빈name-history에서선택별칭을삭제할수있다. 현재query 자동수집우회206과기존stock 관측/rename계약은유지한다. 새full실제기동전이정리책임을분리하며native118의readonly alias getter PASS를full catalog수명정상으로승격하지않는다. [현재full소비자근거](../../tmp/ar05-full-native253-20261102/FULL_LAYOUT_REVIEW253.json)·[정본현황](app-target-20261023.md#0-현재-기준과-진행-현황).

**C07 A1 현재 연결(F02/F03/F07/F09/F15·공통F23/F24/F25, 로컬 기술검증 완료·실제 전환 미수락):** C07의 원래 세 기술 목표를 로컬 완료로 수락했다. 기존 native owner 안에서 정상 history ON의 payload·의미 metadata를 불변 revision 한 곳에 저장하고, current head는 참조와 최신 확인시각을 소유한다. history OFF·관측 없음·선정된 구형 원문은 단독 inline을 유지하며 가짜 history·시각을 만들지 않는다. 실제 순간 기준 slot/index와 writer→현재/이력 reader→API, 늦은 정정·중복·ACK 손실·취소/종료·복원 계약을 함께 전환했다. source180의 실제 v23 SQLite→literal v1 archive→새 PG24 직접 검사1은 PASS(16.974초)이며 원값/Decimal·UTC/KST/UNKNOWN projection·NULL ref·첫 receipt·무DML 재수락·오염 거절·원본 불변과 실제 정리를 확인했다. source179의 수정 관련 PG5/5·실제 제한 role의 sealed 복원8/8과 source178 unit122/122는 소스별 불변 계약 근거로 재사용하며 새180 실행으로 세지 않는다. 원 PG24 21PASS/3FAIL·원177 오류/실패TEMP는 보존한다. PG 실측 SQL [8,7,8,10]의 필요한 잠금 비용을 유지하며 속도 향상을 주장하지 않는다. 새 계층·worker·캐시·라이브러리는 없다. 구조1303site/위반0·증분180/6884/원미결49 보존은 PASS다. guard는 제한 변경 없이 검증 근거만 추가했다. 실제 원본/계좌/T0·선별 이전/인계·설치 소비는 기존 AR05/C18·AR04-C08에서 미완료로 유지한다. C07 후속 목록에 중복 기재됐던 실제 전환 조건을 완료로 간주한 것이 아니다. 전체 AR 로컬1/9·Q0/14, C16 UNKNOWN·C24 원Windows3초ERROR·NAS7 환경계약불일치HOLD·누적gate/CI/운영 미수락은 그대로다. [요건별 국소 수락](../../tmp/ar-v1-wave58-20261101/c07-v1-pg24-180/local-criterion-acceptance180.json)·[직접 PG1/정리](../../tmp/ar-v1-wave58-20261101/c07-v1-pg24-180/acceptance180-v1pg1.json)·[소스별 이전 검증](../../tmp/ar-v1-wave58-20261101/c07-current-head179-pg-fixes/acceptance179-pg5-sealed8.json).

**MAIN147 F10 지수 보완:** 기존 네 fetch의 값/시각/source를 같은 monitor minute/daily/metadata/cache transaction에 저장한다. 저장소 본문/reader는 유지하며 기존 server_now를 주입해 추가 TR 없이 같은 clock을 쓴다. native2와 서비스/reader 보강을 포함한77 PASS, NAS 수집/전체source 인계는 이 수락에 포함하지 않는다. [지수 경로](app-target-20261023.md#main147-index-native-result).

**2026-10-27 시간 감사 보충(수정·새 PASS 아님):** F02/F03의 현재 시장일·순위 가용성은 공통 현재시각 계약을 유지한다. active NAS Ingestor/TOP20의 OS KST와 로컬 후속 주입을 분리한다. F10은 확인일·cache metadata 소비 범위를 확인하고 가격 transaction/기존 성능 개선을 다시 구현하지 않는다. [담당 AR·우선순위·완료 기준](app-target-20261023.md#time-audit-followup).

**AR09 구독 관측 국소 계약(2026-10-26):** native RegistrationProgress/approved atoms·send/ACK·source approval/ready/종료는 Collector, recorder.complete는 명시 주입된 전용 도구 owner다. 현재77 PASS/구독 파일1개 실제 부재를 검증했으며 legacy/upstream/flush·전체 app/actualNAS5는 미완료다. [현재 근거·잔여](app-target-20261023.md#1145-ar09-실시간-구독-관측-상태의-소유권--2026-10-26).

**AR09 REST 국소 계약(2026-10-26):** native transport/ingest/cache/inflight/credential/drain은 기존 broker, tool ordinal/lineage와 기존 scope 관측은 명시 optional module이 소유한다. 관측자가 native 실행·재시도/저장 ACK를 대신하지 않는다. 실제 본류138·capacity caller11 복구·원 seed/실패/취소/종료를 확인했고 전체 producer/app/actualNAS5는 미완료다. [현재 근거·잔여](app-target-20261023.md#1144-ar09-rest-요청관측과-native-실행-소유권--2026-10-26).

**S2 현재 선택 국소 계약(2026-10-26):** Theme business SQL→같은 Settings nativeTx/ACK/close/cache→MainWindow accepted/queued owner와 모든 same-owner editor 게시다. 현재110 관련89 PASS이며 본문·시한·원 실패와 미검증 정책/운영은 보존한다. generic view는 현재 선택 저장과 구분하고 같은 raw filter는 호출 내1회 재사용한다. [현재 범위·근거·잔여](app-target-20261023.md#1143-s2-현재-프로필-선택의-native-소유권과-accepted-write-보호--2026-10-26).

S1은 기존 rank 입력/설정 consumer를 같은 role root에 연결한다. 저장한 query 기준이 재시작 API에 전달되어야 하며 자료 부재를 다른 기준으로 우회하지 않는다. NAS 수집·원시0B/순위 기준은 바꾸지 않는다.

**AR v1 현재 계약.** 순위 artifact의 반입은 명시 one-off operator와 removable native cursor 명령이 소유한다. 정상수집/runtime은 전용 명령을 import하지 않고 원writer/조회 계약을 유지한다. 새offline 대상만 허용하며 head/T0/운영전환을 선택하지 않는다. [판본/검증·중지 현황](app-target-20261023.md#0-현재-기준과-진행-현황).

**AR v1 18차 P0 계약.** P0 operator는 원rank3관계를 faithful readonly snapshot으로내보내며 수집/RAM/현재writer·latest판정은변경하지않는다. targetimport는미연결이고도구를제거해도runtime은import하지않는다. [현재Y132/9·실CLI](../../data/diagnostics/ar-v1-wave18-20261024/verification.json).

**AR v1 16차 계약.** query표시/순위는기존공통공급을재사용하고lateNAS응답도같은worker/GUI소유수명안에서정리한다. 새수집/native저장·단위·순위의미변경0. [현재국소검증](../../data/diagnostics/ar-v1-wave16-20261024/verification.json).

**AR v1 15차 계약.** 테마편집catalog와acceptedgeneration을동일source로보존하고기존rank/0B공급owner는유지한다. 임의timestampwinner/legacy자동귀속없음. [소스별국소검증/미해결](../../data/diagnostics/ar-v1-wave15-20261024/verification.json).

**AR v1 13차 계약.** query runtime만기존remote공급에투영하고full설정파일/flags는보존한다. 직접키·localserver·fallback·parallel수집을기동하지않고공통TR/read수신을재사용한다. [현재국소검증](../../data/diagnostics/ar-v1-wave13-20261024/verification.json).

**AR v1 12차 계약.** query는 명시11TR/path·공통시장 collection에만 접근한다. 기존archive-first/Broker/RAM 공급을 재사용하며 별도collector/매틱DB/polling을 추가하지 않는다. [현재국소근거](../../data/diagnostics/ar-v1-wave12-20261024/verification.json).

**AR v1 8차 계약.** 장후10상태/작업·reconcile은 단일 workflow owner, DailyReference/live 수집/ACK·outbox는 기존 owner를 재사용한다. 실제PG16·SQLite 결과/비용 동등 검증. Collector private 관측/시계 공개화는 별도 격리 후속이다. [소스별 검증](../../data/diagnostics/ar-v1-wave8-20261024/verification.json); 현재 판정은 정본0절만 참조한다.

**AR v1 7차 간접관측.** 기존REST query 의미/결과·저장/수명은그대로이며HTTP요청문맥과Broker첫origin만같은owner로전달한다. 실제shared waiter 취소/캐시/저장·계정조회오류는관련회귀로확인. source/순위판정/구독을바꾸거나운영수락하지않았다.

**AR v1 4차 당시 계약(최신5차와 구분).** DailyReferenceState가 readiness/maps/history refcount·lock을 단일 소유하고 실제 ingest callback/collector/seed가 공개 API를 사용한다. 원래 TOP20 sampling/native driver/outbox·계산 본문은 유지한다. [현재 근거/미확인](app-target-20261023.md#1111-4차-구현--실제-상태workflow-소유권과-불변-수락).

**AR v1 3차 구현 계약(현재 통합 검증 중).** StockRepository/alias·MarketCacheWriter·DailyHighService의 수정 업무시각은 기존 corrected now_provider다. bootstrap와 MainWindow의 기존 dynamic clock bind에 stock_lookup을 포함한다. epoch/aware KST와 legacy SQLite naive UTC 해석·확인일/관측시각 의미를 보존하고 새 TR/poller를 만들지 않는다. [근거/현재 잔여](app-target-20261023.md#1110-3차-진행--승인된-ar의-연속-실행).

**AR v1 1차 연결.** daily-high/fundamentals의 finished 후속은 QObject bound Slot으로 GUI receiver 수명에 귀속하고 worker별 code를 회수한다. historical-high cleanup/기존 ranking·TR·writer 경로는 유지한다. Qt CI 관련 prefix125와 실제 AppController 연결을 포함한 현재 관련 검증은 [1차 상세](app-target-20261023.md#118-1차-구현--ci-원인-보완ar02-선택-상태). 실제 GitHub 재실행·운영 수락은 아니다.

**이전 종료 보완 계약(PC6 보강·NAS 범위 정정 전, 당시 로컬).** 조건검색 전용 metadata/TR·upper-limit fact 생산자를 제거했다. 기존 TOP20/신고가/시장·테마/NXT 기준자료 owner·0s actual day는 유지한다. retired cohort를 새 구독/장후 대상 근거로 사용하지 않는다. [근거](../../data/diagnostics/r3-050-final-20261023/verification.json).

**현재 열네번째 계약(2026-10-21, `fbbb96a0cdbab65d476dd352a90745ba30a1eae4`).** R3-076: theme_dialogs의 과거명 확인→전체 분리→부분 분리·fragment 선택을 _resolve_known_theme_row 한 구현으로 모으고 두 실제 resolver를 연결했다. 처리 완료 빈 결과·미처리 fallback·전체 취소를 구분하며 OCR/Excel 재입력·별칭 저장과 수동 SimilarStock의 다른 최종 UX를 보존한다. root448/25모듈은 통과했고 WT 비용·원본 대조는 별도다. 등록범위076만 완료, 나머지 잔여와 실제PG5/운영 미검증은 각 항목을 따른다.

**이전 열세번째 계약(2026-10-21, `b16c386d107b08218b2f97dc4d305f3d55ebc632`).** R3-076: catalog6개 중복 본문을 기존 StockRepository에 모으고 Theme의6public API는 기존 FK ON factory로 호환한다. 같은 owner의 request-name batch를 기존 matcher에 연결했다. profile_theme resolve와 승인/replace_many1Tx·legacy adapter 순서·동명 native첫행은 유지한다. resolve_unmatched_theme_rows와 _resolve_unmatched_new_rows의 과거명→전체분리→부분분리 공통순서가 잔여다. 서로 다른 재입력/alias저장 UX·profile_theme owner는 유지 대상이다. Theme 공개호환/FK정책 전달은 유지하며 기존Stock owner로 call·파일이1개 늘어난 대가로6개 판단본문을 제거했다. OCR/Excel의 Stock 직접경로는 동일하다. root1301 PC관련검증과실제PG5/053진단실패는구분한다.

**2026-10-21 `7e4fb607ceb09479f6b9c9ed2c03cbf6cdc42bbb` 국소 계약 갱신.** R3-027: 표는 실제 표시할 기간·live/completed 합계와 같은 기간 quality만 계산하고, 일반/제외 테마는 호출 안의 entries만 공유한다. 테마는 계속 live이며 callback간 캐시·주기 변경0. 20종목×120봉의 표 순회16800→9600(live full)/7200(completed)/2400(live_only), 테마35→20·제외 단독15유지. 금액·강도·quality·알림·style,16표시모드/80테마조합과 다음 tick/분경계/history ACK/gap을 대조했다. 기존 숨김TOP20 개선을 보존하며 관련 canonical121+비용1 WT 통과다. 운영 CPU·paint 지연은 미측정이다. 현재root1060/62모듈·원본비용/WT검사는구분한다.

**2026-10-20 `736fce8` 책임/검증 갱신.** 직접 모드 cache 증분에 기존 NXT annual/refinement를 연결하고 KRX since-cut/선택·optional NXT 실패 계약은 유지했다. legacy evidence에 시장열이 없어 NXT annual 전체 범위 확인 비용이 필요하다. first-max winner를 먼저 고른 뒤 (period,date)별 max로 압축해 native overwrite로 고가가 낮아지는 오류를 막았다. 기존 local-only basis version6을 factory/UI가 공유하고 native 저장 성공 뒤에만 receipt를 기록한다. 원본8ID/11 assertion 실패, 실제 factory→Controller→Qt/native→재시도/당일 skip0/다음날 annual2를 검증했다. native INSERT2→1/changes3→2, 연결/COMMIT1 유지. **전체068은 진행**: NAS authority900/evidence200의 부분자료를 보존한 채 직접 모드로 바꾸면 legacy cache가200으로 낮아질 수 있다. provenance/전환 계약은 미해결이며 임의 mismatch 차단이나 새 보완 ID로 완료를 위장하지 않았다.

**2026-10-20 `017bb03` 추가계약.** 초기감사의helper직접fixture를실제OCR진입으로판정한근거를정정했다. 실제이미지선택→OCRworker→row검토/name lookup/preview는catalog helper를호출하지않고catalog실제caller는수동sync와daily/secondary다. 미사용private _with_krx_stock_catalog16줄만제거(메서드294→293/나머지전체AST동일),controller/두실제caller/공개계약/기존검사변경0. 실제Qt manual/daily×성공/실패/취소·OCR취소8상태의원본/현재JSON동일,fetch1/predict1/검토1(취소0)/추가검토0·두worker해제. 새조회/실제호출깊이변화/실행비용절감/새OCR복구주장0. 초기진단의API설정dialog timeout240·trace15는fixture loaderNone원인확정/0통과로보존하고기존FakeRankingLoader정상구성으로대조했다.

**2026-10-20 `539cbad` 추가계약.** R3-080 완료(기본추론중복): 같은engine의첫predict결과를지역pages로한번물질화해첫row/badge해석에인계한다. 3mode×1/2/5장기본predict2→1/3→2/6→5,필요badge추론N/constructor1과정상rows/progress·검사한model/predict/callable-page/준비취소경계를유지한다. public서명과controller제품은그대로며실제QThread완료/실패/cancel/deleteLater를검증했다. 새cache/owner/모듈/모델다운로드없음. 이미초기화된extract_rows의후속파일삭제와malformedpage대Image.open오류우선순위는전후비교하지않았고기존is_file공통화/물질화순서로메시지·우선순위가달라질수있다. 전체오류계약/실제모델출력동일로과장하지않는다.

**003 저장·시각 계약.** MainWindow의 기존 RAM/queue·AppController 정상/retry dispatch·MarketCacheWriter/native fallback은 같은 StockRepository.update_price_cache를 쓴다. cap 및 현재 DB float_ratio로 계산한 circulating_market_cap이 다를 때만 해당 cap UPDATE를 반영한다. ratioNULL은 기존 유통시총 보존, ratio0/후행 비율/NULL 파생값은 필요한 보정을 한다. cap write가 생략되면 shared stocks.updated_at도 보존한다. last_price_updated_at은 같은 가격의 새 수신에도 갱신하며 fundamentals_updated_at의 당일 gate·원일 고가/원자 transaction은 유지한다. 추가 SELECT/ACK 캐시/계층 없이 기존 저장소/writer/native rollback·restart 검사를 재사용한다. 일반 updated_at을 새 전용 cap 수신시각으로 해석하거나 신선도/TTL을 추가하지 않는다.

**현재 책임.** PC의 설정/타이머 → AppController/RankingExecutionCoordinator → RankingWorker/RankingService → 표·후속 구독/기본정보 흐름을 유지한다. NAS의 AutonomousTop20은 순위 회차·membership RAM과 후속 수집을 소유하고 모든 키움 REST는 CentralRestBroker의 우선순위·제한·재시도를 통과한다. 직접 모드도 기존 KiwoomRestClient를 사용한다. 기본정보/NXT/일봉/역사 고가 service는 조회·변환·근거를 만들고 worker가 UI로 결과를 전달한다.

**상태·실패·공식 경계.** `load_top_stocks`, 순위 응답 coordinator와 reference service의 기존 `load/is_enabled` 경계를 사용한다. 로컬 `load_ranking_cache`는 묶음 조회, native `update_price_cache`는 같은 가격 job의 단일 transaction을 소유한다. 기준 자료의 확인시각, 일자, 고가 MAX, coverage와 시장 출처를 유지한다. 순위 재시도는 worker/broker에서 수행하고 저우선 백필이 다음 순위 회차를 막지 않게 한다. 저장 실패와 이미 성공한 화면 조회를 분리한다.

**미해결.** R3-002의 transaction 통합과 R3-026의 묶음 read를 다시 만들지 않는다. 원거래일 실패 재병합은023의 c0cf7ce에서 로컬 검증했고, 이번 TOP20/Ingestor 공통 시계 인계와 dataset receipt도 기존 owner에서 보완했다. Collector의 reference 복원은034, NXT 포함 고가의 cache 증분 누락은 068로 남아 있다.

**관련 검사.** [순위 service](../../tests/unit/test_ranking_service.py), [순위 실행](../../tests/unit/test_ranking_execution.py), [StockRepository](../../tests/unit/test_stock_repository.py). 기준 자료 변경은 KRX/NXT·빈/부분 자료·수신 후 저장/재조회까지 필요한 경계를 선택한다.

<a id="market-data"></a>
## 실시간·저장·복구 — F04~F09

**MAIN174 저장·종료 국소 수락(F03/F07/F09·F23/F24):** membership native1곳의 기존 schedule owner가 저장 완료까지 합류한다. 원본 early close21.477ms/DB0 대비 후보는 원 save/COMMIT 뒤 close였다. 같은 입력 API순서·native호출/COMMIT 각1·payload hash/3관계 행수 동일, 원래0B/RAM/REGACK/index/outbox/시계·저장 의미를 유지했다. 정상·기존 capture/반복취소/실패/after-close 관련132가 독립3worker/원180초에서 PASS이고 Job/tree/UUID/TEMP를 정리했다. 첫132 TIMEOUT은125+60>180의 묶음 오류로 보존하며 실제PG/NAS/authority전환 전체 수락은 아니다. [source174 관련132](../../tmp/ar-v1-wave56-20261101/membership-drain174/acceptance174-related132.json)·[최신 수락 범위](../../tmp/ar-v1-wave56-20261101/c19-recovery175/acceptance175.json). 취소 native cause는 refresh/schedule 경계이며 public close가 모든 DB오류를 직접 던지도록 정책을 바꾸지 않았다.

**현재 NAS 원본 후보의 공식 조회(F03/F07/F09, 읽기 관측):** 기존 인증·설정을 재사용한 GET2에서 membership/Oct30와 ranking/subject5의 저장 snapshot을 각각1개 확인했다(HTTP200, items 각20). ranking 반환 슬롯 Sep21은 실제 최신 순간·기간 끝의 증거가 아니다. 원 시각/epoch를 변경하지 않았고 원문 행/비밀값 저장·export·이전은0이다. 제품 writer·원자 관계·PC 데이터·주문 권한을 바꾸지 않았다. 전체 순위 이력·필수 참조·coherent snapshot/safe cut와 실제 이전/소비는 AR05-C08~C11 잔여로 유지한다. [첫 조회](../../tmp/ar05-existing-read-api-scope173-20261030/execution-report001.json)·[둘째 조회](../../tmp/ar05-existing-read-api-scope173-20261030/execution002/execution-report002.json).

**MAIN173 default5 저장·조회연결(F02/F03/F07/F09의부분경계):** 기존검사1의수제privateRAM seed를실제refresh→검증RAM pending→원SQLite snapshot/meta/revision COMMIT/ACK→live/stored API→기본Remote소비로바꿨다. main exact1은2.108초PASS·원4assert/다른96case AST/원2초gate·180초worker·소스·Job/tree/정확UUID/성공TEMP를보존했다. native불변비교는선택컬럼tuple/원문·ID/hash이며전체row비교가아니다. 제품owner/DBschema/쓰기수변경0,실제Kiwoom/0B수신·구독/Qt표/F13전부/PG/authority전환·중복쓰기제거·저장중close정상성을이검사로수락하지않는다. 이전6은변경없는writer/reader·검사LF의근거로만재사용했다. [현재근거](../../tmp/ar-v1-wave55-20261030/c07-wire173/acceptance173.json).

**MAIN131 입력 소비·복구 연결 국소 갱신:** collector/RAM/native writer 소유권은 유지한다. 그 소비자인 MockAutomationInputProgress가 실제 bootstrap/프레임복구·cursor/protocol/scratch와 ACK-before-install을 단독 소유하고 늦은 commit/ACK손실·같은 scratch 재시도를 기존 계약으로 연결했다. 새소스 core360+공개Supervisor28/실제PCPG10 PASS·native39 정리 확인이다. 새 수집·DB/clock/poll·저장방식 추가0이며 실제NAS/source/T0/미완료 인계 전체 수락은 아니다. [현재 근거·잔여](app-target-20261023.md#1155-ar07-입력진행bootstrap프레임복구-소유권의-통합과-검증--2026-10-27).

**2026-10-27 구현·검증 국소 갱신:** 기존 membership/seed·수신/RAM/ACK·native 저장 owner 유지. AR03 원래 다섯 경계는 현재pin/caller/consumer와 원소스별 근거를 대조했고, 후속 수정된 수신 fixture12를 MAIN129에서 원시한/실제native환경으로 PASS 확인했다. 전체 local responsibility/source/T0/미완료 인계/실제NAS 수락과 구분하며 새collector/clock/중복저장/manager0이다. [현재근거·한계](app-target-20261023.md#0-현재-기준과-진행-현황).

**2026-10-27 시간 감사 보충(수정·새 PASS 아님):** F07/F08은 과거6건의 현재 사용·가용시각/필수이전 영향 미확인을 유지하고, F09는 날짜/cache의 OS 시계가 판단에 미치는 범위를 확인한다. F04 기본 worker의 UTC-aware+9 잠재 경로와 정상 bootstrap 주입을 구분한다. F05/F06의 원분키·정상 writer/receipt·epoch 연결은 유지한다. timestamp 문자열 혼재만으로 전체 손상을 확정하거나 원자료를 변환하지 않는다. [담당 AR·우선순위·완료 기준](app-target-20261023.md#time-audit-followup).

**MAIN116 연결 계약(2026-10-26):** per-Hub관측epoch/readyreceipt는기존lifecycle의명시observer,raw선별seed/typedreadback/첫receipt/rollback은기존Databaseinitializer가소유한다. sourceCLI/조회UI는권한을생성하거나편집을재설치하지않는다. native수집/저장/transport/codec와정상consumer의owner는유지한다. 현재고유656 중 656 PASS/0 FAIL·worker2/원실패·미실행/운영제한은[단일현황](app-target-20261023.md#1149-hub-관측-소유와-current-state-native-이전-통합--2026-10-26)을따른다.

**AR09/07 국소 계약(2026-10-26):** native 수신/RAM/writer/재시도·종료는 기존 owner, optional input/flush/legacy 관측 상태는 명시 lifecycle recorder다. 후보·명세 게시2곳은 기존 native 합류 도구를 재사용하며 주문권한을 생성하지 않는다. 동일본문526·EOF정리후11 PASS/guard34·전체 도구부재/PG/이전·기존 UNKNOWN/운영은 구분한다. [현재 근거·잔여](app-target-20261023.md#1146-ar09-collector-관측-소유권과-ar07-게시-수명--2026-10-26).

**AR09 구독 관측 국소 계약(2026-10-26):** native RegistrationProgress/approved atoms·send/ACK·source approval/ready/종료는 Collector, recorder.complete는 명시 주입된 전용 도구 owner다. 현재77 PASS/구독 파일1개 실제 부재를 검증했으며 legacy/upstream/flush·전체 app/actualNAS5는 미완료다. [현재 근거·잔여](app-target-20261023.md#1145-ar09-실시간-구독-관측-상태의-소유권--2026-10-26).

**AR09 REST 국소 계약(2026-10-26):** native transport/ingest/cache/inflight/credential/drain은 기존 broker, tool ordinal/lineage와 기존 scope 관측은 명시 optional module이 소유한다. 관측자가 native 실행·재시도/저장 ACK를 대신하지 않는다. 실제 본류138·capacity caller11 복구·원 seed/실패/취소/종료를 확인했고 전체 producer/app/actualNAS5는 미완료다. [현재 근거·잔여](app-target-20261023.md#1144-ar09-rest-요청관측과-native-실행-소유권--2026-10-26).

**AR09 Hub:** 구독/필터·queue/drop/gap/readiness와 PublishedHubEvent KST는 제품 소유, 기존 optional lifecycle module은 capture/queue receipt 관측을 소유한다. event stamp1회/clone 보존·실제 Hub 전체 도구 소스 부재를 검증했다. Collector ACK/RegistrationProgress·RAM/native 저장·Risk/계좌 권한은 유지한다. DTO3/전용 private state와 나머지 producer/workload/store 연결은 아직 남는다. [현재 범위](app-target-20261023.md#1142-ar09-hub-공개-관측과-발행시각-소유권--2026-10-26).

**AR09 import 경계:** TOP20/realtime/program/REST/market-events의 원 본문/native owner는 유지하고 owned helper import만 common leaf로 옮겼다. DB/I/O키 변경0·정적 순환edge11 해소는 SQL/latency 측정이 아니다. capture/workload hook과 제품 안전 책임은 남아 있어 normal producer 전체 부재 기동은 미검증이다. [현재 범위](app-target-20261023.md#1141-ar09-공통-수명-경계-통합--2026-10-26).

S1 PC Top20 표시창 geometry는 명시 role INI를 사용한다. NAS index/outbox/봉 writer·원자료 책임은 PC 표시 설정과 분리한다. 실제 source/pruning/query 재시작 검증과 현재 설치/장중 미검증을 정본에 구분한다.

**AR v1 현재 계약.** preserved_rank_targets/data_transition_receipts는 native 반입의 불변plan/첫receipt 자료다. 기존generic6 mutator에서 두 namespace를 I/O 전 차단하며 조회·일반collection은 유지한다. 현재121의실PG12와 source/양target/native 소비로 국소확인했다. [판본/검증·중지 현황](app-target-20261023.md#0-현재-기준과-진행-현황).

**AR v1 20차 계약.** reported_research_results는원manifest전체와최초receipt를같은기존native transaction에불변저장한다. 일반문서upsert우회금지·동시승자/rollback/ACK손실/재시작실제PG4확인; 매틱새DB/캐시/worker0. [현재판본/검증범위](../../data/diagnostics/ar-v1-wave20-20261024/verification.json).

**AR v1 18차 P0 계약.** native READONLYsnapshot/원시각·품질·참조/부분실패-complete구분을보존하고운영DB/기존writer를건드리지않는다. source무료재생성·가짜parent·원신호복원주장0. [현재Y132/9·실CLI](../../data/diagnostics/ar-v1-wave18-20261024/verification.json).

**AR v1 16차 계약.** GUI query의NASread callback만QObject로수명결합하고원0B/RAM/PcMarketSession·writer원본문을유지한다. 실제lateclose/중복·실패/재시작/nativeprice ACK와기존worker종료를검증했다. [현재국소검증](../../data/diagnostics/ar-v1-wave16-20261024/verification.json).

**AR v1 13차 계약.** Header WS·기존RAM/native reader/시계·부족자료의미를유지하며두process actual조회/설정재시작을검증한다. UI종료는자기구독/worker만종료하고supplier는생존한다. 추가poll/per-tick DB/권한확인0. [현재국소검증](../../data/diagnostics/ar-v1-wave13-20261024/verification.json).

**AR v1 12차 계약.** 기존Hub subscriber에scope만 보존하며 최초snapshot과queue게시 모두 동일허용집합이다. query는 계좌/미지event를 queue에 넣지 않고 decode/순서/ACK·native저장과 recovery owner는 그대로다. [현재국소근거](../../data/diagnostics/ar-v1-wave12-20261024/verification.json).

**AR v1 9차 계약.** Collector는RAM/source/native pending·ACK의기존owner다. 소비자는 detached minute_read_status만읽으며 process-local marker를receipt/권한으로쓰지 않는다. 65상태/55본문·원자7경계·native비용3군을보존했다. [현재소스736근거](../../data/diagnostics/ar-v1-wave9-20261024/verification.json). 최신단계/잔여는정본0절이다.

**AR v1 8차 계약.** PC 선택 aggregator/source·coverage owner를 session으로 통합하고 UI 중복10필드/이전 연결을 제거했다. 기존0B 구독/수신·native writer/ACK·final drain을 유지한다. stub adapter의DB0은 native 비용 실측으로 해석하지 않는다. [소스별 검증](../../data/diagnostics/ar-v1-wave8-20261024/verification.json); 현재 판정은 정본0절만 참조한다.

**AR v1 4차 당시 계약(최신5차와 구분).** PcMarketSession은 기존 화면의 관측6개 RAM/ingest·gap/projection만 소유한다. aggregator·cohort·history·AppController pending/ACK 및 writer 저장/재시도/종료는 기존 owner다. 관측 시각은 공급된 보정 시계이며 원체결 시각을 덮지 않는다. [현재 근거/미확인](app-target-20261023.md#1111-4차-구현--실제-상태workflow-소유권과-불변-수락).

**AR v1 3차 구현 계약(현재 통합 검증 중).** PC route 권한은 full requested-code ACK와 실제 fallback 종료 뒤 typed generation 전이로만 전달한다. 문자열은 표시이고 현재 MainWindow aggregator가 소비한다. NAS index sampling과 실제 저장은 같은 서비스의 별도 joined task이며 기존 outbox 등록 fence·pending/inflight·close 의미를 유지한다. 기존0B/FILL/분봉/RAM 값·원본 경로는 유지한다. [근거/현재 잔여](app-target-20261023.md#1110-3차-진행--승인된-ar의-연속-실행).

**AR v1 2차 간접 F06 연결.** PC source reload의 entry writer provider/cache generation 전환과 rollback은 writer가 소유하고 UI/controller는 내부 cache를 읽거나 복원하지 않는다. 기존 0B/분봉 집계·native writer/pending·source ACK는 바꾸지 않았으며 메인 화면/source 교체·관측 job 회귀를 함께 확인했다. Collector/MarketCacheWriter 구조 전환 완료는 아니다. [현재 국소 검증·남은 조건](app-target-20261023.md#119-2차-구현--allocation-공개-계약오류writer-source실제-process-exit).

**AR v1 1차 연결.** CentralRealtimeWorker가 fallback/validation child의 실제 종료까지 소유하며 stop 실패 뒤 ref/결과를 잃거나 중복 producer를 만들지 않는다. 현재 child raw FILL/tick drain·full requested-code ACK·재연결은 유지하고 해제한 child의 늦은 signal만 차단한다. TOP20 seed canonical v1은 보존하며 cold 안전의 실제 index task/flush lock을 별도 확인한다. reconciliation PG fixture는 실제 native rollback/transaction 상태·published day 보존을 확인한다. [현재712 중 해당 관련 범위/환경·잔여](app-target-20261023.md#118-1차-구현--ci-원인-보완ar02-선택-상태). Collector/TOP20 live 저장 상태·transaction 변경0, 전체 AR03 전환/운영 수락 아님.

**이전 종료 보완 계약(PC6 보강·NAS 범위 정정 전, 당시 로컬).** 사용자 철회에 따라 15% 조건검색 종목발굴→추가0B 등록/수신/저장 전용 경로를 제거했다. R3-062는 제거/검증 완료(로컬)이며 burst/drop/조건검색 전체재생의 후속 적용 대상도 소멸했다. TOP20→기존0B REG/ACK→수신→RAM→native 저장/조회→Shadow 연결, VI·신고가·시장/테마·재연결/종료는 유지한다. 과거 테이블/원본·저장 설정 문서·저수준 historical store/replay codec은 보존하며 자동 DDL/데이터 삭제는 없다. 소스만 변경했고 운영 NAS는 미배포이므로 실제 운용 중단을 확인한 것은 아니다. 공통0B/VI와 PostgreSQL8·native ACK/rollback/lost-ACK/close 관련 회귀 PASS. 과거 replay 코덱은 live producer가 아니다. [근거](../../data/diagnostics/r3-050-final-20261023/verification.json).

**이전 집중 검증·이관 계약(당시 2026-10-23, 로컬).** 039 최근 multi-day reader의 등록 실제PG를 확인했다. 062 conditionPG는 reader/replay·metrics 범위이며 운영burst/drop/coalescing/queue 정책은 이관했다. 원본·authority·ACK·KST·저장/복구 책임과 transaction 수명은 바꾸지 않는다. [검증](../../data/diagnostics/r3-closeout-20261023/verification.json).

**이전 스물한번째 추가 계약(2026-10-23, 미커밋 당시).** 033 등록 retention 경계는 로컬 종결. current-day legacy gap 확장은 보정 now 기반 정상 now-30d cutoff보다 늦고 PARTIAL/COMPLETE 소비 계약을 유지한다. repo-only 동기 DI는 호환 경계, 임의 미래 cutoff/31일 clock 역행의 삭제 정책·영구 신호 유실/kill은 미수락. 근거 [통합 검증](../../data/diagnostics/r3-parallel-20261023/wave21/verification.json).

**이전 스무번째 추가 계약(2026-10-23, 미커밋 당시).** 033 명시 minute 기준 fence와 원ACK/실패pending/close gate를 유지한다.062 관측 N128/20matches는 인코딩805627B 보류·native동시1·ACK후 owned pending0이며 heap/RSS·운영 burst 측정이 아니다.새 coalescing/drop 정책은 없다. 근거 [통합 검증](../../data/diagnostics/r3-parallel-20261023/wave20/verification.json).

**이전 열아홉번째 계약(2026-10-23, 미커밋 당시).** R3-033: repo-only AppController가 성공한 applied cutoff를 RAM 한 값으로 보유하고, 명시 retention 요청 없는 후속 stock/index/gap 및 MainWindow 직접 history/empty 저장의 원 success/failure 뒤 같은 explicit-minute fence를 적용한다. 기존 native purge/failure owner를 재사용하며 원 ACK·원 실패·최신 pending·타이머/close gate는 유지한다. older explicit cutoff와 같은 flush 입력, 미래 cutoff/clock 역행, legacy market:() 무날짜 확장, 영구 queued 신호 미소비/process kill은 잔여다. applied 값은 기존 Controller 수명 RAM이며 재시작 영속화가 아니다. 요청 cutoff를 native max로 강화하거나 원본 payload/ACK를 필터링하는 새 정책은 추가하지 않았다. 표준 bootstrap의 FIFO writer는 변경0, 기존023/075/076 완료를 재개하지 않는다. root관련283/17모듈PASS, 원본/진단/신규CAS측정교정과실제운영 수락을분리한다.


**이전 열여덟번째 R3-033 계약(2026-10-23, 미커밋).** bootstrap 30초 retention worker가 실행 시점의 기존 AppController.ranking_now→현재 RankingService/client.server_now를 한 번 읽고 as_kst로 날짜를 정규화하여 30일 cutoff를 만든다. 이미 적용된 API 교체를 따르며 새 clock owner·TR·HTTP·poll·relay는 없다. 기동 retention native 연결2/SELECT2/DELETE6/BEGIN2/COMMIT2 전후 동일, 보존30일/일봉250·실패 처리·worker/thread·timer·종료 합류/lease 계약을 유지한다. 새 clock 읽기1회/fixture HTTP0이며 원본 비교용 별도 diagnostic SELECT1/연결1은 생산 비용에서 제외한다. 공개 no-writer fallback의 지속 applied-cutoff/history·empty fence, legacy market:() 무날짜/미래 cutoff·clock 역행, 영구 queued 신호 미소비/process kill은 잔여다. 이 변경은 현재 clock 샘플 경계이며 API 적용/rollback의 동시 atomic 보장·실제 HTTP Date 동기화·NAS/운영 수락을 증명하지 않는다. R3-033 전체 진행, 기존023 완료를 재개하지 않는다. 관련128/7모듈·guard/소비/실행 gate 로컬 PASS; 실제 시계 동기화/배포 수락은 별도다.


**이전 열일곱번째 계약(2026-10-23, `066d2de1f71e52f1b3507a42e6a80153a092cd53`).** R3-033: 표준 bootstrap은 항상 writer를 조립하며 repo-only 공개 DI 호환 경로에서 AppController.flush_minute_cache가 기존 on_minute_cache_failed를 재사용하고 _complete_shutdown의 failure+pending 조건은 writer 없이도 기존 close gate를 지킨다. 새 writer·cutoff·retry 정책은 없다. 별도 bootstrap._run_desktop은 정상 close의 기존 Qt child 추적을 유지하고 direct exec 반환/예외 finally에서 같은 parent·exact objectName retention worker를 실제 wait한다(제품 +7줄). repo-only open 실패에도 기존 1초 single-shot 재시도를 예약하도록 실패 owner를 재사용한다. fallback은 표준 제품 운영 조립의 관측 회귀가 아니라 공개 DI 호환 범위다. repository._path로 writer를 새로 추정하지 않는다. fallback의 지속 applied-cutoff/history·empty 전체 fence, legacy market:()의 자동 현재일·미래 cutoff/clock 역행, 영구 queued 신호 미소비/process kill은 잔여다. 기존 bootstrap retention kst_wall 시계를 키움 보정 시계에 연결하지 않은 잔여는 R033으로 남기고 R023 등록 완료를 재개하지 않는다. 전체033·운영 재발/lock latency는 완료로 확대하지 않는다. root642/35PASS, WT·원본 진단과 운영 수락은 별도다.


**이전 열여섯번째 계약(2026-10-21, `7d0b8e108893c649e1e56e127eb06f29151779a5`).** R3-033: MarketCacheWriter.run의 gap/empty_coverage 두 분기에서 원래 native 호출과 성공·실패 신호 처리 뒤, 성공 적용 cutoff보다 이른 명시 입력 minute가 있으면 기존 purge를 직렬 재사용한다. 제품 +6줄만 추가했고 새 owner/시계/queue/schema/반환 정책은 없다. empty의 실제 committed tuple·generation·empty=True ACK와 원 실패 신호를 유지한다. 원본 실제 SQLite 관찰 1ID/29조건 PASS(32.754초)는 결함 관측 증거다. gap explicit COMMIT·empty의 C __exit__ COMMIT 뒤 예외와 정상 성공의 만료 4경계에서 metadata 1행이 재등장했고 보완 후 모두 0이다. 현재 canonical10+별도 probe1 PASS(61.348초), 기존 검사 본문 변경0. keeper 네 테이블·확정권한·실제 반환()/ACK·오류·rollback·clock을 대조했다. 성공 empty ACK 뒤 cleanup 실패도 ACK1/history_failed0을 유지하며 retention_failed를 별도 전달하고 같은 writer 재시도를 확인했다. 만료 6조건은 precommit rollback의 no-op을 포함해 연결/SELECT/DELETE/BEGIN/COMMIT +1/+1/+4/+1/+1, clock0이다. current/cutoff/cutoffNone 추가 I/O0. cutoff가 있으면 gap O(C+M)/empty O(M) any 날짜 검사 CPU는 정상 입력에도 추가되며, 만료 발견 시 short circuit한다. 원 empty history_failed에는 기존 전용 pending·close gate가 없고, cleanup retention_failed만 기존 pending/timer/close 경로로 이어진다. gap은 positive ACK가 없다. writer 실제 native와 기존 소비 slot 검사는 확인했지만 worker coverage→queued GUI→UI 전체 연결은 실행 미검증이다. 날짜 없는 legacy market:()의 native 현재일 확장에 미래 cutoff/30일 이상 clock 역행을 적용한 보장은 없으며, 새 clock 조회·반환 정책을 만들지 않았다. no-writer 호환 fallback·별도 bootstrap retention·영구 신호 유실/process kill·운영 빈도/lock latency는 잔여다. 전체033 완료로 승격하지 않는다. root279/13PASS와WT비용/원본대조는별도이며새owner/계층/schema0,전체R3진행유지·075/076기존완료보존.


**이전 열다섯번째 계약(2026-10-21, `7fe20f325d462242de3390c50d678c585e6f0161`).** R3-033: MarketCacheWriter의 기존 history/minute 실패 handler에서 원래 failure를 먼저 보낸 뒤, 성공 적용 cutoff와 만료 payload가 있으면 같은 purge helper를 재사용한다. 정상 반환 flag로 COMMIT 여부를 추정하지 않는다. 원래 False·성공/quality ACK0·원본 pending 재시도를 유지한다. gap/empty-only metadata·no-writer/bootstrap 모드, 강제 process kill·영구 GUI queued 신호 미소비는 미검증. retention은 positive ACK가 없고 실패신호 수신 때만 Controller pending/timer/close gate를 복구한다. 성공 GUI ACK 미소비만으로 writer의 후속purge가 생략되지는 않는다. 실제앱은같은writer를재사용하고새Window는첫tick/history입력전cutoff를다시넣는다. 새persistent ACK정책은 추가하지 않았다. 추가lock대기/운영 빈도·latency 미측정. 만료실패의필수purge비용+1연결/+1SELECT/+4DELETE/+1BEGIN/+1COMMIT·clock0,정상/current/cutoff/미적용cutoff추가0. root369/18모듈과WT비용/원본대조는구분한다.075등록범위로컬완료,나머지R3전체/PG5/운영미검증은백로그를따른다.


**이전 열네번째 계약(2026-10-21, `fbbb96a0cdbab65d476dd352a90745ba30a1eae4`).** R3-033: history 봉 upsert가 정상 반환한 뒤 별도 sync-log 저장이 실패하면 원래 history_failed를 먼저 보내고, 만료 봉이 있는 경우에만 같은 writer의 성공 적용 cutoff로 기존 purge를 재사용한다. 원래 False·ACK 0을 유지한다. root448/25모듈은 통과했고 WT 비용·원본 대조는 별도다. 등록범위076만 완료, 나머지 잔여와 실제PG5/운영 미검증은 각 항목을 따른다.

**이전 열세번째 계약(2026-10-21, `b16c386d107b08218b2f97dc4d305f3d55ebc632`).** R3-033: 기존 writer가 성공 적용한 cutoff 하나만 보존한다. 성공한 late-expired minute/history job의 원래 COMMIT·ACK 뒤 같은 cutoff의 purge를 직렬 재적용한다. 원본 payload/metadata/authority/시계는 바꾸지 않고 cleanup 실패는 기존 retention_failed→pending/timer/close gate로 보낸다. 부분 COMMIT 후 실패 job·gap/empty-only·no-writer fallback·별도 bootstrap은 포괄 fence가 아니다. 운영 재발 빈도는 미측정이다. R3-039: recent API의31일 직렬 read를 기존 QueryStore의 한 multi-day reader로 통합했다. 전체 RAM/retry를 먼저 동결하고 native 날짜선택·operation/authority overlay를 같은 read owner로 처리한다. 31달력일 범위/실제1~5거래일/COMBINED 날짜키/SOR·빈이력·RAM-only 현재일을 보존한다. capture readers/recorded executor 등록·3owner 서명·consumer2site를 함께 갱신했다. 신규 실제PG1 미실행. physical I/O/WAL/운영 latency·writer wait와 실제 NAS/키움 수락은 별도다. R3-062: _condition_list가 승인한 문서를 즉시 고정하고 기존 background owner/tail로 native status save를 직렬 처리한다. cancel/close는 실제 native에 합류하고 첫 미전달 원오류는 poll1회로 기존 readiness 해제/3초 reconnect에 전달한다. 새 queue/pool/drop/coalescing/재시도 정책은 없다. 변경PG loopjoin1 미실행. strict TOP20는 market_events를 기존대로 불허하며 condition capture 전체재생/운영 latency/RSS/processkill은 별도다. DB 지연 중 승인 snapshot closure가 남는 비용은 숨기지 않는다. root1301 PC관련검증과실제PG5/053진단실패는구분한다.

**2026-10-21 `7e4fb607ceb09479f6b9c9ed2c03cbf6cdc42bbb` retention 소유 계약.** R3-033: 첫 tick/거래일 변경의 purge를 기존 FIFO MarketCacheWriter에 dirty minute 뒤 enqueue하고 Controller가 실패 cutoff를 보존·재시도·종료 차단한다. RAM 고가는 source 날짜로만 warm하고 새날 reset/지연 전일 history 오승격을 막는다. GUI DELETE4→0/worker0→4, 연결·SELECT·DELETE·COMMIT1/1/4/1은 유지하고 late dirty 잔류1→0이다. 기존 cutoff/metadata·API 교체·실제 Qt/native 실패·종료 계약을 보존했다. WT canonical112+비용1 통과, root에서는 기존056 추가검사도 함께 유지한다. no-writer 호환 동기 fallback/bootstrap retention·운영 수락은 별도다. 추가 코드검토에서 expired dirty 저장실패→purge성공→later retry 또는 늦은 old history가 만료행을 다시 저장할 수 있는 기존 잔여를 확인했다. 정상0B는통상30일안쪽이며 장기실패대기/날짜도약/old REST응답 조건이필요하다. native교차재현·운영발생은미확인이다. 정상FIFO·purge실패재시도·날짜RAM slice만수락하고 모든실패복구후만료행0으로확대하지않아 전체033은진행유지한다. GUI→기존 Controller pending→FIFO writer→native owner/실패 retry·종료를 기준으로 읽는다. 현재 root1060 관련검증과 WT 비용/운영 미검증을 구분한다.

**2026-10-20 `d6da121` 추가계약.** 숨긴 분리창의 순수 read/render와 미선택 index read·중복 aggregate만 생략한다. 필수 custom 분·일봉 native 복사/metadata/기존 선택시장 backfill 요청은 유지한다. show/reopen은 최신 자료를 읽고 실패시 기존 숨김과 원예외를 복원한다. checkbox→기존 source_changed, daily→raw setter 순서와 caller의 동일interval 중복만 제거, 공개 MinuteChart bytes/forced-reapply 의미는 불변이다. hidden custom 연결12→4/SELECT19→3/집계10→0·필수 INSERT4/BEGIN2/COMMIT2/clock1 동일; custom없는 hidden6→0/13→0/10→0, visible현재종목6→2/13→3/10→4. 원본PASS→중간숨김전체skip FAIL0행→최종PASS(원본purge뒤journal/metadata유지)와 원본/최종54 source상태 동일을 재사용 진단으로 확인했다. R052 dirty/close/account/episode 나머지AST·공개chart는 보존하고 필수 같은값 복사UPSERT/075 주기정책은 바꾸지 않는다.

**2026-10-20 `0a54c35` 추가계약.** R3-065 검증 완료: JsonRecordOutbox는 missing만{}로 처리하고 read/JSON오류·최상위non-object는 명시실패로 전달한다. 정상dict/member filtering/원자write·replace는 유지, 손상/read실패 put의write/replace1→0으로원본bytes를보존한다. 실제Top20 start는subscriber/task/native전실패하고, put실패의RAM입력과 native성공뒤 remove실패의파일/pending을 명시retry/start복구까지 유지한다. 새로운repair/archive/format/queue/자동retry없음. 원본6method28subcase실패를보존, 실제Windows ACL/NAS 손상을재현한것은아니다.

**2026-10-20 `64a74e0` 계약 보완.** R3-035 검증 완료: 실제 index loop에서 collector advance와 frozen record 추출만 기존 RAM lock 안에 두고, 같은 outbox/native save await를 밖으로 옮겼다. 제어 native/outbox 100ms 대기에서 lock 보유 true→false, 실제 ranking prepare/Hub subscription의 barrier 해제 전 완료 false→true. RAM membership은 원래도 먼저 공개됐으며 신규 개선으로 세지 않는다. 동일120억/시장60·60/cohort와 native 연결3/SELECT2/BEGIN·COMMIT2/INSERT5/DELETE2/save1을 유지한다. R036 caller-cut/owned task·실패·취소·close/start 복구를 보존하고 새 queue/계층/transaction은 없다. R3-036 후속 검증 보완(제품 변경0): 기존 replay의 lost-ACK 기대값1은 native thread와 추가된 owned save task의 실패 관측을 합산하는 RuntimeScope 실제 계약을 놓쳤다. thread/task OSError 각각1, raw error_count2, execution_succeeded=False·동일 save2회·outbox 복원을 함께 검증한다. 사건 중복 제거 정책이나 DB 시도 횟수를 바꾸지 않았다. 옛 기대값은 R065의 원본 load를 복원해도 동일 실패, 보강한 기대는 원본/현재 load 모두 통과한다. 초기 R036 관련90건에 replay-outbox 검사가 빠졌던 범위를 명시하고 향후 해당 소비자 검사를 함께 선택한다.

**2026-10-20 `8f3bade` 추가계약.** R3-036 완료: 기존지수pending의caller진입key cut을전용flush lock으로직렬화하고동일service의shield save task가실제native수명을소유한다. 같은cut의native/연결/BEGIN/COMMIT/provider각2→1·INSERT/DELETE각4→2·반영행4→2,최종snapshot동일. 새caller는진입cut의최신payload를저장한뒤반환하고진입후다른key를무한drain하지않는다. 원본barrier PASS→중간합류-only FAIL→최종PASS를보존했다. 취소/실패합류/명시retry/ACK유실/outbox제거실패/start복구·실제RuntimeScope task/thread drain을검증했다. close는진행owner join만하고새강제retry0,collector/outbox lock범위와기존11메서드는그대로다. R3-038 완료: 분봉COMPLETE-only page는불필요한ACTUAL사전조회없이기존native권한판단을따르고,IN_PROGRESS는기존metadata 시간범위로좁힌다. SOR는실제소비page의기존start/end reader를쓴다. 동일120행 fixture에서metadata반환120→1(혼합)/0(완결),SOR120→2;완결연결5→4/SELECT4→3,Python commit호출5→4지만실제BEGIN/COMMIT각2→2다. 최종전체봉/meta/비교문서·반영행/시계동일,UTC-KST/23:59/lateSOR/component merge/필수사전조회실패와저장뒤SOR실패를구분했다. 다른venue component가필요한기존owner문서전체조회와sparse범위내중간행은남는다.

**034 완료·027 일부(2026-10-20, `9955a24`).** R3-027 일부: MainWindow의 숨김 TOP20 주기 처리만 collector.advance→완료 native 저장→cohort 구독 이후에 종료한다. 실제 Qt timeout4회에서 동일120행의집계4회/처리480행/렌더4회→0, NAS periodic set_series/data4→0이며 네트워크요청은원래도0이다. 완료분60억·시장별30/30·구독·열기/날짜/모드·재오픈 최신값은유지한다. 이미진행중인 NAS callback은숨김에서도렌더할수있다. 표/테마 same-cut·기간계산의남은027은미완료다. R3-034 완료: Collector의 stock-reference와 account-day entry 저장실패 복원에 기존 다른 snapshot과 같은 setdefault를 사용한다. awaited native 저장중 도착한 최신pending은그대로두고없는key만복원한다. KRX/NXT 양방향·precommit/ACK유실·원거래일/다음날·credential drain/close의원본9개실패반례가통과하며, writer호출2/3과기존arrival순서·day key를보존한다. 원본FILL원장/봉delta coalescing정책은변경하지않았다.

**037/078 완료 계약(2026-10-20, `fba8994`).** R3-037: 기존 route의 sender cancel/join을 nested finally로 감싸 RealtimeHub.disconnect를 보장한다. 원래 송신 오류/취소를 숨기지 않고 정상 subscriber/구독union을 보존한다. 실패 sender 뒤 수신 종료 시 client 잔류를 native router+제어 socket으로 재현해 제거했고 ready/snapshot/ping·gap·정상 종료/취소도 확인했다. sender만 죽고 receive가 계속 대기하는 경우의 양방향 감시는 기존 별도가설이며 추가 구현하지 않았다. R3-078: 기존 _inputs의 coverage2N SELECT만200code(최대400 owner) batch로 읽는다. 같은 cursor/SQLite transaction·PG repeatable-read wrapper/decoder·subject순서·missing과empty{}·source/generation을 유지한다. 0/3/20/200/201code SELECT는 각각1→1/10→5/44→5/404→5/409→9, coverage는0→0/6→1/40→1/400→1/402→2다. native 반환행/최종input/payload/generation 동일·연결/BEGIN/COMMIT각1/DML0. 실제PG 대신%s SQLite-backed driver shape만 검증했으며 물리I/O/WAL/latency는 미측정이다.

**검증 선택.** 현재 소스 관련4모듈 **고유89건/12.839 worker초 통과**(WS51+reconciliation38, 중복0). 계획/실행/통과ID·5개 변경ID·현재 소스와 frozen worktree SHA·worker/자식 종료·native TEMP 제거를 대조했다. 기존 test_central_realtime_hub/central_realtime_collector/realtime_subscription과 top20_reconciliation(native/runtime/API/client/GUI)을 재사용한다. 구조1374site/위반0·retirable0·기존guard 규칙/승인/QueryStore변경0. 증분35번째 기록5952→5957/추가5·기존본문수정0·삭제0, 이전34기록/미결49 유지. 자동full_required/495모듈은 미실행으로 보존하고 실제 native/runtime/API/client/GUI·Hub/Collector/구독 관련4모듈만 명시 실행했다. F04/F24·F09 부모 운영판정은 승격하지 않는다. 실제 PostgreSQL·키움/장중·다중PC/NAS/운영 DB/계좌/주문·앱 전체 수락·push/GitHub CI·NAS 배포·로컬 전수 회귀는 미실행이다.

**023 봉 관측 시계.** 중앙 minute/daily·revision helper는 유효 updated_at(epoch0/과거 포함)을 그대로 쓰고 결측/invalid일 때만 기존 Ingestor/Collector/store provider를 소비한다. minute의 fallback 시각은 한 번 공유한다. PC writer→repository의 gap/empty/index metadata도 기존 transaction에서 배치당 한 번 받은 시각을 전달하며 COMMIT→ACK를 유지한다. local helper의 명시 available_at은 보존하고 미주입 standalone은 공통 KST fallback이다. PC 정상 upsert의 기존 receipt 정책·원시값·canonical UTC를 재정의하지 않는다. [native/기존 품질](../../tests/unit/test_minute_bar_repository.py)·[실제 ACK](../../tests/unit/test_realtime_gap_quality.py)·[관측/revision 저장](../../tests/unit/test_central_server_database.py)·[가용시각 reader](../../tests/unit/test_minute_bar_revisions.py)를 영향에 맞춰 선택한다. JournalWindow/legacy 수락 시각은 아래 일지 계약으로 보완했다. TradeAnalysisPreparationService의 online daily receipt caller도 아래 계약으로 연결했다. setup/revision 자체 시각도 아래 계약으로 연결했다. enrichment task 수명 시계도 아래 계약으로 연결했다. history 비용/backfill 실패 업무 receipt까지 기존 소유자에 연결해 등록된 R3-023 코드범위는 로컬 검증 완료했다. 실제 PG·운영 수락과 startup/offline fallback·기존 자료 전환은 별도 time-basis 원장에서 구분한다.

**023 서버 시계·dataset receipt.** app의 기존 공통 `server_now`를 Ingestor와 AutonomousTop20의 기존 provider에 전달한다. QueryStore의 정규화된 provider가 dataset batch saved_at(epoch)을 한 번 샘플하며 revision receipt/원본 observation 시각과 구분한다. RAM 게시와 DB 저장은 서로 다른 기존 시점이다. reader/API는 숫자 epoch를 그대로 반환하고 Shadow의 UTC canonical 표현은 같은 순간의 경계로 유지한다. 새 clock 조회·layer·DB 연결/transaction이 없으며 native SQLite·PG SQL fixture/실제 app 조립의 관련 검증은 R3-023 최신 원장을 따른다. 실제 PG/NAS는 미검증이다. 이 경계 변경 시 [앱 조립·HTTP](../../tests/unit/test_central_server_app.py), [native SQLite](../../tests/unit/test_central_server_database.py), [PG batch 계약](../../tests/unit/test_dataset_batch_postgres.py)을 선택한다.

**현재 책임.** NAS Collector는 상류 등록/ACK·수신·RAM 최신값/봉·직렬 flush를, RealtimeHub와 기존 worker는 배포/PC 전달을 소유한다. PC MainWindow의 0B receiver가 MinuteTradeValueAggregator를 갱신하고 AppController가 pending을 MarketCacheWriter에 넘긴다. TOP20 collector는 cohort 활성화·분 내 기여를 계산한다. 서버 TOP20의 outbox/native index 저장과 ProgramSnapshotWriter의 pending/drain은 각각 기존 owner에 남긴다.

**상태·실패·공식 경계.** PC는 `queue_minute_bar/queue_price_cache`, writer의 `enqueue_*`/`stop_and_drain`을 쓴다. native bar/metadata·가격 transaction은 repository/store가 수행한다. 저장 중 batch와 새 pending을 구분하고 실패 복원이 더 최신 값을 덮지 않게 한다. query/history → writer commit ACK → aggregator의 generation별 복구 확인 순서를 유지한다. 화면은 기존 display/live-bar read를 사용하고 저장 조회를 실시간 원본 수집으로 복제하지 않는다. 직접/NAS 전환은 실제 구독 승인과 조회 성공을 소비한다.

**남은 read 범위.** 001/004/035/036/037/038/065/078의등록개선은각로컬검증근거를승계한다. 최근날짜직렬read039와각항목에명시한실PG/운영/late처리잔여를구분하며같은계층통합으로ACK/종료수락을대체하지않는다.

**가격 batch의 날짜·재시도 계약.** 기존 tick/history caller의 `observed_at`은 high의 수신 거래일에 사용하고 pending high는 `(date, code)`별 MAX다. 가격/시총은 개별 tick 정밀시각이 아닌 기존 batch 단위이며 최초 dispatch/enqueue 때 고정한 시각을 재시도에 보존한다. normal batch는 같은 FIFO writer와 Repository에서 연결/BEGIN/COMMIT1·SELECT0으로 처리하며 여러 날짜 high도 같은 transaction에 들어간다.

`price_failed`는5인자이고4번째 object가 `trade_date/observed_at/receipt` context다. 실제 Controller receiver를 동반 변경했고 legacy date/object와 flat-high 입력도 유지한다. origin metadata가 없는 legacy 실패에서 순서/최초시각을 복원했다고 주장하지 않는다. Controller의 field/code별 최신 dispatch metadata와 unique pending field/day별 retry를 재사용하며 실패 횟수별 이력을 추가하지 않는다. UI에서 늦게 받은 실패가 더 최신 가격/시총을 덮지 않게 하되, high는 같은날 MAX·다른날 분리를 지킨다.

**관련 검사.** [MarketCacheWriter](../../tests/unit/test_market_cache_writer.py), [분봉 저장소](../../tests/unit/test_minute_bar_repository.py), [Collector](../../tests/unit/test_central_realtime_collector.py), [AutonomousTop20](../../tests/unit/test_autonomous_top20.py). 최종 값뿐 아니라 단위/metadata·gap 세대·저장 실패·late ACK·drain과 변경한 DB 호출 수를 확인한다.

<a id="shadow"></a>
## 공유 판단과 Shadow — F11/F12/F13

**S12-02 F13 0B 가격·대금 창 분류.** 기존 시장 계산에서 승인 관측을 정렬한 뒤 종목과 두 창에 한 번 나눈다. 개별 같은 초 체결·원래 증거 순서·시장 범위·결측 판정과 기존 가격/대금 계산을 보존한다. 합성 동일 입력 비교와 관련 413건의 범위를 확인했다. [비교 근거](app-target-20261023.md#section12-library-comparison).

**MAIN157 선택창 시작수명(F13·F15 소비):** CandidateMonitorDialog는constructor에서poll/상태timer를시작하지않고게시된MainWindow의명시startup에서자기작업을한번시작한다. persisted alert=True·원candidate출력/알림·RAM선택조회·stop후늦은입장차단을현재query8및불변candidate33/관련소비범위로확인했다. 판단kernel/입력/주문권한·수집기는변경0이며C12전체종료/운영은별도다. [국소수락](../../tmp/ar-v1-wave51-20261029/integration157/acceptance157-c11.json).

**MAIN134 checkpoint 소비 갱신:** InputProgress의bootstrap/repair ACK-before-install과Position의FILL/publication fence는유지하고aggregate checkpoint의revision/pending/lastACK와실제write/성공ACK를같은모듈MockAutomationCheckpoint 한owner로이전했다. 같은18before/after와새unit357/actualPCPG5에서freshcontrol·STOP/consume prefix/ACKloss/force-noop/재시작·공개position소비를확인했다. 공유Shadow owner/원시각·원JSON/native권한은변경0이며source별Shadow PG5/전체PIT·NAS/운영수락은별도다. [현재근거](app-target-20261023.md#1157-main134-통합-검증--2026-10-27).

**MAIN131 입력진행 소유권 국소 갱신:** Mock Runner의 cursor/bootstrap/protocol/frame recovery/receipt와 실제 seed·repair/입력 projection을 한 owner로 이전했다. 같은 Minute/Leader adapter와 Shadow/CandidateMonitor의 기존 상태 소유권은 유지하고 공유 recovery/bootstrap 소비를 새소스 unit388/실제PCPG10에서 확인했다. 기존 시각·ID/실제순간 순서·ACK 전 게시금지·STOP/FILL 중복/복구 계약을 보존하며 별도 현재시계·RAM복제·DB조회는 추가하지 않았다. 전체Shadow/strictPIT/실제NAS 수락과 구분한다. [현재 근거](app-target-20261023.md#1155-ar07-입력진행bootstrap프레임복구-소유권의-통합과-검증--2026-10-27).

**2026-10-27 구현·검증 국소 갱신:** MAIN127 bootstrap membership/기존 live arrival 계약을 보존하고 MAIN129 공유 market frame 기본순서를 실제 순간으로 통합했다. F11/F13의 같은 adapter를 쓰는 bulk native reader·역사문맥과 singleton live receipt/소비·종료를 관련151 범위에서 확인했다. 원 payload/시각/ID·native 저장/주문 owner 변경0, 새 시계·DB 호출·중복 sort0이다. 전체PIT/F12전체안전·NAS/운영 수락은 별도다. [소스별 근거·남은 조건](app-target-20261023.md#time-audit-followup).

**AR09/07 국소 계약(2026-10-26):** native 수신/RAM/writer/재시도·종료는 기존 owner, optional input/flush/legacy 관측 상태는 명시 lifecycle recorder다. 후보·명세 게시2곳은 기존 native 합류 도구를 재사용하며 주문권한을 생성하지 않는다. 동일본문526·EOF정리후11 PASS/guard34·전체 도구부재/PG/이전·기존 UNKNOWN/운영은 구분한다. [현재 근거·잔여](app-target-20261023.md#1146-ar09-collector-관측-소유권과-ar07-게시-수명--2026-10-26).

**AR v1 20차 계약.** AR02 v1행의Session/공개allocation/독립fence·saved/applied/실패·close는로컬종결한다. nativeRisk/control/실제계산/trustedartifact·다른AR완료를대신하지않는다. [현재판본/검증범위](../../data/diagnostics/ar-v1-wave20-20261024/verification.json).

**AR v1 16차 계약.** 상태없는selection_config가schema/metadata/정상검증을단일소유한다. Session은saved/applied/error/fence/ACK/Runtime수명,Monitor는원compute다. namedkernel부재의부분shape는원문보존·안전표시용이며full검증/활성화를대신하지않는다. [현재국소검증](../../data/diagnostics/ar-v1-wave16-20261024/verification.json).

**AR v1 15차 계약.** 테마native accepted현재snapshot과과거ID read를구별한다. 실제current projection/cut/firstreceipt를보존하고연구/판단구현과형식만공유한다. [소스별국소검증/미해결](../../data/diagnostics/ar-v1-wave15-20261024/verification.json).

**AR v1 14차 계약.** strategy_contract가config/DTO/metadata/parse단일값을소유하고기존full registry가원evaluator를직접binding한다. market-theme/research_factors eager compute E-C3는잔여이며전체선택engine부재수락아님. [현재국소근거](../../data/diagnostics/ar-v1-wave14-20261024/verification.json).

**AR v1 13차 계약.** query는판단producer를만들지않고기존시장/테마자료만소비한다. 깊은실행공통data/Shadow선택수명과전체역할수락은별도잔여다. [현재국소검증](../../data/diagnostics/ar-v1-wave13-20261024/verification.json).

**AR v1 12차 계약.** 공통시장/테마조회는 read권한이고 판단/실행/전략작업은 admin이다. 깊은판단데이터와 실제headless execution은 잔여다. [현재국소근거](../../data/diagnostics/ar-v1-wave12-20261024/verification.json).

**AR v1 11차 계약.** 기존Collector/동일시계/입력/native저장/acceptedreader를유지하며CandidateMonitor파일없는공통기동을확인했다. 깊은설정DTO/판단혼합의공통import는잔여이며알고리즘복제/새Collector가없다. [현재국소근거](../../data/diagnostics/ar-v1-wave11-20261024/verification.json).

**AR v1 9차 계약.** 정상caller의Collector private3종 의존을 공개관측·명시시계로제거한다. 공통input/source/phase·계좌Risk/UNKNOWN·주문권한은유지하며 새owner/cache/worker를추가하지 않는다. [현재소스736근거](../../data/diagnostics/ar-v1-wave9-20261024/verification.json). 최신단계/잔여는정본0절이다.

**AR v1 8차 계약.** PC entry 상태조정은 session으로 이전하되 순수 판단/자료부족·계좌/Risk/실행 owner는 유지한다. 처리 위치/판단기 교체와 실제 역할 독립 수락은 아직 남아 있다. [소스별 검증](../../data/diagnostics/ar-v1-wave8-20261024/verification.json); 현재 판정은 정본0절만 참조한다.

**AR v1 5차 간접 보존.** 테마current3자료는 단일 native snapshot으로 PC편집/표시에 전달한다. history accepted-head를 current로 채택하지 않고 기존 Shadow 원자료/판단·generation 게시 의미를 바꾸지 않았다. current조회 검증을 Shadow 전체/운영수락으로 확대하지 않는다.

**AR v1 4차 당시 계약(최신5차와 구분).** MinuteStrategyInputs/LeaderTradeInputs가 bounded minute/실제0B 입력만 소유한다. 원래 순수 kernel·현재 계좌/Risk·lease·FILL/보호/dispatch/저장 분기 순서는 유지한다. [현재 근거/미확인](app-target-20261023.md#1111-4차-구현--실제-상태workflow-소유권과-불변-수락).

**AR v1 3차 구현 계약(현재 통합 검증 중).** Theme native generation은 explicit publication_id/expected predecessor로 history+세 projection을 원자 게시하고 첫 receipt를 보존한다. 아직 API/실제 theme sync에 연결되지 않은 명령이며 Runtime/Risk 값을 새로 캐시하거나 freshness 조회를 생략하지 않는다. 선택 Session-Runtime post-prepare identity/revision 확인은 유지한다. [근거/현재 잔여](app-target-20261023.md#1110-3차-진행--승인된-ar의-연속-실행).

**AR v1 2차 F13 계약.** Session→Runtime.prepare_allocation_models→기존 decoder/native drain→Session revision/identity/closing 재검증→Runtime.offer로 게시한다. Session의 private allocation 접근은0이며 직접 Runtime 준비도 close/cancel 동안 실제 native 종료까지 소유한다. malformed 중첩 객체는 저장 전에 거절, 정상 v1/v2·중복/만료/generation/0B·봉 참고/주문비승인은 유지한다. 내부 단일 stage/cause slot은 공개 receipt에 원문을 복사하지 않고 성공/교체/close에 해제한다. selection/shared fence·다른 bootstrap 경계는 잔여다. [현재 국소 검증·남은 조건](app-target-20261023.md#119-2차-구현--allocation-공개-계약오류writer-source실제-process-exit).

**AR v1 1차 F13 계약.** route→RamShadowSelectionSession.update/check/publish→기존 MarketThemeShadowRuntime→RAM/native store·관측 callback으로 연결한다. 선택·revision·saved/applied/error/account/교체/close 상태는 session 하나가 소유하고 root/router의 옛 closure 전이는 제거했다. native CAS/ACK 손실 settle과 같은 revision 재시도·기존 shared mutation fence·Runtime 계산/slot/drain은 유지한다. 실제 SQLite6입력 결과·DB 호출 수 동일, [현 소스 회귀/후속](app-target-20261023.md#118-1차-구현--ci-원인-보완ar02-선택-상태). allocation 공개 계약·오류 shape·fence 분리와 전체 Shadow/운영 수락은 미완료다.

**이전 종료 보완 계약(PC6 보강·NAS 범위 정정 전, 당시 로컬).** TOP20→기존0B listener/RAM→Candidate·시장/테마 evaluator→기록/조회는 보존했다. 연구 봉 해상도와0B 현재 관측 역할을 유지하며 조건검색의 대체 전략/새 gate를 만들지 않았다. [근거](../../data/diagnostics/r3-050-final-20261023/verification.json).

**이전 집중 검증·이관 계약(당시 2026-10-23, 로컬).** 다음0B/Shadow 기능은 기존 입력/evaluator/RAM·기록·복구/owned drain을 재사용한다. 사용자 미정 전략·해상도/창/조건을 발명하지 않고 Shadow gateway0·가상FILL/실포지션0을 보존한다. [검증](../../data/diagnostics/r3-closeout-20261023/verification.json).

**R3-010 native Shadow.** 기존 저장 owner가 INSERT-first/충돌 후 canonical 비교와 decision/candidate 쌍 rollback을 소유한다. 신규 저장의 중복 SELECT를 생략하고 동일 retry의 첫 monitor/expiry를 유지한다. generic 문서는 기존 read-only duplicate를 위해 SELECT-first이므로 두 정책을 기계적으로 통합하지 않는다. [기존 CandidateMonitor 검사](../../tests/unit/test_candidate_monitor.py)의 native2store/SQLite·PG SQL fixture를 재사용하며 실제PG/모든Family·장중 연결 수락은 별도다.

**005 기준선 입력/비용 계약.** 승인0B callback은 RAM에만 입력하고 기존 monitor worker가 정책 prior window를 조회한다. query end=cut.floor, start=max(end-window,현재KSTday00:00)이며 기존 cache (cut-2초).floor/commit token은 별도로 유지한다. 빈 교집합/미설정/subminute는 read0, full-day API 기본값·native authority/원본은 그대로다. native metadata는 기존 codec에서 completeness/value_kind/source/원래available_at(epoch)4필드로 복사해 순수helper에 인계한다. flat/research 입력·미확인 metadata/미래 availability 의미와 source/cut/stop·pending·주문불가를 유지한다. 같은 정규화 full-day 대비 reference/observation은 동일하나 기존 raw native UNAVAILABLE→COMPLETE는 명시 연결 복구다. 기존 candidate/leader/nativeDB/0B·QueryStore 검사와004의 raw native 비용 회귀를 재사용하고 실제PG/장중 수락은 구분한다.

**현재 책임.** `research_families`는 지원 Family/config/evaluator의 등록 경계다. 순수 evaluator는 제공된 입력과 상태로 판단하며 직접 수집·DB·UI·주문을 소유하지 않는다. CandidateMonitor는 observation cursor/page와 후보 평가·checkpoint를, 0B 후보 경로는 실제 RAM trade 입력을, MarketThemeShadowRuntime은 공통 관측 선택·dispatch·결과 RAM/선별 기록·조회 수명을 소유한다.

**상태·실패·공식 경계.** Family registry/evaluator와 기존 monitor의 page/run 메서드, RAM runtime의 게시/조회 경계를 쓴다. 같은 관측의 불변 frame 변환은 재사용하되 정책 판단·cursor·실패/pending·저장 ACK를 합치지 않는다. 결과는 native Shadow/checkpoint 저장소와 API/관찰창으로 전달한다. Runtime이 가진 작업·lock·drain을 재사용하며 UI 조회 때문에 재수집/재실행하거나 결과 TTL을 연장하지 않는다.

**미해결.** R3-018의 evaluator 공유 필요성과 내부 응집 평가는 별개다. R3-024의 producer/capability 미연결, 047/048의 Runner/checkpoint 책임은 남는다. helper·fixture가 있어도 일반 연구 규칙의 자동 live 연결이나 미정 전략의 완성을 뜻하지 않는다.

**관련 검사.** [CandidateMonitor](../../tests/unit/test_candidate_monitor.py), [완료봉 판단](../../tests/unit/test_breakout_strategy.py), [0B 주도주 판단](../../tests/unit/test_leader_breakout.py). 실제 설정 선택 → 입력/cursor → 판단 → 저장/조회 경계와 미래/부분 입력을 확인한다.

<a id="execution"></a>
## 계좌·Risk·Execution — F14/F16/F17/F18

**S12-03 계획 주문.** 새 계획은 명시적으로 선택한 모의 실행에서만 기존 승인·Risk·control·lease와 함께 수락한다. 계좌 owner의 native 주문 저장에 예약과 계획 판본을 묶고, 주문 원장의 상세 FILL만 해당 계획에 반영한다. 전송 전 취소·만료와 미확정 제출의 대사를 분리한다. [정본](app-target-20261023.md#section12-native-planned-orders) · [범위와 검증](section12-development-roadmap-20261105.md#s12-03-native-order-work).

**12절의 익일 보유 격리 검증(2026-11-05).** 기존 명시 익일 보유 정책에서 마감 뒤 Runner와 lease를 닫고, 다음 합성 세션의 새 lease에서 이전 매수 FILL과 고정 보호 근거를 복구했다. 가짜 브로커의 청산 한 번과 매도 FILL 뒤 종결을 확인했다. 적용 범위는 기존 돌파 시험 명세의 보유 수명이다. [11.239](app-target-20261023.md#rss-and-section12-development).

**F16/F17/F18 실행 명세 337.** native gate는 기존 저장소 조회의 명세 원문을 검사한다. 원문이 달라지면 추가 조회·불변 저장·전송 없이 충돌을 반환한다. 최신 계좌 binding·Risk·STOP·control과 일반 읽기·복구 경계는 유지한다. SQLite와 해당 Windows 연결 검사 범위다. [337 근거](app-target-20261023.md#c24-canonical-execution-spec337).

**AR05-C11 현재선별NONE249(F14/F16/F17/F18/F24).** 사용자는옛앱자동매매를사용하지않아인계실행자료가없음을확정했다.선택execution/episode·두cursor/binding을반입하거나0으로만들지않고원DB/원문은보존한다.기존214실제query기동/재시작·166query/admin·order·account·credential·operations0·현재query조립/seed whitelist와MAIN119fresh역할경계를원소스범위로재사용해현재C11로컬종결을수락했다.기관계좌전체empty·현재주문/보유없음·새매매권한·전체인계/운영완료의뜻이아니다.향후실행활성화에는fresh계좌대조/단일owner·최신Risk·새승인/lease·명시재개를계속요구한다.다른저장복구/Drive/target/T0는별도미완료다. [249 근거](../../tmp/ar05-execution-none249-20261102/ROOT_SELECTION_AND_ACCEPTANCE249.json)·[현재 정본](app-target-20261023.md#0-현재-기준과-진행-현황).

**C24 source182 현재 연결(F14/F16/F17/F18/F23, 미통과):** 원3/5/180초/fixture로 Windows PG1은3003.3519ms 첫Timeout/1ERROR·0PASS, 실제정리COMPLETE·실패TEMP보존이다. 실제client binary3.3.6/libpq18.4·phase9898/errors0과 늦은expired-or-closing guard를 확인했다. 원verifier PID재사용 INCOMPLETE는 보존했고410연결의session 대응은 별도오프라인증거다. 미세CPU해석/OS/TLS 내부 원인은UNKNOWN이며 해당guard의하위조건도미수집이다. source182 stage 변경은 이자동운용·freshRisk/control/최종claim/취소·종료 경로를 바꾸지 않았다. source182 동일Windows client/원3/5/180초의 WSL PG17.11 비교1은 PASS·첫wait1756.4894ms/실제정리·관측완료다. 원주문 관리·freshRisk/control·최종claim·취소/종료 경로는 유지했지만 서버파일시스템/자원 차이가 있어 OS단일 원인은 미확정이다. 원WinPG오류/누적gate/CI 미수락을 대체하지 않는다. [환경비교1](../../tmp/ar-v1-wave58-20261101/nas-source-stage-integration182/c24-environment-comparison182.json). [원오류/정리](../../tmp/ar-v1-wave58-20261101/c24-connect-observation181/acceptance.json)·[별도원인자료](../../tmp/ar08-c24-connect-phases181-postrun-analysis-20261101-evidence001/HANDOFF.md).

**C24 추가귀속/후보(F14/F16/F17/F18/F23, 미통합):** source175의원exact1에기존wrapper를재사용한Episode async3표식만추가해앞구간을1회확인했다. 첫3초wait는3002.291ms ERROR1/0PASS이며관측/상관/driver오류0·Job/tree/UUID/failedTEMP보존·DB비강제drop/status3/port·cluster/password정리완료다. wait→gateway2293.876ms의nonpoll55연결은first advance3·독립monitor11·second advance직접2·producer13·poll26으로귀속됐다. producer493.150ms와gateway전poll1089.401ms를겹치는monitor시간과중복합산하지않는다. 이번deadline snapshot은claim이후native intent조회대기이며늦은ValueError/기존catch의bool반환을성공으로세지않는다. [실제실행](../../tmp/ar08-c24-pregateway-attribution175-20261101-c8f0427d/results/direct.json)·[정리](../../tmp/ar08-c24-pregateway-attribution175-20261101-c8f0427d/post-run-cleanup.json)·[귀속독립검토](../../tmp/ar08-c24-pg-span-packet-review175-20261101-review001/postrun002/REPORT.md). 현재spec/admission/lease의연속3읽기만기존read_scope로묶는후보를준비중이다. scope는native write/ACK·runtime guard 전에닫고freshRisk/STOP/control/lease/최종claim/재검증·원3/5/180초를유지한다. 제품미통합/전후비용·관련회귀미실행이며전체3초통과나원인종결을선언하지않는다.

**MAIN175 Windows PG 연결 검증(F14/F16/F17/F18/F23):** source175/원exact1을 Windows 격리PG에서1회 실행해 같은 첫3초 wait가3003.198ms에ERROR1/0PASS였다. 원본문·단언·3/5/180초·입력·fault는 유지했고 source/guard/ledger/이전자료는 불변이다. 유효한 parent/task 연결로 해당cancel→refresh→recover→reader를 확인했으며 refresh는wait+2926.184ms에정상반환했다. 실패직전에는같은cancel task가execution_runtime:437의native claim_runtime 연결을기다렸고그연결은+3055.395ms에정상반환했다. 최종gateway ValueError는원Timeout뒤에관측돼그타입만으로선행원인·발생본문을확정하지않는다. 원171의refresh대기snapshot과이번단계는구분한다. 관측오류/INCOMPLETE0, 정확ID1·Job/tree/소유UUID·failedTEMP보존·DB비강제drop/status3/port·cluster/password정리완료다. 대기단계귀속만확인됐고전체지연원인·안전하게제거할호출은미확정이므로제품수정0/원인UNKNOWN·C24부분을유지한다. [현재실행·상관·정리](../../tmp/ar08-c24-windows-pg3s-instrument175-20261101-f2cb6329/root-exact1-receipt175.json).  [시간분해](../../tmp/ar08-c24-windows-pg3s-span-analysis175-20261101-analysis001/REPORT.md)·[독립검토](../../tmp/ar08-c24-pg-span-packet-review175-20261101-review001/postrun001/REPORT.md). 같은실행의맹목반복·시한완화·Linux/SQLite성공대체·누적gate/CI/운영수락은하지않는다. 실제계좌/키교체/외부주문0이며기존단일계좌owner·freshRisk/control/lease·native claim/복구·명시신규진입재개계약을완화하지않았다. 취소전caller의기존본문·관측자료추가대조도마쳤다. 동일ACK checkpoint는이미no-op이며, 나머지root/expected·ACK전후Risk/control/FILL조회는동일identity/판본·안전한재사용이입증되지않았다. [pre-gateway 범위/유지근거](../../tmp/ar08-c24-pregateway-source175-20261101-analysis001/REPORT.md). 특정호출삭제·추가native/벤치0이며맹목재실행하지않는다.

**현재 AR07-C20 정책(F14/F16/F17/F18·관련F23/F24, 2026-10-30):** 사용자는동일계좌키교체시새키의계좌확인·구주문/잔고대사후관리인계,신규진입은명시재개로확정했다. 아직구현/검증/실계좌수락은아니며다른계좌거절·단일주문주체·미확정UNKNOWN/원spec·승인/복구보존을유지한다. AUTO runID의UUID혼용거절을안전gate로보지않는다. source165설치server4는owner교체·실패복원·취소종료3PASS/콘텐츠baseline1FAIL이므로해당3경계만근거이며전체C17/085수락은아니다. [정책/제한](../../tmp/ar-v1-wave52-20261029/integration165/AR07-C20-user-policy-decision-20261030.json)·[첫설치서버결과](../../tmp/ar-v1-wave52-20261029/c17-server-source165/native-server4-source165-run001/child-result.json).

**MAIN149 F18 보존:** 실제 entry 저장 ACK 손실 후 pending/cache_failed 동안 observer close0, 같은 payload retry·actualwriter.wait0True/pendingFalse 뒤 close1을 현재120에서 확인했다. row1/save3/provider1/news1·원scope/원자료를 보존하며 source release를 저장 ACK나 주문 권한으로 대체하지 않는다. 계좌/Risk/Execution/085 정책은 변경0이다. [native/C 절단면](app-target-20261023.md#main149-rest-main-lifetime).

**MAIN146 F18 보완source 경계:** EntrySnapshotWriter의source receipt는scalar/backfill3 loader의실제해제만뜻한다. 기존원본/보완native transaction·ACK-loss재시도·pending0/실제thread종료후close계약은그대로이며현재caller8/writer11등관련29에서회귀확인했다. receipt를저장확인/계좌권한이나client close권한으로대체하지않는다. [A국소/B·C미연결](app-target-20261023.md#main146-source-release).

**MAIN137 소유권·MAIN140 설치 연결 계약(F14/F16/F17/F18·간접F23/F24):** MockAutomationEpisode가 업무 상태57개·native 권한/보호/시계/결정/실제명령합류와 Position/InputProgress/Checkpoint를 소유한다. concrete Runner는 Task/loop/event/listener·attach/detach·실제취소/Task합류, Supervisor는 map/조립/현재게시/close registry만 소유한다. 업무 소비자는 `runner.episode`를 사용하며 옛 Runner 업무 facade/중복상태는 없다. 원 Risk/control/lease/FILL·durable-before-QUEUED·원시각/hash·checkpoint no-op/ACK/재시작과 두 close분기는 유지한다. 원136유효399+새137의14=고유413 및 원136실제PCPG5를 국소수락했고 최초412PASS/1FAIL·출처준비오류/실패TEMP는 보존했다. 후속140의 새wheel414/physical40/ROLE6환경쌍도PASS다. 이는 조회 역할의 업무 패키지 부재 연결 검증이며 전체PIT/source/T0/085·실NAS/계좌인계/운영은 미수락이다. [소유권 근거](app-target-20261023.md#1159-main137-runner-업무실행-수명-국소-검증--2026-10-27)·[설치·현재 잔여](app-target-20261023.md#1160-main140-뉴스출처저장operator설치-국소-검증--2026-10-27).

**MAIN134 당시 ACK 소유권 갱신:** Position은FILL/position/fence, InputProgress는cursor/bootstrap/protocol/recovery, MockAutomationCheckpoint는aggregate revision/pending/acknowledged-content와실제native upsert/ACK 전이를소유한다. Runner는현상태projection/보호override와전체분기/drain·native권한을조합한다. 옛Runner ACK3alias제거,같은18before/after·unit357/PCPG5 PASS·native정리확인. nativeRisk/control/lease/계좌권한·원주문·durable-before-QUEUED·시계/STOP/close순서불변, no-op0/force1/changed2·native2write 각SQL1/COMMIT1 유지다. protection/sessionclock/fixedDecision/전체episode·계좌인계085/실제NAS/운영은아직미완료다. [현재근거·잔여](app-target-20261023.md#1157-main134-통합-검증--2026-10-27).

**MAIN131 당시 계좌·실행 연결 근거(이력; 현재 ACK owner는 위 MAIN134):** MockAutomationEpisodePosition의 actual FILL/StrategyState/최초opened_at·fill cursor/seen·publication fence 소유권은 유지하고, 입력진행 다섯 상태와 실제 bootstrap/repair·ordered restore/projection은 MockAutomationInputProgress가 단독 소유한다. 새소스 unit388(core360+공개Supervisor28)/실제PCPG10·native39 종료/정리를 확인했다. protection notice/그 최초시각·session clock·native authority/전체 checkpoint ACK는 Runner에 남고 최신Risk/control·원주문/lease/계좌권한·STOP 순서를 보존한다. 기존 MAIN129 unit360(328신규+32재사용)/PG10은 그 당시 소스 이력이다. episode 전체 책임이전/085·계좌 인계·NAS/운영은 미완료다. [현재 국소 수락·잔여](app-target-20261023.md#1155-ar07-입력진행bootstrap프레임복구-소유권의-통합과-검증--2026-10-27).

**2026-10-27 시간 감사 보충(수정·새 PASS 아님):** F23→F18 계좌 설정/복구 outer updated_at은 OS epoch 소비와 native revision/lease 의미를 별도로 확인한다. FILL/원주문·계좌 snapshot/권한·최신Risk·복구·종료 계약은 유지하며 metadata 시계만으로 F14/F16/F17의 안전 실패를 확정하지 않는다. [담당 AR·우선순위·완료 기준](app-target-20261023.md#time-audit-followup).

**AR09/07 국소 계약(2026-10-26):** native 수신/RAM/writer/재시도·종료는 기존 owner, optional input/flush/legacy 관측 상태는 명시 lifecycle recorder다. 후보·명세 게시2곳은 기존 native 합류 도구를 재사용하며 주문권한을 생성하지 않는다. 동일본문526·EOF정리후11 PASS/guard34·전체 도구부재/PG/이전·기존 UNKNOWN/운영은 구분한다. [현재 근거·잔여](app-target-20261023.md#1146-ar09-collector-관측-소유권과-ar07-게시-수명--2026-10-26).

**AR v1 20차 계약.** 전용결과grant는결과namespace의제출/자기조회만갖고query/admin/계좌비밀/실제주문권한을포함하지않는다. AR02로컬종결과AUTO/원주문/키교체·단일실행인계정책미수락을분리한다. [현재판본/검증범위](../../data/diagnostics/ar-v1-wave20-20261024/verification.json).

**AR v1 16차 계약.** engine부재는선택account관측/새Runtime을활성화하지않고원계좌/FILL·Risk/control/lease/native owner와ACK복구·실행권한을유지한다. existingstudy실재계산/strict승인을일반reported-result저장receipt와혼용하지않는다. [현재국소검증](../../data/diagnostics/ar-v1-wave16-20261024/verification.json).

**AR v1 14차 계약.** 원native계좌/FILL·Risk/control·lease·미체결/ACK복구owner와storedpackage/policy/receipt/pending원문은유지한다. 새20rawsource는명시판본이며oldhash fallback/재서명없다. [현재국소근거](../../data/diagnostics/ar-v1-wave14-20261024/verification.json).

**AR v1 13차 계약.** query파일은full/admin파일과분리되고계좌비밀/주문권한을취득하지않는다. 기존native FILL/lease/control/미체결복구owner는바꾸지않았고원HTTPoracle의누락/오염거절을유지한다. [현재국소검증](../../data/diagnostics/ar-v1-wave13-20261024/verification.json).

**AR v1 12차 계약.** query authority에는 계좌/주문/실행/설정/lease 권한을 주지 않는다. 실제 account/order owner·FILL/Risk/control/native안전은 변경0이며 실제 다중PC 유일성/인계는 검증하지 않았다. [현재국소근거](../../data/diagnostics/ar-v1-wave12-20261024/verification.json).

**AR v1 11차 계약.** Supervisor는기존계좌/Bundle/준비/교체/close owner,Runner는실제생성후기존판단/체결상태owner다. 공개native ACK/UNKNOWN·Risk/control/lease는불변이고재시작은seed없는별도process조회로보강했다. 분산주문유일성/인계는미검증이다. [현재국소근거](../../data/diagnostics/ar-v1-wave11-20261024/verification.json).

**AR v1 9차 계약.** Runtime의시계공급만기존server_now로명시화하며 계좌/freshRisk/FILL/native authority·UNKNOWN복구는기존owner가유지한다. query 토큰/WS정보범위와같은계좌다중PC권한수락은아직미완료다. [현재소스736근거](../../data/diagnostics/ar-v1-wave9-20261024/verification.json). 최신단계/잔여는정본0절이다.

**AR v1 7차 간접보존/교체계약.** 공통HTTP/Broker 관측과candidate artifact owner이전에서계좌/FILL·freshRisk/control·native권한/UNKNOWN·주문저장계약은보존했다. 전략/연구교체나비활성화가실제잔여주문·대사/복구를없애지않으며 판단/관측ID는실행권한이아니다. AUTO085/운영수락은보류다.

**AR v1 6차 현재 국소 계약.** Runner prepare owner가 runner 원문/clock 검증을 decoder·bootstrap 앞에서 한 번 소유한다. invalid clock/read failure는 준비실패·원예외로 남고 부수 checkpoint 저장0, 기존 FILL/Risk/control·권한/주문·ACK/복구/close 계약을 유지한다. 실제 Supervisor→native read 반복취소/close 합류와 현재H 관련497PASS·정적3종을 확인했다. 두 읽기의 원자 snapshot/전체실계좌·085 정책 수락은 아니다. [현재근거/일시정지](app-target-20261023.md#1114-6차-국소-검증과-일시정지--runner-논리복원격리-인계).

**AR v1 4차 당시 계약(최신5차와 구분).** 입력 owner 이전으로 현재 Risk/control 재조회·원주문/claim/FILL 권한을 캐시하거나 우회하지 않는다. legacy decode/재저장·checkpoint ACK와 STOP/실제 task drain은 관련 검증 중이며 AUTO/키교체 정책은 보류다. [현재 근거/미확인](app-target-20261023.md#1111-4차-구현--실제-상태workflow-소유권과-불변-수락).

**AR v1 3차 구현 계약(현재 통합 검증 중).** Runner 생성자는 native 읽기/listener 부착을 하지 않는다. 기존 Supervisor가 미게시 Runner를 먼저 소유하고 prepare_restore native 합류→attach→start→현재 게시를 연결한다. 철회·실패·취소·close는 같은 owner/close registry에 합류한다. FILL/cursor/unfilled/protection/업무시계/native checkpoint·R3-085 차단은 유지하며 실제 계좌 수락이 아니다. [근거/현재 잔여](app-target-20261023.md#1110-3차-진행--승인된-ar의-연속-실행).

**AR v1 2차 F14/F18 계약.** allocation 입력의 계좌 관측·Risk/기존 receipt·현재 generation/권한 검사는 기존 owner에 남는다. 원 exception chain은 Runtime 내부에만 보존하고 원문을 공개 HTTP/receipt에 새로 싣지 않는다. EntrySnapshotWriter는 source pair/cache generation과 실패복원·이미 관측한 pending job을 소유하며 controller 외부 재조립을 제거했다. 계좌/FILL·원주문/085 키정책/native transaction은 변경0이다. [현재 국소 검증·남은 조건](app-target-20261023.md#119-2차-구현--allocation-공개-계약오류writer-source실제-process-exit).

**AR v1 1차 관측 연결.** 선택 계좌 ref와 Runtime 무효화는 selection session, 계좌·FILL·주문 소유는 기존 Monitor/Runtime/Owner다. mock credential publish(bundle=None)는 session의 공개 account context 무효화로 연결하고 publish(bundle)는 선택 계좌 비교를 따른다. 최초 누락 NameError/활성화 FAILED를 계측·수정했고 기존 owner36을 포함해 현재 관련 검증을 통과했다. [실패·복구 및 미수락 범위](app-target-20261023.md#118-1차-구현--ci-원인-보완ar02-선택-상태). R3-085 AUTO/키교체 정책·Risk/control fresh 확인·원주문 안전은 변경0이다.

**공개 관측/owner 계약 국소 구현(2026-10-23, 미커밋).** 실제 app의 기존 position_account_source→Supervisor→Runner→Monitor 경로를 유지한다. Supervisor는 base fallback/등록 map/current/교체/close task, Runner는 공개 position_shadow_observation의 state/portfolio/policy DTO·monitor epoch를 소유한다. Caller의 is_current fence는 원래 copy 단계마다 평가한다. read-only account_ref/credential_profile_id/is_bound_to는 bundle identity만 투영하고 새 RAM 사본/lease 권한을 만들지 않는다. Supervisor private bundle13→0, 같은7입력 DTO/phase 동일·DB0→0; 현재149건(실제 app 교체/취소/종료28·Runner33·관측13·study32·owner36·metadata7) PASS. F14/F17/F24 직접·F16/F18/F23 간접 확인이며 전체047/050·085 성공 회전은 미완료다. 모든 기존 Runner gate/FILL/restore/checkpoint/close 메서드 AST 동일. [현재 검증](../../data/diagnostics/r3-runner-public-20261023/verification.json).



**이전 구조 제안(공개계약 구현 전).** Runner 공개 관측/owner 계약(A)에서 Supervisor는 map/current/close 수명, Runner는 전략 관측 조립, Monitor/Runtime은 계좌 cut·원주문/FILL·lease를 소유하도록 정리한다. 미체결5필드만 추출하면 등록/증거의 공유 lock 경계가 끊길 수 있어 그 분리는 미채택이다. 동일 계좌 키 교체(C)는 activation UUID와 execution run 소유를 구분하며 원 AUTO 주문의 대사/취소 담당·잔여주문 전환/재개 정책 확정 전 성공 회전을 수락하지 않는다. 다른 계좌 ACCOUNT_CHANGED는 유지한다. F 연결은 기존 부분/미검증 상태이며 [구조 후속](../../REFACTOR_BACKLOG.md#nas-구조-후속의-분류와-책임-이전안--2026-10-23)은 구현 완료가 아니다.

**이전 종료 보완 계약(PC6 보강·NAS 범위 정정 전, 당시 로컬).** R3-050의 기존28건은 시한·기대값·본문을 바꾸지 않고 현재 소스에서 전부 통과했다. 계좌 수명 fixture가 관계없는 공개 시장뉴스 수집/3개 분석 worker를 같은 SQLiteStore로 시작하는 것을 계측했고, 같은 모듈의 기존 HTTP helper와 동일하게 news_naver_market_enabled/news_history_jobs_enabled 두 플래그만 False로 고정했다. CAS/회복/Risk 진입의 관측값은 2.655/2.973/2.115초에서 0.823/0.853/0.723초로 줄었고 해당 관측의 뉴스 쓰기는 0이었다. thread dispatch 지연은 밀리초였으며 실제 save_news_source_page/RLock 경합을 관측했다. 최초3FAIL 순간의 유일 원인은 UNKNOWN으로 보존한다. 전체 뉴스서비스 I/O가 0이거나 운영 혼합부하 문제가 해결됐다는 뜻은 아니다. R3-050 제품 수정0이다. 관련28PASS/기존 안전 계약만 수락한다. 최종status/조립/085 AUTO 잔여주문 소유의 운영 미수락은 유지한다. [근거](../../data/diagnostics/r3-050-final-20261023/verification.json).

**이전 집중 검증·이관 계약(당시 2026-10-23, 로컬).** 050 STOP 수락과 앞source27/28의 최초회복markerFAIL·동일canonicalPASS/진단·원인미확정을 분리한다. 현재source AUTO resume control CAS/recovery record/risk read 수명 검사가 실패하여 해당 경로의 활성화 수락은 보류·부분차단한다. 이는 문서상 미수락 범위이며 새 제품gate/시한변경은 없다. 새R3/최적화 후보는 재개하지 않는다. constructor/listener/status·085 AUTO 잔여주문 대사/취소 소유 이행은17후속에 남는다. 079 관측종류 등록 보완은 실제 SQL/FILL/Risk/승인/STOP정책 변경이 아니다. [검증](../../data/diagnostics/r3-closeout-20261023/verification.json).

**이전 스물두번째 추가 계약(2026-10-23, 미커밋 당시).** 050 STOP 최종 query는 기존 drain_thread_call,공개 sync status와 같은 projection을 사용한다. lock-free bundle_changed의 실제 교체를 반영하며 query3→3/최종1→1·오류409·revision/receipt·gate false/no order를 보존한다. cancel3/lifespan 종료가 native 뒤 합류한다.049 단일합산 후보는82컷 동일이나 전체 CPU 이득 미확인으로 미채택;owner ACK RAM/FIFO/FILL 정책 변경0. activate/resume status·생성/listener 및 Runner.status CPU/lock는 잔여다. 근거 [통합 검증](../../data/diagnostics/r3-parallel-20261023/wave22/verification.json).

**이전 스물한번째 추가 계약(2026-10-23, 미커밋 당시).** 050 native 설정 반환까지 Supervisor/owner 잠금과 contexts 수명을 유지한다. B started/unpublished context는 native 종료 뒤 기존 close_services/owner.close가 정리한다. query/원 settings revision/STOP receipt/무주문과 gate false 유지, constructor/listener/status와 direct owner 동시 close는 미검증. 근거 [통합 검증](../../data/diagnostics/r3-parallel-20261023/wave21/verification.json).

**이전 스무번째 추가 계약(2026-10-23, 미커밋 당시).** 049 _recovery_lock 아래 동기 builder가 소유 history를 read-only로 소비한다.별도 position_study tuple/DB safety read·FIFO·revision·ACK→listener는 유지하고 N10000에서 중복참조80040B만 제거했다.050은 첫mode-switch read만drain,후속await/실계좌 주문수락은 별도다. 근거 [통합 검증](../../data/diagnostics/r3-parallel-20261023/wave20/verification.json).

**이전 열아홉번째 계약(2026-10-23, 미커밋 당시).** R3-050: Supervisor.resume의 남은 recovery-record와 실제 resume CAS 두 asyncio.to_thread await만 기존 drain_thread_call로 연결했다. 실제 HTTP STOP/settings/mode 전환→monitor 회복/Risk→immutable lease/typed decision→CAS→취소/종료 경로와 원래 native 본문·승인/UUID/Risk/order 정책을 보존한다. 최초 marker 실패2건의 내부 지연 원인 자체는 미확정으로 보존한다. post-body return barrier이므로 열린 native 연결/실SQL 지연이나 crash 복구 검증이 아니다. mode-switch 내부 await·constructor/listener/status 동기 조회, 나머지R047/048/085 경계·실제 정상 Runner/운영 resume 전체는 별도다. 이번 현재소스 최초3진입 marker 실패의 내부대기원인도 별도미확정이다. 단독계측에서 CASentry2.134/완료0.624·회복2.619·기존Risk1.784초에 표식통과했지만 최초실패를 재현/해결한 근거로 확대하지 않는다. root관련283/17모듈PASS, 원본/진단/신규CAS측정교정과실제운영 수락을분리한다.


**이전 열일곱번째 계약(2026-10-23, `066d2de1f71e52f1b3507a42e6a80153a092cd53`).** R3-050: resume의 load_latest_mock_automation_risk await 한 곳만 기존 drain_thread_call로 바꿔 accepted native 조회가 끝날 때까지 account lock/runtime/store 소유 수명을 유지한다. 실제 HTTP STOP→기존 settings off/on→MANUAL→resume의 switch/refresh 뒤 Risk typed loader 경계다. 승인 발급·Risk 판단·주문/085 정책은 변경하지 않는다. 후속 recovery 기록과 최종 실제 resume CAS 두 native await는 남는다. recovery fixture의 lease receipt 정당성과 accepted mutation/cancellation 계약을 별도로 판단한다. mode switch·constructor/listener/partial construction·status 동기 fresh read/실제 loop latency 및 R047/R048/R085는 별도다. 최초 Risk marker와 기존 activation marker 원인은 미확정으로 보존한다. root642/35PASS, WT·원본 진단과 운영 수락은 별도다.


**이전 열여섯번째 계약(2026-10-21, `7d0b8e108893c649e1e56e127eb06f29151779a5`).** R3-050: resume의 저장된 admission 조회 await 한 곳만 기존 drain_thread_call로 바꿨다. 기존 control/spec drain 뒤 원래 admission loader가 끝날 때까지 계좌 lock/runtime/store 수명을 유지한다. 승인 발급·Risk·주문·R3-085 소유 정책은 변경0이다. 실제 HTTP STOP→resume의 bound admission loader 직전 barrier에서 cancel3/close로 원본 수명 이탈1FAIL을 보존했다. 현재3모듈46고유ID PASS(100.136 worker초), 직접 새 canonical1PASS는 중복 합산하지 않았다. 원래 typed admission 조회1·spec/control/admission/STOP receipt/revision 동일·주문0·CancelledError와 종료 순서를 확인했다. 실제 열린 DB connection을 막는 대역이 아니라 원래 query 직전 경계다. 제품1줄·기존19검사 AST 보존/신규1이며 새 native query나 I/O 감소는 없다. QueryStore protocol/optional 실제 후보 밖이라 consumer baseline 변경0이다. 후속 resume Risk 조회/recovery/실제 resume native await3곳과 mode switch·constructor/listener/status 동기 I/O, 085 주문 소유는 남는다. 전체 자동매매·실계좌/PG driver/운영 종료 수락으로 확대하지 않는다. root279/13PASS와WT비용/원본대조는별도이며새owner/계층/schema0,전체R3진행유지·075/076기존완료보존.


**이전 열다섯번째 계약(2026-10-21, `7fe20f325d462242de3390c50d678c585e6f0161`).** R3-050: resume의 control 확인 다음 spec-read await 한 곳만 기존 drain_thread_call로 바꿨다. 반복 취소에도 native 본문이 끝날 때까지 기존 계좌lock/runtime/store 소유 수명을 유지한다. 후속 resume native await4곳, mode/constructor/listener/status 동기I/O 및085 주문소유는 그대로다. spec loader는현재QueryStore/optional 후보목록밖이어서 소비 identity·baseline 수정0, 새전달계층0. root369/18모듈과WT비용/원본대조는구분한다.075등록범위로컬완료,나머지R3전체/PG5/운영미검증은백로그를따른다.


**이전 열네번째 계약(2026-10-21, `fbbb96a0cdbab65d476dd352a90745ba30a1eae4`).** R3-050: resume의 첫 control 조회 await 한 곳만 기존 drain_thread_call로 바꿨다. 반복 취소에도 원래 native 조회가 끝날 때까지 계좌 잠금과 runtime/store 소유 수명을 유지한다. root448/25모듈은 통과했고 WT 비용·원본 대조는 별도다. 등록범위076만 완료, 나머지 잔여와 실제PG5/운영 미검증은 각 항목을 따른다.

**이전 열세번째 계약(2026-10-21, `b16c386d107b08218b2f97dc4d305f3d55ebc632`).** R3-047: await가 없는 같은 native cut 안에서 이미 계산한 judgment/judgment_at을 재사용한다. pre-await readiness 재검사는 유지한다. 전체 runner/입력/Risk 책임·다른 반복 계산은 잔여다. R3-048: 정확한 KrxMinuteBarFrame의 원자 필드만 fields/is로 복사하고 subclass/nested/slots/None 등 호환 경계는 기존 asdict에 위임한다. 최초 metaclass equality 우회는 독립검토에서 재현해 is로 수정했다. 정상 정확타입의 좁은 CPU 개선이며 fallback 비용증가를 수용한다. 전체 checkpoint 구성·저장 비용 제거/운영 개선율은 미검증이다. R3-049: position_cost_fifo BUY default의 버릴 Fraction(0) 생성만 정수0으로 바꿨다. 정확한 Fraction 결과/FIFO/부분청산·owned basis/UNKNOWN·ACK는 동일하다. 누적 history/과거정정·지연비용/날짜·부분읽기 전체 cut과 운영 빈도는 잔여다. R3-050: stop의 native await2곳만 기존 drain_thread_call로 바꿔 반복 취소에도 실제 native 완료까지 account lock과 소유 수명을 유지한다. STOPPED/control CAS/주문 gate/receipt·승인·085 식별자 정책은 동일하다. resume/mode-switch/constructor/listener/status 동기 I/O·다른 수명은 잔여다. root1301 PC관련검증과실제PG5/053진단실패는구분한다.

**2026-10-21 `7e4fb607ceb09479f6b9c9ed2c03cbf6cdc42bbb` 국소 계약 갱신.** R3-041: 운용 refresh/명령 후 상태 읽기에서 Shadow 전량조회를 제거하고 실제 게시 선택 때 기존 worker로 fresh candidate/binding/event를 검증한 뒤 review를 연다. ACK 뒤 조회 실패는 ACK와 독립 표시하고 stale 제어를 닫는다. worker/token/finished/close drain과 서버 승인·100페이지 제한을 유지한다. refresh2회 Shadow10페이지/10000행→0, 다른5조회각2동일. WT54+비용1 통과. 이미 열린 modal에서 stop 후 Accept 가능한 기존 잔여는 명시 보존, 실제 HTTP/SQL·운영 수락은 아니다. R3-047: offer_unfilled_execution_inputs에서 기존 직렬화된 Risk 문서를 재사용하는 JSON 변환 중복만 제거했다. snapshot deepcopy·동일입력/hash/계좌/run/시계/ACK/FILL/재시도/drain은 유지한다. 101알림 asdict707→606/Risk202→101/encoded4612bytes·hash동일, DB/checkpoint/주문0. WT108 통과. Runner의 입력/미체결/보호/시계/checkpoint 책임 전체는 잔여다. R3-049: _run_positions의 정수 net 합산 앞 sorted만 제거했다. exact-fill dedup/scope, FIFO의 시각순 정렬·owned basis의 접수순 정렬, 양수필터·공개정렬/Risk 저장ACK를 유지한다. 역순/mixed run/부분청산/oversell/음수·0 독립 oracle와267 WT 통과. 합성10000FILL fold7879→1516us/builder74771→65444us, I/O감소0. 누적 history 전체 filter·일별 비용/지연정정과 운영량 cut은 잔여다. R3-050: activate의 기존 native await6개만 기존 drain_thread_call로 바꿔 반복 취소에도 native 종료까지 같은 계좌lock을 유지한다. 원본은 작업 진행 중 owner closing/runtime retired를 재현했다. 신규 실제앱 취소 oracle 통과, 최초 관련55는52PASS/3ERROR, 동일 소스·원래 제한의 실패3개 개별 재실행은 통과했다. 이를 한 번의55 전체통과나 timeout 원인 해결로 보고하지 않는다. constructor/listener/status sync I/O, resume/stop·mode switch는 잔여다. 현재root1060/62모듈·원본비용/WT검사는구분한다.

**2026-10-20 `736fce8` 책임/검증 갱신.** 기존 owner callback을 sync/awaitable 겸용으로 기다리고 app 조립에서 Supervisor bundle_changed로 연결했다. 현재 Runner map 철회와 실제 shielded close 소유를 구분해 cancelled waiter/실패 drain 뒤에도 수명을 보존한다. activate/resume/close가 기존 계좌 lock·current bundle·closing fence를 확인하며 일반 종료 실패 시 기존 map/오류/checkpoint1을 유지한다. 원본 실제 앱은 교체 후 old Runner가 살아 있었고 현재 native drain/lease/복원 실패/닫기 경합을 검증했다. 새 승인·STOP/AUTO 승격 정책은 없으며 성공 AUTO credential 회전은085·일반 activate to_thread 취소는050 잔여다. 반복 조회 절감 수치나 실제 주문 안전 전체 수락을 주장하지 않는다.

**085 미해결 identity 경계(2026-10-20).** AUTO run_id와activation UUID가혼용되어키확인prepare가callback전에실패하는native관측을후속후보로등록했다. 실제키오류/금지정책으로단정하지않고기존UUID검증·승인·주문/STOP정책을보존한다. 042의bundle교체/drain검증과성공AUTOcredential회전수락은구분한다.

**2026-10-20 `964ddbe` 추가계약.** 기존최대원화낙폭을그대로두고같은순회에서relative numerator/peak 두정수로최대비율을따로추적한다. 정수교차곱/마지막ppm내림1회,기존fold/event/carry·양수peak없음/음수equity범위를유지한다. 100→50→200은250000→500000ppm,원화50동일;실제report생성→native불변저장/재조회→candidate→한도판정에서검증했다. Fraction독립655입력일치/원화655동일·120비율정정. 1000event곱셈1001→3001/나눗셈1유지·추가state2정수는정확성계산비용이다. 기존저장report/package는옛수치를보존하며자동rewrite/새차단정책/운영임계값·Risk·주문승인변경0이다.

**2026-10-20 `0a54c35` 추가계약.** R3-066 검증 완료: 기존 variadic drain을 모든context에 한번적용해 producer를멈추고 monitor/writer/read를실제합류한뒤에만 dependency를닫는다. closing예외조건을제거해 미저장pending/실패를정상종료로보고하지않는다. 이미끝난failed close에대한새명시close만 완료된noncancelled eventwriter로원본pending/queue를drain한다. closing/admission/binding/settings/order gate는닫힌채유지,자동retry/REST재조회/새queue없음. 실제native precommit/ACKloss 두실패+두event복구 save4/행2/dependency close0→1,추가REST0. waiter취소/동시close는같은살아있는owner를합류한다. native event readback/diagnostic archive까지검증했고 genericcontent route는이collection을노출하지않아실제일지/API end-to-end는확인하지못했다. processkill RAM유실·직접ownertask취소·socket자동복구는해결범위밖이다.

**2026-10-20 `8f3bade` 추가계약.** R3-040 유지·검증완료: 제품변경0. 첫read뒤기존offline unowned apply가native JSON의account/run을바꿀수있고reader가그JSON을소비하므로두번째최신scope read를제거하지않았다. frozen DTO는native불변성을보장하지않는다. 실제활성lease/독립두store의normal/state/account/run반례와둘째read제거음성대조2assertion실패로지속검사를보강했다. 정상/state는기존첫record반환·추가transport/refresh0,scope변경은거절한다. SELECT2/실행loop동기I/O는그대로이며제거완료나지연개선으로보고하지않는다. R3-009안전조회근거에연결하고042owner/047runner·legacyHTTP오류매핑은별도다.

**R3-010 per-key 저장 책임.** typed profile/report/spec/admission/gate/feedback 검증·Mapping 변환은 기존 ForwardEvaluationRepository, key별 불변 insert/equality/conflict·첫 receipt·transaction/commit ACK는 같은 native QueryStore가 소유한다. 기존 latest stage/binding/STOP/Risk·native claim의 안전 확인은 유지한다. stage의 current-stage 값 비교/append는 기존 native transaction에서 수행하고 binding은 기존 identity owner의 scoped latest 조회를 쓴다. 다중 문서 게시를 원자화했다고 보지 않는다. native 동시2store·ACK retry·기존 admission/Risk/Runner 소비 검사는 [forward report](../../tests/unit/test_forward_report.py), [specification](../../tests/unit/test_mock_automation_specification.py), 기존 Risk/Runner 모듈을 재사용한다. 실제 PG는 미검증이며 generic native SQLite commit 전후의 소유 child 강제종료는 후속 진단2건으로 확인했다. stage 자체/전원 차단 내구성으로 확대하지 않는다.

**현재 책임.** 기존 credential/runtime owner가 계좌 신원·binding/revision·선택과 account query/recovery의 수명을 소유한다. AccountMonitor는 계좌 사건/대사와 ACK된 Risk 관측을 만든다. Gateway는 인증된 명령·멱등 요청·owned command, ExecutionRuntime은 run/lease·operation lock·gate, OrderLifecycle은 QUEUED/UNKNOWN/취소/체결 전이를 소유한다. ExecutionRepository/native store는 원장·control/claim fence·transaction을 소유한다. Supervisor/Runner는 등록된 자동 모의운용과 보호의 수명/상태를 연결한다.

**상태·실패·공식 경계.** 기존 인증 order/account/control/publication API, typed OrderIntent/AccountSnapshot, runtime submit/cancel, Lifecycle reconcile와 native repository를 통과한다. 제출 전 durable claim, UNKNOWN 후 대사, 취소 ACK와 취소 확정의 구분을 유지한다. 실제 position은 상세 FILL로 갱신하고 BROKER_FILL_AGGREGATE의 증가량을 상세 체결로 위장하지 않는다. 포지션/배분/EV의 순수 계산은 같은 account/Risk cut과 기존 RAM 관측을 받아 결과를 내며 별도 계좌 조회·원장·주문 owner를 만들지 않는다.

**미해결.** R3-010의 등록 코드 개선은 로컬 검증 완료다. 보호 context 재조회는 동일 안전시점 중복이 입증되지 않아 유지하며 실제PG/운영 수락은 별도다. 040의두번째조회는안전성근거로유지검증했고066의미저장close등록경계는완료했다. 042 owner연결/049·050 Risk·비동기책임과실제운영잔여는남는다. R3-024의 미지원 입력/정책은 그대로 남기며 연구 PASSED·Shadow 결과·목표 수량을 실행 승인으로 승격하지 않는다. 보호 Risk와 native 주문 경계를 일반 EV 대기로 대체하지 않는다.

**관련 검사.** [OrderLifecycle](../../tests/unit/test_order_lifecycle.py), [ExecutionRepository](../../tests/unit/test_execution_repository.py), [자동 Risk](../../tests/unit/test_mock_automation_risk.py), [Runner](../../tests/unit/test_mock_automation_runner.py). 계좌/세대·중복 명령·UNKNOWN·부분체결·STOP/교체·close의 실제 변경 경계를 검사한다.

<a id="research"></a>
## 연구 — F15/F21

**S12-01 거래일·상장 기준선(관련 F08/F11/F12).** 명시 해시의 거래일·상장 근거를 연구 입력에서 검증한다. 선택한 분봉 재생은 각 원래 가용시각의 달력 판본과 확인된 세션 창을 사용한다. 최신 가용 상장 근거에 불완전·충돌이 있으면 상태를 보존하며 종목의 실제 거래 가능 여부와 분리한다. 기존 연구 작업과 실시간 수신의 기본 경로를 유지한다. [근거](app-target-20261023.md#section12-input-baseline).

**연구 해시 자원 현재 소스 근거 401(F15/F21).** 기존 연구 모듈 19건으로 명세의 원시 자원 20개·독립 해시·패키지 원시 바이트와 자원 누락 거절을 확인했다. 실제 설치물의 연구 소비는 기존 판본 근거로 유지한다. AR08-C18의 별도 Windows 관리형 서버 기동은 402에서 확인했다. 405의 동일 작업본 표준 검사에서 연구 모듈 전체 19건과 패키징 3 ID를 확인했다. 과거 시세 누락 감사의 별도 표준 8 ID로 원시각·누락·해시·재구성의 구분도 확인했다. [405 표준 근거](app-target-20261023.md#current-research-and-data-standard-405) · [401 검증](app-target-20261023.md#c24-research-packaging-and-installed-boundary-401).

**준비 아카이브 현재 소스 근거 406(F15/F21).** 기존 9모듈의 공식 선택 검사 77건을 실행해 누적 판정이 요구한 아카이브 감사 5 ID를 확인했다. 해당 선택 실행은 연구·아카이브의 현재 로컬 검사 근거로 기록한다. [406 아카이브 근거](app-target-20261023.md#current-querystore-and-archive-standard-406).

**연구 패키징·아카이브 통합 판본 근거 410(F15/F21).** 연구 종목 분할 19건 중 패키징 3건과 준비 아카이브 9모듈 77건을 409 통합 소스에서 다시 확인했다. 이전 405·406과 소스별로 구분하며 이 결과의 범위는 로컬 표준·선택 실행이다. [410 판본 대조](app-target-20261023.md#c24-current-official-and-nas-freeze-410).

**입력·자식 경계277 현재(F21/F23/F24, 간접 F15):** 연구 input grant는 기존 서버 `ProducerGrant` registry가 소유하며 `research/observations`의 native fixed export/page와 `themes/history`만 허용한다. 결과 grant·주문/제어/credential·generic read는 기존 owner와 분리한다. `ResearchDialog`는 template별 명시 `nas_config_path`를 기존 repository에 저장하고 `discover_campaign_inputs`가 그 파일을 실제 읽어 준비·게시한다. 자식의 환경은 기존 process owner의 optional `env`로 전달하며 연구7 launch에서 알려진 불필요 권한22개만 제거한다. 기본 None 상속/숨김/우선순위/중지·kill/handle 소유는 유지한다. source277 실제 config→HTTP→native 게시/재열기14, source276 UI/실제 자식86은 소스별 별도 근거다. 기존 broad/operator 연결은 임시 명시 호환이며 최종 인계 전 실제 input grant/파일 전환을 확인해야 한다. 미지의 비밀·OS ACL 격리/전체 프로세스 NAS 실행/전략·운영은 수락하지 않는다. [현재 책임/검증/잔여](app-target-20261023.md#research-input-validation276).

**MAIN172 현재frozen연구소비(F15/F21, 원3case):** 실제B171의empty legacy성공/원연구재시작/명시profile자원소비3이outer5.073초PASS다. 원case/30assert/10함수/시한을유지하고actualcanonicalhash b384…/schema27·RO전후동일을확인했다. source162는불변fixture/과거결과출처이고현재ROOT172와제품420동일·실제EXE171을구분한다. 원/한글사본5000파일·도구/소스보존,Job/processHANDLE 자연종료·정리확인,원계약대로scratchDB/로그보존이다. C19 첫기술소비하위행동만종결하며AR05-C10/C11 실제선별자료/미완료상태·연구PIT/전략성능·전체모드/OS/업데이트·DB/외부ACK복구·운영은수락하지않는다. [원case실제소비/한계](../../tmp/ar-v1-wave54-20261030/nas-receipt172/acceptance172-frozen-consumers.json).

**현재 frozen 연구 소비(F15/F21, 간접 F01/F24):** 한글 경로의 실제 current EXE에서 원 legacy 정상/같은DB 재시작2와 기존 명시 profile canonical1을 수락했다. native schema27/run1/report1·원 legacy 전체상태/manifest 증가0, canonical raw20 실제 소비 hash를 확인했으며 원시각/입력/판본을 고치지 않았다. empty INELIGIBLE은 PIT/전략 유효성이나 전체 GUI/이전/업데이트·운영 수락이 아니다. [실제 연구3](../../tmp/ar-v1-wave51-20261029/integration162/acceptance162-c19-frozen-research3.json).

**MAIN162 독립 연구 설치(F15/F21):** 불필요 package-init→차트→Kiwoom REST 연결을 제거했고 REST/화면/뉴스 source가 물리적으로 없는 기존 -S headless 검사를 보강해 수락했다. 현재 core-only 설치물414py/raw20/정확4dist에서 실제 success/invalid/cancel/restart4 및 native schema27/run·report/immutable manifest·실제 종료/UUID/TEMP를 확인했다. 원 TradeTick·0B·분봉/과학 raw20·원시각/hash/판본·계좌/주문 권한은 변경0이다. 빈입력 결과는 INELIGIBLE이며 과학적 유효성/모든 입력·모드/다른 역할·원격·운영 수락은 아니다. [설치4](../../tmp/ar-v1-wave51-20261029/integration162/acceptance162-c17-research4.json).

**MAIN157 연구창 시작수명(F15/F21):** ResearchDialog의preview/restore예약은조립후게시된owner의명시startup에서만시작하고stop뒤이미예약된callback은기존suspended/stopping상태로차단한다. 현재새stop oracle와정확불변research47의기존준비/실행/재시작·선택소비를재사용했다. 연구계산/과학입력·원hash/시각·결과/승인·headlessCLI는변경0이며C12와설치/운영은별도다. [국소수락](../../tmp/ar-v1-wave51-20261029/integration157/acceptance157-c11.json).

**MAIN133 연구 준비·작업 수명 국소 갱신:** 기존 execute_research/ResourceGuard가 준비 취소·자원 cadence를 소유하고 native 등록/취소·실패 원 handler·process/final 수락은 기존 owner에 유지한다. 도달한 malformed 입력은 row0 거부, 조기 운영 오류/취소는 검증 미완료 CANCELLED/FAILED row1로 보존한다. source133의 관련40+동결8=48건 신규 PASS·native13 정리이며 같은9행의 사전취소 key9→0/중간 취소·자원9→3·native 연결3/저장status는 유지한다. legacy None의6 identity 필드와 원입력 불변, 새프로필 codehash는 실제 구현변경을 반영하며 native current-hash 수락/타hash 거부도 확인했다. 최초 신규검사 row형식 ERROR·정적 metadata 누락은 원문 보존 후 최소 보완했다. 전체PIT/AR07·08/large-N·C-sort/NAS·운영은 미수락이다. [소스별 수락·비용·잔여](app-target-20261023.md#1156-ar0708-정렬-준비의-취소자원-경계--2026-10-27).

**MAIN131 연구 입력·실행 경계 국소 갱신:** 입력진행/bootstrap/frame복구의 새 owner와 position owner 연결을 unit388/실제PCPG10에서 새로 검증했다(publication32도 재사용0). scientific raw20에 NAS Runner가 포함되지 않으므로 명시연구/legacy identity·원 입력·hash/판본을 바꾸지 않았고 freshRisk/주문권한도 유지한다. MAIN129/130의 시간·entry 근거는 각각 원소스로 보존한다. 이 MAIN131 수락 당시에는 정렬 V4가 TMP 후보였고, 현재 정렬 경계의 별도 통합/검증은 위 MAIN133 갱신을 따른다. 전체strictPIT·나머지episode/실계좌/운영은 미완료다. [현재근거·잔여](app-target-20261023.md#0-현재-기준과-진행-현황).

**2026-10-27 구현·검증 국소 갱신:** MAIN126/127 cursor/membership 경계의 원소스 근거를 유지하며 MAIN129에서 F15/F21 최종 시장요약이 실제 순간 정렬/cut을 소비한다. UTC/KST 혼합으로2분 앞선 cut과 성숙180s 누락을 실제 run/SQLite에서0→1로 보완했다. 관련151·동결4 PASS, 원 입력·시각 문자열/기존 판단건수8 유지와 nonempty None 전체 JSON/run ID/logical/codehash 동일을 확인했다. 새 프로필 raw scientific digest는 코드변경을 반영하며 옛 결과/해시를 재작성하지 않는다. 미래180s/PENDING/다른60s 필터는 별도 consumer 재현으로 확인했고 기존 기사strict기간가설1FAIL/strict=false 사후분석은 유지한다. 전체PIT·PG/운영과 기존AR07/08 sort 수명/자원 후속은 별도다. [소스별 근거·남은 조건](app-target-20261023.md#time-audit-followup).

**2026-10-27 시간 감사 보충(수정·새 PASS 아님):** F15/F21의 UTC/KST 실제 순간·문자열 순서·동시각 accepted prefix·늦은 판본/기사기간을 우선 검증하고 과거6건의 실제 dataset/run 참조와 결과 영향은 미확인으로 둔다. frozen replay/partition/as_of는 입력 시각으로 비교하며 현재 키움 clock이 필수인 기능과 구분한다. run 기록/lease/heartbeat의 시계 필요성은 실제 소비로 판단한다. canonical hash·원결과·명시 final/reconstructed 분석과 기존 불확실성 경계는 유지한다. [담당 AR·우선순위·완료 기준](app-target-20261023.md#time-audit-followup).

**AR09/07 국소 계약(2026-10-26):** native 수신/RAM/writer/재시도·종료는 기존 owner, optional input/flush/legacy 관측 상태는 명시 lifecycle recorder다. 후보·명세 게시2곳은 기존 native 합류 도구를 재사용하며 주문권한을 생성하지 않는다. 동일본문526·EOF정리후11 PASS/guard34·전체 도구부재/PG/이전·기존 UNKNOWN/운영은 구분한다. [현재 근거·잔여](app-target-20261023.md#1146-ar09-collector-관측-소유권과-ar07-게시-수명--2026-10-26).

**AR v1 20차 계약.** 연구계산/finish_run/원불변manifest owner와선택delivery target/첫ACK owner를분리한다. 정상POSTfullreceipt 뒤중복GET없음·불확실응답만ownGET; 명시pending/exit4·수동resend·no-flags기존0추가작업. 저장수락은과학적합격/nativejobCOMPLETE아님. [현재판본/검증범위](../../data/diagnostics/ar-v1-wave20-20261024/verification.json).

**AR v1 18차 P0 계약.** 원관측/revision/accepted sequence와source범위를보존한다. verifier는artifact구조/무결성만확인하고원source진실성·과학적합격·주문권한을부여하지않는다. [현재Y132/9·실CLI](../../data/diagnostics/ar-v1-wave18-20261024/verification.json).

**AR v1 17차 계약.** 간접 영향은 headless/child의 기존 explicit 경로·lease·종료 회귀다. 계산core/과학20/원 immutable 자료는 바꾸지 않는다. result-only API/생산workflow는 별도 격리 구현이며 이 수락에 포함하지 않는다. [현재 국소 근거](../../data/diagnostics/ar-v1-wave17-20261024/verification.json). actualwheel/선별이전/운영은 별도 미검증.

**AR v1 16차 계약.** recorded결과reader는공통selection metadata만소비하고원과거결과/typed config·scientific20 raw를유지한다. 실제compute없는strict입증은여전히불가이며kernel부재를정상과학검증으로표시하지않는다. [현재국소검증](../../data/diagnostics/ar-v1-wave16-20261024/verification.json).

**AR v1 15차 계약.** ThemeSnapshotSource의새currentprojection선택인자는기존defaultFalse/과거결과reader를보존한다. rawscientific20은T와동일하며currentreceipt와과거입력원본을혼용하지않는다. [소스별국소검증/미해결](../../data/diagnostics/ar-v1-wave15-20261024/verification.json).

**AR v1 14차 계약.** simulation_contract는실제순수비용/체결DTO값을소유하고Paper연산/task/nativewriter는기존owner다. old18와new20 ordered raw판본을구분해직접native정책/게시소비267건까지현재T로확인했다. [현재국소근거](../../data/diagnostics/ar-v1-wave14-20261024/verification.json).

**AR v1 11차 계약.** 선택판단producer없는공통server의비어있지않은기존native결과조회와중복/충돌/재시작을source별검증했다. 일반연구원격결과수락은별도잔여다. [현재국소근거](../../data/diagnostics/ar-v1-wave11-20261024/verification.json).

**AR v1 10차 계약.** 일반연구 -m 입력/결과/오류·취소는기존engine/workflow/native owner를재사용하고GUI/news backup을import하지않는다. 명시desktop child만기존StrictRestoreCoordinator의실제data-folder lease를보유한다. 새state/worker/저장계약이없다. 현재91검증과실제부재oracle는국소기동수락이며 frozen/UI수명/일반연구NAS결과수락·새설치/운영은잔여다. [근거](../../data/diagnostics/ar-v1-wave10-20261024/verification.json).

**AR v1 8차 계약.** candidate B2의 preview/review/baseline은 기존 src workflow/loader가 소유하고 CLI/UI는 공개 계약을 소비한다. scripts 역의존·반복 parsing을 제거하되 native 읽기·legacy identity/새판본을 보존했다. engine 부재·headless 독립/설치 수락은 미완료다. [소스별 검증](../../data/diagnostics/ar-v1-wave8-20261024/verification.json); 현재 판정은 정본0절만 참조한다.

**AR v1 7차 당시 계약/근거.** candidate artifact의실제7본문은research_candidate, CLI/UI는공식함수·기존public converter를소비한다. 원문/read-onlyDB/불변artifact·고정KST/옛UTC identity/canonical18보존, read/parse2/2→1/1. B2/실제설치·선별이전은잔여다. 판단/연구구현은교체가능하며실제Risk/주문권한을소유하지않는다. [교체기준](app-target-20261023.md#1116-자동매매연구-교체와-뉴스-처리-위치-경계).

**AR v1 6차 간접 보존.** Runner 복원 정리에서 frozen specification/position study·leader 입력 소비 회귀를 포함했다. 후보 workflow의 별도 격리365 결과를 현재main 결과에 합산하지 않는다. 과거 승인·lease의 자동권한 복원은 없고 새 기능/운영 활성화는 미수락이다.

**AR v1 4차 당시 계약(최신5차와 구분).** 실제 연구 실행/rank 비교/identity 본문은 src.research_run, 기존 CLI는 parsing/exit를 소유한다. 정확18 canonical label/order/raw bytes를 package resource로 읽으며 spec이 같은 source를 포함한다. 구형 hash/승인을 새 combined identity에 귀속하지 않는다. [현재 근거/미확인](app-target-20261023.md#1111-4차-구현--실제-상태workflow-소유권과-불변-수락).

**AR v1 3차 구현 계약(현재 통합 검증 중).** NAS 입력의 export/preparation 본문은 기존 research_data_source가 소유하고 CLI는 같은 함수 import adapter다. 새 현재 scientific hash와 옛 frozen 원문/read/실행 mismatch 거절을 구분한다. 실제 run/candidate workflow 및 child operation 자료 수명은 남아 있으며 새 자료/전략 정책을 만들지 않는다. [근거/현재 잔여](app-target-20261023.md#1110-3차-진행--승인된-ar의-연속-실행).

**AR v1 2차 F21 수명 계약.** 기존 보조 프로세스 stop의 kill 요청을 실제 종료 확인으로 마무리한다. native wait(None) 또는 원래 명시 timeout을 따르고 실패한 child handle은 보존한다. 연구/캠페인/final/비교·등록 창의 실제 manager 소비와 restart/자료 정리 회귀를 확인했으나 live clear/start의 operation·파일 전이는 후속으로 남는다. frozen 입력/결과/승인 hash·CLI 역의존 정리 전체는 미완료다. [현재 국소 검증·남은 조건](app-target-20261023.md#119-2차-구현--allocation-공개-계약오류writer-source실제-process-exit).

**이전 스물한번째 추가 계약(2026-10-23, 미커밋 당시).** 017 CLI는 보호 검사에서 읽은 정상 JSON object를 같은 resolved root의 기존 converter에서 소비한다. 정상4모드 read/parse2→1, 오류3경로의 loader·공개 standalone API·실제 native executor·입력 보존·atomic 결과 게시 유지. 새 캐시/worker/owner0. 근거 [통합 검증](../../data/diagnostics/r3-parallel-20261023/wave21/verification.json).

**2026-10-20 `964ddbe` 추가계약.** 기존 ResearchRepository start/finish/fail/cancel의 상태SELECT 앞에서 같은 native연결에 BEGIN IMMEDIATE를 사용한다. 7개실제두writer반례를기존순차상태/hash계약으로직렬화하며 API/owner/generation/시계/schema/결과publication정책은변경0이다. 정상write의연결/SELECT/BEGIN/COMMIT/반영/clock 각1유지, no-op write API는writer예약·BEGIN/COMMIT0→1,거절은BEGIN/ROLLBACK0→1(반영0/clock0). 필요한정합성비용이며latency절감주장0. nativeCOMMIT거절/ACK유실4메서드·resume/immutablehash/기존final·campaign·child를검증했다. 기존180초묶음timeout은0보고통과로보존하고동일소스를모듈분할하여전부재실행했다. 이미재개한뒤새로호출된옛generation fail/cancel을새fence로구분하거나실운영writer contention/전원복구까지해결한것은아니다. 기존최대원화낙폭을그대로두고같은순회에서relative numerator/peak 두정수로최대비율을따로추적한다. 정수교차곱/마지막ppm내림1회,기존fold/event/carry·양수peak없음/음수equity범위를유지한다. 100→50→200은250000→500000ppm,원화50동일;실제report생성→native불변저장/재조회→candidate→한도판정에서검증했다. Fraction독립655입력일치/원화655동일·120비율정정. 1000event곱셈1001→3001/나눗셈1유지·추가state2정수는정확성계산비용이다. 기존저장report/package는옛수치를보존하며자동rewrite/새차단정책/운영임계값·Risk·주문승인변경0이다.

**2026-10-20 `64a74e0` 계약 보완.** R3-046 검증 완료: 일반 research CLI의 request/result/cancel resolved alias를 parsing/오류 게시 전에 보호하고, 기존 request 경로 helper로 선언된 DB/dataset도 보존한다. 정상 독립 오류 envelope/atomic result·등록/leader/final/validation/comparison 기존 분기는 유지한다. 기존 보호계약을 재사용했고 새 path 계층/설정/시계/DB 호출은 없다. 원본20 subcase 실패→수정 후 통과; 실제 child4개 conflict/success/cancel/resume 종료2/0/2/0과 원본 bytes·결과 소비를 확인했다. 일반 GUI가 평소 충돌한다는 증거는 없고 전체 ResearchDialog 클릭·동시 파일교체 보안 경계의 수락으로 확대하지 않는다.

**현재 책임.** ResearchDialog/CLI가 명시 request와 입력을 선택하고 `research_process`가 child 실행·등록/claim·진행/중단·복구·결과 게시를 조정한다. 기존 연구 실행기는 Family 판단과 simulation 원장을, 평가기는 outcome/짝 비교/report를 소유한다. ResearchRepository는 동결 입력·run/lease/generation·결과/최종평가의 native 저장을 소유한다. 후보 package/specification 게시 이후에도 별도 admission·Risk·Execution을 통과한다.

**상태·실패·공식 경계.** 기존 request/typed split·dataset·Family registry, repository의 owned claim/finish 계약과 후보 게시 API를 사용한다. 실제 지원하는 봉 해상도·정책·입력 가용시점을 명시하고 분봉을 가짜 틱/초봉으로 바꾸지 않는다. 개발/최종평가·과거 완결 결과와 현재 가용 관측을 분리한다. retry/재시작은 원래 run identity·owner·결과 불변성을 보존하고 source/result 경로를 혼용하지 않는다. child는 stop/checkpoint와 실제 완료를 부모에게 전달한다.

**남은 연구 범위.** 일반 request=result 충돌046은로컬검증완료, terminal전이063/최대낙폭067도등록경계로컬검증완료다. 과거잘못된수치의불변보고서및일반API의옛generation새호출은잔여로구분한다. R3-024의 feedback/context/thesis producer 및 일반 연구→live 적용 범위는 미완료다. 수동 전략팩의 일지 사후분석을 이 Family/자동매매 경로와 합치지 않는다.

**관련 검사.** [연구 process](../../tests/unit/test_research_process.py), [연구 저장소](../../tests/unit/test_research_repository.py), [실행](../../tests/unit/test_research_execution.py), [평가](../../tests/unit/test_research_evaluation.py). 고정 입력/분할·소유권·중단/복원·원본/결과 보존과 후속 후보 소비를 확인한다.

<a id="news"></a>
## 뉴스·장전 — F19/F20

**F19 소비자 감사 337.** 자동 뉴스 작업이 유료 호출 직전 기존 저장 책임자에게 작업 시도·본문 판본을 확인하는 연결을 기준에 기록했다. 현재 테스트는 SQLite와 시험용 provider의 동일·늦은 시도 및 저장 응답 유실을 확인했다. [337 근거](app-target-20261023.md#c24-canonical-execution-spec337).

**현재 설치 조회·차트 연결(F01/F08/F18/F22/F23/F24/F25, MAIN165):** 동일source165 wheel의GUI9와선택기능40파일을실제로제거한독립374py사본에서원3시나리오를각각통과했다. 표20행·실제chart/가격·설정/열·선별raw/receipt·saveACK·실제query child·실패/재시작/정상종료계약과원GUI5959파일전후hash를확인했다. worker/tree·HKCU UUID·성공TEMP정리완료. 최초controller의PowerShell위치오류는보존하고미실행clone/pruned만계속했다. 조회역할수락이며C17전체/원Abort원인/NAS·운영수락은아니다. [현재6쌍/범위](../../tmp/ar-v1-wave52-20261029/integration165/acceptance165-c17-query6.json).

**현재 설치 뉴스GUI 수락(F01/F19/F23/F24/F25, MAIN165):** source165 wheel414/raw20과고정캐시만으로독립GUI9를설치하고실제뉴스전용창의fresh/503/restart3시나리오를수락했다. 데이터/IPC·한국어발행시각·설정/geometry·기존자료보존·정상종료/재기동을원시한으로확인했고12소유process/handles·HKCU UUID·성공TEMP가정리됐다. 최초controller의PID/token인자오류는보존하고수집/검사연결4줄만보완했다. paidprovider/NAS·전체C17·다른role설치/운영은미수락이다. [현재3수락/경계](../../tmp/ar-v1-wave52-20261029/integration165/acceptance165-c17-news-gui3.json).


**MAIN165 C06 연결(F19, 관련F22/F24/F25):** 실제UI표시/자동후보→유료호출전native입장→provider결과·immutable수락→own/적용가능shared/current→기본catalog동기화·PC표시를연결했다. 이전BODY판단/PREVIOUS·UNKNOWN은보존·표시하고새BODY유료재분석은명시요청만허용하며신규기사최초분석/RULE/prompt-only/명시restore는유지한다. 실제bound입력만기존정렬락/READ COMMITTED·fresh source조회로보호하고legacy unbound는불필요SET/lock/read3회를제거했다. 성공SQL3/COMMIT1·실패SQL2/ROLLBACK1의원기준을현재PG에서그대로통과했다. unit203은정확불변164수락,PG30조건은16429+현재1653의고유30(중복2)이며fresh233/PG30아니다. 원163/164실패·원자료/hash/판본/첫receipt·시한을보존했다. 기능연결은해당격리경로정상이며C17/실NAS·유료provider/다중PC/전체CI·운영은미검증이다. [수락/정확범위](../../tmp/ar-v1-wave52-20261029/integration165/acceptance165-c06.json).


**현재 설치 BODY 연결(F19, 간접 F01/F24/F22):** core-only 실제 producer6개/3흐름에서 seeded 401·native 작업0 및 같은 원문의 정상 CLI, 실제 HTTP→native 저장/공개조회, COMMIT 응답 손실의 정확 receipt·재계산/재POST 없음, 기존 재시도·native stop drain·재시작과 Job/UUID/TEMP 정리를 수락했다. 공급자/유료AI·RULE·전체 뉴스GUI·NAS/운영 수락은 아니다. [설치 BODY3](../../tmp/ar-v1-wave51-20261029/integration162/acceptance162-c17-body3.json).

**사용자 확정 뉴스 정책(F19/F22, 2026-10-29):** 같은 기사 새 BODY의 이전 판단은 이전 판단임을 표시해 유지하고 새 분석은 명시 사용자 요청 때만 실행한다. P01 결정은 이미 완료1건이며 C06 v3는R1/T1·동기화연결의정적확인후PG effective scope 1HOLD를남겼고v4 사본만보완/봉인했다. v4 독립검토·본코드통합·실제검사전이며사용자요청으로중지/수락0이다. 신규 기사 최초 분석·명시 백업 복원·RULE/prompt-only와 기존 원판단/출처/시각/hash/판본은 보존한다. 다른 PC/NAS 동시 분석 충돌 정책을 다시 요구하지 않는다. [정본 잔여](app-target-20261023.md#checklist-ar06)·[v2 검토](../../tmp/ar-v1-wave51-20261029/ar06-current-policy-review/REPORT-v2.md).

**MAIN162 뉴스 child 수명(F19/F22, 간접 F01/F23/F24):** 기존 coordinator3/1/1과 실제 두 창/content client/native store를 사용한 대표2에서 A COMMIT 뒤 ACK손실+B 실제 미전송이 재시작 후 정확 A/B 원문·identity/hash로 수락됨을 확인했다. 본문/RULE/AI claim의 별도 owner·084 current/유료 정책·NAS 실제 인계는 변경/수락하지 않았다. 부모 종료≠저장 ACK를 유지하며 entry/lease·Job/tree·UUID/성공TEMP 정리를 확인했다. [News2](../../tmp/ar-v1-wave51-20261029/integration162/acceptance162-c14-news2.json).

**현재 MAIN154 뉴스 연결(F19/F20/F22, F25 호환 참조):** 원출처/BODY 판본·가용시각→API/typed envelope→SQLite 재열기/실제 묶음 입력과 원BODY/immutable raw 분리는 본 코드에 통합하고 관련293+실제 PC 격리PG22로 C05 기술조건을 수락했다. PG의 정확22 ID·원자성/충돌/stale/rollback/ACK와3worker/Job/HKCU/TEMP·DB/port/cluster 정리 및 source불변을 확인했다. P01은 확정됐지만 current/자동 요청의 C06 구현·키 정책, NAS6/Linux4/운영은 별도 미수락이며 원153 실패와 원본/시각/hash는 보존했다. [국소 수락](../../tmp/ar-v1-wave50-20261029/integration154/acceptance154-c05.json)·[현재 잔여](app-target-20261023.md#v1-detailed-checklist).

**MAIN140 뉴스 출처 연결 계약(F19·간접F22/F23/F24):** native accepted writer가 결과와 같은 article/BODY/analysis IDs·inference/input/output/provider/판본·첫 available receipt를 transaction 안에서 확정한다. additive API→CentralAIClient tuple7→기존 completed7/slot→로컬 own/shared 저장→sync/backup3/reopen까지 같은 결과의 metadata를 전달한다. 순수 DTO6필드·원발행/원시각/hash/판본은 변경하지 않고 schema5의 nullable 두 열로 legacy UNKNOWN을 보존한다. generic 복사6경로는 REPORTED이며 기존 정확한 native echo만 최초 수락/receipt를 유지한다. DB 추가 SELECT 없이 원 SQL/COMMIT 비용을 유지했다. 단위176/177·실제PCPG20/20의 소스별 데이터/저장 국소수락이며 조회 QtAbort1·current084/RULE·AI 처리기 이관/키·실NAS/운영은 미완료다. F20의 새 판단 정책은 변경0이다. [단일 정본·검증 범위](app-target-20261023.md#1160-main140-뉴스출처저장operator설치-국소-검증--2026-10-27).

**2026-10-27 구현·검증 국소 갱신:** 독립뉴스child도기존configure_logging API로새PC14일정책을소비하며두실제process의공유폴더/종료는MAIN128에서국소검증했다. 뉴스원발행·BODY/AI가용시각/판본의업무clock을이로그예외로교체하지않았다. 기사사건분할strict기간가설실패와084/키·새수락owner시간계약은별도남고구형운영전수정상화는하지않는다. [소스별 근거·남은 조건](app-target-20261023.md#time-audit-followup).

**2026-10-27 시간 감사 보충(수정·새 PASS 아님):** F19/F20·기존F20.1은 원발행의 변환, 수집 receipt, BODY/AI의 수락·available/판본을 구분한다. 원발행 변환에 현재 키움 시계는 필요 없지만 공유 as-of에 쓰이는 availability는 일관된 수락 owner가 필요하다. active runner/직접BODY/AI enqueue와 PC 독립창 checked/link의 OS 사용은 소비 영향·필요성 검증 전 유지/미해결로 기록한다. 기존28차 연결 수락과 정책084/키는 별도다. [담당 AR·우선순위·완료 기준](app-target-20261023.md#time-audit-followup).

S1 조회는 뉴스 처리기·naver/rules/providers/선택 패키지가 물리적으로 없어도 필요한 조회/종료 경로를 소비한다. 처리기 내부를 UI 기동 조건으로 만들지 않는다. 이번 변경은 기존 NAS BODY/RULE/AI·입력 판본 정책을 바꾸지 않는다.

**AR v1 28차 현재 계약(F01/F19/F23/F24).** NEWS 업무 현재 시각은 기존 app server_now→store/service→source·historical claim/finish가 소유한다. 온라인 PC는 기존 응답 시계, 일회성 broker 없는 importer는 명시 공통 KST fallback을 사용한다. 원 발행/fetched/snapshot 순간·hash/판본·native CAS/stale120/재시도/원자성·commit 뒤 wake와 종료 owner를 유지한다. 새 polling/clock 서비스/중계 계층은 없다. 현재 source102 관련189(실PC PG20 포함)/정적3종 PASS이며 원 fixture 실패·UNKNOWN과 정책/전체 역할/운영 미수락을 보존한다. [단일 정본과 근거](app-target-20261023.md#0-현재-기준과-진행-현황).

**이전source99 추가차단 — 11.34에서국소해결:** 기존watchlist/local_projection_sync의publisher포함fullhash와source-page의제외hash가공존한다. 새remoteBODY입력validator의일괄해석은코드상불일치이며현재158의publisher없는fixture로수락되지않는다. 원데이터/판본을보존한실제writer재현·정확producer계약보완/변조거절회귀전remote watchlist경계는미수락이다.

**MAIN123 NEWS BODY 수락 증거 계약(2026-10-27):** 계산/작업은 기존Runner, 불확정 전달확인은 client, 현재attempt의 원입력/수락증거·transaction은 native owner다. `body_input_ref`에 exact envelope hash/최초receipt를 결과와 같은COMMIT으로 보존하고 claim에서초기화한다. mutable상태만으로ACK손실을성공처리하지않으며 old/missing/mismatch/reclaimed는safeUNKNOWN이다. 직접POST/기존terminal중복응답·원자료/retry/RULE/AI/취소수명은유지한다. 새table/계층/poll/rePOST는없다. [소스별 실제 검증·비용·혼합판본 제한](app-target-20261023.md#1152-ar0406-body-시도별-수락-증거와-응답-손실-회복--2026-10-27)을따르며아래과거source99근거와전체운영수락을합산하지않는다.

**AR v1 NEWS BODY 역할 계약.** 계산/작업 수명은기존NewsJobRunner,headless기동은news_body_processor,전달은NewsBodyClient/기존CentralContentClient,인증·HTTP는선택등록news_body_routes,수락/transaction은기존news revision owner다. 공유queue decoder는기존하위writer,기사내용hash는domain단일본문이며불필요한새native파일/mixin2개와순환5개를제거했다. 기본grant는비어있고연구/조회/admin/계좌권한을혼용하지않는다. owner/attempt/source/판본/cache pin과BODY/RULE/job terminal원자성·취소native합류를보존한다. 일반error 재시도ACK는확정할수없을때safeUNKNOWN이며현재뉴스판본/AI·RULE이관/키정책을임의결정하지않는다. 현재99소스관련124+실PG34=158통과·정적3종PASS,원98소스queryAbort/계좌계약실패유지이며전체역할/운영완료가아니다. [단일정본](app-target-20261023.md#0-현재-기준과-진행-현황).

**AR v1 17차 계약.** query 시작에서 private 뉴스설정/자료를 자동import하지 않는다. 기존 full의 명시 child 경로·NHS Runner 소유/claim/native 종료는 유지한다. 뉴스정책/remote COMPLETE 권한 변경0. [현재 국소 근거](../../data/diagnostics/ar-v1-wave17-20261024/verification.json). actualwheel/선별이전/운영은 별도 미검증.

**AR v1 15차 계약.** service/두source의Store계약은20→9이고jobnative는기존Runner소유다. root가같은조건/시계/parallelism3로Runner를직접조립하고source는실제priority/enqueue만소비한다. sourceclose실패에도runnernative를합류하고primary예외/AI-store fail-stop을유지한다. 일반headless/remote/084판본정책은잔여. [소스별국소검증/미해결](../../data/diagnostics/ar-v1-wave15-20261024/verification.json).

**AR v1 13차 계약.** 공통news_ai_contract가실제DTO/model값단일정의를소유하고producer는같은객체를직접import한다. reader/UI/native/backup은producer없이소비한다. 원prompt/필드/계산/JSON/pickle호환·legacy GLOBAL정책을보존하고headless/일반원격결과수락은잔여다. [현재국소검증](../../data/diagnostics/ar-v1-wave13-20261024/verification.json).

**AR v1 12차 계약.** 기존 news_read_routes가 accepted cached/confirmed read와 codec를 소유하고 service도 같은 함수로 연결한다. query stored-page는 producer/automation/priority 변경 없이 읽고 admin 처리 의미는 유지한다. legacy GLOBAL on-read 판정/DTO와 뉴스headless 수명은 잔여다. [현재국소근거](../../data/diagnostics/ar-v1-wave12-20261024/verification.json).

**AR v1 11차 계약.** query설치에OCR/Drive/ChatGPT/server처리패키지를강제하지않는다. 설치metadata국소수락이며headless뉴스처리/원격수락·실제이관/삭제는완료가아니다. [현재국소근거](../../data/diagnostics/ar-v1-wave11-20261024/verification.json).

**AR v1 9차 계약.** 뉴스GUI의작업3종을실제QObject owner로추적하고 original ACK→local mirror→native tail→close 순서를확인했다. 원예외에후속stage실패를보존한다. headless/원격producer결과경계·뉴스정책은별도잔여다. [현재소스736근거](../../data/diagnostics/ar-v1-wave9-20261024/verification.json). 최신단계/잔여는정본0절이다.

**AR v1 8차 계약.** 실제 provider 추론 불변기록과 requester job/attempt/terminal 귀속을 분리했다. native receipt/usage·settlement 오류 관측을 유지하고 추가 SQL은 필수 안전비용으로 명시한다. NAS-local BODY pins를 미래 PC 결과 계약으로 고정하지 않으며 GUI owner115·headless/read-command는 별도 잔여다. [소스별 검증](../../data/diagnostics/ar-v1-wave8-20261024/verification.json); 현재 판정은 정본0절만 참조한다.

**AR v1 7차 당시 계약/근거.** content-sync 실제Thread 종료뒤childlease를반납하며 HTTP/Broker 문맥을연결했다. AI251격리/새뉴스획득후속은main미통합이다. 향후PC처리→NAS결과/최소근거저장을허용하도록처리producer와native수락/조회owner를분리할것이며 현재NAS full-input/처리기조립결합은잔여다. 현재이관/제거완료가아니다. [기준](app-target-20261023.md#1116-자동매매연구-교체와-뉴스-처리-위치-경계).

**AR v1 5차 간접 연결.** news_process가 조립한 실제 content client/sync의 테마읽기만 단일current API로 전환했다. 뉴스 BODY/RULE/AI 저장·유료/표시 정책은 이번 변경0이며 AI requester provenance/settlement 차단은 남아 있다.

**AR v1 3차 구현 계약(현재 통합 검증 중).** BODY/RULE의 실제 native Revision owner가 valid attempt와 결과/revision/current+terminal을 한 transaction에 수락한다. stale attempt는 어느 결과도 게시하지 않고 동일 completed/failed replay는 원래 receipt다. live mapper/Naver source는 oneoff collection/archive에서 독립했고 자료·옛 sealed hash를 유지한다. AI shared outcome/usage/requester 수락·R3-084 표시/유료 정책은 별도다. [근거/현재 잔여](app-target-20261023.md#1110-3차-진행--승인된-ar의-연속-실행).

**구조 후속 설계(2026-10-23, 미구현).** immutable BODY/분석 원본 소유는 기존 store에 두고 공식 본문+동일버전 판단 선택을 기존 read/projection owner 한 곳에 모으는 B안을 기록했다. 최신 본문부터 표시/마지막 완성 세트 유지·failed BODY 처리·유료AI 재실행은 사용자 정책이다. 목록/detail/AI가 그 계약을 소비하기 전 F19/F22 버전 불일치는 미해결이며006/019/043 완료와 구분한다. F20은 변경하지 않는다. [정책/책임안](../../REFACTOR_BACKLOG.md#nas-구조-후속의-분류와-책임-이전안--2026-10-23).

**현재 NAS 뉴스 claim 계약(2026-10-23, 미커밋).** F19의 기존 `NewsJobRunner.run_once(limit1) → PostgresQueryStore.claim_news_jobs`는 같은 native owner/transaction에서 stale120초 복구 후 후보 잠금·RUNNING 전이를 한 SQL로 수행한다. 반환은 잠금 시점의 후보 값으로 `queued_at`·`attempts+1`을 보존하고 결과 ORDER BY도 명시한다. 우선순위/상한4·historical BODY/RULE 제외·AI 허용·attempt CAS·wake-up·재시도/취소/종료 owner는 유지한다. job1 SQL3→2/job4 6→2/empty2→2, 연결/COMMIT1·전체 반환/저장 결과 동일. 실제PG 중복 claim/peer SKIP LOCKED·진짜SQL 오류 rollback과 관련89건 PASS. 현재 운영 지연/공급자·R084 판단 정합성 수락은 별도이며 F20은 변경하지 않았다. [근거](../../data/diagnostics/r3-news-claim-20261023/verification.json).

**이전 종료 보완 계약(PC6 보강·NAS 범위 정정 전, 당시 로컬).** R3-050 계좌 fixture에서만 공개 시장뉴스/분석job 시작2플래그를 격리했고 제품 뉴스 경로는 불변이다. 실제PG043 finish/retry2 PASS;084 BODY판단·020/O19운영 잔여는 유지한다. [근거](../../data/diagnostics/r3-050-final-20261023/verification.json).

**이전 집중 검증·이관 계약(당시 2026-10-23, 로컬).** 043 attempt CAS의 등록 실제PG2 확인과084 BODY/최종판단·failed소비 미확정,020/O19 별도process·실공급자/AI/과금 운영 수락을 구분한다. 새jobwinner/재시도 정책을 만들지 않는다. [검증](../../data/diagnostics/r3-closeout-20261023/verification.json).

**2026-10-21 `7e4fb607ceb09479f6b9c9ed2c03cbf6cdc42bbb` 국소 계약 갱신.** R3-019: refresh만 _collect(owner_only=True)로 호출해 article/sync ACK 뒤 native owner를 한 번 읽는다. 일반 검색의 fresh+confirmed·A→B→A 검색A/자동AI B, confirmed-only 성공/AI0·빈 실패·날짜/assessment·부분실패와 다음 종목 진행을 유지한다. 기존 catch에 오류 로그를 남긴다. publication 조회3→1, fresh/recent/empty/partial 전체연결9→7/7→5/8→6/9→7, 전체 공급자 실패7→7. 관련48+native2 WT 통과, 원본 신규7실패. 쓰기·공급자·AI 호출 동일, R084 body cut은 별도다. 현재root1060/62모듈·원본비용/WT검사는구분한다.

**2026-10-20 `736fce8` 책임/검증 갱신.** 기동 때 기존 delta manifest를 한 번 확인하고 before/after push signature가 같고 outbound pending이 없을 때만 ACK한다. pull 뒤 최신 signature를 clean으로 덮지 않는다. 기존60초/1초/Event/daemon·범위·시계를 유지했다. 원본3검사6 assertion 실패, concurrent news/AI/link 편집·partial/restart·unsupported-v2·echo POST0을 확인했다. 필요한 startup/purepull 후속 확인은 연결8→9/SELECT8→12/capabilityGET8→9/POST0, 그 뒤 steady는 동일하다. WAL-only/동일 파일 signature·daemon 종료join·실제 원격 same-key 충돌은 미검증이다.

**2026-10-20 `017bb03` 추가계약.** 같은poll owner의await collect_once 경계에서escaped회차오류를기존diagnostic/warning으로관측하고원래poll주기로회복한다. 정상연결7/SELECT1/BEGIN6/INSERT8/COMMIT6/provider6/fetch4/sleep60동일. native readfault에서원본1회후task종료/close오류→동일owner2회·sleep[60,60]뒤정상fetch/close. 기존TypeError/ValueError잘못된입력과cancel은전파,새task/간격/즉시retry/상태추가쓰기없음. daily성공표식·설정disable/close합류는유지한다. 최초to_thread proxy/비용진단release순서오류는수정된제품근거와분리보존했다. 기존trailing_volume은사용가능한window행이없으면None,실제0행은0,기존부분유효합/공통24h window를유지한다. 필요한active/next가미확인이면비교·roll-state쓰기를하지않고이전count/price/reference/rolled_at전체row를보존하며기존failedstatus로관측한다. 정상float evaluate_roll/임계값/확인횟수·auto-off의명시(0,0)평가는그대로다. native14상태/재시작/회복과unknown5반례19assertion실패→통과;unknown의잘못된rollstate1회쓰기제거로connection/BEGIN/COMMIT/INSERT반영각-1,정상on/off비용동일. 미조회candidate의기존nullable필드는JSONnull이며float계산유입0. 원본bars/daily성공은보존하고과거bad state자동수정·새coverage/같은window연속count정책은추가하지않았다.

**2026-10-20 `964ddbe` 추가계약.** 기존자동판정의 provider!=none && (local credentials OR central client)를같은news_execution모듈의순수함수로공유해detail/start/finished를일치시켰다. 실제Qt수동·자동→기존AINewsWorker→인증client의제어POST→native결과/usage→표시가2회,중복/busy/한도추가POST0·localfallback0이다. 링크/busy/기존quota·ChatGPTpause/manualqueue/server키·permission·수명정책을유지한다. 선택/idlefinished의dailyDB조회추가0,finished는현재설정1회확인비용이필요하다. client존재는라우팅가능성일뿐실제서버권한/키/구독/NAS성공을보장하지않으며원격POST취소·late결과폐기는이번범위밖이다.

**2026-10-20 `64a74e0` 계약 보완.** R3-043 진행(PC 구현통합·실제PG 대기): 기존 claim attempts를 필수 keyword로 모든 finish/retry caller와 QueryStore/CentralNewsStore 계약에 전달한다. native WHERE job_key/RUNNING/attempt 일치 때만 상태·output_ref를 갱신하고 실제 matched PENDING commit 뒤에만 깨운다. 정상 retry SELECT1→0, UPDATE/연결/BEGIN/COMMIT 각1 유지. 120초 stale/3회 FAILED/None 반환·immutable 원문과 결과는 보존한다. old/new 성공·실패 역순, ACK 유실/취소복구를 native SQLite와 PG SQL/rollback fixture로 확인했다. 행 삭제 후 같은 key 재생성 ABA는 보장 범위 밖이다. R3-043 실제 PostgreSQL 변경2 ID 및 해당87건 모듈은 미실행이다. 전용 diagnostic DSN 부재로 증분 실행 증거 검사는 누락을 감지해 실패했으며 성공 처리하지 않았다. 기존 push/PR의 필수 postgres-integration이 postgres_required 전체 모듈을 실행하고 실패·누락을 required 증거로 전달하는 본문만 확인했다. 현재 hosted CI 실행은 없다.

**2026-10-20 `8f3bade` 추가계약.** R3-073 완료: 기존StockNewsRepository의11개UPDATE필드를DB현재행과NULL-safe IS NOT으로비교한다. 동일5기사의article반영5→0·필수receipt1유지로총6→1,정정1건포함총6→2/partial2총3→1. 연결/ID SELECT/BEGIN/COMMIT각1과raw원행/first_seen/발행시각·pin/retention/limit·새건수반환은보존한다. 각11필드외부DB정정/NULL·새identity·receipt trigger실패전체rollback/retry와기존201건pin검사를확인했다. actualsearch/prepare→기존창/contentsync소비는그대로이며같은기사라도확인시각저장을생략하지않는다.

R3-069 완료: 과거뉴스 initializer의 기존identity index를 original_url/portal_url 기존ALTER 이후에 생성한다. URL열0/1/2개 legacy·기존index·fresh logical schema31·원행/URL/시각보존과 ALTER/index실패후닫기/같은DB재시도를검증했다. 기존SQL의위치만옮겨같은schema/index·연결/commit을유지한다. executescript의부분DDL영속은그대로며초기화전체rollback/운영archive수락을주장하지않는다.

**현재 책임.** CentralNewsService/공급자 수집은 기사 revision을 저장하고, 기존 job runner가 BODY/RULE/AI와 retry를 진행한다. 원문/body/AI/event revision은 서로 다른 원본·가용시각을 가진다. PC 뉴스 창/worker는 조회·그룹/대표 기사 선택·수동/자동 분석 요청·표시를 소유한다. 장전 service는 별도의 외부 공통 시황 request/예약·claim·raw/result·latest/context를 소유한다.

**상태·실패·공식 경계.** 기존 news 수집/page/history·job claim/finish/retry·인증 AI API와 PC worker 신호를 사용한다. native 저장소가 revision·job 상태·transaction을 소유하고 AI service가 accepted 요청/실행 lock을 소유한다. UI용 그룹 대표와 NAS의 저장 body revision 입력을 섞지 않는다. 미확인 자료는 원값과 확인 상태를 함께 보존한다. 장전 context의 `advisory_only/consumer_binding=not_connected`는 실시간 판단/주문 연결과 구분하며 공급자 호출이나 polling task를 소비자마다 복제하지 않는다.

**남은 뉴스 범위.** 073동일기사쓰기등록경계는완료,043terminal은PC통합·실제PG대기,072가용성은등록UI연결로컬검증완료다. 서버실제권한/키및remote취소수락은별도다. 019read/error·020프로세스결합·084body/projection소비계약은남는다. 장전결과와외부지연시세F20.1은서로다른입력이다. 060/061등록범위는기존collector/원본bars/diagnostics의로컬검증을완료했으며장전AI/매매판단의새소비연결을구현한것이아니다.

**관련 검사.** [CentralNewsService](../../tests/unit/test_central_news_service.py), [news jobs](../../tests/unit/test_news_jobs.py), [기사 저장소](../../tests/unit/test_stock_news_repository.py), [장전 계약](../../tests/unit/test_morning_market_brief.py). 원문 revision·중복/실패·가용시각·선택한 provider/버튼과 실제 worker 입력을 대조한다.

<a id="journal-sync"></a>
## 일지·편집·동기화 — F22 및 F18 소비

**실제PC main자료 full 반입254(F01/F22/F23/F24, native소비 정상·앱기동미수락).** 보관raw11의3977행/34보관을독립tagged oracle로새fulltarget에대조하고원type/UTC-KST/판본·공개settings/columns/theme/aliases·승인image9(새대상2행쓰기)/noop·plain/동일plan재진입과firstreceipt를수락했다. 원source/251/223/승인52핀·139연결close/Job/HKCU/TEMP를보존했다. 원11 fixture118과같은source253의actual1을구분하며root사전기록필드/도구분기실패도보존했다. 실제full root/자식/catalog/CURRENT출처·자산·최종인계/설치·운영은미수락이다. [254범위·실패구분](app-target-20261023.md#ar05-actual-full254).

**명시full 선별저장253(F01/F19/F22/F23/F24, native/API 로컬검증·앱연결 미수락).** 기존Database가query/full의한native transaction·schema식별/원11seed·firstreceipt/close를공유한다. full선택/CLI layout을명시하고다른DB를채택하지않는다. 원값/type/UTC-KST/판본을보존한뒤없는fresh기본값만추가하고full plain재진입은원연결의readonly replay라legacy별칭/default/profile를재실행하지않는다. 관련118·실제SQLite ACK/close복구/편집유지·구조1305/위반0·증분190/6908/49를수락했다. 아직actual3977full/새root/자식·CURRENT출처/자산복원·catalog·설치/인계수락은아니다. [책임/검증253](app-target-20261023.md#ar05-selected-full-native253).

**새 구조 백업 소유권250(F01/F19/F22/F23/F24).** 기존Drive서비스가backup-set·폴더·동일tag v2 manifest 수락을소유하고full bootstrap은CURRENT=architecture-v1을명시한다.query는기동하지않는다.CURRENT의옛alias fallback/중복쓰기·untagged/다른manifest의자동반입을제거했고dirty/ACK/복원/종료·계정연결·뉴스AI payload 검증과시계소유는유지했다.legacy는명시옛백업읽기/복구소비만위한호환이며C09의소비자/미완료복구확정·전환완료가제거조건이다.현재관련29·source/ID/Job/HKCU/TEMP·guard1303/위반0을수락했으나실제Drive계정/운영연결·최종설치수락은아니다. [250·251계약](app-target-20261023.md#ar05-current-backup-set250-251)·[수락](../../tmp/ar05-drive-backup-set250-20261102/ROOT_ACCEPTANCE250.json).

**조회용 선별의 현재 범위(251, F22/F23/F24).** 4011행은3977이전/34원본보관/0미확정으로분류했다.새묶음정책에따라옛Drive 변경/성공ACK/미전송과version6행은원본보관하며현재target/원자료/첫receipt·9편집을바꾸지않는다.선별은query draft이고실제owner quiet/finalACK·복원pointer/input·일관된cut/새full로컬target/T0/인계는미수락이다.원remote묶음분리로기존로컬키가reset되지는않으며CURRENT full은별도새대상에연결해야한다. [251](../../tmp/ar05-actual-selection-validation190-20261101/pc-query-actual001/unresolved-review/current-backup-set-state-retention251/SAFE_SUMMARY251.json)·[현재 정본](app-target-20261023.md#0-현재-기준과-진행-현황).

**Drive 연결·시간 보존 근거(246r2).** 시작과재개가위공유pending predicate를사용하며정상기동의원격metadata충돌확인을혼합offset문자열순서때문에누락하지않는다.기존순수표식3읽기·dirty/최초ACK/strictrestore/Drivev1/v2·뉴스AIv1~4/원문bytes·hash/서비스수명은유지한다.두테스트fixture JSON에뉴스AIv4필수빈목록만추가했고validator/기대값·단언은유지했다.현재27건은isolated native DB/Qt +mock transport 범위이며실제Drive연결/owner quiet·최종ACK/미완료복구부재·T0/인계는미수락이다.선별옛설정원본보관은사용자247/후속결정의정확행범위에만적용하고기존DB/원영수증/설치target은수정하지않는다. [정본 현황](app-target-20261023.md#0-현재-기준과-진행-현황)·[246r2](../../tmp/ar05-drive-time246-20261102/ROOT_ACCEPTANCE246.json).

**현재PC 최소이전 후보(F22/F18/F23, 읽기확인만):** 사용자가NAS순위+현재PC최소자료후보를선택했다. 현재설정/편집과테마prepared/pending·ACK미확인,일지보완pending120/retryable_failed1 및HKCU일지설정109를확인했다. 미완료보완121을주문/미완료저장전체와동일시하거나자동이전하지않는다. 원격중앙경로이므로PC일지로실행owner/미확정주문없음을확정하지않고계좌/run/T0/target을임의정하지않는다. DB/registry쓰기·사본/export0이다. [현재후보/보존한계](../../tmp/ar05-pc-source-candidate172-20261030/report.json).

**C14 로컬 수락(F18/F22, 간접 F01/F23/F24):** 현재162 News2의 실제 부모3/1/1 강제종료→같은 자료 재시작에서 A 저장후 ACK손실+B 미전송·원본/설정·정확 manifest·entry/lease를 확인했다. Journal160 대표2는 원본/설정·running 복구/부모 정책의 정확 불변범위로 재사용한다. C14는 국소 완료이며 forced exit를 graceful/native ACK 성공으로 계산하지 않는다. 실제source/T0 인계·AUTO 정책·전체 F/AR08·설치/CI/운영은 남는다. [수락](../../tmp/ar-v1-wave51-20261029/integration162/acceptance162-c14.json)·[단일 정본](app-target-20261023.md#v1-detailed-checklist).

**MAIN149 F22 간접 수명:** main observer 정리는 entry의 같은 pending payload/native ACK 완료 뒤에만 진행된다. 저장/분석/원본 의미는 그대로이고120의 real DB retry/실제 종료를 확인했다. Journal 별도client는 기존 own-instance 계약을 유지하며 전체 content/다른workflow 수락은 별도다. [현재 범위](app-target-20261023.md#main149-rest-main-lifetime).

**MAIN147 F22 지수 연결:** 선택 지수→실제 worker→native 저장→completed→UI 상태/차트 reader를 관련77에서 확인했다. UI는 같은 결과를 다시 저장하지 않는다. fetch/storage 실패 원문구·bound 수신·requested_days RAM cache와 pending-day는 유지하며 trigger rollback/완료 금지/실제 종료를 검증했다. 이 경로는 격리 PC에서 연결 확인됐고 다른 일지workflow/운영 수락은 별도다. [전후·잔여](app-target-20261023.md#main147-index-native-result).

**MAIN146 F22의entry 소비:** source교체는이미관측한pending enriched/backfill자료를그대로보존하며재시도에loader를다시호출하지않는기존계약을유지한다. loader해제와저장/ACK·실제writer종료는별개이다. 현재관련29PASS는A source-release만수락하며mainclient실제retirement/종료전체를뜻하지않는다. [기능연결/잔여](app-target-20261023.md#main146-source-release).

**MAIN145 F22 관측/보존 계약:** Window→Confirm실fetch→QThread native저장→metadata→queued UI에서가용시각은결과관측값이다. request target/현재봉/세션cut는시작값을유지한다. 신규confirmed_at의분수초가preserve후에도유지되며실제reader의끝-1us false/끝 true와rollback·종료를관련98에서확인했다. 기존row/transaction·원시각/hash/판본은유지한다. 연구PIT export/실제누출은미확인이고bar.metadata전체정규화나중앙연구완료로확대하지않는다. [전후·미수락](app-target-20261023.md#main145-observed-availability).

**MAIN144 Journal의client 종료(F22·간접F01/F23/F24/F25):** 창이직접만든선택client만기존QThread actualjoin후닫고sync/client중한쪽실패도다른종료를시도하며원first exception을보존한다. constructor초기실패/실제borrower·thread/FIFO/보고서오류를관련87에서확인했다. 다른maininstance/실source인계/지연가용시각은별도미완료다. [책임·실행근거](app-target-20261023.md#main144-validation-lifetime).

**MAIN143 확인분봉 task(F22·간접F01/F23/F24):** ConfirmWorker가조회→확정필터→기존repo원자저장/실패→결과신호를소유하고창은투영한다. native join전저장·실제rollback·후속UI동일결과중복저장없음과serverclock의naiveKST경계를관련61에서검증했다. 기존다른workflow/History취소·계좌cut/선택daemon과전체source종료를추가수락하지않는다. [책임·시간/소스별근거](app-target-20261023.md#main143-confirm-native).

**MAIN142 창 초기화 예외(F22·간접F01/F23/F24):** saved history→실제 ConfirmWorker/native SQLite를 사용해 constructor/entry setup실패에서도 실제종료 전 lease/복원 차단, 종료후 같은row/원예외/PID정리를 검증했다. 기존 공개 native owner와 UI/review 계약을 재사용하며 선택client daemon과 queued결과 최종저장은 별도 잔여다. [현재 관련50·실패2](app-target-20261023.md#main142-journal-setup).

**MAIN141 창 소유 native 완료(F22·간접F01/F23/F24):** 기존 분석·계좌조회 작업이 원본 SQLite를 사용 중이면 공개 `finish_shutdown()`이 실제작업을 합류한 뒤 기존 sync를 닫고, entry가 PID/lease를 해제한다. 참조 None/finished signal만으로 source 종료를 선언하지 않는다. 계좌요청도 기존 parent 인자로 같은 창에 귀속하며 새 상태/작업/중계는 없다. 원41검사와 UI3000/review 실패·수동입력/고정시각·저장/예외 계약은 유지했고 같은 신규4의 전4FAIL→후PASS 및 관련45+2+1=48을 실제 native/임시DB/UUID HKCU에서 검증했다. 제품 SQL/COMMIT 증가는0이며 queued workflow 결과 최종저장·실선별 이전/복구·parent force-kill은 수락하지 않았다. F18 계좌/주문/085 정책 변경0이다. [단일 정본·소스별 근거](app-target-20261023.md#1161-main141-일지-창-소유-native-종료-통합--2026-10-27).

**MAIN140 동기화·저장 소비 계약(F22/F23):** 뉴스 결과의 수락 provenance는 결과행과 sync/backup3에 함께 저장·복원하고 legacy 누락은 UNKNOWN으로 읽는다. 실제 profile별 manifest를 공유하지 않는 독립 설치에서 저장→동기화→재열기 원값/출처를 검증했다. `theme_active_profile`은 일반 app_settings의 중복 채널에서 제외하되 accepted theme generation의 `active_profile`로 공유·복원하는 기존 계약을 유지한다. 실제PG는 새 revision→current→usage 한 transaction에서 두 번째 SQL 실패 시 새revision/usage를 남기지 않고 원행을 보존하며 비용 `(3,1,0)/(2,0,1)`은 동일하다. 새 판본/원시각 일괄변환이나 과거 자료 이전은 없다. 전체source closure/미완료 작업 인계·조회Abort·운영은 별도 미완료다. [소스별 수락·최초 실패](app-target-20261023.md#1160-main140-뉴스출처저장operator설치-국소-검증--2026-10-27).

**MAIN134 source 복원·선별 경계 갱신:** 실제StrictRestoreCoordinator의STAGED/COMMITTED와APPLYING/ROLLED_BACK 종결·native값/원version·asset·durable pointer→실제lease해제→기존exclusive readonlycut→설정/버전명시선별→새query DBreceipt/재열기를관련25에서검증했다. sourceledger/assets/bytes보존·target에는옛restore미이식·권한/role_ready=false를유지한다. MAIN130 entry작업의원payload/ACK·명시retry수락과다른owner경계이며Runtime/content/account 전체인계를뜻하지않는다. [현재근거·미완료](app-target-20261023.md#1157-main134-통합-검증--2026-10-27).

**MAIN130 실제 entry 저장/ACK·종료 국소 검증:** 같은원payload의 base→enriched COMMIT→응답손실 후 기존owner가 enriched를보존하고, sources detach 뒤 명시 같은writer 재시도로 native1행/원scope·news·program/investor·market/orderbook을유지했다. 관련9 PASS·정상종료보호이며 과거일지/미완료RAM작업을 raw11로이전한것이아니다. caller선언 CLOSED settings-only seed는journal table이없고재열기후 role_ready/order_authority=false다. 실제source closure/운영 인계는미완료로남긴다. [현재근거·범위](app-target-20261023.md#0-현재-기준과-진행-현황).

**2026-10-27 시간 감사 보충(수정·새 PASS 아님):** F22/F18의 기존 Window→prepare/revision/task/history 시계 전달은 보존한다. ConfirmWorker 완료분 판정과 편집/설정·Drive/backup updated를 실제 판단/동기화 소비에 따라 분류한다. legacy naive KST와 news first_seen의 UTC naive를 일괄 해석하지 않으며 정상 clock/첫receipt를 검증 없이 제거하지 않는다. [담당 AR·우선순위·완료 기준](app-target-20261023.md#time-audit-followup).

**MAIN116 연결 계약(2026-10-26):** per-Hub관측epoch/readyreceipt는기존lifecycle의명시observer,raw선별seed/typedreadback/첫receipt/rollback은기존Databaseinitializer가소유한다. sourceCLI/조회UI는권한을생성하거나편집을재설치하지않는다. native수집/저장/transport/codec와정상consumer의owner는유지한다. 현재고유656 중 656 PASS/0 FAIL·worker2/원실패·미실행/운영제한은[단일현황](app-target-20261023.md#1149-hub-관측-소유와-current-state-native-이전-통합--2026-10-26)을따른다.

**AR04 source 원본 계약(2026-10-26):** 기존 source writer는 한 readonly snapshot을 빌려 settings4/theme7 원문·출처/판본을 보존하고 명시 selection만 반환한다. 원 settingsv1 소비를 유지하며 source capture/참조 CLOSED를 target seed·적용/consumer/주문권한으로 승격하지 않는다. delete/fresh active/fallback/version은 같은native 저장owner의 한transaction으로 반영하고 UI가 coherent 선택을 한 번 게시한다. 현재core74/guard14 PASS와query1 FAIL·종료UNKNOWN 및 미통합seed/consumer 제한은 [단일 정본](app-target-20261023.md#1148-s2-delete-현재-통합과-조회-실패-분리--2026-10-26)을 따른다.

**S2 현재 선택 국소 계약(2026-10-26):** Theme business SQL→같은 Settings nativeTx/ACK/close/cache→MainWindow accepted/queued owner와 모든 same-owner editor 게시다. 현재110 관련89 PASS이며 본문·시한·원 실패와 미검증 정책/운영은 보존한다. generic view는 현재 선택 저장과 구분하고 같은 raw filter는 호출 내1회 재사용한다. [현재 범위·근거·잔여](app-target-20261023.md#1143-s2-현재-프로필-선택의-native-소유권과-accepted-write-보호--2026-10-26).

**S2 current rename:** ThemeRepository는 같은 profile_id/SQLite NOCASE·이름/active/version 업무 SQL, 기존 SettingsRepository는 실제 같은 DB/포트의 native BEGIN/terminal ACK·close/cache 수명을 소유한다. ThemeBackup injected 복원도 이 수명을 쓰되 typed 복원 오류/원 revision conflict·no-port/shared 소비를 보존한다. MainWindow는 accepted main/editor/queued 게시 admission·목록1회로 모든 창 표시·full/query 단일 unapplied 수명을 소유한다. UI 별도 Settings.set/reselect/구형 no-port rename은 제거했다. UNKNOWN을 성공/rollback으로 위장하거나 accepted 작업을 취소하지 않는다. [현재161/본류59·비용2→1·잔여](app-target-20261023.md#1140-s2-현재-프로필-이름-변경의-native-소유권--2026-10-26). 일반 create/select/delete·raw 이전은 후속이다.

**S1 테마 복원:** 실제 UI SettingsRepository 포트와 같은 native 연결에서 theme/profile·active+version을 쓴다. COMMIT 뒤 같은 포트 cache를 무효화하고 UI owner가 재연결한다. native False/UNKNOWN/True와 close/cache/UI 오류를 구분하며 UNKNOWN 자동 재저장·옛 applied 표시를 금지한다. 기존 main/embedded/숨긴 편집창의 accepted 저장/queued 게시가 남으면 명시 복원만 거절하고 원 작업을 완료시킨다. legacy no-port/shared caller는 유지한다. 일반 profile create/select/rename과 원자료 이전은 후속이며 단일 DB만으로 종결하지 않는다.

**AR v1 P2a 계약.** raw현재설정/열/원version의선별이전은일반backup(DEFAULT/폭보정)과분리한다. source는P0/P2가공유하는readonly SQLite owner,수락은기존SettingsBackupService의명시offline transaction이며재시도는최초receipt/원version·무쓰기를보존한다. 원artifact는private/기본UNRESOLVED이고명시선택만수락한다. OFFLINE_UNBOUND carrier를최종앱DB/설치/정상writer호환으로표시하지않으며P3 schema/쓰기/initialize/restart가필수다. [현재158/잔여](../../tmp/ar-v1-wave25-20261025/p2-main/verification.json).

**AR v1 17차 계약.** 기존 복원 원장의 의도적 absence와 before-image/rollback이 자동 private/default copy보다 우선한다. 원래 일지 child의 명시 DB 경로·lease·종료와 settings/theme/news backup 계약을 보존하며 실제 NAS 되감기/선별이전을 자동 실행하지 않는다. [현재 국소 근거](../../data/diagnostics/ar-v1-wave17-20261024/verification.json). actualwheel/선별이전/운영은 별도 미검증.

**AR v1 15차 계약.** 편집/revision/prepared/ACK는기존ThemeBackupService의native transaction,dispatcher는pending/retry/close,중앙generation은CAS/firstreceipt를소유한다. generic news/journal sync는테마를우회갱신하지않는다. 전체COMMIT3→3과미완료복구backup을함께기록한다. [소스별국소검증/미해결](../../data/diagnostics/ar-v1-wave15-20261024/verification.json).

**AR v1 13차 계약.** native조회/backup/일지snapshot은공통DTO를직접사용하며full 편집/동기화의권한·범위를유지한다. query는sync owner/Drive를기동하지않고query전용설정backup/선별이전은후속완료조건이다. [현재국소검증](../../data/diagnostics/ar-v1-wave13-20261024/verification.json).

**AR v1 12차 계약.** 시장 content read allowlist와 사용자 일지/변경 권한은 분리한다. 기존 admin 편집·native receipt/동기화 계약은 유지한다. [현재국소근거](../../data/diagnostics/ar-v1-wave12-20261024/verification.json).

**AR v1 8차 계약.** 공통 native 결과·content/query·ledger 소비의 관련 PG/단위 계약을 확인했다. AI revision 재수락은 최초 receipt를 반환하고 실제 native 실패 rollback을 유지한다. 전체 restore writer·선별 이전 수락으로 확대하지 않는다. [소스별 검증](../../data/diagnostics/ar-v1-wave8-20261024/verification.json); 현재 판정은 정본0절만 참조한다.

**AR v1 7차 간접보존.** content HTTP 문맥/child accepted-sync 수명만변경하며일지불변수락·편집/manifestACK·기존typed404호환은유지했다. native ingest/cache 실연결검사에서실제문서수락/재조회를확인했으나 restore모든writer/원격충돌정책·새자료이전은별도잔여다.

**AR v1 5차 현재 계약.** current-theme HTTP1/nativeSELECT1은 actual legacy현재값·빈값·메타시각/1003행·동시snapshot을 보존한다. 실제구형404만 fallbackHTTP4; 해당서버가 지원대상에서 없어질 때 제거한다. J2 acquired-native 연결은 실제COMMIT후UNKNOWN예외를 보존해 close1/강제rollback0/retry0, 첫receipt/SQL3/9/9/5 유지. accepted writer/sync/local-task/restore는 잔여다. [소스별 검증](../../data/diagnostics/ar-v1-wave5-20261023/verification.json).

**AR v1 4차 당시 계약(최신5차와 구분).** 원격 immutable batch는 기존native accept_journal_analysis_revisions 한 Tx·정렬 identity+원행 readback이 소유한다. 첫 raw/time/receipt 불변·혼합rollback·ACKloss와 PC sync를 연결하며 tombstone 삭제 권한은 유지한다. 원격 stale false와 명시복원 정책은 확정하지 않는다. [현재 근거/미확인](app-target-20261023.md#1111-4차-구현--실제-상태workflow-소유권과-불변-수락).

**AR v1 3차 구현 계약(현재 통합 검증 중).** J1 manual scope key는 schema10에서 legacy 미귀속 원문과 공존한다. frozen effective manual v3와 raw manual CAS를 구분하며 immutable/current은 최종 native transaction이다. v2/첫 receipt·동일ID충돌·owner tombstone·backup/reopen 보존. theme 한 로컬 snapshot/native generation은 준비됐으나 API/sync·모든 writer restore/legacy baseline 정책은 후속이다. [근거/현재 잔여](app-target-20261023.md#1110-3차-진행--승인된-ar의-연속-실행).

**현재 열일곱번째 계약(2026-10-23, `066d2de1f71e52f1b3507a42e6a80153a092cd53`).** R3-032: CentralJournalSyncRunner가 기존 단일 실행의 실제 Thread 참조·closed admission·Lock을 소유하고 state lock 밖에서 같은 Thread를 합류한다. 자식 _run_journal_process의 app.exec finally는 matching-PID 정리와 outer lease 반환 전에 runner.close를 호출한다. 정상 dirty review 저장 실패 때 창을 유지하는 Window.shutdown에는 close를 추가하지 않았다. 부모의 기존 2초 terminate/kill이 child join을 끊을 수 있는 정책·선택 밖 constructor/setup/cleanup 실패·강제 종료는 별도다. 기존 Window.shutdown의 dirty review 거절/창 유지, 500ms/60초 schedule, 071 merge/전송·시계는 변경0이다. child join을 전체 부모 종료/실제 OS 자식·운영 수락으로 확대하지 않는다. root642/35PASS, WT·원본 진단과 운영 수락은 별도다.


**현재 열다섯번째 계약(2026-10-21, `7fe20f325d462242de3390c50d678c585e6f0161`).** R3-075: JournalBarRepositoryMixin._upsert_bars의 기존 confirmed-row SELECT를 이미 만든 raw minute key의 inclusive min/max로 제한한다. 같은 transaction/확정권한/metadata receipt/필수 복사와1초주기를 유지한다. 원래 기준9를 hidden/live/history read/write/render·확정봉/일자/선택/visible history/close의 현재ID 및 변경없는UI7본문에 대응했다. 현재확인범위에서 추가필수 제품작업은 발견하지 못했다. 원본 monitor의임의과거정정·삭제target복원에source전체조회는필요하고receipt/주기는보존요건이다. 제품등록범위의로컬완료이며 전체main IPC/운영수락·물리I/O/평균latency는미검증이다. confirmed반환/decoded1001→1·1010→10이며물리index scan/latency감소는미확정이다.081이번후보는CPUfallback증가로미채택했고기존chartcache계약은보존한다. root369/18모듈과WT비용/원본대조는구분한다.075등록범위로컬완료,나머지R3전체/PG5/운영미검증은백로그를따른다.


**이전 열네번째 계약(2026-10-21, `fbbb96a0cdbab65d476dd352a90745ba30a1eae4`).** R3-075: JournalBarRepositoryMixin._upsert_bars가 이미 만든 raw row의 minute key/source를 metadata loop에도 재사용한다. 네 줄만 바꿨고 필수 봉 복사·metadata receipt·확정권한·transaction·시계는 유지한다. R3-076: theme_dialogs의 과거명 확인→전체 분리→부분 분리·fragment 선택을 _resolve_known_theme_row 한 구현으로 모으고 두 실제 resolver를 연결했다. 처리 완료 빈 결과·미처리 fallback·전체 취소를 구분하며 OCR/Excel 재입력·별칭 저장과 수동 SimilarStock의 다른 최종 UX를 보존한다. R3-081: 같은 visible rows의 ISO 시각 변환 한 벌을 chart 내부 강참조 identity로 재사용한다. paint/drawing/hit의 세 변환식을 같은 helper로 연결하며 성공한 변환만 게시한다. geometry·현재 시계·MA·nearest·setter는 유지한다. root448/25모듈은 통과했고 WT 비용·원본 대조는 별도다. 등록범위076만 완료, 나머지 잔여와 실제PG5/운영 미검증은 각 항목을 따른다.

**이전 열세번째 계약(2026-10-21, `b16c386d107b08218b2f97dc4d305f3d55ebc632`).** R3-075: preserve_confirmed=True raw UPSERT에8필드 NULL-safe 차이 조건만 추가했다. 동일 raw의 metadata receipt available_at 갱신·확정권한/False 분기/len/시계·transaction은 유지한다. 필수 일지 복사의 조회·metadata receipt·주기 자체는 남으며 숨김이라는 이유로 보존을 중단하지 않는다. R3-076: catalog6개 중복 본문을 기존 StockRepository에 모으고 Theme의6public API는 기존 FK ON factory로 호환한다. 같은 owner의 request-name batch를 기존 matcher에 연결했다. profile_theme resolve와 승인/replace_many1Tx·legacy adapter 순서·동명 native첫행은 유지한다. resolve_unmatched_theme_rows와 _resolve_unmatched_new_rows의 과거명→전체분리→부분분리 공통순서가 잔여다. 서로 다른 재입력/alias저장 UX·profile_theme owner는 유지 대상이다. Theme 공개호환/FK정책 전달은 유지하며 기존Stock owner로 call·파일이1개 늘어난 대가로6개 판단본문을 제거했다. OCR/Excel의 Stock 직접경로는 동일하다. R3-081: rows/fills 강참조 exact identity의 최신1벌 nearest 숫자index만 재사용한다. tie첫행/duplicate키마지막/비정렬·범위밖·현재 geometry/clock·MA/setter/drawing/hit는 동일하다. paintEvent row datetime/grid/annotation_x, _drawing_data_point/_find_annotation_at의 반복계산은 잔여다. root1301 PC관련검증과실제PG5/053진단실패는구분한다.

**2026-10-21 `7e4fb607ceb09479f6b9c9ed2c03cbf6cdc42bbb` 국소 계약 갱신.** R3-057: coordinator의 같은 평가에서 실제 선택 manifest를 Prepared.cycle_selected_packs→Window→viewmodel에 전달한다. 이름/prefix 역추정을 실제 화면에서 제거하고 기존 반환2tuple/동률·모드·판정객체·수동유형·revision identity를 유지한다. 생략한 과거 직접caller만 legacy helper를 사용한다. 원본 short출처/count1→실제 long출처/count3, 조회총5/판정·revision동일. WT105+반례1 통과이며059후행 변경은 순차 통합했다. R3-059: prepare 지역의 lazy dict가 실제 요청된 key의 draft와 None을 한 번 읽어 전체/회차/count가 공유한다. 최초읽기 예외·미요청 key·다음 prepare의 새값/재시도·계좌/수동유형·불변revision을 유지한다. pack 전체 원자 snapshot이나 장기 cache가 아니다. 단일pack N1/2/8 SELECT3/4/10→1,2pack·2회차7→2/연결13→8, BEGIN/INSERT/COMMIT각2·시계1 동일. 최종71+비용1 WT 통과. 최초 Qt abort·동일version draft편집 fixture의 revision충돌 오류를 보존하고 실제 version2 갱신 경로로 보강했다. 원래 동일version 충돌도 그대로 검증한다. R3-071: 같은 회차·wire collection·owner/key의 문서 전체를 비교해 spec/legacy/v2 업로드3지점에서 동일자료만 생략한다. bool/int·None·revision·시각·scope·tombstone을 제외하지 않고 full pull/merge/로컬재조회·ACK손실·부분실패·진행중편집을 보존한다. WT33+비용1 통과, 동일3행 POST3→0/1116→0B/중앙연결6→3,1편집3→1/1120→360B/연결6→4. 로컬연결1/SELECT8/PRAGMA6·GET4/다운로드3은 동일. 복원 뒤 필수 신규 sync-state1의 최초 oracle 오류를 보존했다. 실제 다중PC 충돌·full pull 비용·Runner 전체 종료 수락은 별도다. R3-075: JournalWindow.refresh의 필수 import_live_bars 성공 뒤 숨김이면 순수 load_bars/load_chart_bars·표/차트 재구성만 생략한다(제품2줄). 반복copy·DML/metadata·선택/날짜·확정봉/retention 이후 자료보존과 retry/close를 유지한다. hidden 연결4→2/SELECT5→2/셀16→0/차트설정·집계1→0, INSERT4/BEGIN1/COMMIT1/반영3동일. 원본 새회귀실패와 WT82+비용1 통과를 보존한다. 반복복사/저장 주기 자체는 잔여다. R3-076: 기존 ThemeRepository의 요청코드 batch reader와 call-local preview만 재사용한다. profile·중복행/정렬·비bulk adapter·승인후 replace_many1Tx·FK/rollback을 보존한다. WT48+비용1 통과, 기존profile preview연결20→1/SELECT40→2, match포함40→21/60→22. DML0은 기존profile 조건이며 lazyprofile생성·preview전 별도alias승인 쓰기는 그대로다. 단일catalog owner/matcher/resolve중복은 잔여다. R3-077: _import_profile 자체 연결을 무조건 닫아 성공/원예외를 NameError가 가리던 오류를 수정하고, 무호출 private _export_all_profiles만 제거했다. 공개 개별profile·전체파일·settings caller transaction/CAS/manifest/Drive·strictrestore를 유지했다. WT84+비용1 통과, 원본 NameError/close0→성공 또는 IntegrityError원인 보존/close1, 성공SQL9·실패SQL10/연결·transaction·반영수 동일. 독립 AST계획이 async1누락83인 최초 wrapper 기록을 보존하고 실제 worker84 ID를 정적 재대조했다(재실행0). 외부 스크립트/실제cloud 수락은 별도다. R3-081: MA만 latest1 rows강참조/활성period 기준으로 재사용하고 현재 view/style로 매번 투영한다. __init__/paintEvent/helper1 외 기존46메서드·setter/events bytes를 유지했다. WT85+동일Qt29상태 비교/비용1 통과. 실제20paint build20→1/close24000→1200/rolling115700→5785,별도200paint CPU1.328125→1.140625s. 최초 linear pan의 동일그림은 원본에도 재현돼 peak25 입력으로 fixture를 강화했고 NotEqual/assert완화0, 실패TEMP/PNG/font자료를 보존했다. geometry/nearest/현재시각문구 계산은 잔여다. 현재root1060/62모듈·원본비용/WT검사는구분한다.

**2026-10-20 `736fce8` 책임/검증 갱신.** 이전 N+1 개선(d6da121)을 보존하고 optional backfill 저장의 known OSError/SQLite 오류만 호출별 callback으로 전달한다. 수동 분석은 성공 prefix와 원본 tail을 표시하고 첫 오류 뒤 쓰기0·기존 두 label에 경고를 유지하며 명시 재시도 성공 시 해제한다. 자동 작업은 기본 strict 예외→기존 item_failed/task.fail/retry를 그대로 사용한다. 원본 실제 summary 선택은 native trigger IntegrityError로 중단됐고 현재 수동 표시·immutable revision/receipt/계좌 격리와 자동 실패→재시도/거짓완료0을 확인했다. 핵심 native 비용은 전후 연결4/SELECT3/UPDATE2/BEGIN2/COMMIT1/ROLLBACK1/receipt2로 같다. 수동 필수 분석 저장 복구를 전체 쓰기 감소로 보고하지 않는다. 기존 read-empty/뉴스0 정책은 이 등록 write 경계 밖에 보존한다. 기존 pending_target을 업로드 시작 후 편집 표식으로 재사용한다. start 전 pending을 local로 이동하고 false/예외 시 reentrant 새 요청을 덮지 않으며, 이전 ACK는 새 pending이 없을 때만 dirty/unsynced를 해제한다. 이전 성공 receipt와 새 changed_at을 분리하고 기존 debounce1.5초/close_after/target/복원/최초백업을 유지했다. 실제 native export→fake Drive manifest→owner-thread ACK 두 세대·동일초 편집·busy debounce·실패/취소/재시작을 확인했다. 필요한 두 번째 전송으로 전체 연결25→33/SELECT40→63/COMMIT9→10, 첫 ACK 연결2→1/행4→2다. 별도 metadata-close 경계/실제 종료 cloud 완료·다중DB 원자 snapshot·외부 Drive 수락은 보장하지 않는다. statistics의 기존 시점에 load_reviews를 한 번 호출하고 같은 모듈의 순수 cost batch가 일별 cost map/전체 fill 분모를 한 번 만든다. dirty review flush 앞의 summary 결과를 뒤 statistics에서 재사용하지 않으며 scalar API·round/완료/net·계좌/정규화·legacy/중복 cost 마지막값을 유지한다. E8/F8 실제 native 연결16→9/SELECT19→12/review SELECT10→3/쓰기0·표시값 동일, E8/F16/C16 순수 fill 순회9회/144개→2회/32개·cost8회/128개→1회/16개다. 새 장기 cache/계층0. scalar는 동일 모듈 compatibility 호출1개가 생기며 scalar latency 감소는 측정하지 않았다.

**2026-10-20 `8cc2d19` 추가계약.** ThemeBackupService의 기존 revision SELECT에서 stocks만 export/import 소유필드 code/name/market으로 좁혔다. 다른표/active profile/alias/승인편집·BEGIN IMMEDIATE CAS/rollback·dispatcher generation/pending/retry는 유지한다. 실제 가격/시총/receipt 변경의 거짓충돌을 제거하고 실제 catalog/theme 편집 충돌은 보존했다. stocks hash입력21→3열/188→30bytes·거짓retry1→0/연결3동일; 수락된 native import SELECT18→23/COMMIT1→2는 원래 필요한 작업의 복구다. 가격/시총·전용receipt는 유지하며 일반 stocks.updated_at의 기존 legacy SQL 재작성은 이 범위에서 변경하지 않았다. 현재 full theme backup format/version/profiles를 가진 theme_metadata 컬렉션의 최상위 created_at만 manifest hash 입력에서 제외한다. 실제 전송 backup 생성시각/중첩timestamp·편집필드/effective/history/remote 완료표식은 보존하고 push/ACK/seed/pull echo가 같은 helper를 사용한다. generation-only metadata 업로드1→0/manifest쓰기1→0/중앙 연결·SELECT·INSERT·COMMIT·반영행 각1→0, 로컬연결2/SELECT14 동일. 기존 manifest는 최초1회 재전송 후 restart/no-op0이며 새초기화/이관은 없다. 서비스 수준 다음편집/실패retry를 확인했으나 news_process ACK R044나 실제 다중PC/원격 충돌정책을 해결했다고 하지 않는다.

**2026-10-20 `d6da121` 추가계약.** 한 load_episode_entry_snapshots 호출 안에서 기본 news.load(code,limit=50)를 코드별1회만 수행한다. 순수 snapshot별 48h~+5m/최대10/순서·timezone·계좌/기존news·custom loader 의미와 native write/metadata/clock/예외는 유지한다. missing10 뉴스 연결·SELECT10→1, 전체연결21→12, journal연결11/SELECT11/UPDATE·BEGIN·COMMIT·receipt각10 유지. 기존 read오류 empty와 write오류전파는 그대로이며 유효read/선택write 실패 UI·자동task 정책분리는 미결로 진행 유지한다. 실제 preparer→QThread→native task failed/retry/complete와 수동 메서드 예외를 관측했으나 실제 child 전체진입/운영전파 수락은 아니다. 숨긴 분리창의 순수 read/render와 미선택 index read·중복 aggregate만 생략한다. 필수 custom 분·일봉 native 복사/metadata/기존 선택시장 backfill 요청은 유지한다. show/reopen은 최신 자료를 읽고 실패시 기존 숨김과 원예외를 복원한다. checkbox→기존 source_changed, daily→raw setter 순서와 caller의 동일interval 중복만 제거, 공개 MinuteChart bytes/forced-reapply 의미는 불변이다. hidden custom 연결12→4/SELECT19→3/집계10→0·필수 INSERT4/BEGIN2/COMMIT2/clock1 동일; custom없는 hidden6→0/13→0/10→0, visible현재종목6→2/13→3/10→4. 원본PASS→중간숨김전체skip FAIL0행→최종PASS(원본purge뒤journal/metadata유지)와 원본/최종54 source상태 동일을 재사용 진단으로 확인했다. R052 dirty/close/account/episode 나머지AST·공개chart는 보존하고 필수 같은값 복사UPSERT/075 주기정책은 바꾸지 않는다.

**2026-10-20 `017bb03` 추가계약.** 기존dirty/active/debounce owner를재사용해account/다른episode교체와shutdown전에원래scope복기를저장하고동일identity dirty refresh의재로드를막았다. 실패는원draft/origin·canonical계좌/의존객체·예외를보존하고실패표시를남긴다. close는성공전ignore,process shutdown/sync ACK는flush뒤다. parent-loss는기존command timer정지를유지해자동retry0·UI명시retry만가능하다. dirty-close 저장0→1/700ms debounce1→1/clean추가0/계좌전환1/실패retry2. 기존native schema/시계·chart·분봉·비용·parent force-stop정책은변경0,영구실패/kill에는RAM유실위험이남는다. 최초account findData fixture오류는제품원인에서제외했고corrected원본6메서드음성대조로유실을검증했다. 기존chart/account fake의편집없음계약2필드만명시해옛assertion을유지했다.

**2026-10-20 `0a54c35` 추가계약.** R3-030 검증 완료: 기존 EntrySnapshotWriter에 짧은 cache lock 하나로 loader/cache 동시 포착·setter의 dict 교체·현재dict/loader 확인후게시를 묶었다. provider/native I/O·signal·job/backfill은 lock 밖이다. 동일callable/None/A→B→A도 옛 in-flight 결과가 새cache를 채우지 못하고 원래job의 provenance는 보존한다. 실제 AppController의 기존 public setter 적용/rollback을 재사용(제품0). A→B old1/new0→old1/new1은 필요한 새조회 복구이며 이후cache hit 추가조회0, TTL60초/code-only/얕은copy·오류uncached 유지. Controller의 별도 private cache backup/update 전체를 새lock으로 원자화하거나 cross-date/global singleflight를 해결한 것은 아니다. R3-064 검증 완료: 같은 execution_key의 serialized 빈news [] 재전달은 이미 저장된 news_json을 native CASE로 보존한다. nonempty 정정·명시 save_snapshot_news_backfill(empty) 의미는 유지한다. 같은transaction의 기존stored조회 결과로 retained news metadata UPSERT만 생략해 원래 BACKFILLED/source/effective/available/hash를 유지한다. CASE-only 중간안은 시각을 소급/연기해4assertion 실패했으며 원문을 보존했다. 보완receipt11:30·재전달10:30/11:30/12:30 모두 news1/동일metadata, 실제 was_available_by는11:00불가/11:30가능. retained news INSERT시도11→10, SELECT/연결/BEGIN/COMMIT 각1 유지. 최초empty는MISSING·legacy metadata없음은unknown, 명시empty/nonempty정정의기존metadata전반정책까지재정의하지않는다.

**2026-10-20 `539cbad` 추가계약.** R3-031 완료(등록저장/종료경계): 기존단일FIFO에서실패한현재job·단계/이미관측한backfill값만보존한다. native저장실패는failed를전달하고후속queue를소비하지않고멈춘다. AppController는기존cache_failed/안내를재사용해거짓ready를막고새명시close만멈춘writer를재시작한다. base5회재시도·정상scope/뉴스/수급/0w원문·optional loader오류/None continue는그대로다. 이미조회한값은setter(None)뒤에도저장하고명시retry의외부loader재호출0/논리중복행0(기존native후보조회는유지)·prefix commit/후속원본을보존한다. 새queue/schema/relay/자동restart·poll/durability는없다. 영구저장오류면다음job도명시retry까지RAM에대기하며process kill/전원복구를제공하지않는다. R030provider cache/R064snapshot덮어쓰기는별도다.

**012 열 설정 책임·변경셋 계약(2026-10-20, `47e780d`).** 기존 ColumnSettingsRepository.save가 같은 native writer transaction에서 nonempty의 기존 writer lock을 BEGIN IMMEDIATE로 확보한 뒤 시계를 얻고 변경 전 DB의 visible/position/width로 version을 조건부 갱신한 뒤 다른 value row만 UPDATE한다. 같은 값+기존 version은 편집 시각을 보존하고 누락 version은 최초 등록한다. 별도 SELECT/새 RAM 상태·계층·schema·debounce를 추가하지 않았다. reset은 명시적 편집으로 원래 전체 값/version 갱신을 유지한다. 실제 MainWindow header 이동/폭·표시 변경→기존 MainTableColumnController→repository.save→native commit→재조회/Qt 복원과 embedded ColumnManager itemChanged→save→apply를 검증했다. 종료 저장·재개방·reset2회, 기존 복원 중 signal 억제·Controller 수명은 보존한다. CentralSettingsSyncService+격리 SQLite에서 같은 저장 뒤 최신 UTC 원격 편집 수락·partial pull·실제 로컬 layout 편집과 최초 등록 업로드·다음 sync0·명시 reset 전파를 확인했다. 폭은 원래 공유 payload에 없지만 폭 변경도 같은 column revision을 올리는 기존 결합을 유지한다. stale UI의 전체 snapshot으로 외부 편집을 다시 쓰는 last-write 정책, unknown 이름의 version-only 등록, empty 호출의 시계/commit, 중복 이름의 최종 값 우선도 보존한다. empty 호출에는 이 명시 writer lock을 추가하지 않는다. 새 CAS/필드별 revision·충돌 정책을 만들지 않았다. R3-032/056 수명·ACK와 다른 미결은 별도다.

**012 비용·검사 선택.** 기본16열의 실제 native SQLite 반영행은 동일 저장32→0, 한 열(폭 또는 layout) 변경32→2, 누락 version 등록1이다. 명시 reset은32행을 유지한다. 비어 있지 않은 save의 연결/BEGIN/COMMIT/시계 각1·UPDATE16/INSERT16 시도는 그대로이며 기존 version 비교에 인덱스 기반 inline lookup이 있다. 별도 SELECT 왕복0, 물리 I/O/WAL·잠금 대기·장중 latency·신호 빈도는 미측정이다. 기존 test_column_settings_repository·test_central_settings_sync·test_main_window_layout에7건을 추가하고 기존 main_table_column_controller/MainWindow/AppController·backup/Drive 검사를 재사용한다. 현재 소스 관련 **9모듈·고유152건/127.308 worker초 통과**(독립2묶음 병렬). 계획/발견/실행/통과 ID·변경7개 ID·소스 불변·worker/자식 종료·native TEMP 정리를 대조했다. F01은 조립/복원의 간접 경계이며 전체 기동 수락은 별도다. **R3-012 검증 완료(등록 코드·로컬 관련검증)**. 후속은 사용자 지정 **의존성 DAG 준비큐**로 전환했다. R3-027·037·078은 격리 worktree 개발 배정, 다른 후보는 기존 근거의 상태/계약 충돌 분류 후 배정한다. 번호순013 강제 대기는 해제하되006/008/020/024의 명시 보류는 유지한다. [정본](../../REFACTOR_BACKLOG.md#r3-012-열-설정-변경행revision-저장-완료--2026-10-20).

**011 설정 소유·revision·cache 계약(2026-10-20, `aeb63c6`).** SettingsRepository의 기존 native transaction에서 version UPSERT가 변경 전 DB 값을 비교한 뒤 조건부 value UPSERT를 수행한다. 같은 값+기존 version은 revision을 보존하고, 같은 값+누락 version은 최초 공유 등록을 남기며, 실제 변경은 값/version을 함께 commit한다. 같은 값의 저장을 새 편집·재승인 시각으로 취급하지 않는 것이 의도한 변경이다. 혼합 UTC/KST의 실제 순간 비교와 더 최신 원격 편집 수락·진짜 로컬 편집의 업로드를 기존 CentralSettingsSyncService+격리 SQLite로 검증했다. 기존 RAM cache에 짧은 Lock과 무효화 세대를 적용했다. get의 DB 조회 중 clear/새 write가 끝나면 이전 결과를 cache에 다시 넣지 않는다. write의 commit·close 성공 및 동일 세대일 때만 값을 게시하고, 늦은 ACK/실패는 해당 키를 무효화한다. DB I/O는 Lock 밖이며 진행 중 get 자체의 이전 snapshot 반환은 허용한다. 외부 프로세스가 clear_cache 없이 바꾼 값을 자동 감지하는 기능은 추가하지 않았다. MainWindow._save_window_geometry와 AlertSettingsDialog._save의4개 set을 각각 기존 set_many1회로 합쳤다. geometry의350ms singleShot·종료 시 최종 저장·frame 좌표/복원, Alert의 검증·실패 시 미수락·rollback/재시도를 실제 Qt 경로로 확인했다. bootstrap의 sync→clear_cache와 Drive restore→clear_cache를 유지하며, 별도 direct SQL backup/import·명시 Drive 업로드와 다른 설정폼 worker의 기존 수명은 바꾸지 않는다.

**011 비용·검증 선택.** 격리 native SQLite의 동일4키 저장은 연결1/BEGIN1/COMMIT1/시계1/INSERT 시도8을 유지하며 실제 반영행8→0이다. 모두 실제 변경한 geometry/Alert4키는 각 연결·BEGIN·COMMIT·시계4→1, 반영행8 유지다. version 비교에는 인덱스 기반 scalar subquery가 있으며 별도 SELECT 왕복은 추가하지 않았다. no-op도 연결/transaction/SQL 시도는 남는다. 물리 I/O·WAL·잠금 대기·장중 latency와 cache Lock 비용은 미측정이다. 기존 test_settings_repository/test_central_settings_sync/test_main_window를 보강하고 관련 설정폼·backup/Drive·AppController 검사를 재사용했다. 현재 소스 관련 **14모듈·고유175건/140.776 worker초 통과**(독립2묶음 병렬). 계획/발견/실행/통과 ID, 변경8개 ID, 소스 불변, worker/자식 종료와 native TEMP 제거를 대조했다. F01의 전체 기동과 F22/F23/F24의 운영 수락은 승격하지 않는다. **R3-011은 검증 완료(등록 개선 코드·로컬 관련검증)**다. 다음 미보류 대기 작업은 **R3-012(열 설정 저장의 변경셋)**이다. R3-032의 bootstrap daemon/restore 수명과 R3-056의 전송 중 편집 ACK 문제는 별도 미결로 유지한다. [근거/실패진단/정확한 범위](../../REFACTOR_BACKLOG.md#r3-011-설정-no-opcacheui-batch-완료--2026-10-20).

**003 테마 revision 영향.** 동일 cap/파생값은 일반 updated_at과 SELECT * revision을 바꾸지 않아 cap-only 충돌 없이 원격 import한다. 실제 테마/catalog 편집은 기존 CAS/충돌/pending·retry로 보호한다. 가격 확인시각은 계속 revision에 포함되므로029의 광범위 hash 책임 결합은 별도 미결이다. 기존 hash열·편집 보호 정책은 바꾸지 않는다.

**023 Window·legacy 시계/소유.** JournalWindow는 기존 query-client.server_now로 현재 날짜·분 gate·수신 receipt를 얻고, 조회일/봉 원본을 별도로 보존한다. live import의 선택 observed_at은 metadata에만 쓰며 partial confirmed_at=NULL과 기존 확정 데이터 권한을 유지한다. monitor daily 복사의 lookup day와 receipt도 분리한다. CentralJournalSync는 기존 cached CentralContentClient 시계로 새 legacy 수락 imported_at만 기록하며 원본/fingerprint·동일 source idempotence·merge transaction은 보존한다. SettingsBackup은 legacy catalog INSERT/UPDATE에 배치당1회 KST receipt를 명시한다. MainWindow는 기존 동적 controller clock을 Drive service에 묶어 v1/v2 실제 restore까지 전달한다. archive 생성시각/bytes와 schema DEFAULT·과거 migration imported_at은 바꾸지 않는다. API 조립 전 strict startup/standalone은 공통 KST fallback이며 키움 동기화 완료가 아니다.

**023 선택 검사/잔여.** 변경 영향에 맞춰 기존 journal detached/database/schema/selected-account/workers, central journal/content, settings backup/Drive/MainWindow/AppController/strict restore/worker controller 검사를 재사용한다. fake transport+native SQLite로 수신→저장→reader·원일/확정 보호·동일 재수락0·시계오류 rollback/retry를 확인한다. 전체 감사를 다시 읽거나 all-local을 자동 실행하지 않는다. TradeAnalysisPreparationService는 기존 JournalWindow의 지연 query-client clock을 주입받고 prepare마다 KST observed_at1회를 실제 daily import로 넘긴다. 조회 episode 날짜와250 limit는 유지하며 수동 UI와 자동 worker가 같은 preparer를 쓴다. provider 오류를 DB import 오류로 감추지 않고, 원래 DB오류 cached 분석은 보존한다. 미주입 standalone/startup만 공통 KST fallback이다. 실제 ctor/UI/QThread 및 native daily metadata/reader 검사는 기존 test_journal_detached_flow·test_trade_analysis_preparation_service를 재사용한다. prepare의 동일 observed_at을 기존 save_trade_analysis_setup의 legacy/scoped now와 journal_analysis_revision now에 전달한다. 원자 판정/수동 cycle 이전·계좌 origin/canonical과 revision hash/ID는 그대로이며, 동일 ID는 최초 created_at을 보존하고 새 입력의 새 revision만 이번 시각을 쓴다. 기존 service/native·Window/QThread·journal_enrichment/research link·central_journal_sync 검사를 영향에 맞춰 재사용한다. setup/cycle의 updated_at 및 v2 revision created_at sync 버전 계약을 보존하며 legacy revision 전송이나 실제 다중PC/network 수락으로 확대하지 않는다. 기존 JournalEnrichmentService는 지연 query-client provider를 쓰고 implicit 시각은 KST로 정규화한다. 명시 legacy now의 저장 형식은 유지하며 retry는 양쪽 as_kst로 순간을 비교한다. 자동분석 등록/선별5종·각 started/completed/failed callback은 같은 현재시각1회를 공유한다. API 조립 전 recover는 명시 공통 KST fallback으로 기존 순서와 원본 기록을 보존하며 동기화 완료가 아니다. 서비스 native/창·worker/backup 검사를 선택 재사용하고 ID·scope·전이·MAX3/5·10분/partial30·완료/복구 원본을 확인한다. HistoryWorker의 수신 callback은 scope 검증 후 기존 client 시각1회를 저장 전에 얻어 native upsert_history_sync의 비용 confirmed_at과 commit후 task/설정에 공유한다. 원체결/정산일·금액/scope는 보존하며 clock 오류는 저장전 기존 실패 callback으로 보낸다. invalid/거절에서는 새 수락 receipt를 샘플하지 않으며 실패 callback의 후속 task 시계는 별도다. COMMIT뒤 account refresh→task→reload 순서는 유지한다. backfill 실패 callback은 유효 day의 같은 client 시각1회를 native mark_bar_backfill의 updated_at과 저장 성공후 task.fail/retry에 공유한다. SQL/clock 오류에는 이전 row/실행 task가 보존되고 mark 오류는 기존대로 전파된다. 수락표본을 쓰므로 긴 저장이 날짜/20:05를 넘더라도 cutoff는 그 표본을 따른다. registered023 코드·관련 로컬 검증 완료 당시 다음은003이었다. 당시003 완료 뒤005로 이어졌으며 현재 작업은 정본을 따른다. 이 시계 작업의 SQL/transaction·owner·계층·I/O지점은 늘리지 않았다. task ledger의 중앙 송수신을 새로 구현하거나 검증한 것은 아니다. 외부 Drive/NAS·PG·설치 앱/장중·모든 Failover/Parallel clock 위임의 운영 수락은 별도 미검증이다.

**023 시각 비교 경계.** MainWindow는 기존 체결 signal을 받아 압력 snapshot을 enqueue한다. 표본/기한을 `as_kst`로 비교하고 결측 gap은 결측으로 다룬다. 기존60초 경계·gap만료를 유지하며 실제 저장/원장은 기존 writer owner가 소유한다. finalization의5분 retry·최대2회도 같은 시각 정규화로 유지한다. 관련 native 전체 일지/운영 계좌 수락을 뜻하지 않는다.

**현재 책임.** EntrySnapshotWriter는 체결 스냅샷과 수급/프로그램 보완 queue, journal 저장소는 account scope·원본/provenance·분석 revision을 소유한다. JournalWindow는 history/query·그룹 편집·복기·차트/분석의 UI와 worker 수명을 조정한다. ThemeRepository는 프로필 테마 원본·사용자 승인/편집을 소유하고 OCR/추출/preview는 적용 전 제안 경계다.

**상태·실패·공식 경계.** 기존 snapshot enqueue/backfill, scoped history/query/analysis, 테마 preview→bulk apply와 백업/복원 API를 쓴다. 네트워크 동기화는 기존 service/runner가 수행하고 native repository의 transaction을 우회하지 않는다. CentralContentSync의 identity/hash delta와 CentralJournalSync의 매 회차 원격 스냅샷 대조에 따른 변경 문서 전송은 서로 다른 기존 경로가 소유한다. 전송 ACK는 새 로컬 편집까지 저장됐다는 뜻이 아니며 원본/보완/사용자 편집을 구분한다. Drive/NAS/파일 복원도 같은 변경 필드와 revision의 소비자다.

**기존 감사 후보(최신 완료 상태는 백로그 정본).** R3-044/045의 ACK/내용 hash, 051·055·057~059의 일지 read/출처/사후입력, 071/074/075의반복작업,076~077 catalog/미사용profile,081~082 표시책임(064뉴스보존·080기본추론중복은로컬완료,054실제catalogcaller는별도재평가)을 개별 처리한다. 기존 delta 구현을 재개발하거나 모든 일지·뉴스 sync가 이미 delta라고 설명하지 않는다.

**관련 검사.** [snapshot service](../../tests/unit/test_journal_snapshot_service.py), [일지 보완](../../tests/unit/test_journal_enrichment.py), [콘텐츠 sync](../../tests/unit/test_central_content_sync.py), [일지 sync](../../tests/unit/test_central_journal_sync.py), [테마 backup](../../tests/unit/test_theme_backup.py). 원본 재전달/보완 보존·편집 중 ACK·실패 retry·restore/close를 변경 경계에 맞춰 확인한다.

<a id="diagnostics"></a>
## 진단 — F25

**Windows 서버 RSS(2026-11-05).** 인증 진단은 기존 서버 측정 함수에서 현재 프로세스의 Windows RSS를 `psutil 7.2.2`로 읽는다. 기존 API와 Windows 설정 화면은 같은 바이트 필드와 측정 불가 상태를 소비한다. 연구 자원 제한과 프로세스 신원/수명은 기존 소유자가 맡는다. [비교·실패 근거](opensource-assessment-20261030.md#windows-server-rss-psutil).

**NAS test/운영 수락 경계(F25/F23/F24, source181 로컬 통합·실제NAS 미수락):** 관리자는 optional test contract/image pin과 helper2+config의 exact3-target 갱신/원복을 소유한다. 기존 Operator는 pin 복사1회·selected/no-pause admission/cache 재검증·고정 image/parent report binding을 소유하며 test 결과로 prod gate를 쓰지 않는다. 기존 shared runner/runtime·prod contract/image·권한·PG/lease/정리 owner는 유지한다. 현재 main 관련81 PASS/정리·동일바이트 offline Linux25 PASS를 구분하고 기존 후보unit76은 중복 계산하지 않는다. 실제NAS의 기존 index/config/runner/runtime·source-mode/image 무결성은 관측했지만 version/lock 계약 항목이 없고 최신 test와 다르다. 실제 image package/CLI·source freeze·새3-target 적용 승인·NAS7/valid lease/post-fence는 미수락이다. NAS/운영 변경0이며 helper2-only 승인을 확대하지 않는다. [소스별 검증](../../tmp/ar-v1-wave58-20261101/nas-test-policy-integration181/acceptance181.json)·[실제NAS 관측](../../tmp/ar08-nas7-nonsecret-contract-inspection180-20261101/revision2/user-run001-receipt.json).

**현재 PC artifact와 실제자료 소비의 구분(F25/F23, source182):** 최신 full1 빌드는250.752초·5000파일/변경9모듈·기존자원·source/도구/venv 불변·실제Job/HANDLE 종료로 수락했다. 결과EXE와 앱/실제자료 소비는 실행하지 않았고 AR08-C18은 부분 완료다. 빌드 중 shared11 pin과 빌드 종료 뒤 정본 현황 갱신을 구분하며 원빌드 receipt/manifest를 재작성하지 않는다. NAS191은 기존 정확3 SQL의EXPLAIN만 확인했고 업무SELECT/추출0이다. root 추정행수/비용은 실제데이터수/시간/용량이 아니다. 기존 native 저장·복구·권한/수명 계약은 유지하며 실제사본/이전은 별도승인이다. [최신 빌드](../../tmp/ar08-c18-artifact188-20261101/preparation/root-actual-build-acceptance188.json)·[EXPLAIN3](../../tmp/ar05-export-plan191-20261101/execution/acceptance191.json).

**AR 테스트 임시권한 현재수락(F25/F23, record194):** 사용자의검증방식명시승인뒤후보0668b307/관련14격리검사·수동관리자인증·별도SSH 캐시없는id0와정확원본문/신규receipt를수락해만료22:28:26KST를확인했다. 새ruleSHA158e62a9…/dev2304 inode40247/0440이며원operator·과거활성화/회수/복원원문은보존한다. 시간연장을업무범위확대로보지않으며actualAR세션종료/새만료에이새rule만검증회수한다. 최초실패/거절은이력이고현재NAS권한대기가아니다. [현재권한](../../tmp/ar-v1-test-access185-20261101/execution/extension194/current-access194.json).

**실행 snapshot과 개발 감사 이력의 소유(F25/F23, source182):** 실행stage와 원repo/CI 감사원장을 분리하고 기존 source별 stage21/helper81/Linux25/로컬119 근거를 보존한다. 실제 NAS119 및 current exact7의 두fresh owned PG/job(계좌1+wrapper6)을 actual ID/저장report·nonroot/job-bound fixture/유효 ReplayDatabaseLease·원복/cleanup/post-fence로 수락해 AR08-C20을 완료했다. native helper2+격리pin/config exact3·백업3/index6·shared runner/runtime/active raw는 확인됐다. 사용자 승인4h 전체root 임시규칙은 실제 native 만료 검증 후 사용했고14:51:45 KST에 회수, cache-independent 일반root 무암호 거부와 원래 제한 operator 성공을 확인했다. 후속 사용자 정정으로 이 권한은 전체AR 테스트 세션용이며 개별NAS7 완료를 회수시점으로 삼지 않는다. 원만료18:28:26KST까지동일규칙을실제복원했고13개격리검사/정적리뷰와별도SSH비캐시root허용·원3파일/hash/inode보존을확인했다. 새4시간/무기한/자동연장은허용하지않고원만료또는전체테스트세션종료·정리때끝낸다. PIDs 미지원은 사실대로 유지하고 memory/CPU/시한/격리/신원·실패/복구는 기존 owner가 담당한다. 실제자료 인계·운영앱/DB·배포/CI·전체로컬회귀는 이번수락밖이다. 원실패/역사UNKNOWN을 지우거나 실제성공ID를 요청에서 합성하지 않는다. [최종 수락](../../tmp/ar08-nas7-official-path182-20261101/execution001/C20-acceptance182.json)·[권한회수](../../tmp/ar08-nas7-official-path182-20261101/execution001/temp-access-close001.json).

**AR05 실제자료 준비의 연결 근거(F14/F23/F24/F25, 실제 이전 미수락):** 실제 목록 GET1은 mock1/real조회1, 유일mock status GET1은 HTTP200·identity일치/MANUAL·control/runner응답null을 반환했다. 설정전후불변/실제Job종료, 원문·식별자 비공개/최소DPAPI mapping을 확인했다. source-level GET 검토는 종결했고 반복하지 않는다. 이는 native 미확정/QUEUED/미완료 없음이나 원자control/RAM cut·source_instance/run/T0/target/이전 선택을 뜻하지 않는다. 현재 공개 projection만으로 native 전체 inventory를 대신하지 않는다. [실제목록](../../tmp/ar05-account-context-get181-20261101-prepare001/execution-receipt001.json)·[실제한정상태](../../tmp/ar05-selected-mock-status-get181-20261101-prepare001/execution-receipt001.json).

**MAIN172 검증 결과 연결(F25, F24 수명 보존):** 기존 `nas_operator_worker.run_tests`가 실제 suite를 발견하고 `startTest`/`addSuccess`를 관측해 discovered/executed/passed ID를 결과에 보낸다. parent의 기존 `worker_failure_diagnostics`는 안전한 완전 배열을 전달하고 잘못된/비밀 포함 배열은 unavailable로 표시한다. 이 available 값은 안전한 전달 가능성이지 선정 일치/전체 성공 수락이 아니다. class setup 실패·skip/xfail·부분 실행을 성공 ID로 합성하지 않는다. 기존2MiB·owner/lease·mount·drain/restore·post-fence·실패 판정·시한과 제품src420파일은 유지했다. 현재 관련77 PASS(일반사용자1.035초, 초기14포함), 실제 root import/소스·기존worker/Job/tree·정확UUID/성공TEMP 정리와 구조1300site/증분172기록/6869정의를 확인했다. 이는 로컬 도구 계약 수락이며 실제NAS helper 설치/정확7건 fresh2job/유효lease·정리 수락은 미완료다. [현재 operator77 근거](../../tmp/ar-v1-wave54-20261030/nas-receipt172/acceptance172-related77.json)·[정본](app-target-20261023.md#0-현재-기준과-진행-현황).

**현재 MAIN155 선택 관측 owner(F25) 연결:** matching/보고/원오류·RAM·시각·차용자원은유지한다. 현재target CPython3.13에서native생성후ident게시전실패도기존동일owner가실Cjoin까지소유함을2gate/원오류/receipt재사용으로수락했다. 관련62는실제caller포함고유합집합이며도구전체/설치·운영완료가아니다. C11/C12·다른runtime·원App98/91timeout/queryAbort UNKNOWN은별도남긴다. [수락](../../tmp/ar-v1-wave51-20261029/integration155/acceptance155-c10.json)·[현재잔여](app-target-20261023.md#v1-detailed-checklist).

**MAIN150 기존 Recorder 수명:** publicclose는 수락 관측/flush와 기존FIFO attempt를 마친 뒤 실제 writer join/첫 원오류 전달을 소유한다. OSError continue/MISSING/skip/log와 matching/시각/큐limit·unmatched RAM 보존, close 강제expiry/새MISSING/retry0. 기존golden8 realfile/실caller3 포함23 PASS다. controlled stream은 실디스크장애 증거가 아니며 owner는wait_for_writes 대신 직접close를 소비해야 한다. factory/Window/Controller receiver·전체tool/운영은 미수락이다. [근거와 제한](app-target-20261023.md#main150-recorder-class).

**MAIN149 REST 비교 observer:** 공개 immutable4fact는 기존RAM/Condition/실thread만 읽는다. 원 report PermissionError·failedjob/localresult/remaining FIFO2를 보존하고 재쓰기하지 않으며 actualstop/nativejoin 확인 뒤 core 종료를 영구 차단하지 않는다. UNKNOWN은 ready가 아니다. 관련120 PASS, 별도Recorder class/owner·RAM 재시작 내구성/실NAS는 미수락이다. [증거·한계](app-target-20261023.md#main149-rest-main-lifetime).

**MAIN148 선택 REST 비교 factory:** 아직 입력/수락job이 없는 새 비교daemon이 조립 실패를 넘어 남지 않도록 publicclose/actualjoin을 연결했다. actual factory/daemon/전송 guard와 기존17 등 관련97 PASS이며 기본 수신·계좌·원본/정상 import와 현재 연결은 그대로다. 별도 RealtimeValidationRecorder는 class 후보만 준비됐고 owner/native 수락이 남아 있다. [범위와 다음](app-target-20261023.md#main148-factory-rest-client).

**MAIN144 PC 선택비교도구:** publicclose가observer의입장/수락FIFO/actualjoin·원오류/RAMremaining을소유하며원본수신/주문·계좌/공통token·clock이나다른client를닫지않는다. B1경고잠금회귀는negative1로검출했고V2같은ID/관련87은PASS다. 기본로그는stage/API/error class1회이며비밀본문0·report자동재쓰기0이다. graceful I/O/parent시한·main전체/실NAS idle/lease attestation은새로수락하지않는다. [단일현재근거](app-target-20261023.md#main144-validation-lifetime).

**MAIN139 owner-state 현재 계약(F25·간접F01/F23/F24):** app의 실제 조립이 installed/not_installed를 소유하고 기존 trace/run/control owner가 thread/queue/ACK/retained child의 한정된 사실을 제공한다. admin `/api/v1/operator/state` version1을 strict operator가 소비하며 public health에는 버전만 둔다. version1은2GET, 비광고 구형은 원4GET·기존3진단 route를 유지한다. 광고 후 malformed/404는 fallback하지 않으며 valid UNKNOWN은 readiness 관측만 허용하고 idle은false다. `diagnostic_exclusion`은 계좌/주문/모든 업무 idle 권한이 아니다. portable55/55와140의도구40모듈부재 ROLE6환경쌍을 국소수락했다. native thread/자식/ACK 일부는 실제 실행, Docker/NAS 전달은 모의 경계이며 실제Linux4/helper/NAS6·validlease/source/post-fence·운영은 미실행이다. 도구와 원자료는 제거0이다. [단일 정본·검증 범위](app-target-20261023.md#1160-main140-뉴스출처저장operator설치-국소-검증--2026-10-27).

**MAIN135 당시 operator 관측 국소 갱신:** 기본status만선택진단404를UNAVAILABLE/idleUNKNOWN(운영bool은false)으로명시하고성공한health/busy원값을보존한다. defaultsnapshot/fence/pause/설치·복원·배포는원strict이며새권한을만들지않는다. 같은4GET/5초와정상shape·인증/5xx/timeout실패를portable25에서모의검증·native정리했다. 실제Linux4/NAS/helper/owneridle·lease/배포수락은없고도구/자료실제제거도0이다. [당시 근거](app-target-20261023.md#1158-main135-operator-상태-관측-검증--2026-10-27).

**MAIN134 선택 validation 연결 갱신:** 도구는현재그대로유지한다. bootstrap/factory는원래enabled분기에만canonical recorder/parallelclient를조립하고common worker는TYPE_CHECKING만참조한다. query/disabled구성에서validation파일까지실제물리부재39와미import, 기존native기동/수신·fallback/worker/종료43관련검사PASS를확인했다. 원기록경로/설정/UI/14일로그정책·공통0B/REG/ACK/재연결·권한변경0이다. fullenabled desktop통합과operator/설치NAS전체tool제거수락은별도미검증이다. [현재근거](app-target-20261023.md#1157-main134-통합-검증--2026-10-27).

**2026-10-27 구현·검증 국소 갱신:** MAIN128 신규PC로그정책은safe기록/정확owner/PID일별회전/14KST일·sticky retention오류를관련118+실제2child1로국소수락했다. 실제기존로그/원자료/도구삭제0이며NAS로그는기존동작을유지한다. marker없는파일/열린만료파일보존·닫은뒤정리·자식/Job/HKCU/TEMP종료를검증했다. 전체관측/NAS/operator/원queryAbort는미수락이다. [소스별 근거·남은 조건](app-target-20261023.md#time-audit-followup).

**2026-10-27 시간 감사 보충(수정·새 PASS 아님):** F25/F01의 기존 기본로그 reported_only/sticky/unknown과 도구 분리 근거는 유지한다. 파일명·14일 정리에만 PC KST 예외를 허용하며, MAIN125 미구현/로그 TMP HOLD와 업무시계 범위를 분리한다. 이 감사·문서 반영은 새 recorder/replay/운영 probe나 전체 관측 수락이 아니다. [담당 AR·우선순위·완료 기준](app-target-20261023.md#time-audit-followup).

**MAIN116 연결 계약(2026-10-26):** per-Hub관측epoch/readyreceipt는기존lifecycle의명시observer,raw선별seed/typedreadback/첫receipt/rollback은기존Databaseinitializer가소유한다. sourceCLI/조회UI는권한을생성하거나편집을재설치하지않는다. native수집/저장/transport/codec와정상consumer의owner는유지한다. 현재고유656 중 656 PASS/0 FAIL·worker2/원실패·미실행/운영제한은[단일현황](app-target-20261023.md#1149-hub-관측-소유와-current-state-native-이전-통합--2026-10-26)을따른다.

**AR09/07 국소 계약(2026-10-26):** native 수신/RAM/writer/재시도·종료는 기존 owner, optional input/flush/legacy 관측 상태는 명시 lifecycle recorder다. 후보·명세 게시2곳은 기존 native 합류 도구를 재사용하며 주문권한을 생성하지 않는다. 동일본문526·EOF정리후11 PASS/guard34·전체 도구부재/PG/이전·기존 UNKNOWN/운영은 구분한다. [현재 근거·잔여](app-target-20261023.md#1146-ar09-collector-관측-소유권과-ar07-게시-수명--2026-10-26).

**AR09 구독 관측 국소 계약(2026-10-26):** native RegistrationProgress/approved atoms·send/ACK·source approval/ready/종료는 Collector, recorder.complete는 명시 주입된 전용 도구 owner다. 현재77 PASS/구독 파일1개 실제 부재를 검증했으며 legacy/upstream/flush·전체 app/actualNAS5는 미완료다. [현재 근거·잔여](app-target-20261023.md#1145-ar09-실시간-구독-관측-상태의-소유권--2026-10-26).

**AR09 REST 국소 계약(2026-10-26):** native transport/ingest/cache/inflight/credential/drain은 기존 broker, tool ordinal/lineage와 기존 scope 관측은 명시 optional module이 소유한다. 관측자가 native 실행·재시도/저장 ACK를 대신하지 않는다. 실제 본류138·capacity caller11 복구·원 seed/실패/취소/종료를 확인했고 전체 producer/app/actualNAS5는 미완료다. [현재 근거·잔여](app-target-20261023.md#1144-ar09-rest-요청관측과-native-실행-소유권--2026-10-26).

**AR09 Hub 현재:** 설치 app/replay와 공통 source fixture의 기존 observer/clock을 명시 연결했다. 원301의 오류1은 strict delivery oracle이 공통 factory 누락을 잡은 결과이며 원문/TEMP를 보존하고 helper 입력만 보완한6을 재검증했다. 기존 도구/자료는 유지하며 전체producer5/app 부재·actual NAS sealed5는 미완료다. [현재 근거·제거 조건](app-target-20261023.md#1142-ar09-hub-공개-관측과-발행시각-소유권--2026-10-26).

**AR09 현재:** 도구/자료를 유지하고 Runtime/Tape의 실제 policy/수명과 공통 dispatch를 분리했다. 실제 tool caller2의 동일 re-export/canonical QueryStore key는 caller 이관·계약 동시 변경까지 유지한다. 물리 부재 실행은 common leaf만이며 producer5 전체가 아니다. 남은 capture/workload 안전 책임·전용 처리를 구분한 후 부재 시나리오를 확보한다. actual NAS sealed5/유효 owner·lease·source admission은 필수 미실행이고 PC55/45/unit15로 대체하지 않는다. [107 정정·원실패/UNKNOWN·후속](app-target-20261023.md#1141-ar09-공통-수명-경계-통합--2026-10-26).

**AR v1 17차 계약.** 기본 로그 경로를 재조회해도 개인자료 복사를 유발하지 않는다. 녹화·재생·정밀 진단의 실행/저장/정책은 변경하지 않는다. [현재 국소 근거](../../data/diagnostics/ar-v1-wave17-20261024/verification.json). actualwheel/선별이전/운영은 별도 미검증.

**AR v1 15차 계약.** 진단workload는service의privateRunner가아닌root의actualRunner getter만관측한다. observer로권한/worker를새기동하지않고기존HTTP응답을유지한다. [소스별국소검증/미해결](../../data/diagnostics/ar-v1-wave15-20261024/verification.json).

**AR v1 8차 계약.** 장후 seed/capture는 공개 after_close 경계로 전환했다. 기존 녹화/진단과 공통 안전 owner는 유지하며 제품 역할의 전용 도구 부재/전체 관측 수락은 별도 잔여다. [소스별 검증](../../data/diagnostics/ar-v1-wave8-20261024/verification.json); 현재 판정은 정본0절만 참조한다.

**AR v1 7차 당시 관측근거.** 제품단일ContextVar/첫origin과기존진단scope는별개로보존. SQL/queue/worker증가없이sharedjoin에bounded ID2개만추가했다. 현재도구 installed계약을유지하며도구물리부재·per-store observer는미수락이다.

**AR v1 5차 현재 계약.** 새 acquisition owner의 실제native release와 기존 관측record의 UNKNOWN/close_ms미상은 구분한다. 기존 installed metric 샘플/1회factory와 low-level borrowed/retained 계약을 유지했다. 직접 metric 연결은 실제per-store observer 이관 후 제거할 임시호환이다. 현재 도구/자료를 제거하거나 운영진단을 실행하지 않았다.

**AR v1 4차 당시 계약(최신5차와 구분).** source/request ContextVar 소유권을 정상 domain으로 이전하고 postgres legacy aliases는 같은 객체다. 자료 읽기 edge는 영향 검사에 남고 구조 순환은 실제 코드 import로 판단한다. 현재 진단 기능/자료는 삭제하지 않았다. [현재 근거/미확인](app-target-20261023.md#1111-4차-구현--실제-상태workflow-소유권과-불변-수락).

**AR v1 3차 구현 계약(현재 통합 검증 중).** 현재 도구를 유지한다. Runs가 쓰는 실제 측정 본문은 diagnostic_sampling, 테스트 PG URL 순수 검증은 postgres_test_target가 소유한다. 두 CLI 물리부재 검증을 전체 tool 제거 가능성으로 확대하지 않는다. ContextVar/native observer/metadata/store/root 조립의 남은 역의존은 AR09 후속이다. [근거/현재 잔여](app-target-20261023.md#1110-3차-진행--승인된-ar의-연속-실행).

**AR v1 1차 호환 계약.** 삭제된 Collector private3필드를 요구하지 않으며 현행 index-save task/flush lock의 cold 거부를 canonical seed 밖에서 검증한다. 기존 v1 seed/digest와 설치된 기록 reader/fixture role·lease는 유지했다. 실제 PC PG session2/reconciliation16과 unit 관련 범위는 [1차 근거](app-target-20261023.md#118-1차-구현--ci-원인-보완ar02-선택-상태). nas_operator_owned/운영 전체 재생은 미실행·미수락을 유지한다. recorder/replay/정밀진단은 지금 제거하지 않으며 향후 물리 부재 검증은 AR09 후속이다.

**재설계 대응·검증 구분:** v1 제시 당시 진단/collector/기본 안전 owner는 바꾸지 않았고, 이후 국소 구현 근거는 바로 위 AR v1 계약에 분리한다. 도구의 installed/absent와 기본 오류·작업 상태 경계는 [AR-09 기준](app-target-20261023.md#113-ar별-구현-세부-범위와-순서)을 따른다. `0d7da46`의 현재 CI 실패 원문과 사용 제한은 [단일 최신 현황](app-target-20261023.md#0-현재-기준과-진행-현황)에 연결하며, 로컬 관련 PASS·과거 운영/owned PG 근거를 현재 전체 연결/새 구조 수락으로 대체하지 않는다.

**현재 뉴스 claim 단계 계약(2026-10-23).** 실제 `recover_stale`, `claim_candidates` 두 phase만 기록한다. 후자는 잠금 SELECT+RUNNING UPDATE+최종 정렬/fetch를 함께 포함하므로 과거 `select_candidates`/각 `mark_running` 시간과 직접 일대일 비교하지 않는다. 공통 cursor SQL/COMMIT·call ID/backend·오류 상관관계 및 probe 실패 격리를 재검증했다. OFF 전후 비용과 ON raw 호출은 분리하고 wait 표본 수를 대기 시간으로 환산하지 않는다. 녹화/리플레이 실행·코드 변경은0이며 전체 F25 운영 상태는 확인 불가를 유지한다. [뉴스 claim 검증](../../data/diagnostics/r3-news-claim-20261023/verification.json).

**이전 종료 보완 계약(PC6 보강·NAS 범위 정정 전, 당시 로컬).** historical condition/native/replay codec/원문은 유지했다. 현재 동결 소스 관련998건/49모듈 PASS(실패·오류·skip0), worker 1064.759초/경과 571.080초, 49worker/tree 종료·성공TEMP49개 부재다. 별도 실제 operator PostgreSQL8건 PASS(039/043의3건·보존VI4건·079 owned1건), 두 격리job의 source/lease·cleanup·post-fence·활성release/보호metadata 불변을 확인했다. 전체1157파일 LF `75049d8149c2e630029bd6d4ea0c26644df5d52b6bb64d640629e2fc5abe4ec5`. 증분60기록/6147정의, 이전57기록·미결49 원문을 보존했다. 전용 검사68건을 명시 철회했고 설정 검사1개 이름변경·기존8개 본문/후속 Compose1개만 범위대로 수정했다. 최초 현재998의997PASS/Compose1FAIL과 이전723의720PASS/marker3FAIL·오류capture를 별도 보존했다. 자동query full_required의 전체 확장은 미실행이며 caller/owner/consumer 근거로 관련998만 실행했다. 전체회귀 수락이 아니다. 구조·QueryStore 소비·증분 정적 검사는 PASS다. 기존 실행증거 gate는 설치된 NAS operator가 성공 건수만 내보내고 성공 test ID 이벤트를 내보내지 않아 실제PG 성공ID를 대조하지 못해 FAILED다(현재 첫 거절은 VI4건). 079의 별도 nas_operator_owned·유효 lease 검사도 PASS지만 성공ID 배열을 selector로 합성하지 않았다. 필요한 최소 후속은 기존 격리 operator의 실제 실행ID 보고 연결·동일 gate 재확인이며 운영 helper 변경은 실행하지 않았다. 이 증거 차단이 해소되기 전 R3 전체 종료를 승인하지 않는다. [근거](../../data/diagnostics/r3-050-final-20261023/verification.json).

**이전 집중 검증·이관 계약(당시 2026-10-23, 로컬).** 039 최근 reader·043 뉴스 terminal attempt·079 격리 replay의 등록 잔여PG만 로컬 종결했다. 083 상시guard 기반은 유지하되 현재 실행실패로 canonical 진행을 유지하며 전체 종료는 보류한다. clock2write observed_kinds 등록1항목으로22record/native관측을 연결하고22/19/3 oracle·SQL/module나머지AST를 유지했다. local723/36·실제operatorPG5는 별도 영수증이며 이전source/최초실패를 현재PASS로 재사용하지 않는다. 동결 현재소스 로컬 관련723건/36모듈 720PASS/3FAIL(종료검증 보류), 별도 실제 operator PostgreSQL 필수5건 PASS를 확인했다. 로컬 worker 1102.074초/경과 675.188초. 전체1160파일 LF `1aa733b665c961d8dbc5801b4d6c019f745f93f9ca64ec8a97cbb3ba75565b98`. 증분57기록/6215정의, 추가·기존본문수정·삭제0. 로컬/PG 보고서는 분리하고 계획·발견·실행 ID/소스/worker·tree 종료/성공 TEMP 정리를 최종 verification 원문과 대조했다. 36worker/tree 종료·성공TEMP 35개 실제부재·실패TEMP 1개 보존. 구조/consumer/static PASS, execution gate는 현재3실패 증거를 거절하여 FAILED다. CI연결은 로컬확인/새GitHub실행은 미실행이다.  상시 guard/consumer/증분원장/전체필수CI는 유지하고 R3자동준비큐는 중단한다. [검증](../../data/diagnostics/r3-closeout-20261023/verification.json).

**이전 스물두번째 추가 계약(2026-10-23, 미커밋 당시).** 083 STOP의 정적 reference 증가를 consumer gate가 차단했고427개 기존 identity와 분류를 보존하여 정확 occurrence1만 추가했다. runtime 조회수3→3으로 호출 예산 증가는 없다. guardrails.json/CI/full_required·외부/freshness 불변,이번 관련658/30과 과거655/29 및 완료033 supplemental3을 분리한다. local build3token 정적대조와 기존 source-runtime2/ASGI health1 subset을 실행했으며 실제 운영build/hostedCI는 미검증이다. 근거 [통합 검증](../../data/diagnostics/r3-parallel-20261023/wave22/verification.json).

**이전 스물한번째 추가 계약(2026-10-23, 미커밋 당시).** 083 한 명령의 검증된 raw execution report들을 기존 count-fold 위치에 전달한다. ID/count 동일 snapshot·원 platform raw counts·lastwins·기존 실패순서 유지, read/parse2N→N. raw dict를 count phase까지 보유하여 N3 traced peak +331045B이며 지속 cache/RSS 측정이 아니다. CPU/전체CLI 속도 개선 주장0·source/worker/ID/full_required/CI 범위 유지. 근거 [통합 검증](../../data/diagnostics/r3-parallel-20261023/wave21/verification.json).

**이전 스무번째 추가 계약(2026-10-23, 미커밋 당시).** 083 inventory root shallow-copy로 모든 해시/원AST·선정·full_required·CI 범위를 보존했다.3pair CPU19.125→7.063초는 관리도구 비용이다.기존20검사와root증분54/6209·관련현재검증을 연결하고 원래미결49/guard/baseline을 보존한다. 근거 [통합 검증](../../data/diagnostics/r3-parallel-20261023/wave20/verification.json).

**현재 열세번째 계약(2026-10-21, `b16c386d107b08218b2f97dc4d305f3d55ebc632`).** R3-039: recent API의31일 직렬 read를 기존 QueryStore의 한 multi-day reader로 통합했다. 전체 RAM/retry를 먼저 동결하고 native 날짜선택·operation/authority overlay를 같은 read owner로 처리한다. 31달력일 범위/실제1~5거래일/COMBINED 날짜키/SOR·빈이력·RAM-only 현재일을 보존한다. capture readers/recorded executor 등록·3owner 서명·consumer2site를 함께 갱신했다. 신규 실제PG1 미실행. physical I/O/WAL/운영 latency·writer wait와 실제 NAS/키움 수락은 별도다. R3-062: _condition_list가 승인한 문서를 즉시 고정하고 기존 background owner/tail로 native status save를 직렬 처리한다. cancel/close는 실제 native에 합류하고 첫 미전달 원오류는 poll1회로 기존 readiness 해제/3초 reconnect에 전달한다. 새 queue/pool/drop/coalescing/재시도 정책은 없다. 변경PG loopjoin1 미실행. strict TOP20는 market_events를 기존대로 불허하며 condition capture 전체재생/운영 latency/RSS/processkill은 별도다. DB 지연 중 승인 snapshot closure가 남는 비용은 숨기지 않는다. R3-079: 기존 version별/real archive 삭제범위는 유지하며 v4 CLOCK_COLLECTION만 기존 DELETE bind에 추가했다. parent clock seed는 v1 seal 전 준비해 digest·sequence/rollback 의미를 보존한다. 변경 nas_operator_owned PG1은 disposable fixture/valid lease 환경이 없어 미실행이며 ordinary PG CI로 대체하지 않는다. root1301 PC관련검증과실제PG5/053진단실패는구분한다.

**2026-10-20 `64a74e0` 계약 보완.** R3-036 후속 검증 보완(제품 변경0): 기존 replay의 lost-ACK 기대값1은 native thread와 추가된 owned save task의 실패 관측을 합산하는 RuntimeScope 실제 계약을 놓쳤다. thread/task OSError 각각1, raw error_count2, execution_succeeded=False·동일 save2회·outbox 복원을 함께 검증한다. 사건 중복 제거 정책이나 DB 시도 횟수를 바꾸지 않았다. 옛 기대값은 R065의 원본 load를 복원해도 동일 실패, 보강한 기대는 원본/현재 load 모두 통과한다. 초기 R036 관련90건에 replay-outbox 검사가 빠졌던 범위를 명시하고 향후 해당 소비자 검사를 함께 선택한다.

**현재 책임.** 기존 registry/trace/capture가 실제 호출의 단계·결과·출처를 기록하고 manifest/reader가 보존 자료를 해석한다. dedicated replay store/clock·lease와 owned executor는 재생의 격리/복원·실제 작업 완료를 소유한다. 관측 wrapper는 측정 책임이며 원래 DB 연결·transaction·업무 판단 owner를 대신하지 않는다.

**상태·실패·공식 경계.** 기존 진단 API/CLI와 capture schema/manifest/lease를 쓴다. 원본 trace와 replay 전용 쓰기를 분리하고 일반 앱/주문 transport의 새 필수 의존성으로 만들지 않는다. capture 성공, 입력 완전성, 재생 결과 일치, 실제 운영 수락은 각각 보고한다. 손상/용량/누락·취소·종료의 실패를 성공 표본으로 숨기지 않는다.

**미해결.** R3-022의 wrapper 유지 판단과 내부 비용·책임 평가는 별개다. v4 clock 삭제 범위(079)는 조건부 가설이며 실제 dedicated PG 재현 전 수정 완료로 처리하지 않는다. 운영 OFF/ON 비용과 전체 재생/복구 수락도 별도다.

**관련 검사.** [진단 runs](../../tests/unit/test_diagnostic_runs.py), [account context](../../tests/unit/test_diagnostic_account_context.py), [replay](../../tests/unit/test_diagnostic_replay.py), [PG 격리 replay](../../tests/integration/test_diagnostic_replay_postgres.py). 운영 접근을 새로 만들지 않고 해당 격리 계약을 선택한다.

<a id="contract-change"></a>
## 정당한 책임·계약 변경

1. 기능군과 실제 진입/소비자를 고른다. 현재 owner가 부적절하면 구체적인 상태·정책·수명·저장 경계와 변경 이유를 적는다. 파일 길이 또는 Manager/Service라는 이름만으로 분리/합치지 않는다.
2. 입력/단위/시계·RAM 상태·영속 키·transaction/ACK·실패/retry·교체/종료의 전후 차이를 적는다. 한 기능 이해에 필요한 파일/호출 깊이가 늘면 실제 책임 이득을 설명하고, 단순 전달 단계만 생기면 기존 경계에서 구현한다.
3. 필요한 caller·저장/조회·API/UI·연구/Shadow/실행·복원/기존 설정 소비자를 함께 수정한다. 과거 비교용 경로와 미연결 capability는 표시를 유지하고 미정 매매 정책을 임의로 채우지 않는다.
4. 관련 테스트와 실제 반례/비용 기준을 선택해 같은 의미로 검증한다. 위 검사 목록은 출발점이며 통과 주장이나 전체 검사의 대체가 아니다. PC/NAS의 `all-local`을 자동 시작하지 않고 GitHub CI의 전체 검사는 유지한다. 실제 PG/GUI/provider/운영 수락과 미실행은 따로 기록한다.
5. 책임이 바뀌면 이 문서의 해당 기능군과 MODULE_MAP, 관련 R3/F 상태·근거를 함께 갱신한다. 기존 owner를 삭제/이관할 때는 저장 설정·호환 API·재시작 복원·종료 책임의 남은 소비자가 없는지 확인한다. 문서만 바꾼 것을 구현·배포 완료로 보고하지 않는다.

<a id="automated-checks"></a>
## 변경 영향에 따른 검증과 비용 기준

```powershell
python -m scripts.check_architecture --base-ref HEAD --report tmp/architecture.json
python -m scripts.check_test_changes --query --base-ref HEAD --path src/변경한_경로.py --limit 5 --report tmp/test-selection.json
python -m scripts.run_regression --selection tmp/test-selection.json --output tmp/selected-run
python -m scripts.run_regression --summarize tmp/selected-run/run.json
```

첫 명령은 제품 import/DB/네트워크 없이 소스 구문과 기존 영향 그래프를 검사한다. 콘솔은 실패 최대8개만, 상세는 지정 보고서에 보존한다. 나머지 명령은 기존 [선택 실행](../INCREMENTAL_TEST_REVIEW.md#선택적-조회실행-명령)을 재사용한다. 경계가 불명확한 `full_required`는 자동 전체 실행/부분 성공으로 바꾸지 않고 관련 소비자와 필요한 환경을 먼저 좁힌다. 단순 문서 수정에 기능 전체 검사를 요구하지 않는다.

실행기 CLI의 stdout은 기존 단일 JSON 요약 계약이며 CI worker 진행 진단은 stderr를 사용한다. Windows 전체 CI는 기존 worker를 두 독립 runner에서 실행할 수 있으나, 부분 결과는 전체 성공이 아니다. 기존 required 검증기가 현재 full plan·연속 범위·소스/환경·전역 ID·종료와 두 job 성공을 대조한 뒤 기존 단일 전체 보고서로 합류한다. worker 내부 격리·검사 범위·실패 수집은 유지하고 로컬 전수 실행을 추가하지 않는다. sealed native 실행은 기존 operator의 선택적 `--report`에서 같은 ID/source/result helper와 PG 보고 형식으로 기록하고, 선택된 job의 기존 artifact→required review에 전달한다. 별도 재실행·저장 owner·검사 체계를 만들지 않으며 실패/skip/누락·중복ID/소스 불일치나 기존 restore 안전 fence를 완화하지 않는다. 실제 hosted 결과와 미검증 범위는 [R3-083](../../REFACTOR_BACKLOG.md#r3-083--개선된-책임실행-비용기능-연결의-퇴행-방지) 말미에서 이어간다.

| 확인 대상 | 자동 검사/관련 회귀 | 한계·사람이 확인할 내용 |
| --- | --- | --- |
| 새 계층 역방향 의존·순환 edge | `check_architecture`, 기존 `select_ci_groups` graph 재사용 | TYPE_CHECKING/조건부 import도 보수적으로 포함한다. 기존 cycle은 실행 중 순환 실패가 아니라 코드 의존 증거다. 의존을 늘리는 이유·책임을 검토한다. |
| 새 DB 연결/execute/commit/rollback 지점 | 같은 checker의 함수별 source-site delta, 기존 PG 직접 연결·QueryStore 계약 회귀 | 지점 수는 실제 실행 횟수가 아니다. 동적 receiver/별칭 재대입·반사 호출/도우미 속 I/O 전부를 증명하지 않는다. 새 helper/우회 state 변경은 실제 caller/owner 검토로 보완한다. |
| RAM 경로의 load/save·파일/HTTP/동기 대기 후보 | 계약에 지정한8개 함수와 중첩 함수, 이동/삭제도 검출 | 한 회차 공유 여부·helper 내부의 증가·반복 계산은 실제 cost/연결 검사로 확인한다. 새 고빈도 기능은 해당 함수와 테스트를 같은 계약에 추가한다. |
| API·이벤트·DB contract·수명·중복 처리 | 기존 QueryStore/source/PG와 해당 기능군의 통합·실패/중복 입력·교체/재시작/종료 검사 | 새 테스트 파일이나 동일 검사를 복제하지 않는다. 필요한 저장·Risk/control 재검증을 단순 호출 수로 제거하지 않는다. |
| Controller/Service/Manager 중복·얇은 relay·광범위한 상태 소유 | 위 기능군과 변경 diff 검토 | 이름·파일 크기·클래스 수 임계값으로 판정하지 않는다. 신규 owner의 독립 상태/정책/수명/저장 책임과 파일 수·호출 깊이의 실제 이득을 확인한다. |

[기계 계약](guardrails.json)의 `adopted_limits`는 현 미커밋 checkout의 **source-site 목록 채택**이다(boundary23/cycle261/DB1041/dynamic41 keys). 기존 R3 결함 해결·모든 비용 승인·타 작업의 의미 검토가 아니다. Git 기준을 전달하면 최초 목록과 검토 이력 재작성은 실패한다. 기존 QueryStore/PG 검사는 이미 전체 CI에 있으므로 별도 CLI로 중복 실행하지 않는다. 새 정적 검사는 기존 Windows CI에서 한 번 실행하고 fixture 검사는 기존 `runner` profile에서 실행한다. 기존 전체 CI/필수 환경·일정은 유지하며 새 주기 검사는 추가하지 않았다. 이 checkout의 활성 pre-push hook은 없고, push/PR의 자동 검증은 기존 GitHub workflow가 담당한다.

**정당한 변경:** 실패 보고의 정확한 `key`에만 `reviews`의 고유 `id`, `reason`, `connection`, `validation`, `limits`를 기록한다. 필요한 비용 증가는 독립 결과·안전성/연결 회귀와 함께 허용한다. R3로 지점/책임이 없어지면 해당 limit을0(또는 감소값)으로 좁혀 재유입을 막는다. 기준을 통째로 새 snapshot으로 만들지 않는다. `rules` 변경은 이전/새 규칙의 `canonical_digest`를 `rules_change.from/to`에 기록하고 책임 문서·관련 검사를 함께 수정한다. CLI는 계약을 자동 수정하지 않는다.

```json
{"id":"해당-R3-또는-기능-ID","reason":"필요한 비용과 이전 비용의 차이","connection":"실제 caller → owner → 소비·실패/종료","validation":"관련 현재 실행 근거","limits":{"실패 보고의 정확한 key":1}}
```

### 재현 가능한 실제 비용 기준

**열세번째 현재 기준:** 현재 통합 소스 관련62개 실제 모듈·고유1301건/1113.091 worker초 PASS(경과1143.775초, native2lane·모듈별격리). 계획=발견=실행=통과 ID, 전체1160파일 LF `6c4bb027723a56fa9dd07a5f79f9b409c1792eab43b3434c0990da6912dbaa7c`·변경44파일/159함수AST·47번째증분원장·62worker/tree종료·성공TEMP실제부재를 대조했다. 6128→6161/추가33·수정6·삭제0, 이전46기록/미결49/394모듈·77세부경로·F25감사깊이를 보존했다. packet/역사 관련1245+root비용1에 기존 발견건수 계약이 있는 관련3모듈38건과 기존QueryStore/구조검사17건을 보강했다. 정적 증분검토47기록과 구조1364site/위반0/retirable0은 통과했지만 전체 실행증거 gate는 실제PG 필수5개 미실행으로 실패 상태다. 누락ID를 대체·skip·제외하거나 로컬PASS로 숨기지 않았다. 최초root1284계획은1265PASS/condition19worker를467초정체로중단한불완전실행이다. 소스·기대값·내부deadline을전혀바꾸지않고해당전체19를독립재검증해통과한유일완료worker와계약17을수락합집합으로선택했다. 최초raw실패/보존TEMP는그대로이고정체원인은미확정이며해결됐다고보고하지않는다. 로컬 전수6161/all-local·실제PG·키움/provider/실계좌주문/운영DB·NAS·실제설치/runas/UAC·새GitHub CI/push/배포는 미실행이다. R032 최초95중94PASS+기존1FAIL·후속무수정계측1PASS·root현재49PASS는 별개이며 최초 원인 미확정이다. R053 최종진단1PASS+1FAIL(conhost109)은 제품0 상태로 보존한다. R048 정상CPU-10.53%와 nested fallback+26.83%를 함께 기록한다. 개별전후비용은R3-039/047/048/049/062/075/076/081 상세와원본fixture를재사용한다.

**열두번째 통합:** 현재 통합 소스 관련62개 실제 모듈·고유1060건/825.210 worker초 통과(경과415.748초, native2개 독립 lane·모듈별 격리). 계획=발견=실행=통과 ID, 전체1154파일/변경41파일·46번째 증분원장·worker/tree 종료·native TEMP 제거를 대조했다. 6073→6128/추가55·수정7·삭제0이며 기존45기록/미결49는 보존한다. packet972ID에서 역사 검토5개를 보강하고 변경검사 모듈 전체를 포함해 중복 없는1060으로 선정했다. 구조1373site/위반0이며 adoption/rules를 초기화하지 않고 exact review3개만 추가했다. 최초 root adoption 수정은 실제 gate가 거절했고 append-only review로 바로잡은 실패 기록도 보존했다. 수치·필요비용증가·원본재현은위해당owner만선택참조한다. 048얕은복사는의미차이로미적용,084 BODY/판단cut과085회전후주문소유는미해결·root미수락이다. 전체394모듈/77세부경로·부모F25/감사깊이25·미결49 보존. 로컬전체6128/all-local·실제PG/provider/AI·키움/실계좌/주문·운영DB·실다중PC/망·실제Windows installer/UAC·새 hosted GitHub CI/push·NAS배포는 미실행. 039신규PG1/079변경nas_operator_owned PG1·043실제PG2는 미실행이며 이전 PC/statement fixture를 대신 증거로 쓰지 않는다.

**열한번째 통합:** 최종 검증2026-10-21: 현재 통합 소스 관련42개 실제 모듈·고유533건/398.798 worker초 통과. 최초483건 뒤 증분 gate가 같은 파일의 기존7ID 누락을 감지해 MainWindow61을 보강했고, 중복11실행을 포함한 aggregate도 거절해 유일한 완료 worker로 선택했다. 이어 root의 검토기록2개(Drive snapshot→056, statistics→074) 분류 오류를 수정하고 제품/검사 본문 변경 없이 수정 원장까지 동결한 현재533건을 다시 실행했다. 이전483+61·최초 gate 실패/중복 거절/수정 전 검증은 예비 기록으로 보존한다. 최종 증분45기록/6073개·추가36개/기존 메서드 변경·삭제0, 전체 소스/계획·실행·통과 ID, 동결22파일/함수별 병합, worker/tree 종료·native TEMP 제거가 일치한다. 구조1374site/위반0/기준 변경0. 별도 WT 비용·반례는 현재 root533과 구분한다. 비용 기준/필요 증가·미측정은 위 각 owner 계약과 R3 원장/개별 manifest를 선택 참조한다. 성능 기준을 새 snapshot으로 초기화하지 않았다. 실제 키움/계좌·주문/운영DB·Drive/망/다중PC/NAS·새 hosted GitHub CI/push/배포·로컬 전수회귀는 미실행. R043 실제PG2ID는 외부대기 그대로다. 기존394모듈/77경로·부모F25개/깊이25행·미결49·보류4를 보존한다.

**열번째 통합:** 통합 현재 소스 관련11개 실제모듈 고유107건/75.253 worker초 통과. 계획/실행/통과 ID·전체지문·동결8파일·worker/tree 종료/native TEMP 제거를 대조했다. 증분44기록/6037건(추가3·기존보강1·삭제0), 구조1374site/위반0/기준변경0. WT의 별도 원본/후속 비용2·legacy seed1은 보존하되 root107에 합산하거나 재실행했다고 보고하지 않는다. 029거짓충돌제거는필요import비용을복구하고045불변생성시각만의전송/중앙저장1회를제거한다. 028/082실행비용절감0,단순새계층분리없음. 실계좌/주문/운영DB·실제키움·Drive/네트워크/다중PC/NAS/실제GitHub CI/push/배포/로컬 전수회귀는 미실행. R3-043 실제PG2ID는 미실행/외부대기 그대로다. 원래394모듈/77경로·부모F25개/깊이25행·미결49·보류4를 보존한다.

**아홉번째 통합:** 통합 현재 소스 관련15모듈 고유185건/104.818 worker초 통과. 별도 기존 native 진단5건도 통과했다. 계획/실행/통과 ID·전체 소스 지문·함수별 병합·worker/tree 종료와 native TEMP 제거를 대조했다. 증분43기록/6034건·추가4/fixture2·삭제0, 구조1374site/위반0/기준변경0. 051뉴스입력10→1/필수journalwrite동일,055hidden custom read19→3/aggregate10→0/필수복사동일. 진단은 고정 fixture/native SQL 측정으로 운영 latency나 OS I/O 절감 실측이 아니다. 운영DB/실계좌·주문/실제키움·망/NAS/실제GitHub CI/push/배포/로컬 전수회귀는 미실행. R3-043 실제PG2ID는 여전히 미실행/외부대기다. 051 선택쓰기의 유효read/UI경고/자동task정책과075 봉보존·다른주기 문제는 별도다.

**여덟번째 통합:** 현재 통합 소스 관련17개실제모듈 고유212건/307.507 worker초 통과(일지118/catalog65/외부시장29). 별도현재MainWindow→두QThread→row editor/name lookup/preview 진단1건·8상태도29.698초통과/원본관측JSON동일. 변경16ID·계획/실행/통과·전체지문/동결7파일·worker/tree종료/nativeTEMP제거를대조했다. 구조1374site-key/위반0·retirable0·guard/adoption/rules/review변경0. 증분42기록6016→6030/추가14·기존fixture보강2/삭제0,과거41기록·미결49보존·실행증거검사통과. full_required495/external32/불확실성4는자동실행하지않고변경caller/owner/native/소비/실패·종료의17모듈과별도실제Qt진단을선정했다. DB/API는직접연결된기존2ID만선택했고selector/probe SHA와fixture HERE경로만다른동일class AST를보존했다. 052dirty저장0→1은필요비용,060정상비용동일/다음회차회복,061unknownstate연결·tx·반영각1회생략,054실제경로실행비용변화0. 실제PG(043대기2건포함)·운영DB/실계좌/주문·실제키움/외부시세·모델정확도/NAS/network·push/hostedCI/배포·로컬전수회귀는미실행이다. 물리I/O/운영오류빈도·강제kill/전원/독립journal child종료및기존미확정031nativeabort원인해결로확대하지않는다.

**일곱번째 통합:** 현재 통합 소스 관련20모듈 고유231건/317.202 worker초 통과(연구153·뉴스65·기존구조차단13). 변경8 ID·전체 계획/실행/통과, 현재 전체지문/동결9파일, worker/tree종료·nativeTEMP제거를 대조했다. 구조1374고유site-key/위반0·retirable0. 063terminal3키의허용execute횟수만2→3으로승인review1건을추가했고adopted/rules/기존review이력은동일하다(start는기존site재사용). 기존차단13건도재사용통과했다. 증분41기록6009→6016/추가7·기존availability검사보강1/삭제0,과거40기록·미결49보존·실행증거검사통과. 동적full_required495/external32/불확실성7은자동실행하지않고실제owner/소비/실패·종료의관련20모듈만선정했다. 필요한063writer예약/067교차곱/072설정확인증가와절감은구분한다. 실제PG(043변경2건포함)·키움/장중/실계좌/주문·운영DB·외부provider/NAS/network·push/hostedCI/배포·로컬전수회귀는미실행이다. 과거 immutable연구결과 재작성/재승인과 원격POST취소·late결과폐기는수락하지않았다.

**여섯번째 통합:** 현재 통합 소스 관련 27개 실제 모듈 고유394건/431.634 worker초 통과(journal167/index112/real115). 변경14 ID·계획/실행/통과, 전체 소스 지문, 동결9파일과035 기존본문을 보존한065 추가2메서드 병합, worker/자식 종료·native TEMP 제거를 대조했다. 구조1374site/위반0·retirable0/guard변경0. 증분40번째기록5996→6009/추가13·기존socket실패검사보강1·삭제0, 이전39기록·미결49보존. 기존socket실패assertion은유지하고close실패/owner보존을추가했으며finally에서는주입한socket실패만원복해실제fixture종료한다. full_required495/external32/불확실성7은자동실행하지않고실제변경caller/owner/native/consumer/실패·종료의27모듈과앱종료기존1ID만선정했다. 전체확장범위통과로보고하지않는다. 030필요새provider조회/이후hit0,064metadata시도11→10·native tx1유지,065오류시write/replace1→0,066명시실패2+event2복구save4/행2/REST0. 실제 키움/장중·실제PG(기존043 변경2 ID 포함)·운영 DB/계좌/주문·외부망/NAS·push/GitHub CI·배포·로컬 전수 회귀·물리I/O/운영발생률은 미검증이다. 이번 증거 검사 통과는 이전043의 외부검증 미완료를 해소하지 않는다.

**다섯 번째 통합:** 현재 통합 소스 관련 20개 실제 모듈, 고유 305건/124.603 worker초 통과(index107/news113/research85). 계획·실행·통과 ID, 변경 로컬14 ID, 전체 소스 지문·동결12파일, worker/자식 종료와 native TEMP 제거를 대조했다. R043 retry 두 execute-site의 유효 한도2→1을 정확히 한 건의 review로 낮췄다. adopted 목록/기존 rules/승인이력은 변경0이다. QueryStore 검사가 의도한 필수 attempts 서명6개만 review_required로 잡아 호출자/SQLite/PG/Protocol을 대조 후 해당6개 계약값만 갱신했다. site/forward/candidate 추가·삭제0, 기존 unresolved same-name39도 유지했다. 최초113건 중 audit1 실패 원문을 보존하고 외부 JSON을 읽는 audit4건만 재실행 통과; 나머지5개 성공 모듈은 같은 소스 결과를 그대로 합성했다. 증분 원장은39기록/5996 case/추가8·기존수정8/삭제0, 과거38기록·미결49 보존, metadata 정적검사 통과와 PG 누락으로 실패한 전체 실행 증거 검사를 구분한다. R3-043 실제 PostgreSQL 변경2 ID 및 해당87건 모듈은 미실행이다. 전용 diagnostic DSN 부재로 증분 실행 증거 검사는 누락을 감지해 실패했으며 성공 처리하지 않았다. 기존 push/PR의 필수 postgres-integration이 postgres_required 전체 모듈을 실행하고 실패·누락을 required 증거로 전달하는 본문만 확인했다. 현재 hosted CI 실행은 없다. 실제 키움/장중·운영 DB/계좌/주문·외부망/NAS·push/GitHub CI·배포·로컬 전수 회귀·물리 I/O/WAL/운영 발생률은 미검증이다. 뉴스body/asof·assessment projection은084에서 소비자별 확인 후 정한다.

**031/080 최신증거:** 현재통합소스 관련8개실제모듈 **고유121건/79.524 worker초 통과**(journal/controller85+실제MainWindow기존signal1+OCR/controller/theme35). 11개변경ID·계획/실행/통과·현재전체지문/frozen7파일·worker/tree종료/nativeTEMP제거를대조했다. 031base5회/이미관측payload의외부loader재호출0·논리중복행0/기존native후보조회유지,080기본N+1→N/필수badge N. 구조1374site/위반0·retirable0,기존guard/rules/승인이력변경0. 증분38번째기록5978→5988/추가10·기존controller검사의pending=False명시1·삭제0,이전37기록/미결49보존. 기존FakeWriter는새RAM pending계약False를명시하고writer QApplication fixture로queued실패를전달한다. 자동full_required495/external32/불확실성3은미실행으로보존,MainWindow는직접연결기존1ID만기존runner selector로실행했고selectorSHA도보존한다. 031의중간동일소스Qt native abort3221226505는보고된통과0/원문ERROR traceback미확보·정확원인미확정으로실패TEMP/로그를보존한다. 이후단독/모듈·계측86건과root현재121건에는재현되지않았지만원인해결로보고하지않는다. 080은provider를통제한실제parser/Qt검증이며WT필수6모델파일이없어실제모델정확도/추론latency를수락하지않았다. 실제모델정확도/처리시간·실제PG·장중/키움·운영DB/계좌·주문·외부망/NAS·push/GitHub CI·배포·로컬전수회귀는미검증이다. [통합정본](../../REFACTOR_BACKLOG.md#r3-031080-네-번째-병렬-통합--2026-10-20).

**036/038/040/073 최신증거:** 현재 통합소스 관련21모듈 **고유479건/428.669 worker초 통과**(index90+ingest/app241+gateway90+news58,중복0). 계획/실행/통과·14개변경ID/현재전체지문·frozen7파일·worker/tree종료/nativeTEMP제거를대조했다. 구조1374site/위반0·retirable0,기존guard/rules/승인이력변경0. 증분37번째기록5965→5978/추가13·기존pin검사1보강·삭제0,이전36기록/미결49유지. 자동full_required495/external32/불확실성7은미실행으로보존했다. root는nativeDB/app모듈을현재전체발견73/88건으로실행해WT038의선택8/3건보다150건많고,WT들의329건단순합계를현재479건과혼용하지않는다. 040은안전상SELECT2유지,실제비용증가는필요근거와함께판단한다. 실제 PostgreSQL·장중/키움/운영 DB/실계좌·주문·외부망/NAS·push/GitHub CI·배포·로컬 전수 회귀와물리IO/WAL/운영latency는미검증이다. [통합정본](../../REFACTOR_BACKLOG.md#r3-036038040073-세-번째-병렬-통합--2026-10-20).

**선택검증/비용.** 현재 소스 관련7모듈 **고유157건/79.485 worker초 통과**(수집/과거자료/테마74+화면83, 중복0). 계획=실행=통과ID·현재 소스/frozen worktree SHA·worker/자식 종료·native TEMP 제거를 대조했다. 구조1374site/위반0·retirable0. 069의같은DDL 이동에따른정확한connection.execute source key12→13만review로추가하고기존adopted/rules/승인이력은그대로다(실행SQL증가아님). 증분36번째기록5957→5965/추가8/기존본문수정0·삭제0,이전35기록/미결49유지. 현재기록의금액단위표기만60billion→60억(6billion KRW),140/40billion→140/40억으로정정했으며제품/검사값은동일하다. v1통과와정정후현재지문v2를분리보존한다. 자동full_required495·external32/동적불확실성7은미실행으로남기고선정한7모듈만검증했다. 실제 PostgreSQL·키움/장중·NAS/외부망/운영 DB/실계좌·주문·앱 전체 수락·push/GitHub CI·배포·로컬 전수 회귀는 미실행이다. 069는F19/F21의archive 초기화와F22의간접파일경계,070은F02/F06/F23의실제설정소비와F22간접공유만수락한다.

| 범위 | 현재 정상 기준과 oracle | 재사용 위치 |
| --- | --- | --- |
| R3-001 live metadata | 20종목 native SELECT20, BEGIN IMMEDIATE/COMMIT 경계와 값/metadata·rollback/retry | `test_minute_bar_repository.MinuteBarRepositoryTests.test_live_batch_reads_each_metadata_row_once_inside_the_writer_transaction` 및 같은 파일의 authority/실패 검사 |
| R3-002 price batch | 연결/BEGIN/COMMIT 각1, SELECT0; empty/partial·전체 rollback·retry·process exit | `test_stock_repository.StockRepositoryTests.test_price_cache_uses_one_transaction_and_keeps_field_and_date_contracts`와 같은 파일의 실패 검사. 원거래일·원batch시각/latefailure·명시종료128건 검증. 복수날짜도1transaction은 `test_two_high_dates_share_one_transaction_without_cross_day_max_or_extra_reads`가 보호한다. 넓은023시계 잔여는 별도 |
| R3-004 RAM minute | 20종목/400행 중 실제 native 반환200행(`[10]*20`); 무제한 양성 대조400행(`[20]*20`), 8소비자/100회 조회/close에 추가 read0·native 무쓰기 | `test_candidate_monitor.CrossThemeH1SharingTests.test_minute_primary_uses_existing_sqlite_reader_without_writes_or_consumer_scaling` 기존 검사 보강. 과거9000→200 측정과 이번400→200 fixture는 구분 |
| 미해결 반복 비용/정합성 | R3-039/041 및 063~082 중 미완료 항목의 반례는 개선 목표이며 정상 budget으로 승인하지 않음 | 기존 R3 항목의 재현/완료 기준에서 수정 후 관련 비용 검사를 선택·승격 |

wall time·WAL/디스크 쓰기·실제 PG/장중 지연은 위 호출·행 수로 추정하지 않는다. 입력 규모·단위/metadata·ACK·최종 내용이 같다는 증거와 함께 비용을 비교하고 필요한 비용 증가는 계약/테스트 기대값과 이유를 함께 갱신한다. 미해결 반례를 통과시키는 현행 버그 기대값을 정상 CI 기준으로 옮기지 않는다.

### R3 최종 종료 조건과 점진 적용

2026-10-18 최종 검증: 실제 CLI19조건과 현재 workflow pwsh의 정상0/위반1, 기존 비용 검사의 SELECT20→40/반환200→400 거절과 복원 재통과를 확인했다. CI 단위검사는 비활성화/오류무시·누락·필터6변이를 검증한다. `d5bec26`의 [실제 전체 CI](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/39115204095)는7job 성공·Windows5577건과 sealed native 보고서의 최종 source/ID 대조까지 통과했다. 실제 PR/의도적 위반 push 실패·운영 전체 수락은 별도다. 불필요한 순수 relay의 실익/모든 helper I/O/동일 지점 반복/미등록 고빈도·수명은 자동 완전 판정하지 않는다. PC 정적7.082초·추가 CI변이 본문0.116초, hosted 신규3worker·50건4.287초는 해당 범위의 관찰값이고 정확한 토큰 비용은 미측정이다. 현재 근거와 남은 범위는 R3-083 말미를 따른다.

R3-083이 이 체계의 진행 원장이다. 다른 R3 개선을 중단시키는 별도 선행 전면 정리는 아니다. 각 개선이 끝날 때 관련 owner/공식 경계·비용/실패 계약·F 원장을 좁혀 반영하며 초기 구현 순서는002+023이었고 현재 NAS 한정 선별 범위·006/008/020/024 보류는 REFACTOR_BACKLOG 정본을 따른다. 최종 종료에는 책임 계약의 현재 구현 일치, 개선된 고빈도 경로의 재현 비용/중복·실패/종료 검증, 구조 위반 시 실패/정당 변경 시 통과, 자동 CI 연결 및 실제 실행 결과 확인을 포함한다. 문서·로컬 검사 완료와 hosted CI/운영 환경 수락은 분리한다.

**R3-005 비용 기준(2026-10-19).** 고정20종목450분의 native 반환9000→100, SELECT/연결 각20·봉쓰기0/같은key추가read0. 진단11조건·PG SQL fake계약과 현재 관련 실행은 정본005를 따른다. 정책 window를 바꾸면 필요한 bound와 native 반환/같은 결과를 함께 갱신하며 미정 window를 임의 기본값으로 채우지 않는다.

**R3-010 비용/검증 기준(2026-10-20).** `2bd43df`: 신규 native 문서 연결3→1·SELECT2→1·INSERT/COMMIT/clock1; 최초 existing duplicate 연결1/SELECT1·쓰기/clock0, 늦은 경합 loser는 추가 비교가 필요하다. 관련18모듈 739건/worker 합계 629.437초이며 실제SQL/PG fixture와 nativeSQLite를 구분한다. 비용 회귀는 기존 forward/candidate/centralDB 모듈에 있고 새 주기 검사/관리 계층은 없다. 자동 선정의 full_required는 남기고 로컬 명시 관련 범위와 구분한다. [정본과 잔여](../../REFACTOR_BACKLOG.md#r3-010-불변-저장충돌ack-경계--2026-10-20).

**R3-010 stage/binding 완료 계약(2026-10-20).** stage의 같은 이전 단계 확인과 append를 기존 native writer transaction으로 묶었다. 기존 동일 ID는 읽기 전용으로 재시도하며 SQLite BEGIN IMMEDIATE, PG의 해당 transaction만 READ COMMITTED→strategy scoped advisory lock→단건 key 재확인→기존 reader와 같은 이력 범위/순서→단계 비교/insert→commit 순서다. broker-mock PASSED report gate·원래 hash·계좌/증거·단계/시각 정책은 유지한다. 동일 시점에 stale stage와 missing report가 겹치면 거절 오류의 우선순위만 달라질 수 있다. 기존 identity owner에 load_latest_account_binding(profile, broker, environment)를 추가해 native 인덱스/LIMIT1을 쓰고 route helper와 Forward의 승인 사양/후보 게시 3개 caller를 연결했다. account_ref나 기대 revision으로 조회를 좁히면 rotation을 숨기므로 사용하지 않는다. 기존 full history API·각 안전시점의 독립 최신 조회·STOP/Risk/control/native claim은 유지한다. stage 신규 native SQLite 연결3→2·SELECT3/INSERT1/BEGIN1/COMMIT1/clock1 유지, 기존 동일 ID는 연결1/SELECT1·쓰기/BEGIN/COMMIT/clock0이다. binding fixture는 실제 native 반환·변환1031→1, SELECT/연결 각1·쓰기/clock0이며 기존 index SEARCH를 확인했다. 물리 I/O·WAL/장중 latency는 미측정이다. PG stage의 명시 isolation/lock 추가 SQL은 올바른 snapshot과 직렬화를 위한 안전비용이다. 기존 execution_activation/central_server_database/mock_automation_specification 검사를 보강해 현재17모듈 546건으로 연결·실패·재시도를 확인했다. native lock/시각/반환량 변경 시 해당 비용과 stage/current binding 검사를 함께 갱신한다. context 캐시나 추가 relay·주기 전수 검사는 만들지 않는다. [최신 판정/실측/한계](../../REFACTOR_BACKLOG.md#r3-010-stagebinding-등록-개선-완료--2026-10-20).

**AR v1 27차 연결 계약:** 기존Database가명시role의4설정관계/원seed·version/첫receipt를한native transaction으로초기화하며기존repository의정상편집/재시작을재사용한다. 기존보존CLI만install을조립하고실제bootstrap/Runtime/sync·부분선별NULL소비·trusted artifact를수락하지않는다. 뉴스입력은각기존producer의원hash를native provenance로구분하며원기사/SQL/시계는그대로다. 계좌수정은검사기대의정확requestID/독립스키마해시로제품권한정책변경0이다. 현재main source100 관련269/정적3종이통과했으며전체F/운영수락이아니다. currentquery10은비재현이며간헐Abort위험을유지한다. 단일정본0/11.34를따른다.

**이력 구분:** 앞선source99의watchlist추가차단은11.34 source100에서원hash를보존한정확validator/실제producer·HTTP·native회귀로국소해결했다. source-page hostclockgap과query간헐Abort/AR09원cleanup·실제P3 rolebootstrap/부분선별NULL소비/trustedartifact는현재잔여다. 최신판정은단일정본0을따른다.

**28차 이력 구분:** 이전 source-page hostclock 잔여 중 이번 source/collector/service/reader·historical 외부 작업·PC 직접 소비 경로는 위 현재 계약으로 국소 종결했다. 앞선 source100269·source99158 등은 각 소스 근거이며 전체 앱 시간 통일/운영 수락으로 합산하지 않는다. 조회 P3/미완료 상태/trusted artifact·query Abort/AR09 UNKNOWN은 정본0절의 잔여다.

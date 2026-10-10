# 서버 시작 실패와 종료 책임 보완

2026-10-11 · worktree c32b · A+B 구현·로컬 회귀 완료 / 간헐 Windows rename 원인은 별도 보류

2026-10-11 후속 확인: 최초 A+B 전체 all-local 기록은 WinError 5 한 건으로 failed 상태를 보존한다.
이후 동일 현재 source tree를 Windows 사용자 TEMP에서 실행한 전체 all-local은 272/272 worker,
3,755/3,755 tests 통과, 오류·skip·미실행·timeout·잔류 자손 0이었다. 재부팅 뒤 실패했던
`test_historical_news_review_dialog`도 격리 worker에서 2/2 통과했고 source는 현재 worktree였다.
별도 동일 길이 최소 재현에서는 WinError 5가 반복 중 다시 발생했지만 즉시 재시도는 성공했다.
따라서 A+B 코드와 해당 회귀 범위는 로컬 검증을 마쳤고, native 오류의 소유 프로세스/filter 원인만
별도 미확정으로 유지한다. 새 전체 회귀를 반복하지 않는다. Hosted CI·실제 PostgreSQL/NAS·게시/병합/
배포는 수행하지 않았다.

## 현재 진행

A의 app 단일 종료 경로·실패/취소 보호를 구현했다. 관련 6개 모듈 141건 통과(84.634초),
실패/오류/skip/미실행/잔류 자손 0, worker와 자손 종료 확인. native TOP20 최종 저장을
Event로 지연하고 종료 waiter의 반복 취소/본문 취소/본문 오류 세 조건에서 DB·vault 유지,
COMMIT 뒤 저장 행과 값, vault 재획득, 원래 예외 객체/취소 전달을 확인했다. 내부 종료 task
취소와 waiter 취소+close 오류는 종료 실패이며 store를 해제하지 않는다. 원본 main 23조건
재실행은 v1 기록과 동일하고, 현재 23조건은 명시한 새 정책과 일치한다. v1 JSON은 보존했다.
메모리 결함 3종은 각각 assertion 실패 3/3/1건(errors/skip 0)으로 탐지했다.

최초 관련 실행 117건은 실패 3건(기존 fixture의 double close 기대)과 오류 2건(새 native
테스트의 import 누락)이었다. 원인을 확인하고 exact-once 기대와 import를 고친 뒤 위 141건을
재검증했다. 이전 실패 기록을 성공으로 바꾸지 않았다. DB 감사는 close 호출 3→1 통합과
binding 전달 함수 소유 이름 변경만 원장에 반영해 pass, catalog unit 417/integration 21 pass.
실제 DB 트랜잭션/계좌·주문 기대값이나 API 기준선은 변경하지 않았다.

**B 구현과 관련 검증도 완료했다.** replay child를 기존 parent finally에서 실제 join한 뒤
capture/report/lease를 정리하고 app의 parent/trace 종료는 timeout=None로 off-loop 대기한다.
수동 stop/close 기본 제한 시간은 유지한다. supervisor는 소유 종료 task를 재호출에도 기다리고
모든 runner 결과를 받은 뒤 오류를 전파하며 실패 참조/결과를 유지한다. native 선행 4건 중
3건이 assertion 실패로 이전 결함을 재현했고, 관련 8개 모듈 147건은 통과했다(90.441초).
worker/자손 실제 종료·저장 값·실패/취소·lease 유지와 결함 4종 탐지, 감사/catalog pass.
초기 checkpoint 기대 오류 1건과 app 새 검사 import 오류 1건은 실제 setup 원인을 보완해
재검증했고 실패 기록을 보존한다. A+B 전체 all-local은 3,755/3,755건·272 worker 실행 후
오류 1건으로 failed 종료했다(1,673.941초). assertion 실패/skip/미실행/잔류 자손 0, 모든
worker/자손 종료·tree 동일·변경 테스트 12개 포함을 확인했다. 오류는 과거뉴스 review queue
staging 폴더 이동의 WinError 5이며 서버 수명 검사에서는 오류가 없었다. 해당 dialog와
blind-validation 별도 재실행 4건은 통과했지만 원래 전체 실패 기록은 유지한다. 직전 폴더
이동 오류와 같은 원인이라는 증거는 없고 목적지/handle 소유자가 미확정이라 최종 종료는
보류한다. 다음은 그 경계의 재현/상태·소유자 증거 확보이며 전체 범위를 다시 확장하지 않는다.
hosted CI/실제 PostgreSQL/NAS 검증·커밋/게시/병합/배포는 실행하지 않았다.
증거는 CURRENT_STATUS 첫 기록 및 tmp/regression/lifespan-A-*/lifespan-B-*에 보존했다.

## 범위와 결정

API 책임 분리 19개 경계 이후 확인한 **기존 수명 결함의 별도 보완**이다. 라우트의 기계적
이동과 구분한다. 이번에는 수명 코드를 다른 파일로 옮기지 않는다. `create_app`이 조립과
수명 조정을 계속 소유하고, 각 서비스는 자신의 실제 작업 종료를 소유한다. 새로운 Runtime,
Manager, 범용 자원 registry나 의존성 그래프 실행기를 만들지 않는다.

현재 두 중첩 context manager에 흩어진 정리를 하나의 명시적 종료 경로로 모은다.
동적 main binding/query manager, mock bundle과 `operational_state.candidate_monitor`의
소유권은 그대로다. 시작 때 객체 목록을 복사해 종료에 사용하는 방법은 채택하지 않는다.
기능을 이해하는 파일 수는 app + 기존 owner로 유지하고, 새 전달 계층을 추가하지 않는다.
기존 `lifespan → service_lifespan` 중첩을 줄이고 app 내부의 시작/종료 함수로 책임을 구별한다.

외부 API, DB 스키마/transaction/revision, 주문 판단, 자격증명 적용 정책은 변경 대상이 아니다.
정상 시작 순서와 정상 종료의 **첫 번째 실행 순서**는 유지한다. 중복 close 호출 제거와
실패/취소 시 정리 보장은 의도한 동작 변경이며 별도 기대 결과로 검증한다.

## 증거와 한계

- `app.py`의 `service_lifespan`은 bare yield 뒤에 정리가 있다. 부분 시작 실패와 본문 예외는
  이를 건너뛴다. 바깥 `lifespan.finally`는 일부 계좌 자원만 정리하며 close 실패 시 중단된다.
- `server_lifespan_v1.json`은 원래 main의 23개 시나리오 기록이다. 현재 app 80건 통과는
  이 기존 동작과의 일치이며, 올바른 종료의 승인 기준은 아니다.
- native vault 재획득 6조건에서 정상 2조건 성공, 주입한 credentials/real close 지속 실패
  4조건 잠금 유지가 재현됐다. 네트워크 owner start/close는 통제했다. 실제 worker drain과
  데이터/주문 영향은 이 실험으로 입증하지 않았다.
- `RealCredentialOwner`, `MockCredentialOwner`, `MockAccountBundle`, collector와 broker는
  close task를 보유한다. 외부 waiter 취소와 소유 작업 종료는 다르며, 실패한 close를 다시
  호출해도 같은 실패 task를 기다릴 수 있다. TOP20은 별도의 재시도 규칙을 갖고 있다.
- `DiagnosticRuns.close`는 기본 10초 join 뒤 worker 생존을 판정하지 않는다. 내부 replay
  worker도 제한된 join 후 생존할 수 있다. `diagnostic_trace.stop` 역시 기본 join 제한이
  있다. 반환 또는 상위 thread 종료만으로 모든 자손 종료를 단정할 수 없다.
- `MockAutomationSupervisor.close`는 runner close 결과를 `return_exceptions=True`로
  기다린 뒤 오류를 전파하지 않는다. `_closing` 뒤 재호출은 바로 반환한다. 이는 코드상
  확인이며, native checkpoint 실패/지연 재현은 다음 구현 묶음의 선행 검증이다.

## app의 종료 계약

### 단일 소유 작업

1. lifespan 진입 직후부터 credential runtime 시작, 서비스 시작, yield 본문 전체를 하나의
   정리 보장 범위에 둔다. 시작 성공 플래그만으로 정리 대상을 고르지 않는다. 생성자에서
   획득한 자원과 start 도중 일부 생성된 작업도 정리 대상이다. 각 close의 미시작 안전성을
   재현 테스트로 확인한다. create_app 자체의 생성 도중 예외는 이번 범위 밖이다.
2. 정리가 필요해지면 app이 종료 coroutine task를 한 번 만들고 강하게 참조한다. 기다리는
   호출자의 취소가 그 task를 취소하지 않도록 shield하고, 반복 취소에도 실제 종료 task가
   끝나거나 실패할 때까지 기다린다. 소유 task 자체의 취소는 성공으로 처리하지 않는다.
   단순 `await shield(...)` 한 번만 하고 finally에서 빠져나오면 요구를 만족하지 않는다.
3. 원래 identity 검증 실패 분기의 수동 close와 바깥 finally의 중복 close를 제거한다.
   실제 정리는 같은 경로에서 정확히 한 번 수행한다. 예외: legacy 선택 모의계좌의 start
   실패 격리는 유지한다. 그 bundle의 close가 성공한 뒤에만 현재 포인터를 비우고 시세·뉴스
   시작을 계속한다. 그 close가 실패하면 실패 결과를 유지하고 전체 시작을 실패시킨다.
   최종 정리에서 동일 실패 close를 무조건 재호출하거나 완료된 것으로 취급하지 않는다.
4. app은 실행 중인 전체 task를 검색하거나 private `_task` 필드를 읽어 종료를 추정하지
   않는다. 실제 작업의 완료는 각 owner의 명시적 close 계약으로 확인한다.

### 순서와 실패 정책

아래 현재 순서를 명시적으로 유지한다. 없는 자원은 건너뛰되 존재하는 미시작 자원은 정리한다.

1. diagnostic runs → diagnostic trace
2. credential runtime → mock automation supervisor → real owner → mock owner/legacy bundle
3. 현재 candidate monitor → external market → news → AI
4. TOP20 → collector → market events → 현재 account query manager → shared broker
5. vault → store

진단 callback과 credential 적용은 다른 서비스를 변경할 수 있으므로 먼저 종료한다.
real owner는 공유 market broker를 닫지 않는다. collector는 market events의 생산자이며
broker/store는 앞선 owner들의 의존 자원이다. owner 내부의 세부 drain/flush 순서는 바꾸지 않는다.

**첫 close 실패에서 후속 정리를 멈추고 실패로 보고하는 보수적 정책을 채택한다.**
현재 공개 close 계약만으로는 실패 뒤에도 worker가 남아 있는지 모두 구분할 수 없다.
따라서 실패를 수집하면서 모든 자원을 무조건 닫거나, finally에서 vault/store를 강제 해제하지
않는다. 독립 자원까지 모두 회수하는 일반 그래프/병렬 정리는 이번 목표가 아니다.
부분적으로 자원이 남는 경우는 종료 실패이며 정상 종료나 자동 복구 완료라고 보고하지 않는다.
같은 객체에 대한 무제한 재시도, 임의 timeout 후 잠금 해제, 같은 app 재시작도 추가하지 않는다.

종료의 현재 단계와 완료/실패를 고정된 안전한 단계 이름으로 남긴다. 예외 문자열·자격증명·계좌
값을 새 로그에 출력하지 않는다. 실패 지점 뒤 DB/vault가 남는 것은 이 정책의 의도된 보호이며,
이전 실험의 잠금 실패를 무조건 잠금 해제 성공으로 바꾸는 것이 완료 조건이 아니다.

### 원래 오류와 취소

- 시작/본문에서 이미 발생한 예외가 있으면 그것을 주 오류로 유지한다. 종료 오류가 이를
  덮어쓰지 않도록 안전한 단계 코드로 부가 기록하고, 최초 예외 객체/traceback을 보존한다.
- 선행 예외가 없으면 첫 종료 오류가 lifespan 실패가 된다.
- 호출자 취소는 정리를 포기시키지 않는다. 정리가 끝난 뒤 취소를 다시 전달한다. 종료도
  실패했다면 그 사실을 부가 기록한다. 내부 종료 task 취소도 실패로 기록한다.
- 정상, 시작 실패, 본문 실패, 취소 각각에서 반환/raise와 자원 보존 결과를 별도로 검증한다.
  `BaseException`을 포괄적으로 성공 처리하거나 종료 오류를 조용히 삼키지 않는다.

### 교체 가능한 상태

credential runtime/owner 종료가 끝난 뒤 현재 query manager와 bundle을 읽는다.
candidate는 `operational_state.lock` 안에서 현재 포인터를 읽고 close하여, 이미 진행 중인
운영 설정 교체와 겹치지 않게 한다. 다른 owner 종료 전체에 이 lock을 걸지 않는다.
ASGI 서버의 요청 drain 이후 lifespan shutdown이라는 기존 실행 경계를 유지한다. HTTP가
계속 들어오는 hot restart나 병렬 create_app 수명은 새로 지원하지 않는다. 진단의 내부 호출도
끝나야 한다. 교체 전 객체는 기존 교체 owner가, 현재 객체는 app이 정리하는 계약을 검증한다.

## 구현과 검증을 두 묶음으로 진행

### A. 확인된 app 예외 경로 보완

- 수정 중심: `central_server/app.py`, 기존 `test_central_server_app.py`.
- 위의 단일 종료 경로/소유 task/오류 우선순위 구현. 서비스 파일 추출은 하지 않는다.
- 23개 기존 시나리오에 대해 이전 결과와 의도한 차이를 항목별로 적는다. v1 원본 JSON은
  덮어쓰지 않는다. 새로운 계약 기대값은 코드 실행 결과를 그대로 승인해 생성하지 않고,
  위 순서와 정책에서 명시적으로 작성한다. 현재 동작을 v1과 같다고 주장하는 테스트는
  이력 비교와 새로운 규범 검증으로 구분하되 원래 시나리오/assertion 범위를 줄이지 않는다.
- start/본문 실패 뒤 close 도달, 미시작 close, 중복 close 제거, legacy 모의 실패 격리,
  원래 오류 + 정리 오류, close 지속 실패 시 의존 자원 미해제, 동적 owner 교체를 보호한다.
- native Event로 실제 저장/close를 대기시킨 상태에서 lifespan waiter를 반복 취소한다.
  대기 중 DB/vault가 살아 있고 waiter가 종료하지 않으며, 해제 후 저장 행/작업 종료/잠금
  재획득과 취소 전달이 모두 맞는지 검사한다. 네트워크만 통제하고 실제 drain을 우회하지 않는다.
- 실패/취소 상황에서 제품 내부 close의 새로운 결함이 나오면 app에 private task 조회나
  강제 unlock 우회로를 넣지 않고 B의 해당 owner 최소 수정으로 처리한다.

### B. 알려진 완료 판정의 허점 검증·최소 보완

A 완료만으로 전체 수명 개선 완료를 선언하지 않는다. 다음 세 경계에 한정한다.

| 경계 | 구현 전 재현 | 필요한 계약 |
| --- | --- | --- |
| DiagnosticRuns | parent/replay child를 Event로 지연하고 close 반환과 생존 비교 | 서버 종료는 자신이 시작한 parent와 replay child가 실제 종료될 때까지 기다림. 자식 참조와 lease는 기존 owner가 보유. bounded 대기 반환을 종료 성공으로 사용하지 않음 |
| diagnostic trace | writer를 지연하고 stop의 상태/생존 비교 | 서버 종료용 기다림은 실제 trace thread 완료를 보장. 수동 stop의 기존 응답/제한 시간은 유지 |
| MockAutomationSupervisor | runner close/checkpoint 실패·지연 주입, 반복 close 비교 | 모든 보유 runner 종료를 기다리고 실패를 명시. 첫 close의 완료/실패를 재호출에도 유지하며 `_closing`만으로 성공 반환하지 않음 |

진단의 서버 종료 경로는 기존 API에 `timeout=None` 같은 명시적 무제한 join 옵션을 사용할 수
있다. 단, parent만 join하는 것으로 replay child까지 끝났다고 간주하지 않는다. 필요하면 해당
owner 내부에 자식 참조를 남기고 함께 drain한다. 외부 수동 진단 HTTP 계약은 유지한다.
서버 종료 대기 중 이벤트 루프는 살아 있어야 하므로 동기 thread join은 off-loop에서 수행한다.
강제 프로세스 종료 시간은 운영 환경의 별도 제한이며 정상 drain 보장으로 표현하지 않는다.

supervisor는 각 runner의 결과를 모두 기다린 후 실패를 전파한다. 공유 broker/DB 정리는 app이
그 실패를 받은 뒤 멈춘다. 주문 규칙·영속 control·자동 재개 정책을 바꾸지 않는다.
이번 조사만으로 모든 native close가 완전하다고 단정하거나 전체 owner를 일괄 재작성하지 않는다.

### 실행 비용과 종료 조건

1. A 수정 중 app 및 실제 변경한 close 관련 모듈을 실행한다. 직전 app 80건은 약 58초였으며
   새 시나리오의 실행 시간은 실측 전 미정이다. 실패 시 해당 모듈부터 다시 실행한다.
2. B는 각 진단/자동매매 owner 테스트와 직접 영향을 받는 app 계약만 실행한다. 기존 HTTP
   기준선, catalog 누락 검사, DB 소비자 감사도 관련 변경을 묶어서 확인한다.
3. A+B라는 의미 있는 수명 보완 단위가 끝나면 전체 all-local을 한 번 실행한다. 직전 전체는
   약 29분이었고 Windows rename 오류 1건으로 실패했다. 예상 시간의 참고일 뿐 이번 실행
   성공/비용을 보장하지 않는다. 원인 미확정 rename은 별도 항목으로 유지한다.
4. 성공 판정에는 실제 worker/자손 종료, 실행 대상 목록과 변경 파일 포함, 저장/실패/복구
   assertion 유지가 필요하다. timeout·skip·미실행은 성공이 아니다.
5. native 지연/취소/실패 검증과 기존 API 기준선이 유지되고 위 A+B 공백이 해소되면 수명
   개선 단계를 닫는다. 광범위한 독립 자원 회수, create_app 생성 실패, 새로운 restart 지원은
   확장하지 않는다. 해결되지 않은 native 조건은 정확히 남기고 완료라고 표시하지 않는다.

설계 확정 당시에는 문서만 작성했다. A+B의 현재 구현·실행 결과와 남은 검증 항목은 위 진행
기록을 따른다. whole 결과는 위의 오류 1건이며 hosted CI/PostgreSQL 실검증·커밋/게시/main 병합/NAS 배포는 아직 없다.
최종 게시 때 빌드 식별자 3곳과 Dockerfile의
이동 전 app.py 문자열 검사를 함께 정비해야 하며 이는 이번 수명 구현과 별도다.

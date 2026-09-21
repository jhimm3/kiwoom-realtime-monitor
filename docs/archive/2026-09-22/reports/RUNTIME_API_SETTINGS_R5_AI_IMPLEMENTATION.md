> **과거 기록** · 원래 경로: `reports/RUNTIME_API_SETTINGS_R5_AI_IMPLEMENTATION.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# R5c — AI 공급자 런타임 인증 교체

2026-09-15. 로컬 구현 완료, NAS 소스 동기화·배포 전.
누적 build: `2026.09.15-runtime-credentials-r5-ai-rotation-v1`.
설계 계약: [런타임 인증 설계](RUNTIME_API_SETTINGS_DESIGN_REVIEW.md), [API](../root/API_CONTRACT.md).

## 구현 범위

- OpenAI/Gemini/Claude의 고정 global 기본 프로필을 기존 prepare/apply API에 연결한다.
  vault 설치에는 키가 없어도 metadata와 같은 CentralAIService/뉴스 작업기를 조립한다.
- 키 준비만으로 유료 분석을 실행하지 않는다. 후보는 UNVERIFIED, 명시 적용 뒤 ACTIVE는 키 적용 완료다.
  비활성화 후보의 VERIFIED는 비활성화 준비 완료를 뜻한다.
- 프로필의 validation은 vault 저장 당시 기록이다. 첫 실제 분석의 인증 결과는 현재 revision의
  메모리 runtime_validation으로 구분한다. 성공/401/403/429·5xx/기타 오류를 고정 상태로 공개한다.
  성공 캐시 읽기로 새 키를 검증하지 않는다. 재시작/교체는 실행 검증 상태를 초기화한다.
- 본문 준비 전에 공급자/model/key/revision을 고정한다. 준비 task와 공유 실행 task를 구분해
  caller 취소 뒤에도 실제 준비·분석·DB 저장을 끝내며 공급자별 교체/서버 종료가 완료를 기다린다.
- 공유 실행 키는 revision/body hash를 포함해 같은 identity의 다른 본문을 합치지 않는다.
  성공 캐시의 품질 키는 그대로여서 키 교체로 유료 재분석을 강제하지 않는다.
- JSON 결과/사용량에 실행 credential_revision을 추가한다. 응답 최상위는 접수 revision이고
  캐시 results는 과거 실제 분석 revision을 보존한다. 구형 결과에는 해당 필드가 없을 수 있다.
- 교체/비활성화는 기사·작업·캐시·일일 사용량/상한을 보존한다. 키 교체만으로 과거 AI 작업을 재예약하지 않는다.
  commit 이후 실패는 해당 공급자만 키 부재/복구 필요로 두고 이전 ENV 키를 사용하지 않는다.

## 변경 경계와 확인된 원인

기존 서비스는 본문 준비 뒤 공급자를 선택하고 실행 잠금에서 키를 다시 읽었다. 따라서 준비/대기 중
교체 시 한 요청이 다른 설정을 사용하고, 취소된 HTTP 대기자가 실제 thread/최종 저장의 완료를
대표하지 못했다. 접수 snapshot과 shielded owned task를 추가하고 교체 장벽을 실제 완료에 연결했다.
새 owner 파일은 후보/프로필/commit 수명만 담당하며 분석/캐시/예산 정책은 기존 AI 서비스에 남긴다.
호출 경로는 기존 runtime → AI owner → AI service이며 별도 manager/범용 provider 프레임워크는 없다.
새 SQL 테이블·migration·프롬프트/분류/요약 품질 변경·실시간 순위 경로 변경은 없다.

## 검증

- 신규 경계 및 로컬 HTTPS API 17개 통과: 세 공급자 무과금 prepare/apply, keyless 시작,
  본문 준비 전 snapshot, 실행 대기 drain, 동일 요청 병합/다른 본문 분리, caller 취소,
  shutdown/최종 사용량 저장, 다른 공급자 독립 교체, deadline, 늦은 인증 실패,
  disable/ENV 차단, commit/publish 실패와 명시 복구, cache/사용량/상한 보존, 검증 상태 구분.
- 기존 AI/뉴스/작업/인증/서버 회귀 97개 통과. 누적 관련 회귀 351개 중 350 통과,
  Windows POSIX 권한 검사 1개 생략, 종료 코드 0 (`tmp/r5c-regression.log`).
- 모든 검증은 가짜 AI 응답·임시 암호화 vault/SQLite·offscreen Qt다.
  실제 유료 AI API·NAS/PostgreSQL·사용자 키/DB 검증은 수행하지 않았다.
- 최종 구문/공백 확인: Python 6개 정상, 이번 단계 tracked diff 공백 검사 정상,
  app/compose/Dockerfile의 누적 build 표기 3곳 일치. 신규 owner/테스트 파일 공백도 확인했다.

## 다음 단계

R5d 일반 공급자 PC 입력 UI/기존 운영값 확대를 진행한다. R6 실전 owner와 R7 누적 동기화/실환경 검증은 남았다.
R7까지 NAS 중간 동기화·재빌드를 요청하지 않는다. 실제 공급자 인증 성공은 적용 이후 명시 분석으로 확인해야 한다.
모델 에스컬레이션 없음.

# 회귀검사와 CI의 적용 기준

검사 선택·결과·증분 검토 절차는 [증분 테스트 관리](INCREMENTAL_TEST_REVIEW.md), 실행 범위는 [GitHub 워크플로](../.github/workflows/dependency-regression.yml)와 [검사 프로필](../tests/regression_profiles.json)을 따른다.

| 검증 | 확인할 근거 |
| --- | --- |
| 변경의 로컬 검증 | 변경된 호출·저장·수명 계약과 관련 검사 ID, 소스 일치, 실제 실행·종료 |
| GitHub 회귀 | 해당 커밋의 필수 결과. Windows·격리 PostgreSQL·Linux/operator·봉인 자료 결과는 실행된 범위로 구분 |
| 실제 NAS operator | 지정 환경·도구·lease의 별도 근거. 최신 변경이 그 수락 계약에 영향을 주는지 대조 |
| 설치·운영 수락 | 확정 설치물·데이터·환경과 실제 연결·복구를 확인. 승인된 범위에서 실행 |

이번 AR08-C24의 수락은 [540의 CI와 526 NAS 근거](architecture/app-target-20261023.md#c24-hosted-539-run-540)에 있다. 현재 전환 조건은 [AR 정본 0절](architecture/app-target-20261023.md#0-현재-기준과-진행-현황), 기존 검사 감사와 최초 실패는 [이전 전문](archive/2026-11-05/TEST_FINAL_QUALITY_AUDIT.md)에 있다. skip·완화·미실행을 통과로 계산하지 않는다.

# 현재 상태 요약

<a id="current-summary"></a>

정리 기준: **2026-10-11 · 제품 2.1.0**. 현재 소스, NAS operator/health·예약 잠금, 원격 main SHA·CI·필수 검사 설정을 이번 작업에서 직접 확인했다.

| 항목 | 현재 결론 |
|---|---|
| 현재 소스 SHA | HEAD·원격 main `89fd6fda19f1f5bc9c7a62695d767f783a81fa79` 일치. 미커밋 통합본에 CI 실행 기준 보완까지 적용하고 최종 검증 중이다. 빌드 `2026.10.11-mock-state-api-v1` 3곳 일치. |
| 실제 배포 release | NAS `2026.10.11-recorder-route-merge-v1-588b0b0b785f2c0a`, health `ok`. 사용자 승인으로 예약을 백업·일시 중지·같은 조건으로 재등록하며 격리 검사 중이다. 운영 서버는 기존 release를 유지한다. 예약은 10/12 **08:59:50부터 60분 / 20:10 저장**이며 NAS 재부팅 후 유지되지 않는다. |
| 통과한 검증 | 최종 소스 Windows 3,914건·279 worker 전체 범위 검증: 최초 E의 파일 교체 오류 1건을 보존하고 동일 소스의 해당 worker 549건을 AppData temp에서 재검증해 통과. 공유 검사기의 현재 소스 지문 대조 통과. NAS PG required 163건·extended 24건·후보 종료 14건·native PG/API·Linux 필수 65건 통과. catalog 427 unit/25 integration·lock 97개 일치. 문서 251개·원문 54개·ID 37개 보존. |
| 미실행·미확인 검증 | 커밋/push 후 hosted CI·최종 NAS storage gate·배포/health/예약 확인은 후속. PC 수동·장시간·다른 PC, 10/12 실제 녹화/저장, public operator Replay는 별도 운영 잔여다. |
| 남은 문제 | Windows 임시 디렉터리 rename 접근 거부의 직접 원인은 미확정. 다른 writer/모든 판단 입력의 종합 관측과 최초 enqueue 대기 시간, P1 COMMIT 대기·성능/보존 및 제품 잔여는 [원장](OPEN_ITEMS.md)을 따른다. 구형 서버·불충분한 빈 분 근거는 복구 대기를 유지한다. |

현재 완료·잔여 판정은 위 표를 정본으로 사용한다. [기능 보존 검증 계획](DEPENDENCY_REFACTOR_VERIFICATION_PLAN.md)은 검증 절차와 범위를 관리한다. 소스 변경·배포·검증 시 이 표를 갱신하고, 단계별 증거는 날짜별 아카이브에 기록한다.

## 단계별 근거

- [2026-10-11까지의 구현·검증·배포 이력](archive/2026-10-11/history/CURRENT_STATUS_through_20261011.md)
- [문서별 분류·이동 목록](archive/2026-10-11/README.md)

이전 커밋의 hosted 성공은 미커밋 작업본의 검증을 뜻하지 않는다. 이번 검증·배포는 사용자 요청으로 진행하며 예약 일시 중지·동일 조건 재등록도 명시 승인받았다. 완료 시 위 표와 아래 이력을 갱신한다.

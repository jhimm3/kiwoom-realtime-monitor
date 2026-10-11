# 현재 상태 요약

<a id="current-summary"></a>

정리 기준: **2026-10-11 · 제품 2.1.0**. 현재 소스, NAS operator/health·예약 잠금, 원격 main SHA·CI·필수 검사 설정을 이번 작업에서 직접 확인했다.

| 항목 | 현재 결론 |
|---|---|
| 현재 소스 SHA | 제품·배포 소스 `44af48de387e93ed6d0885e8fbad3f4a04515fea`. 후속 문서 커밋은 같은 실행 소스를 유지한다. 빌드 `2026.10.11-mock-state-api-v1` 3곳 일치. |
| 실제 배포 release | NAS `2026.10.11-mock-state-api-v1-6f6782b2c18171af`, health `ok`·실제 source path·불변 1,042개 파일 확인. 예약 PID 7076 `armed`·잠금·preflight 통과: 10/12 **08:59:50부터 60분 / 20:10 저장**, 기존 5개 capture flag 보존. NAS 재부팅 후 예약은 유지되지 않는다. |
| 통과한 검증 | [제품 소스 Actions](https://github.com/jhimm3/kiwoom-realtime-monitor/actions/runs/38137039403) **9개 job 성공**: Windows 3,914건·279 worker, Linux 65건, PG required 163건·extended 24건, sealed v1 4+6/v2 15건·이미지 소스·operator·종합 gate. 로컬 전체 범위 3,914건은 최초 파일 교체 오류를 보존하고 동일 소스 worker 549건 재검증·현재 소스 대조 통과. NAS 최종 storage 114건·배포/health/예약 확인. |
| 미실행·미확인 검증 | PC 수동·장시간·다른 PC, 실제 장중 누락/복구·10/12 실제 녹화와 저장, 등록 baseline의 public operator Replay는 별도 운영 잔여다. |
| 남은 문제 | Windows 파일 교체 접근 거부의 직접 소유자/필터는 미확정. 외부 registry 일시 장애는 로컬 전수로 보증할 수 없다. 최초 enqueue 대기·다른 writer/모든 판단 입력의 종합 관측, P1 COMMIT 대기·성능/보존 및 제품 잔여는 [원장](OPEN_ITEMS.md)을 따른다. 구형 서버·불충분한 빈 분 근거는 복구 대기를 유지한다. |

현재 완료·잔여 판정은 위 표를 정본으로 사용한다. [기능 보존 검증 계획](DEPENDENCY_REFACTOR_VERIFICATION_PLAN.md)은 검증 절차와 범위를 관리한다. 소스 변경·배포·검증 시 이 표를 갱신하고, 단계별 증거는 날짜별 아카이브에 기록한다.

## 단계별 근거

- [2026-10-11까지의 구현·검증·배포 이력](archive/2026-10-11/history/CURRENT_STATUS_through_20261011.md)
- [문서별 분류·이동 목록](archive/2026-10-11/README.md)

제품 소스의 실제 Actions·배포 결과를 위 표에 반영했다. 후속 문서 커밋의 main 상태와 Actions 결과는 최종 전달에 별도로 표시하며 제품 소스 SHA와 혼합하지 않는다. 단계별 실패·후속 통과는 아래 이력에 보존한다.

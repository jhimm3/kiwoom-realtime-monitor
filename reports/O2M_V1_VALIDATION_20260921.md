# O2-M V1 누적·장시간 검증

## 현재 상태

- O2-M0과 Me1~Me3 제품 구현 및 결정적 단일 fake 전체 경로는 완료했다.
- `scripts/check_mock_automation_v1.py`는 같은 Python 프로세스에서 핵심 62개 회귀를 기본 30분 동안
  반복한다. 매 반복은 게시·입장·위험 대사·매수/매도·A5 투영, runner checkpoint 재시작과 중복 체결,
  stop/recovery/fault limits, 계좌 scope와 projection cursor를 포함한다.
- 각 반복마다 저장소와 임시 DB를 새로 만들고 닫아, 재시작 후 상태 복원과 자원 회수가 반복된다.
  실패하면 즉시 중단하고 마지막 unittest 출력을 JSON에 포함한다.
- 결과에는 wall/CPU 시간, 반복·검사 수, 시작/종료/최고 RSS, Python 추적 peak를 남긴다.

## 빠른 확인 결과

- 실행: `python scripts/check_mock_automation_v1.py --duration-seconds 120 --max-cycles 2`
- 결과: 2회, 124개, 실패 0, 25.485초.
- 시작 RSS 24,780,800 bytes, 종료 52,948,992 bytes, 최고 66,334,720 bytes.
- 첫 반복의 모듈·SQLite·unittest 캐시가 포함된 값이므로 2회 결과만으로 누수 여부를 판정하지 않는다.

## 30분 반복 결과

- 실행 시각: 2026-09-21 14:27:56~14:58:02 KST.
- 실행 시간: 1,805.750초, 151회, 총 9,362개 검사, 실패 0.
- CPU 시간 318.391초, wall 대비 CPU 비율 0.1763.
- RSS 시작 24,924,160 bytes, 종료 54,403,072 bytes, 최고 72,097,792 bytes.
- Python 추적 메모리 최고 17,305,017 bytes.
- 종료 RSS가 실행 중 최고치보다 낮고 151회 전체가 완료됐으므로 반복마다 제한 없이 증가하는 형태는
  관측되지 않았다. 시작 대비 증가는 첫 반복의 모듈·SQLite·unittest 캐시를 포함한다.
- `reports/o2m-v1-soak-result.json`에 기계 판독 결과를 보존했다. stderr는 비어 있다.

## 30분 판정 명령

```text
python scripts/check_mock_automation_v1.py --duration-seconds 1800 --output reports/o2m-v1-soak-result.json
```

완료 조건은 종료 코드 0, 모든 반복의 실패 0, 반복이 계속 진행되고 RSS가 반복마다 제한 없이 증가하지
않는 것이다. 결과 파일은 실행 환경의 검증 산출물이며 제품 DB나 NAS 설정을 변경하지 않는다.

## V1 잔여 순서

누적 소스 860개를 NAS에 동기화했고 SHA-256 불일치는 0개다. 기존 파일 839개는
`X:\kiwoom-monitor-backups\20260921-150747-o2me3-fake-cycle-v1`에 백업했다. `.env`,
`postgres-data`, `server-data`, `server-secrets`는 보존했다.

1. 사용자가 새 이미지를 빌드하고 프로젝트를 시작한다.
2. 새 컨테이너에서 PostgreSQL 실제 마이그레이션·왕복·rollback 검사를 실행한다.
3. 승인된 제한 모의계좌로 주문 수량·계좌·세션을 고정한 시험을 수행한다.
4. PC 연구와 NAS 순위·실시간 수집을 함께 장시간 운용해 queue lag, RSS, CPU, DB 증가량과 순위 지연을
   연구 OFF 기준과 비교한다.

## 새 이미지 기동 확인

- 외부 `https://mfactory.duckdns.org:8443/health`와 내부 `http://192.168.0.5:8787/health`가 모두
  `status=ok`, `server_build=2026.09.21-o2me3-fake-cycle-v1`을 반환했다.
- 이 PC의 기존 SSH known_hosts에는 NAS IP 키가 있지만 SSH 개인키가 없어 비대화형 컨테이너 명령은
  실행할 수 없다. PostgreSQL 검사는 Synology의 서버 컨테이너 터미널에서 실행해야 한다.

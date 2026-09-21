> **과거 기록** · 원래 경로: `reports/CR1B_RESOURCE_LIMITS_AND_BENCHMARK.md` · [현재 문서](../../../README.md) · 당시 미구현·다음 단계는 현재 상태가 아니다. 원문 바이트는 아카이브 ZIP에 보존했다.

# CR1b — 연구 자원 제한과 계측

2026-09-16. **CR1 최소 기능/fixture 검증 완료. 다음 구현은 CR2 지속 캠페인.**
실제 NAS 전체 기간·장시간 처리량까지 완료한 상태는 아니다.

## 적용 기능과 계약

PC의 별도 연구 프로세스와 CLI가 기본 512MiB/CPU 목표 50%를 사용한다.
단일/비교 요청의 최상위 `resource_budget`, 검색 요청의 기존 `search.resource_budget`으로
`memory_mb`(128~4096), `cpu_duty_percent`(10~100)를 지정한다.
CLI는 `--memory-mb`, `--cpu-duty-percent`다. 운영 예산은 과학 identity에 포함하지 않는다.

- 기존 데이터 reader가 파일 크기/제한된 manifest를 먼저 검사한다. 현재 RSS + 입력 바이트×8이
  예산을 넘으면 전체 JSONL을 읽기 전에 `input_memory_estimate_exceeds_limit`로 차단한다.
  배수 8은 실제 필요량을 보증하는 값이 아니라 보수적 추정이다.
- Windows는 현재 프로세스의 GetProcessMemoryInfo, Linux는 `/proc/self/statm`으로 실제 RSS를 읽는다.
  계측 불가도 차단하며, 로딩·bundle 검증·실행·결과 계산 경계에서 한도를 확인한다.
- 약 50ms batch마다 CPU 계산시간과 경과시간을 비교해 양보한다. 이미 IO로 기다린 시간은
  추가 휴식에서 제외한다. 휴식은 최대 200ms이며 취소/lease 점검은 계산 checkpoint에서 유지한다.
  협력적 목표이며 OS 강제 quota나 순간 메모리 초과 방지 장치는 아니다.
- bundle의 `ResearchReplayCursor`가 ingest를 한 번씩 기존 reader로 변환하고 최신 revision을 유지한다.
  최신 무효 정정이 이전 유효 봉을 숨기는 규칙도 전체 replay와 동일하다. 하루/legacy 실행 계약은 유지한다.
- 자원 중단은 검색의 완료/FAILED trial로 기록하지 않고 기존 `INTERRUPTED` attempt에 이유를 남긴다.
  job 저장 상태는 기존 cancelled이며 응답의 `job_status`는 resource_blocked다. 같은 예산으로 GUI가
  무한 자동 재개하지 않는다. 예산 변경 후 수동 실행하면 미완료 trial을 다시 시도한다.
- 단일 실행도 자원 중단 시 run을 취소 상태로 남긴다. preflight 차단은 출력 DB를 만들지 않는다.
  CLI/연구 프로세스 직접 차단은 status resource_blocked/종료 코드 3이다.

## 변경 전후 실행 계측

이 PC의 기존 Python 실행기로 **1종목·일별 30분**의 같은 임시 KRX fixture를 별도 프로세스에서 순차 실행했다.
여러 날짜는 평일만 선택했으며 실제 거래소 휴장 달력 검증은 아니다. 운영 CPU 목표 100%로 비교했다.
최고 RSS는 별도 10ms sampler의 최고 표본이며 순간 최고값을 보증하지 않는다.
본 비교에서만 구현 identity를 고정해 decision/fill/outcome/performance까지의 논리 hash를 비교했다.
제품의 실제 구현 hash 계산은 변경하지 않았다. 실제 NAS/API/사용자 DB/키/주문은 사용하지 않았다.

| 거래일 | 관측 행 | 변경 전 완료시간 | 변경 후 완료시간 | 변경 전 CPU | 변경 후 CPU | 변경 전 최고 RSS | 변경 후 최고 RSS | 논리 hash |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 31 | 2.0703초 | 2.0307초 | 0.2812초 | 0.2656초 | 41.01MiB | 41.18MiB | 일치 |
| 5 | 155 | 9.0758초 | 8.5598초 | 1.5469초 | 1.2812초 | 48.42MiB | 50.53MiB | 일치 |
| 20 | 620 | 39.2255초 | 38.9533초 | 8.7812초 | 6.0781초 | 75.44MiB | 84.57MiB | 일치 |

입력/결과 파일 증가량은 각각 1일 20,554/447,089바이트, 5일 101,263/1,184,424바이트,
20일 403,919/3,933,035바이트이며 전후 결과 증가량도 일치했다.
20일 CPU 비용은 약 31% 줄었지만 완료시간은 거의 같다. 계산 외 대기 비용이 크다는 관측이며,
특정 저장 함수가 원인이라고 확정하지 않았다. DB 저장 경계를 이번 작업에서 재작성하지 않았다.
증분 상태 보관 때문에 최고 표본 RSS는 늘었다. 메모리 절감으로 보고하지 않는다.

CPU 목표 50%의 5일 추가 실행은 8.7364초/CPU 1.4688초/최고 표본 RSS 50.18MiB이며 논리 hash가 같다.
시험 자체에 IO 대기가 많으므로 실제 CPU 사용률이 정확히 50%라는 증거는 아니다.
계산 위주의 fake clock 회귀에서 짧은 양보·IO 대기 제외·200ms 상한을 별도로 검증했다.

## 큰 입력의 사전 검증

**20종목·일별 390분**의 실제 임시 JSONL을 생성해 동일 날짜 수의 파일 크기와 preflight를 측정했다.
이 입력은 순위/봉 식별 형식과 일별 hash를 갖는 크기 시험이며 실제 NAS 수집 완전성 검증은 아니다.
큰 자료의 전체 시뮬레이션은 실행하지 않았다.

| 거래일 | 입력 바이트 | 당시 RSS | 기본 512MiB preflight |
| --- | ---: | ---: | --- |
| 1 | 4,742,467 | 46.91MiB | 통과, 실행 미검증 |
| 5 | 23,712,335 | 47.17MiB | 통과, 실행 미검증 |
| 20 | 94,849,340 | 48.19MiB | 입력 메모리 추정 초과로 차단 |

20일이 기본 예산에서 실행된다고 보장하지 않는다. 예산을 늘려 사전검사를 통과하더라도
실행 중 RSS 제한은 계속 적용한다. 실제 전체 시장 처리시간과 적절한 예산은 V1에서 계측한다.

## 검증과 파일

자원 한도/계측 불가 차단, preflight/실제 RSS 초과, CPU 시간 기반 양보,
무효/늦은 정정의 전체 prefix와 cursor 동일성, 자원 중단이 trial 결과에 들어가지 않음,
중단 attempt에서의 재시도와 기존 reader/실행/검색/Qt 화면 회귀를 검증한다.

최종 검증: `tmp/cr1-resource-regression.log`의 **95개 / 13.576초 / OK / 종료 코드 0**.
변경 Python 9개 파일의 AST 문법 검사와 관련 문서 `git diff --check`도 통과했다.

주요 파일: `application/research_resources.py`, `application/research_replay.py`,
`infrastructure/research_data_source.py`, `research_process.py`, `scripts/run_research.py`,
`presentation/research_dialog.py`, `tests/unit/test_research_resources.py`.
새 정책은 자원 한도/계측 수명 경계를, cursor는 독립적인 ingest 상태 경계를 가진다.
단순 전달 Manager/새 DB/API/의존성/서버 build는 추가하지 않았다. NAS 재빌드/동기화는 필요 없다.

임시 근거는 `tmp/cr1-resource-baseline-*.json`, `tmp/cr1-resource-after-*.json`,
`tmp/cr1-resource-full-shape-*.json`, `tmp/cr1-resource-duty50-5.json`이다.
V1에서 실제 NAS 전체 기간의 처리량/최고 부하/디스크 증가/장시간 복구를 추가 검증한다.
메모리의 순간 초과와 긴 단일 연산은 협력적 제한의 한계다. 하드 quota 도입은 이번 구현 범위가 아니다.
모델 에스컬레이션: 없음.

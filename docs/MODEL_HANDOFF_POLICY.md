# 모델 단계 전환 정책

첨부된 `Codex Mandatory Bidirectional Model Handoff Policy`의 요약이다. **정본은 저장소 루트 [AGENTS.md](../AGENTS.md)** 이며 충돌 시 AGENTS.md의 현행 규칙을 따른다. 목표는 필요한 정확도와 안정성을 만족하는 가장 낮은 모델·추론 수준을 쓰는 것이다. 모델 이름만 바뀌고 같은 작업을 계속하는 것을 handoff로 간주하지 않는다.

## 사용 가능한 모델과 추론 수준

- 모델: GPT-6 Luna, GPT-6 Sol, GPT-6 Astra.
- 저장소 런타임 표기: Low, Medium, High, XHigh, Max, Ultra.
- 첨부 정책 표기 변환: Light = Low, Extra High = XHigh.
- 사용 중인 런타임이 특정 조합을 지원하지 않으면 지원한다고 주장하지 않는다.
- 추론 수준은 선택한 모델에서 지원하는 조합만 사용한다. 단순 작업은 Low부터, 일반 구현은 Medium, 더 깊은 추적·검토는 High 이상을 검토하며, 상위 추론 수준은 모델 자체가 맞는지 다시 확인하는 신호로 삼는다.

## 사용자 지정 최소 모델

사용자의 명시 요청(2026-10-09)에 따라 이 사용자의 이후 작업과 handoff에서는 GPT-6 Luna를 사용하지 않는다. 모델 선택은 GPT-6 Sol부터 시작하며, 설계 판단이 필요한 경우에는 Astra를 선택한다. 이 지정은 아래의 Luna 우선 평가 및 Luna로 하향 handoff하는 기본 지침보다 우선한다. 현재 런타임 모델·추론 수준은 최신 실행 기록으로 확인하고, 확인되지 않으면 추측하지 않는다. 실제 런타임을 바꾸지 못했다면 전환했다고 주장하지 않는다.

## 현재 채팅의 실제 설정 확인

handoff를 작성하기 전, 직전 메시지나 사용 가능한 모델 목록으로 현재 설정을 추측하지 말고 **현재 채팅의 최신 실행 기록**을 확인한다. Windows Codex에서는 현재 채팅의 `CODEX_THREAD_ID`와 같은 이름으로 끝나는 JSONL 파일이 `%USERPROFILE%\.codex\sessions\<연도>\<월>\<일>\rollout-*-<thread-id>.jsonl` 아래에 있다. 최신 `turn_context` 레코드의 `payload.model`과 `payload.effort`가 그 턴에 실제 적용된 모델과 추론 수준이다. `session_meta`의 모델 제공자나 이전 `turn_context`를 현재 설정으로 쓰지 않는다.

현재 Codex 터미널의 PowerShell에서 다음을 실행한다. 마지막에 출력된 `model`, `effort`, `turn_id`를 확인하고, 긴 턴이라 레코드가 안 나오면 `-Tail 120` 값을 늘린다.

```powershell
$threadId = $env:CODEX_THREAD_ID
$sessionRoot = Join-Path $env:USERPROFILE '.codex\sessions'
$log = Get-ChildItem -LiteralPath $sessionRoot -Filter "*-$threadId.jsonl" -File -Recurse |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $threadId -or -not $log) { throw '현재 채팅의 Codex 실행 기록을 찾지 못했습니다.' }
Get-Content -LiteralPath $log.FullName -Tail 120 | ForEach-Object {
    try { $_ | ConvertFrom-Json -ErrorAction Stop } catch { }
} | Where-Object { $_.type -eq 'turn_context' } |
    Select-Object -Last 1 |
    ForEach-Object { [pscustomobject]@{
        turn_id = $_.payload.turn_id
        model = $_.payload.model
        effort = $_.payload.effort
    } } | Format-List
```

이 셸 변수나 실행 기록에 접근할 수 없는 다른 환경에서는 Codex 화면의 현재 채팅 모델 선택기를 확인한다. 어느 쪽에서도 현재 턴 값을 확인하지 못하면 `확인 불가`라고 적고, 이전 턴·기본 설정·사용 가능한 모델 목록으로 대신 채우지 않는다. `effort` 필드가 없으면 추론 수준도 확인 불가로 둔다.

## 단계마다 새로 판정

새 사용자 메시지, 독립된 구현/검토/테스트 단계, 주요 원인·설계·알고리즘·transaction 결정의 완료 시점마다 다음 작업을 Luna Low부터 다시 평가한다. 직전 모델과 추론 수준은 다음 단계에 승계되지 않는다.

- Luna로 정확히 처리할 수 있으면 Luna를 유지한다. 유지 사유 보고는 요구하지 않는다.
- Sol은 현재 작업에 복잡한 상태, 비동기 실행, 동시성, transaction/DB 정합성, 여러 핵심 모듈의 흐름, 높은 회귀 위험이 남아 있을 때만 쓴다. 이런 판단이 끝나면 다음 독립 단계는 Luna로 다시 평가한다.
- Astra는 문제 정의, 아키텍처·책임 경계, 복수 설계안 또는 시스템 규모 trade-off 판단이 현재 단계에 남아 있을 때만 쓴다. 설계·파일·함수·데이터 흐름·검증 방법이 구체화되면 Astra 단계는 끝난다.
- Sol/Astra를 유지하려면 다음 작업에도 해당 모델의 강점이 남아 있고 Luna(또는 Astra의 경우 Sol)가 부족한 구체적인 이유를 입증한다. 중요도·코드 길이·파일 수·남은 작업량·이미 사용 중이라는 사실은 근거가 아니다.
- 상향은 현재 모델로 계속할 때 정확도 저하·큰 회귀·재작업 위험이 높거나 필수 구조 판단이 부족한 경우에 한다. 하위 모델이 충분하면 상위 모델을 편의상 유지하지 않는다.
- 작업량·파일 수·기능 중요도·직전 모델 사용 여부만으로 모델을 올리거나 유지하지 않는다.

원인·설계·인터페이스 결정, 핵심 알고리즘·transaction·concurrency 해결, 주요 TODO 완료, 테스트 실패 원인 확인, 작업 범위 축소 때마다 다음 작업을 처음부터 재평가한다. Luna가 정확히 할 수 있으면 즉시 하향 handoff한다. 명확한 테스트·lint·acceptance 확인은 설계 모델을 자동 승계하지 않는다. 새 단계마다 추론 수준도 재설정하되, 같은 모델 안에서는 중단 없이 조절한다.

## 모델 변경 시 강제 handoff

다음 단계에서 **모델이 바뀌어야 하면** 결과를 표시하고 즉시 멈춘다. 상향·하향 모두 같다. 사용자에게 매 단계에서 모델을 고르게 묻는 선택형 질문으로 대체하지 않는다. handoff 사유가 있으면 권고만 한 채 현재 모델로 계속하지 않는다.

```text
[HANDOFF REQUIRED — ESCALATE]
Current: <현재 모델과 추론 수준; 알 수 없으면 확인 불가라고 명시>
Switch to: <권고 모델과 추론 수준>
Reason: <현재 단계에 남은 구체적 위험 또는 설계 판단>
Completed: <완료된 분석·결정>
Remaining: <handoff 뒤의 작업>
```

하향 handoff도 같은 강도로 적용한다.

```text
[HANDOFF REQUIRED — DOWNGRADE]
Current: <현재 모델과 추론 수준>
Switch to: <낮은 비용으로 충분한 모델과 추론 수준>
Reason: <복잡한 단계가 끝났고 다음 작업이 단순해진 근거>
Completed: <완료된 핵심 단계>
Remaining: <다음 모델이 수행할 작업>
```

handoff 뒤에는 모델 전환이 확인될 때까지 다음 파일 수정, 테스트, 문서 작업, TODO 수행을 시작하지 않는다. 다만 이미 시작한 수정이 안전하지 않은 중간 상태라면 그 원자적 수정 단위만 안전한 상태로 마무리한다. 런타임에서 모델을 직접 전환할 수 없으면 전환했다고 주장하지 않고 정확한 다음 모델·추론 수준을 안내한다. 하향도 선택형 권고가 아니라 단계 경계다.

같은 모델 안에서 추론 수준을 바꾸는 것은 모델 handoff가 아니다. 현재 작업에 맞춰 Low/Medium/High/XHigh 등 지원되는 수준으로 유연하게 높이거나 낮추며, 그 조절만으로 작업을 멈추거나 사용자에게 전환을 요구하지 않는다. 추론 수준을 높여도 필요한 판단이 해결되지 않고 다른 모델이 필요한 종류의 문제라면 모델 handoff를 한다.

## 재평가 결과

- 테스트·lint·명확한 acceptance 확인은 설계 모델을 자동 승계하지 않는다. 내용이 정해졌으면 Luna부터 평가한다. 테스트 실패가 복잡한 정합성 문제면 Sol, 구조 문제면 Astra로 재평가한다.
- Astra가 설계를 구체화한 뒤 구현은 Luna Medium부터 평가한다. 구현에서 데이터 경계·동시성 판단이 남으면 Sol Medium/High를 쓴다.
- Sol에서 transaction·동시성 핵심 문제가 해결된 뒤 반복 적용·문서화만 남으면 Luna로 handoff한다.
- 같은 모델 내 추론 수준 상향·하향은 자유롭게 적용하고 별도 보고를 최소화한다. 새 요청·단계가 시작될 때 적정 수준을 다시 고른다.
- handoff 사유는 단계별로 판단한다. 이전에 낸 권고를 근거로 불필요한 추가 모델 전환을 강제하지 않는다.

이 정책은 위험 경계의 검증 의무, 데이터 보존 규칙, 테스트 환경 제한, NAS 배포 절차를 완화하지 않는다. 모델 전환은 그 작업 계약을 대체하지 않는다.

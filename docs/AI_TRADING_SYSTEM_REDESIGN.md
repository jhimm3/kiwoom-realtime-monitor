# 연구·Shadow·매매 개발의 설계 안내

현재 목표는 [네 운용군 설계](architecture/app-target-20261023.md#12-연구노트-기반-네-운용군의-목표-설계)과 [연구 근거의 AR 반영](architecture/app-target-20261023.md#mimosa-research-refactor-contract)에 있다. 진행·검증·사용 제한은 [AR 정본 0절](architecture/app-target-20261023.md#0-현재-기준과-진행-현황)과 [기능별 후속](OPEN_ITEMS.md)을 따른다.

- **입력과 시점.** 실시간 현재가·체결은 기존 0B를 사용한다. 연구는 필요한 해상도의 자료를 쓰고, 판단 cut에서 실제 가용했던 출처·판본을 보존한다. [시간 계약](architecture/app-target-20261023.md#time-audit-followup).
- **공통 계산과 목적별 상태.** 같은 입력·판본·정의는 공유한다. 진입·보호·보유 상태와 계좌별 권한은 목적에 맞게 분리한다. [계산 효율 계약](architecture/app-target-20261023.md#strategy-compute-efficiency).
- **연구 → Shadow → 실행.** 동결 입력과 재현 가능한 결과를 먼저 검증한다. 후보 수락과 실제 주문 권한은 별개이며 보호·체결·미확정 주문·재시작 복구는 [실행 계약](architecture/r3-ownership.md#execution)을 따른다.
- **추가 개발.** [개발 로드맵](../FUTURE_DEVELOPMENT_ROADMAP.md)에서 현재 순서를 확인한다. 미정 기준은 관련 설계의 미결로 둔다.

이전 단계의 의사결정, 비교 실험, 파일·테스트 근거는 [이전 전문](archive/2026-11-05/AI_TRADING_SYSTEM_REDESIGN.md)에서 찾는다.

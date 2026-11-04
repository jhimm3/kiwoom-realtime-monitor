"""final 실행 전 기준을 동결하고 완료 원장으로 모의 후보 게시 파일을 만든다."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
for module_root in (REPOSITORY_ROOT, REPOSITORY_ROOT / "src"):
    if str(module_root) not in sys.path:
        sys.path.insert(0, str(module_root))

from kiwoom_monitor.domain.time_basis import kst_now
from kiwoom_monitor.research_candidate import (
    freeze_policy, load_publication, prepare_publication,
    inspect_final_preparation, final_preparation_text, CRITERIA_NAMES,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect-preparation", help="고정 개발 입력에서 동결 전 기준·기간을 읽기 전용 점검")
    inspect.add_argument("--development-root", required=True, type=Path)
    inspect.add_argument("--request", type=Path, help="동결할 고정 연구 요청의 전략·비용·세션만 대조; 자료 접근 없음")
    inspect.add_argument("--family", choices=("breakout", "pullback"), default="breakout")
    inspect.add_argument("--strategy-ref", default="")
    inspect.add_argument("--final-start")
    inspect.add_argument("--final-end")
    inspect.add_argument("--cost-reference", type=Path)
    inspect.add_argument("--gap-provenance", type=Path)
    for name in CRITERIA_NAMES:
        inspect.add_argument("--" + name.replace("_", "-"), type=int)
    freeze = commands.add_parser("freeze-policy", help="final 실행 전 적격 기준 동결; 생략한 기준은 미정")
    freeze.add_argument("--request", required=True, type=Path)
    freeze.add_argument("--output", required=True, type=Path)
    freeze.add_argument("--strategy-ref", required=True)
    freeze.add_argument("--minimum-closed-trades", type=int)
    freeze.add_argument("--minimum-active-days", type=int)
    freeze.add_argument("--minimum-net-realized-pnl-won", type=int)
    freeze.add_argument("--maximum-drawdown-ppm", type=int)
    prepare = commands.add_parser("prepare-publication", help="완료된 final 원장으로 게시 파일 생성; 네트워크 호출 없음")
    prepare.add_argument("--request", required=True, type=Path)
    prepare.add_argument("--policy", required=True, type=Path)
    prepare.add_argument("--output", required=True, type=Path)
    prepare.add_argument("--account-ref", required=True)
    prepare.add_argument("--final-batch-id", required=True)
    prepare.add_argument("--final-run-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect-preparation":
            report = inspect_final_preparation(args.development_root, family=args.family,
                strategy_ref=args.strategy_ref, final_start=args.final_start, final_end=args.final_end,
                request_path=args.request,
                cost_reference=args.cost_reference, gap_provenance=args.gap_provenance, now_provider=kst_now,
                **{name: getattr(args, name) for name in CRITERIA_NAMES})
            print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        if args.command == "freeze-policy":
            policy = freeze_policy(
                args.request, args.output, strategy_ref=args.strategy_ref,
                minimum_closed_trades=args.minimum_closed_trades,
                minimum_active_days=args.minimum_active_days,
                minimum_net_realized_pnl_won=args.minimum_net_realized_pnl_won,
                maximum_drawdown_ppm=args.maximum_drawdown_ppm,
                now_provider=kst_now,
            )
            summary = {"status": "BLOCKED" if policy.tbd_fields else "FROZEN",
                       "policy_id": policy.policy_id, "candidate_spec_hash": policy.candidate_spec_hash,
                       "reasons": [f"TBD:{name}" for name in policy.tbd_fields]}
        else:
            documents = prepare_publication(
                args.request, args.policy, args.output, account_ref=args.account_ref,
                final_batch_id=args.final_batch_id, final_run_id=args.final_run_id,
            )
            receipt = documents["eligibility_receipt"]
            summary = {"status": receipt["status"], "reasons": receipt["reasons"],
                       "package_hash": documents["package"]["package_hash"]}
        print(json.dumps({**summary, "output": str(args.output.resolve())}, ensure_ascii=False))
        return 0
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
        print(json.dumps({"status": "ERROR", "reason": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

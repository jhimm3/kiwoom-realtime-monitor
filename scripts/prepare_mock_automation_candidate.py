"""final 실행 전 기준을 동결하고 완료 원장으로 모의 후보 게시 파일을 만든다."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
for module_root in (REPOSITORY_ROOT, REPOSITORY_ROOT / "src"):
    if str(module_root) not in sys.path:
        sys.path.insert(0, str(module_root))

from kiwoom_monitor.application.mock_automation_candidate import (
    MAX_CANDIDATE_PUBLICATION_BYTES,
    MockAutomationEligibilityPolicy,
    candidate_package_from_dict,
    eligibility_receipt_from_dict,
    eligibility_policy_from_dict,
    evaluate_candidate_eligibility,
    validate_publication_size,
)
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.research_process import (
    final_candidate_spec_hash,
    final_candidate_spec_document,
    load_development_validation_request,
    load_research_process_request,
    prepare_mock_automation_candidate_publication,
)
from scripts.run_research import research_implementation_hash
from scripts.summarize_historical_baseline_results import (
    _daily_development_requests, inspect_daily_development_inputs,
)


CRITERIA_NAMES = ("minimum_closed_trades", "minimum_active_days",
                  "minimum_net_realized_pnl_won", "maximum_drawdown_ppm")


def inspect_final_preparation(
    development_root: Path, *, family: str = "breakout", strategy_ref: str = "",
    minimum_closed_trades: int | None = None, minimum_active_days: int | None = None,
    minimum_net_realized_pnl_won: int | None = None, maximum_drawdown_ppm: int | None = None,
    final_start: str | None = None, final_end: str | None = None,
    request_path: Path | None = None,
    cost_reference: Path | None = None, gap_provenance: Path | None = None,
    checkpoint: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """동결 전 선택 사항을 점검한다. OOS·DB·최종 실행/기준 파일에는 접근하지 않는다."""
    checkpoint()
    if family not in {"breakout", "pullback"}:
        raise ValueError("지원하지 않는 개발 전략입니다.")
    root = Path(development_root).resolve()
    plan, _ = _daily_development_requests(root)
    request = load_development_validation_request(root / "requests" / f"daily-{family}.json").request
    implementation_hash = research_implementation_hash(request.session_profile)
    candidate = final_candidate_spec_document(request, implementation_hash)
    candidate_hash = final_candidate_spec_hash(request, implementation_hash)
    if request_path is not None:
        fixed = _request(request_path)
        if final_candidate_spec_hash(fixed, implementation_hash) != candidate_hash:
            raise ValueError("고정 연구 요청과 개발 후보의 전략·비용·세션 설정이 다릅니다.")
    # 기존 계약으로 값만 검사한다. 이 임시 객체의 시각·ID를 동결 근거로 반환하지 않는다.
    criteria = dict(minimum_closed_trades=minimum_closed_trades, minimum_active_days=minimum_active_days,
                    minimum_net_realized_pnl_won=minimum_net_realized_pnl_won, maximum_drawdown_ppm=maximum_drawdown_ppm)
    validation = MockAutomationEligibilityPolicy(strategy_ref="preparation-validation-only",
        candidate_spec_hash=candidate_hash, frozen_at=datetime.now(UTC), **criteria)
    if not isinstance(strategy_ref, str):
        raise ValueError("전략 이름은 문자열이어야 합니다.")
    def moment(value):
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise ValueError("final 기간은 시간대가 있는 ISO 시각이어야 합니다.")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("final 기간에는 시간대가 필요합니다.")
        return parsed.astimezone(UTC)
    start, end = moment(final_start), moment(final_end)
    if start is not None and end is not None and start >= end:
        raise ValueError("final 시작은 끝 시각보다 빨라야 합니다.")
    folds = plan["evaluation"]["folds"]
    development_end = max(datetime.fromisoformat(fold["end"]).astimezone(UTC) for fold in folds)
    # 현 작업은 개발 뒤의 시간순 final 준비다. 재사용·겹침은 자료 읽기 전에 거절한다.
    if ((start is not None and start < development_end)
            or (end is not None and end <= development_end)):
        raise ValueError("final 기간은 모든 고정 개발 기간 이후여야 합니다.")
    checkpoint()
    inputs = inspect_daily_development_inputs(root, cost_reference=cost_reference,
        gap_provenance=gap_provenance, checkpoint=checkpoint)
    reasons = [f"TBD:{name}" for name in validation.tbd_fields]
    if not strategy_ref.strip():
        reasons.append("TBD:strategy_ref")
    for name, value in (("final_start", start), ("final_end", end)):
        if value is None:
            reasons.append(f"TBD:{name}")
    costs = request.execution.cost_model
    if start is not None and end is not None:
        if not (datetime.fromisoformat(costs.valid_from) <= start < end <= datetime.fromisoformat(costs.valid_to)):
            reasons.append("COST_VALIDITY_OUTSIDE_FINAL_WINDOW")
    if family != "breakout":
        reasons.append("MOCK_SHADOW_FAMILY_NOT_SUPPORTED")
    reasons.extend(["ACTUAL_BROKER_COSTS_UNVERIFIED", "FULL_SESSION_COMPLETENESS_UNVERIFIED",
                    "FINAL_INPUT_AND_UNUSED_WINDOW_UNCHECKED", "DEVELOPMENT_PERFORMANCE_UNACCEPTED",
                    "SAME_SETTINGS_SHADOW_UNCHECKED"])
    report = {"version": "mock_candidate_final_preparation/v1", "status": "REVIEW_REQUIRED",
            "strategy_ref": strategy_ref.strip() or None, "family": family,
            "candidate_spec": candidate, "candidate_spec_hash": candidate_hash,
            "criteria": criteria, "criteria_frozen": False,
            "final_window": {"start": start.isoformat() if start else None,
                             "end": end.isoformat() if end else None, "end_exclusive": True,
                             "after_development_checked": start is not None and end is not None},
            "development_end": development_end.isoformat(), "input_review": inputs,
            "reasons": reasons, "final_request_created": False, "oos_queried": False,
            "candidate_eligibility_evaluated": False, "orders_started": False,
            "limitations": ["preview_is_not_a_frozen_policy_or_final_admission",
                            "final_candidate_identity_excludes_dates_and_source_paths",
                            "final_batch_must_separately_bind_input_hash_window_and_unused_ledger"]}
    if request_path is not None:
        report["fixed_request"] = {"path": str(Path(request_path).resolve()), "candidate_spec_matches": True,
                                   "source_and_period_admitted": False}
    return report


def final_preparation_text(report: dict[str, Any]) -> str:
    labels = {"minimum_closed_trades": "완료 거래 최소(건)", "minimum_active_days": "운용일 최소(일)",
              "minimum_net_realized_pnl_won": "순실현손익 최소(원)", "maximum_drawdown_ppm": "낙폭 최대(ppm)"}
    window, inputs = report["final_window"], report["input_review"]
    parameters = report["candidate_spec"]["parameters"]
    costs = report["candidate_spec"]["execution_model"]["cost_model"]
    lines = ["후보·final 동결 준비 · 확인 필요", f"전략: {report['strategy_ref'] or '미정'} / {report['family']}",
             f"후보 설정 hash: {report['candidate_spec_hash']}",
             f"보유 {parameters['max_hold_minutes']}분 / 손절 {parameters['stop_loss_bps']}bp / 익절 {parameters['target_bps']}bp",
             f"수량 {parameters['quantity']}주 / 전략 자금 {parameters['capital_won']}원",
             "시간 정책: " + (f"정규장 단일가 이전 청산 / 여유 {parameters['session_exit_buffer_seconds']}초"
                if parameters.get("session_exit_policy") == "krx_regular_before_auction/v1" else "기본 정책"),
             f"비용: 수수료 각 {costs['commission_bps']}bp / 매도세 {costs['sell_tax_bps']}bp / 슬리피지 {costs['slippage_bps']}bp",
             "", "적격 기준 · 아직 동결하지 않음"]
    lines.insert(3, "고정 연구 요청 설정: " + ("후보와 일치 · 자료·기간 승인은 별도" if "fixed_request" in report else "별도 대조 필요"))
    lines.extend(f"{labels[name]}: {report['criteria'][name] if report['criteria'][name] is not None else '미정'}"
                 for name in CRITERIA_NAMES)
    lines.extend(["", f"개발 기간 끝: {report['development_end']}",
                  f"final 시작: {window['start'] or '미정'} / 끝(제외): {window['end'] or '미정'}",
                  "개발 이후 범위: " + ("시각 대조 완료" if window['after_development_checked'] else "미확정"),
                  "final 자료·미사용 원장·개발 성과 승인·같은 설정 Shadow는 별도 확인이 필요합니다.",
                  "실제 계좌 비용·종일 입력 완전성: 미검증"])
    if "cost_reference" in inputs:
        lines.append(f"개발 기간 매도세 참고값 불일치: {inputs['cost_reference']['tax_mismatch_day_count']}일")
    if "gap_provenance" in inputs:
        counts = inputs["gap_provenance"]["counts"]
        lines.append(f"긴 공백 {counts['long_gap_count']}구간 / 과거 감사 일치 {counts['archived_gap_match_count']}구간 / 현재 원응답 대조 {counts['current_raw_input_match_count']}종목·일")
    reason_labels = {"COST_VALIDITY_OUTSIDE_FINAL_WINDOW": "비용 유효기간이 final 범위를 포함하지 않음",
                     "MOCK_SHADOW_FAMILY_NOT_SUPPORTED": "현재 모의 Shadow는 돌파 전략만 지원"}
    lines.extend(reason_labels[reason] for reason in report["reasons"] if reason in reason_labels)
    lines.extend(["", "준비 점검은 기준 동결·final 실행·후보 적격 판정이 아닙니다."])
    return "\n".join(lines)


def _read_object(path: Path, *, maximum_bytes: int) -> dict[str, Any]:
    with Path(path).open("rb") as stream:
        encoded = stream.read(maximum_bytes + 1)
    if len(encoded) > maximum_bytes:
        raise ValueError("입력 파일 크기 상한을 초과했습니다.")
    value = json.loads(encoded)
    if not isinstance(value, dict):
        raise ValueError("JSON 객체가 필요합니다.")
    return value


def _request(path: Path):
    _read_object(path, maximum_bytes=4 * 1024 * 1024)
    return load_research_process_request(path)


def _output_path(output: Path, request) -> Path:
    path = Path(output).resolve()
    if path.suffix.lower() != ".json":
        raise ValueError("출력은 JSON 파일이어야 합니다.")
    if path.is_relative_to(request.dataset.resolve()):
        raise ValueError("동결 연구 입력 안에는 출력할 수 없습니다.")
    return path


def _write_immutable(path: Path, document: dict[str, Any]) -> None:
    encoded = (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != encoded:
            raise ValueError("다른 내용의 동결 파일이 이미 있습니다.")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".candidate-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != encoded:
                raise ValueError("다른 내용의 동결 파일이 이미 있습니다.")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def freeze_policy(
    request_path: Path, output: Path, *, strategy_ref: str,
    minimum_closed_trades: int | None = None,
    minimum_active_days: int | None = None,
    minimum_net_realized_pnl_won: int | None = None,
    maximum_drawdown_ppm: int | None = None,
    now_provider: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> MockAutomationEligibilityPolicy:
    request = _request(request_path)
    output = _output_path(output, request)
    candidate_hash = final_candidate_spec_hash(request, research_implementation_hash(request.session_profile))
    values = {
        "strategy_ref": strategy_ref, "candidate_spec_hash": candidate_hash,
        "minimum_closed_trades": minimum_closed_trades,
        "minimum_active_days": minimum_active_days,
        "minimum_net_realized_pnl_won": minimum_net_realized_pnl_won,
        "maximum_drawdown_ppm": maximum_drawdown_ppm,
    }
    if output.exists():
        policy = eligibility_policy_from_dict(_read_object(output, maximum_bytes=MAX_CANDIDATE_PUBLICATION_BYTES))
        if any(getattr(policy, name) != value for name, value in values.items()):
            raise ValueError("기존 동결 기준이나 후보 설정을 변경할 수 없습니다.")
        return policy
    policy = MockAutomationEligibilityPolicy(frozen_at=now_provider(), **values)
    _write_immutable(output, policy.to_dict())
    return policy


def prepare_publication(
    request_path: Path, policy_path: Path, output: Path, *,
    account_ref: str, final_batch_id: str, final_run_id: str,
) -> dict[str, Any]:
    request = _request(request_path)
    output = _output_path(output, request)
    policy = eligibility_policy_from_dict(_read_object(policy_path, maximum_bytes=MAX_CANDIDATE_PUBLICATION_BYTES))
    implementation_hash = research_implementation_hash(request.session_profile)
    if policy.candidate_spec_hash != final_candidate_spec_hash(request, implementation_hash):
        raise ValueError("동결 기준과 현재 전략·비용·구현 설정이 다릅니다.")
    if not account_ref.strip() or not final_batch_id.strip() or not final_run_id.strip():
        raise ValueError("계좌 식별자와 final batch/run 참조가 필요합니다.")
    repository = ResearchRepository(request.database, read_only=True)
    documents = prepare_mock_automation_candidate_publication(
        repository, request, implementation_hash=implementation_hash,
        strategy_ref=policy.strategy_ref, account_ref=account_ref,
        final_batch_id=final_batch_id, final_run_id=final_run_id,
        eligibility_policy=policy,
    )
    _write_immutable(output, documents)
    return documents


def load_publication(path: Path, *, account_ref: str) -> dict[str, Any]:
    """검토·게시 때 같은 bounded 파일과 재계산한 판정을 확인한다."""
    document = _read_object(path, maximum_bytes=4 * 1024 * 1024)
    if set(document) != {"package", "eligibility_policy", "eligibility_receipt"}:
        raise ValueError("후보 게시 파일의 문서 구성이 올바르지 않습니다.")
    if any(not isinstance(value, dict) for value in document.values()):
        raise ValueError("후보 게시 문서는 JSON 객체여야 합니다.")
    validate_publication_size(document["package"], document["eligibility_policy"], document["eligibility_receipt"])
    package = candidate_package_from_dict(document["package"])
    policy = eligibility_policy_from_dict(document["eligibility_policy"])
    receipt = eligibility_receipt_from_dict(document["eligibility_receipt"])
    if receipt.account_ref != account_ref:
        raise ValueError("선택한 모의계좌와 후보 파일의 계좌가 다릅니다.")
    expected = evaluate_candidate_eligibility(package, policy, account_ref=account_ref)
    if receipt.to_dict() != expected.to_dict():
        raise ValueError("후보 파일의 적격 판정이 재계산 결과와 다릅니다.")
    session = package.candidate_spec["session_profile"]["profile"]
    if package.scientific_implementation_hash != research_implementation_hash(session):
        raise ValueError("후보 파일과 현재 전략 구현이 다릅니다.")
    return document


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
                cost_reference=args.cost_reference, gap_provenance=args.gap_provenance,
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

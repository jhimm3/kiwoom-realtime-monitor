"""NAS D1 원장을 고정 manifest와 JSONL 파일로 내보낸다."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
from kiwoom_monitor.infrastructure.research_data_source import (
    export_daily_dataset, prepare_campaign_nas_input,
    load_frozen_research_bundle, load_frozen_research_export, write_frozen_research_bundle,
)
from kiwoom_monitor.application.market_session_schedule import (
    SUPPORTED_RESEARCH_SESSION_PROFILES,
)


def _aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("시각에는 UTC 오프셋이 필요합니다.")
    return parsed


def export_date_bundle(client, dates, kinds, subject, output, session_profile, *, reuse_days=False):
    """Explicit KST dates; complete matching daily exports are reused without API calls."""
    selected = sorted(set(dates))
    if not selected or len(selected) != len(dates):
        raise ValueError("연구 날짜는 중복 없이 하나 이상 지정해야 합니다.")
    if not session_profile:
        raise ValueError("다기간 묶음에는 session-profile이 필요합니다.")
    if output.exists() and not output.is_dir():
        raise ValueError("연구 묶음 출력은 디렉터리여야 합니다.")
    if output.exists() and not reuse_days:
        raise ValueError("출력 경로가 이미 존재합니다. 재사용하려면 --reuse-days가 필요합니다.")
    # A published index cannot admit another date, even if the directory is reused.
    if (output / "manifest.json").exists():
        existing = load_frozen_research_bundle(output)
        if [entry["selected_date"] for entry in existing.manifest["children"]] != [d.isoformat() for d in selected]:
            raise ValueError("완료된 연구 묶음의 날짜는 변경할 수 없습니다.")
    children = []
    for selected_date in selected:
        start = datetime.combine(selected_date, time.min, tzinfo=timezone(timedelta(hours=9)))
        end = start + timedelta(days=1)
        relative = "days/" + selected_date.isoformat()
        child = output / relative
        if child.is_symlink() or not child.resolve().is_relative_to(output.resolve()):
            raise ValueError("연구 날짜 폴더가 묶음 밖을 가리킵니다.")
        if child.exists():
            dataset = load_frozen_research_export(child)
            captured = dataset.manifest.get("captured_range", {})
            if (datetime.fromisoformat(captured.get("start", "")) != start or
                    datetime.fromisoformat(captured.get("end", "")) != end or
                    sorted(dataset.manifest.get("kinds", [])) != sorted(kinds) or
                    dataset.manifest.get("subject", "") != subject or
                    (dataset.manifest.get("research_session_profile") or {}).get("profile") != session_profile):
                raise ValueError("기존 날짜 자료와 요청 조건이 다릅니다.")
        else:
            export_daily_dataset(client, start, end, kinds, subject, child, session_profile)
        children.append(relative)
    return write_frozen_research_bundle(output, tuple(children))


def main() -> int:
    parser = argparse.ArgumentParser(description="고정된 관측 데이터셋 또는 날짜별 묶음을 내보냅니다.")
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--access-token", default=os.environ.get("MONITOR_SERVER_ACCESS_TOKEN", ""))
    parser.add_argument("--start", type=_aware_datetime)
    parser.add_argument("--end", type=_aware_datetime)
    parser.add_argument("--dates", help="쉼표로 구분한 명시적 KST 날짜: YYYY-MM-DD,...")
    parser.add_argument("--reuse-days", action="store_true")
    parser.add_argument("--kinds", default="ranking,top20_membership")
    parser.add_argument("--subject", default="")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--session-profile", choices=SUPPORTED_RESEARCH_SESSION_PROFILES)
    args = parser.parse_args()
    if not args.access_token:
        parser.error("--access-token 또는 MONITOR_SERVER_ACCESS_TOKEN이 필요합니다.")
    if args.dates:
        if args.start or args.end or not args.session_profile:
            parser.error("--dates는 --start/--end와 함께 쓸 수 없으며 --session-profile이 필요합니다.")
        try:
            dates = [date.fromisoformat(value.strip()) for value in args.dates.split(",")]
        except ValueError:
            parser.error("날짜 형식은 YYYY-MM-DD입니다.")
    elif not args.start or not args.end or args.reuse_days:
        parser.error("하루 추출에는 --start/--end가 필요하고 --reuse-days는 --dates 전용입니다.")
    elif args.output.exists():
        parser.error("출력 경로가 이미 존재합니다. 불변 export는 덮어쓰지 않습니다.")
    kinds = tuple(dict.fromkeys(value.strip() for value in args.kinds.split(",") if value.strip()))
    client = CentralContentClient(args.server_url, args.access_token)
    if args.dates:
        bundle = export_date_bundle(client, dates, kinds, args.subject, args.output,
                                    args.session_profile, reuse_days=args.reuse_days)
        print(json.dumps({"status": "ok", "output": str(args.output),
                          "bundle_id": bundle.manifest["bundle_id"],
                          "child_count": bundle.manifest["child_count"],
                          "unique_revision_count": bundle.manifest["unique_revision_count"]}, ensure_ascii=False))
        return 0
    manifest = export_daily_dataset(client, args.start, args.end, kinds, args.subject,
                                    args.output, args.session_profile)
    print(json.dumps({
        "status": "ok", "output": str(args.output),
        "revision_count": manifest["revision_count"],
        "revision_ids_hash": manifest["revision_ids_hash"],
        "theme_snapshot_count": manifest["theme_snapshot_count"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

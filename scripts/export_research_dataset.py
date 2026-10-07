"""NAS D1 원장을 고정 manifest와 JSONL 파일로 내보낸다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from contextlib import nullcontext
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
from kiwoom_monitor.infrastructure.research_storage import (
    write_research_storage_marker, research_storage_remaining_bytes, research_storage_text_bytes,
    validate_research_storage_root,
)
from kiwoom_monitor.application.research_resources import ResearchResourceBlocked
from kiwoom_monitor.infrastructure.research_data_source import (
    CentralResearchDataSource, load_frozen_research_bundle, load_frozen_research_export,
    write_frozen_research_bundle, load_research_input, campaign_input_scope, campaign_input_fingerprint,
)
from kiwoom_monitor.application.market_session_schedule import (
    SUPPORTED_RESEARCH_SESSION_PROFILES,
)


def _aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("시각에는 UTC 오프셋이 필요합니다.")
    return parsed


def export_daily_dataset(client, start, end, kinds, subject, output, session_profile=None, *, initial_page=None, theme_history=None, checkpoint=lambda: None, max_encoded_bytes=None, reserve_bytes=lambda count: None):
    """Keep the existing single-day file contract for both CLI modes."""
    dataset = CentralResearchDataSource(client).load(
        start, end, kinds, subject=subject, session_profile=session_profile,
        initial_page=initial_page, checkpoint=checkpoint, max_encoded_bytes=max_encoded_bytes,
    )
    checkpoint()
    theme_history = theme_history if theme_history is not None else client.load_theme_history(as_of=end.timestamp(), limit=1000)
    checkpoint()
    raw_theme_snapshots = theme_history.get("snapshots", [])
    if not isinstance(raw_theme_snapshots, list) or not all(isinstance(value, dict) for value in raw_theme_snapshots):
        raise ValueError("테마 이력 응답 형식이 올바르지 않습니다.")
    theme_snapshots = [value for value in raw_theme_snapshots if isinstance(value, dict)]
    output.mkdir(parents=True)
    observations_path = output / "observations.jsonl"
    def write_rows(path, values):
        digest = hashlib.sha256()
        size = 0
        with path.open('wb') as handle:
            for value in values:
                checkpoint()
                encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
                size += len(encoded)
                if max_encoded_bytes is not None and size > max_encoded_bytes:
                    raise ValueError('research_export_encoded_byte_limit_exceeded')
                reserve_bytes(len(encoded))
                handle.write(encoded)
                digest.update(encoded)
        return digest.hexdigest()
    rows_hash = write_rows(observations_path, dataset.observations)
    themes_path = output / "theme_snapshots.jsonl"
    themes_hash = write_rows(themes_path, theme_snapshots)
    manifest = dict(dataset.manifest)
    manifest["observations_file"] = observations_path.name
    manifest["observations_file_hash"] = rows_hash
    manifest["theme_snapshots_file"] = themes_path.name
    manifest["theme_snapshots_file_hash"] = themes_hash
    manifest["theme_snapshot_count"] = len(theme_snapshots)
    manifest["theme_snapshot_limit"] = 1000
    manifest["theme_snapshot_history_may_be_truncated"] = len(theme_snapshots) == 1000
    text = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)
    reserve_bytes(research_storage_text_bytes(text))
    (output / "manifest.json").write_text(text, encoding="utf-8")
    return manifest


def prepare_campaign_nas_input(client, source, template_path, scope, known_fingerprints, *, checkpoint=lambda: None, max_encoded_bytes=32 * 1024 * 1024, begin_preparation=lambda: None, publication_context=nullcontext, record_staging=lambda path: None, rolling_range=None, validate_dataset=None):
    """Probe fixed snapshots, stage verified files, then publish a new directory."""
    checkpoint()
    if (template_path / 'manifest.json').stat().st_size > 1024 * 1024:
        raise ValueError('input_manifest_too_large')
    template_manifest = json.loads((template_path / 'manifest.json').read_text(encoding='utf-8'))
    children = template_manifest.get('children') if template_manifest.get('bundle_version') else None
    if template_manifest.get('bundle_version') and (not isinstance(children, list) or not children):
        raise ValueError('NAS preparation bundle child contract is invalid')
    if rolling_range is not None and children:
        raise ValueError('rolling NAS preparation requires a single daily template')
    target_scope = {**scope, 'captured_range': rolling_range} if rolling_range is not None else scope
    ranges = [entry['captured_range'] for entry in children] if children else [target_scope['captured_range']]
    pages, themes, signatures = [], [], []
    for captured in ranges:
        checkpoint()
        start, end = (datetime.fromisoformat(captured[key]) for key in ('start', 'end'))
        if start.tzinfo is None or end.tzinfo is None or not timedelta(0) < end - start <= timedelta(days=1):
            raise ValueError('NAS preparation requires bounded daily exports or a daily bundle')
        page = client.load_research_observations_page(start, end, tuple(scope['kinds']), subject=scope.get('subject') or '', limit=1)
        checkpoint()
        manifest = page.get('manifest')
        if not isinstance(manifest, dict) or not page.get('watermark') or not isinstance(page.get('observations'), list) or len(str(manifest.get('revision_ids_hash', ''))) != 64:
            raise ValueError('NAS preparation probe contract is invalid')
        count = int(manifest.get('revision_count', -1))
        values = page['observations']
        if count < 0 or len(values) != min(1, count) or page.get('next_cursor') != (1 if count > 1 else None) or (values and (not isinstance(values[0], dict) or values[0].get('ordinal') != 1 or not values[0].get('revision_id'))):
            raise ValueError('NAS preparation probe is incomplete')
        if manifest.get('captured_range') != captured or sorted(manifest.get('kinds', [])) != sorted(scope['kinds']) or (manifest.get('subject') or '') != (scope.get('subject') or ''):
            raise ValueError('NAS preparation probe scope does not match the study')
        if rolling_range is not None and count == 0:
            return {'status': 'empty', 'signature': '', 'path': None}
        history = client.load_theme_history(as_of=end.timestamp(), limit=1000)
        checkpoint()
        snapshots = history.get('snapshots')
        if not isinstance(snapshots, list) or not all(isinstance(row, dict) for row in snapshots):
            raise ValueError('NAS preparation theme history contract is invalid')
        signatures.append({'revision_ids_hash': manifest['revision_ids_hash'], 'themes': hashlib.sha256(json.dumps(snapshots, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()})
        pages.append(page)
        themes.append(history)
    signature_input = {'ranges': ranges, 'signatures': signatures} if rolling_range is not None else signatures
    signature = hashlib.sha256(json.dumps(signature_input, sort_keys=True).encode()).hexdigest()
    if rolling_range is None and signature == source['remote_signature']:
        return {'status': 'unchanged', 'signature': signature, 'path': None}
    operation_id = begin_preparation()
    root = Path(source['root'])
    validate_research_storage_root(root)
    root.mkdir(parents=True, exist_ok=True)
    cap = source.get('storage_cap_bytes', 0)
    capacity_remaining = research_storage_remaining_bytes(root, source['source_id'], cap, checkpoint=checkpoint)
    def reserve_bytes(count):
        nonlocal capacity_remaining
        if capacity_remaining is not None:
            if count > capacity_remaining:
                raise ResearchResourceBlocked('research_storage_capacity_reached')
            capacity_remaining -= count
    with tempfile.TemporaryDirectory(prefix='.nas-preparing-', dir=root) as temporary:
        stage = Path(temporary)
        if stage.resolve().parent != root.resolve():
            raise ValueError('NAS staging directory is outside the source root')
        record_staging(stage)
        write_research_storage_marker(stage, source['source_id'], kind='preparing', reserve_bytes=reserve_bytes, operation_id=operation_id)
        payload = stage / 'payload'
        profile = (scope.get('research_session_profile') or {}).get('profile')
        remaining = max_encoded_bytes
        paths = []
        for index, captured in enumerate(ranges):
            relative = 'days/' + date.fromisoformat(children[index]['selected_date']).isoformat() if children else ''
            output = payload / relative
            start, end = (datetime.fromisoformat(captured[key]) for key in ('start', 'end'))
            export_daily_dataset(client, start, end, tuple(scope['kinds']), scope.get('subject') or '', output, profile, initial_page=pages[index], theme_history=themes[index], checkpoint=checkpoint, max_encoded_bytes=remaining, reserve_bytes=reserve_bytes)
            remaining -= sum((output / name).stat().st_size for name in ('observations.jsonl', 'theme_snapshots.jsonl'))
            if remaining < 0:
                raise ValueError('research_export_encoded_byte_limit_exceeded')
            paths.append(relative)
        if children:
            write_frozen_research_bundle(payload, tuple(paths), reserve_bytes=reserve_bytes)
        checkpoint()
        dataset = load_research_input(payload, session_profile=profile, checkpoint=checkpoint)
        if campaign_input_scope(dataset) != target_scope:
            raise ValueError('prepared NAS dataset scope does not match the study')
        if validate_dataset is not None:
            validate_dataset(dataset, payload)
        fingerprint = campaign_input_fingerprint(dataset, checkpoint=checkpoint)
        if rolling_range is not None:
            fingerprint = hashlib.sha256((json.dumps(rolling_range, sort_keys=True) + fingerprint).encode()).hexdigest()
        del dataset
        target = root / ('nas-' + source['source_id'][:12] + '-' + fingerprint)
        if fingerprint in known_fingerprints:
            return {'status': 'unchanged', 'signature': signature, 'path': None, 'fingerprint': fingerprint}
        checkpoint()
        if target.is_symlink() or target.resolve().parent != root.resolve():
            raise ValueError('published NAS input target is outside the source root')
        if target.exists():
            existing = load_research_input(target, session_profile=profile, checkpoint=checkpoint)
            existing_fingerprint = campaign_input_fingerprint(existing, checkpoint=checkpoint)
            if rolling_range is not None:
                existing_fingerprint = hashlib.sha256((json.dumps(rolling_range, sort_keys=True) + existing_fingerprint).encode()).hexdigest()
            if campaign_input_scope(existing) != target_scope or existing_fingerprint != fingerprint:
                raise ValueError('published NAS input conflicts with verified snapshot')
        else:
            write_research_storage_marker(payload, source['source_id'], kind='published', fingerprint=fingerprint, reserve_bytes=reserve_bytes, operation_id=operation_id)
            # Count again to catch other files written during preparation. This is
            # an application budget, not a filesystem quota against outside writers.
            research_storage_remaining_bytes(root, source['source_id'], cap, checkpoint=checkpoint, allow_full=True)
            checkpoint()
            with publication_context():
                validate_research_storage_root(root)
                if target.is_symlink() or target.resolve().parent != root.resolve() or stage.resolve().parent != root.resolve() or payload.is_symlink() or payload.resolve().parent != stage.resolve():
                    raise ValueError('NAS publication path escaped the source root')
                payload.rename(target)
        return {'status': 'prepared', 'signature': signature, 'path': str(target), 'fingerprint': fingerprint}


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

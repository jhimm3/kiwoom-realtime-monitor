"""이전 배포 폴더와 새 배포 폴더를 비교해 부분 업데이트 ZIP을 만든다.

예:
  python scripts/create_update_package.py --previous release/1.1.1 \
      --current dist/KiwoomMonitor --version 1.1.2 --output release
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def files_under(root: Path) -> dict[Path, Path]:
    return {path.relative_to(root): path for path in root.rglob("*") if path.is_file()}


def main() -> None:
    parser = argparse.ArgumentParser(description="KiwoomMonitor 부분 업데이트 패키지 생성")
    parser.add_argument("--previous", type=Path, required=True, help="이전 버전 배포 폴더")
    parser.add_argument("--current", type=Path, required=True, help="새 버전 배포 폴더")
    parser.add_argument("--version", required=True, help="새 버전 (예: 1.1.2)")
    parser.add_argument("--output", type=Path, required=True, help="ZIP 출력 폴더")
    args = parser.parse_args()
    if not args.previous.is_dir() or not args.current.is_dir():
        raise SystemExit("이전·새 배포 폴더를 모두 지정하세요.")

    previous, current = files_under(args.previous), files_under(args.current)
    changed = sorted(relative for relative, source in current.items() if relative not in previous or sha256(source) != sha256(previous[relative]))
    deleted = sorted(relative.as_posix() for relative in previous if relative not in current)
    manifest = {"version": args.version, "changed": [path.as_posix() for path in changed], "deleted": deleted}

    args.output.mkdir(parents=True, exist_ok=True)
    output = args.output / f"KiwoomMonitor-Update-{args.version}.zip"
    with tempfile.TemporaryDirectory() as temporary:
        staging = Path(temporary)
        (staging / "update_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for relative in changed:
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(current[relative], target)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for file in staging.rglob("*"):
                if file.is_file():
                    archive.write(file, file.relative_to(staging).as_posix())
    checksum_path = output.with_suffix(".zip.sha256")
    checksum_path.write_text(f"{sha256(output)}  {output.name}\n", encoding="ascii")
    print(output)
    print(checksum_path)
    print(f"변경 {len(changed)}개, 삭제 {len(deleted)}개")


if __name__ == "__main__":
    main()

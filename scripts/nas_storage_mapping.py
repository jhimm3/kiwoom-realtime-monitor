"""Read-only NAS host mapping from PostgreSQL bind path to block-device slaves.

Run on the NAS host, passing the verified Docker bind source for PGDATA.
The report carries the mountinfo/sysfs evidence; device names are never
silently equated with a particular physical disk or cache policy.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path


def mount_for(path: Path, mountinfo: str) -> dict[str, str] | None:
    target = path.resolve().as_posix()
    matches = []
    for line in mountinfo.splitlines():
        left, separator, right = line.partition(" - ")
        if not separator:
            continue
        fields, filesystem = left.split(), right.split()
        if len(fields) < 5 or len(filesystem) < 2:
            continue
        mountpoint = fields[4].replace("\\040", " ")
        if target == mountpoint or target.startswith(mountpoint.rstrip("/") + "/"):
            matches.append({"major_minor": fields[2], "mountpoint": mountpoint,
                            "filesystem": filesystem[0], "source": filesystem[1]})
    if not matches:
        return None
    selected = max(matches, key=lambda entry: len(entry["mountpoint"]))
    try:
        source_stat = os.stat(selected["source"])
        if stat.S_ISBLK(source_stat.st_mode):
            selected["source_major_minor"] = f"{os.major(source_stat.st_rdev)}:{os.minor(source_stat.st_rdev)}"
    except OSError:
        pass
    return selected


def block_graph(major_minor: str, *, sys_dev_block: Path = Path("/sys/dev/block")) -> dict[str, object]:
    seen: set[str] = set()

    def descend(device: Path) -> dict[str, object]:
        resolved = device.resolve()
        name = resolved.name
        if name in seen:
            return {"name": name, "cycle": True}
        seen.add(name)
        slaves = resolved / "slaves"
        children = [descend(child) for child in sorted(slaves.iterdir())] if slaves.is_dir() else []
        return {"name": name, "slaves": children,
                "sysfs": str(resolved)}

    device = sys_dev_block / major_minor
    return descend(device) if device.exists() else {"major_minor": major_minor, "unavailable": True}


def map_storage(pgdata_host: Path, *, mountinfo_path: Path = Path("/proc/self/mountinfo"),
                sys_dev_block: Path = Path("/sys/dev/block")) -> dict[str, object]:
    if not pgdata_host.is_dir():
        raise ValueError("verified PostgreSQL bind source directory is required")
    mountinfo = mountinfo_path.read_text(encoding="utf-8")
    paths = {"PGDATA": pgdata_host.resolve(), "pg_wal": (pgdata_host / "pg_wal").resolve()}
    result: dict[str, object] = {"captured_at_utc": datetime.now(timezone.utc).isoformat(),
                                 "pgdata_host": str(paths["PGDATA"]), "paths": {}}
    for label, path in paths.items():
        mount = mount_for(path, mountinfo)
        result["paths"][label] = {
            "resolved_path": str(path), "mount": mount,
            "block_graph": block_graph(mount.get("source_major_minor", mount["major_minor"]),
                                       sys_dev_block=sys_dev_block)
            if mount else None,
        }
    result["limits"] = ["Docker bind source must be independently verified with docker inspect",
                        "device graph shows block slaves, not Synology cache policy or observed latency"]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pgdata-host", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(map_storage(args.pgdata_host), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

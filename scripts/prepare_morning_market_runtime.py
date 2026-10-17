"""Prepare a pinned administrator installation bundle; never execute it."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "2026.10.14-recorder-shared-10m-v1-af0f8072f960415b"
NAME = "news-chatgpt-plan-ed9d-runtime-v3"
REMOTE = "/volume1/docker/kiwoom-monitor/artifacts/" + NAME


def prepare(candidate: Path):
    candidate = candidate.resolve()
    candidate.relative_to((ROOT / "data/diagnostics/news-chatgpt-plan").resolve())
    manifest = json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))
    if not manifest["release_id"].startswith("2026.10.15-morning-market-brief-v1-"):
        raise ValueError("unexpected candidate")
    share = Path("X:/kiwoom-monitor")
    if not share.is_dir(): raise RuntimeError("mapped share unavailable in this context")
    runtime_raw = (share / "source-runtime/runtime.json").read_bytes()
    installer = (ROOT / "scripts/install_nas_chatgpt_runtime.py").read_bytes()
    session_operator = (ROOT / "scripts/nas_chatgpt_session_operator.py").read_bytes()
    hash_ = lambda data: hashlib.sha256(data).hexdigest()
    descriptor = {"format": 1, "base_release": BASE, "base_runtime_sha256": hash_(runtime_raw),
        "candidate_release": manifest["release_id"],
        "candidate_manifest_sha256": hash_(json.dumps(manifest, sort_keys=True).encode()),
        "google_auth": "2.41.1", "installer_sha256": hash_(installer),
        "session_operator_sha256": hash_(session_operator)}
    descriptor_raw = (json.dumps(descriptor, indent=2, sort_keys=True) + "\n").encode()
    descriptor_hash = hash_(descriptor_raw)
    bootstrap = ("#!/bin/sh\nset -eu\numask 077\nPATH=/usr/local/bin:/usr/bin:/bin; export PATH\n"
        + "DIR=" + REMOTE + "\n[ \"$(id -u)\" = 0 ] || exit 1\ncd \"$DIR\"\n"
        + "printf '%s  %s\\n' '" + hash_(installer) + "' install_nas_chatgpt_runtime.py | sha256sum -c -\n"
        + "printf '%s  %s\\n' '" + descriptor_hash + "' runtime-extension.json | sha256sum -c -\n"
        + "exec python3 -I -S \"$DIR/install_nas_chatgpt_runtime.py\" " + descriptor_hash + "\n").encode()
    files = {"install.sh": bootstrap, "install_nas_chatgpt_runtime.py": installer,
             "nas_chatgpt_session_operator.py": session_operator, "runtime-extension.json": descriptor_raw}
    local = ROOT / "data/diagnostics/morning-market-brief/admin-bundle-v3"
    local.mkdir(parents=True, exist_ok=True)
    target = share / "artifacts" / NAME
    target.resolve().relative_to((share / "artifacts").resolve())
    if target.exists():
        for name,data in files.items():
            if (target / name).read_bytes() != data:
                raise RuntimeError("existing installation bundle differs; refusing overwrite")
    else:
        target.mkdir()
        for name,data in files.items(): (target / name).write_bytes(data)
    for name,data in files.items():
        (local / name).write_bytes(data)
        if hash_((target / name).read_bytes()) != hash_(data):
            raise RuntimeError("installation bundle readback mismatch")
    command = 'ssh -t k379@192.168.0.5 "sudo sh ' + REMOTE + '/install.sh"'
    (local / "admin-install-command.txt").write_text(command + "\n", encoding="utf-8")
    report = {"candidate_release": manifest["release_id"], "descriptor_sha256": descriptor_hash,
        "bundle_path": REMOTE, "files": {name:hash_(data) for name,data in files.items()},
        "admin_command": command, "installed": False, "oauth_transferred": False,
        "operationally_deployed": False}
    (local / "preparation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate",type=Path)
    print(json.dumps(prepare(parser.parse_args().candidate)))

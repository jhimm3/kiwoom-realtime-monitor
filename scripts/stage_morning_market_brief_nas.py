"""Stage verified code in a new immutable NAS release; never select active source."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def stage(candidate: Path):
    candidate = candidate.resolve()
    candidate.relative_to((ROOT / "data/diagnostics/news-chatgpt-plan").resolve())
    manifest = json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))
    release = manifest["release_id"]
    if not re.fullmatch(r"2026\.10\.15-morning-market-brief-v1-[a-f0-9]{16}", release):
        raise ValueError("unexpected release")
    archive = candidate.parent / (release + ".zip")
    contents = {}
    for name in ["manifest.json", *sorted(manifest["files"])]:
        path = (candidate / name).resolve()
        path.relative_to(candidate)
        data = path.read_bytes()
        if name != "manifest.json" and hashlib.sha256(data).hexdigest() != manifest["files"][name]:
            raise ValueError("candidate hash mismatch")
        contents[name] = data
    if archive.exists():
        with zipfile.ZipFile(archive) as previous:
            if set(previous.namelist()) != set(contents) or any(previous.read(n) != d for n,d in contents.items()):
                raise RuntimeError("existing local archive differs")
    else:
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for name,data in contents.items():
                info=zipfile.ZipInfo(name,(2026,10,15,0,0,0))
                info.compress_type=zipfile.ZIP_DEFLATED
                output.writestr(info,data)
    data = archive.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    share = Path("X:/kiwoom-monitor")
    if not share.is_dir(): raise RuntimeError("mapped share missing in this execution context")
    target = share / "artifacts" / archive.name
    target.resolve().relative_to((share / "artifacts").resolve())
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise RuntimeError("existing archive differs; refusing overwrite")
    else:
        target.write_bytes(data)
    # The remote script has only fixed verified code paths; it accepts no
    # environment, database, active pointer or arbitrary filesystem operation.
    remote = """from pathlib import Path
import hashlib,json,zipfile,os
release=RELEASE
expected=SHA
base=Path('/volume1/docker/kiwoom-monitor/source-runtime/releases')
archive=Path('/volume1/docker/kiwoom-monitor/artifacts')/(release+'.zip')
assert base.resolve()==base and not base.is_symlink()
assert archive.resolve().parent==Path('/volume1/docker/kiwoom-monitor/artifacts')
assert hashlib.sha256(archive.read_bytes()).hexdigest()==expected
with zipfile.ZipFile(archive) as z:
 manifest=json.loads(z.read('manifest.json'))
 assert manifest['release_id']==release
 assert set(z.namelist())==set(manifest['files'])|{'manifest.json'}
 for name,checksum in manifest['files'].items():
  assert name=='pyproject.toml' or name.startswith(('src/','tests/','scripts/'))
  assert not name.startswith('/') and '..' not in Path(name).parts
  assert hashlib.sha256(z.read(name)).hexdigest()==checksum
 destination=base/release
 assert destination.resolve().parent==base
 if destination.exists():
  assert not destination.is_symlink()
  assert json.loads((destination/'manifest.json').read_text())==manifest
  for name,checksum in manifest['files'].items():
   file=destination/name
   file.resolve().relative_to(destination)
   assert not file.is_symlink()
   assert hashlib.sha256(file.read_bytes()).hexdigest()==checksum
 else:
  temporary=base/('staging-'+release)
  assert temporary.resolve().parent==base and not temporary.is_symlink()
  if temporary.exists():
   assert temporary.stat().st_uid==os.geteuid()
  else: temporary.mkdir(mode=0o750)
  for name in z.namelist():
   file=temporary/name
   file.resolve().relative_to(temporary)
   file.parent.mkdir(parents=True,exist_ok=True,mode=0o750)
   if file.exists():
    assert not file.is_symlink() and file.read_bytes()==z.read(name)
   else:
    with file.open('xb') as out: out.write(z.read(name))
   file.chmod(0o640)
  temporary.rename(destination)
 print(json.dumps({'release':release,'files':len(manifest['files']),'staged':True,'active_source_changed':False}))
""".replace("RELEASE", repr(release)).replace("SHA", repr(digest))
    reply = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        "k379@192.168.0.5", "python3 -"], input=remote, text=True, capture_output=True, timeout=180)
    if reply.returncode:
        (candidate.parent / "stage-error.txt").write_text(reply.stderr[-4000:], encoding="utf-8")
        raise RuntimeError("NAS code stage verification failed; active source was not selected")
    result = json.loads(reply.stdout)
    result.update(archive_sha256=digest, archive_bytes=len(data))
    (candidate.parent / "staging.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    print(json.dumps(stage(parser.parse_args().candidate)))

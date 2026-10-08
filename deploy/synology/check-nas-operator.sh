#!/bin/sh
# Prepared, optional offline acceptance; does not install or touch live services.
set -eu
umask 077
PATH=/usr/local/bin:/usr/bin:/bin
export PATH
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT=$(CDPATH= cd -- "$HERE/../.." && pwd)
DOCKER=$(command -v docker)
if [ "$#" -ne 1 ]; then
  echo 'Usage: check-nas-operator.sh EXISTING_RUNTIME_IMAGE_ID' >&2
  exit 2
fi
case "$1" in
  sha256:????????????????????????????????????????????????????????????????) ;;
  *) echo 'A pinned local runtime image ID is required.' >&2; exit 2 ;;
esac
case "${1#sha256:}" in
  *[!0-9a-f]*) echo 'Invalid runtime image digest.' >&2; exit 2 ;;
esac
PINNED=$("$DOCKER" --host unix:///var/run/docker.sock image inspect --format '{{.Id}}' "$1")
[ "$PINNED" = "$1" ] || { echo 'Local image identity mismatch.' >&2; exit 2; }
# Root here belongs only to this disposable container, with no Docker socket,
# credentials, source-runtime or operational data mount. The gate has no DB IO.
exec "$DOCKER" --host unix:///var/run/docker.sock run --rm --network none --user 0:0 \
  --read-only --memory 256m --memory-swap 256m --cpus 1 --pids-limit 64 \
  --security-opt no-new-privileges --cap-drop ALL \
  --tmpfs /op-fixture:rw,nosuid,nodev,size=32m,mode=0755 \
  --env PYTHONDONTWRITEBYTECODE=1 --env KIWOOM_OPERATOR_FS_GATE_ROOT=/op-fixture \
  --mount "type=bind,src=$PROJECT/scripts,dst=/app/candidate/scripts,readonly" \
  --mount "type=bind,src=$PROJECT/tests,dst=/app/candidate/tests,readonly" \
  --entrypoint python "$1" -I -c \
  'import sys,unittest; sys.path.insert(0,"/app/candidate"); s=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(n) for n in ("tests.unit.test_nas_operator","tests.integration.test_nas_operator_linux")); r=unittest.TextTestRunner(verbosity=2).run(s); sys.exit(0 if r.wasSuccessful() and r.testsRun>0 and not r.skipped else 1)'

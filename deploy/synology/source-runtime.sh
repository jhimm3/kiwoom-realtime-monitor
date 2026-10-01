#!/bin/sh
# Run with sudo. Prepare/test before restart; keep source releases for rollback.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
ROOT=$(CDPATH= cd -- "$HERE/../.." && pwd -P)
STORE="$ROOT/source-runtime"
DOCKER=/usr/local/bin/docker
IMAGE=${KIWOOM_SOURCE_RUNTIME_IMAGE:-kiwoom-monitor-server:2026.10.01-db-writer-candidate-fixes-v1}
SERVER=kiwoom-monitor-server-1
ACTION=${1:-status}
shift || true

[ -f "$STORE/runner.py" ] && [ -f "$STORE/runtime.json" ] || {
    echo "Prepare the source-runtime store from the PC first." >&2; exit 1;
}
cd "$HERE"

tool() {
    "$DOCKER" run --rm --network none --entrypoint python \
        --mount "type=bind,src=$STORE,dst=/app/source-runtime,readonly" \
        "$IMAGE" /app/source-runtime/runner.py "$@"
}
select_release() {
    "$DOCKER" run --rm --network none --entrypoint python \
        --mount "type=bind,src=$STORE,dst=/app/source-runtime" \
        "$IMAGE" /app/source-runtime/runner.py select --release "$1"
}
compose_image() {
    "$DOCKER" compose -p kiwoom-monitor -f docker-compose.yml "$@"
}
compose_source() {
    "$DOCKER" compose -p kiwoom-monitor -f docker-compose.yml -f docker-compose.source.yml "$@"
}
wait_ready() {
    deadline=$(($(date +%s) + 90))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if "$DOCKER" exec "$SERVER" python -c '
import json,os,sys,urllib.request
p=os.environ.get("KIWOOM_SERVER_PORT","8787")
d=json.load(urllib.request.urlopen("http://127.0.0.1:"+p+"/health",timeout=3))
assert d.get("status")=="ok" and d.get("server_build")==sys.argv[1]
if sys.argv[2]:
    from pathlib import Path
    entries=Path("/proc/1/environ").read_bytes().split(b"\0")
    active=dict(entry.split(b"=",1) for entry in entries if b"=" in entry)
    assert active.get(b"PYTHONPATH")==("/app/source-runtime/releases/"+sys.argv[2]+"/src").encode()
print(json.dumps({"status":d["status"],"server_build":d["server_build"],
 "source_release":sys.argv[2] or "image",
 "realtime_phase":(d.get("realtime_connection") or {}).get("phase")}))
' "$1" "${2:-}" 2>/dev/null; then return 0; fi
        sleep 1
    done
    return 1
}
marker() {
    tool info --release "$1" | sed -n 's/.*"server_build": "\([^"]*\)".*/\1/p'
}
database_guard() {
    target="/app/source-runtime/releases/$1"
    [ "$1" != image ] || target=/app
    [ -n "$GUARD_RELEASE" ] || return 0
    if [ "${2:-}" = offline ]; then
        [ "$("$DOCKER" inspect -f '{{.State.Running}}' "$SERVER")" = false ] || {
            echo "Database conversion requires the server to be stopped." >&2; return 1;
        }
        compose_source run --rm --no-deps --entrypoint python server \
            "/app/source-runtime/releases/$GUARD_RELEASE/scripts/check_source_database.py" \
            --target-source "$target" --offline
    else
        compose_source run --rm --no-deps --entrypoint python server \
            "/app/source-runtime/releases/$GUARD_RELEASE/scripts/check_source_database.py" \
            --target-source "$target"
    fi
}

case "$ACTION" in
    status) tool info; exit 0 ;;
    test)
        candidate=${1:?release ID required}; shift
        [ "$("$DOCKER" inspect -f '{{.Image}}' "$SERVER")" = "$("$DOCKER" image inspect -f '{{.Id}}' "$IMAGE")" ] || {
            echo "Running server and selected dependency image differ." >&2; exit 1;
        }
        tool check --release "$candidate"
        "$DOCKER" exec "$SERVER" python /app/source-runtime/runner.py test --release "$candidate" "$@"
        exit $? ;;
    install|deploy|rollback) ;;
    *) echo "Usage: $0 install|deploy RELEASE_ID | rollback | status | test RELEASE_ID [--test NAME ...]" >&2; exit 2 ;;
esac

# A second deployment must not change the pointer while the first restarts.
mkdir "$STORE/deploy.lock" || { echo "Another deployment owns deploy.lock." >&2; exit 1; }
trap 'rmdir "$STORE/deploy.lock"' EXIT
previous=""
if [ -f "$STORE/active.json" ]; then
    previous=$(tool info | sed -n 's/.*"release_id": "\([^"]*\)".*/\1/p')
fi
if [ "$ACTION" = rollback ]; then
    [ -f "$STORE/previous-release" ] || { echo "No previous source release." >&2; exit 1; }
    candidate=$(cat "$STORE/previous-release")
else
    candidate=${1:?release ID required}
fi
tool check --release "$candidate"
configured_image=$(tool info --release "$candidate" | sed -n 's/.*"runtime_image": "\([^"]*\)".*/\1/p')
[ "$configured_image" = "$IMAGE" ] || { echo "Source store and selected dependency image differ." >&2; exit 1; }
expected=$(marker "$candidate")
[ -n "$expected" ] || { echo "Release build marker missing." >&2; exit 1; }
DB_BEFORE=$("$DOCKER" inspect -f '{{.Id}}' kiwoom-monitor-database-1)
IMAGE_ID=$("$DOCKER" image inspect -f '{{.Id}}' "$IMAGE")
SERVER_IMAGE=$("$DOCKER" inspect -f '{{.Image}}' "$SERVER")
[ "$IMAGE_ID" = "$SERVER_IMAGE" ] || { echo "Running server and selected dependency image differ." >&2; exit 1; }
old_build=$("$DOCKER" exec "$SERVER" python -c 'import json,os,urllib.request; p=os.environ.get("KIWOOM_SERVER_PORT","8787"); d=json.load(urllib.request.urlopen("http://127.0.0.1:"+p+"/health",timeout=3)); assert d.get("status")=="ok"; print(d["server_build"])')
if [ "$ACTION" != install ]; then
    mounted=$("$DOCKER" inspect -f '{{range .Mounts}}{{if eq .Destination "/app/source-runtime"}}{{.Source}}{{end}}{{end}}' "$SERVER")
    [ "$mounted" = "$STORE" ] || { echo "Install source mode once before deploying code." >&2; exit 1; }
fi

GUARD_RELEASE=""
for guard_candidate in "$candidate" "$previous"; do
    if [ -n "$guard_candidate" ] && [ -f "$STORE/releases/$guard_candidate/scripts/check_source_database.py" ]; then
        GUARD_RELEASE="$guard_candidate"; break
    fi
done
if database_guard "$candidate"; then :; else
    guard_result=$?
    [ "$guard_result" = 3 ] || exit "$guard_result"
    echo "Target requires offline checkpoint/schema conversion before restart."
fi

# Stop the concrete old process before any downgrade or source selection.
# The DB container remains running; conversion failure leaves its schema intact.
started=$(date +%s)
"$DOCKER" stop --time 60 "$SERVER"
if ! database_guard "$candidate" offline; then
    "$DOCKER" start "$SERVER"
    echo "Database compatibility step failed; the execution source was not changed." >&2
    exit 1
fi

# The old process has stopped; select a verified release before starting again.
if ! select_release "$candidate"; then
    # Selection may have succeeded before its acknowledgement was lost. Verify
    # and select the previous source explicitly before starting any process.
    if [ -n "$previous" ]; then
        database_guard "$previous" offline && select_release "$previous" && "$DOCKER" start "$SERVER" || {
            echo "Source selection recovery failed; server stays stopped." >&2; exit 1;
        }
    else
        database_guard image offline && compose_image up -d --no-deps --no-build --force-recreate --timeout 60 server || {
            echo "Image selection recovery failed; server stays stopped." >&2; exit 1;
        }
    fi
    echo "Source selection failed; previous execution source restored." >&2
    exit 1
fi
failed=0
if [ "$ACTION" = install ]; then
    compose_source up -d --no-deps --no-build --force-recreate --timeout 60 server || failed=1
else
    "$DOCKER" start "$SERVER" || failed=1
fi
if [ "$failed" = 0 ] && wait_ready "$expected" "$candidate"; then
    DB_AFTER=$("$DOCKER" inspect -f '{{.Id}}' kiwoom-monitor-database-1)
    [ "$DB_BEFORE" = "$DB_AFTER" ] || { echo "Database container changed unexpectedly." >&2; exit 1; }
    if [ -n "$previous" ] && [ "$previous" != "$candidate" ]; then
        printf '%s\n' "$previous" > "$STORE/previous-release.tmp"
        mv "$STORE/previous-release.tmp" "$STORE/previous-release"
    fi
    echo "source_release=$candidate http_ready_seconds=$(($(date +%s) - started)) database_container_unchanged=true"
    echo "HTTP readiness is not proof of zero lost realtime events. Check subscriptions/observed timestamps."
    exit 0
fi

echo "Candidate failed readiness; restoring previous execution source." >&2
"$DOCKER" stop --time 60 "$SERVER"
if [ "$ACTION" = install ]; then
    database_guard image offline || { echo "Image fallback conversion failed; server stays stopped." >&2; exit 1; }
    compose_image up -d --no-deps --no-build --force-recreate --timeout 60 server
    wait_ready "$old_build" || echo "Previous image did not become ready; inspect server logs." >&2
elif [ -n "$previous" ]; then
    database_guard "$previous" offline || { echo "Previous-source conversion failed; server stays stopped." >&2; exit 1; }
    select_release "$previous"
    "$DOCKER" start "$SERVER"
    wait_ready "$(marker "$previous")" "$previous" || echo "Previous source did not become ready; inspect server logs." >&2
fi
exit 1

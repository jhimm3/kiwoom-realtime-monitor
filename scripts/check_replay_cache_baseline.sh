#!/bin/sh
# Offline correctness gates; never selects a release or mounts operational data.
set -eu
case "$#:$*" in
    '0:'|'1:--top20-lifecycle') ;;
    *) echo 'Only --top20-lifecycle is accepted.' >&2; exit 1 ;;
esac
ROOT=/volume1/docker/kiwoom-monitor
DOCKER=/usr/local/bin/docker
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
CANDIDATE=$(CDPATH= cd -- "$HERE/.." && pwd -P)
RELEASE=$(basename "$CANDIDATE")
[ "$CANDIDATE" = "$ROOT/source-runtime/releases/$RELEASE" ] || {
    echo 'Run only the reviewed source-runtime candidate.' >&2; exit 1;
}
SERVER=kiwoom-monitor-server-1
DATABASE=kiwoom-monitor-database-1
ACTIVE_BEFORE=$(sha256sum "$ROOT/source-runtime/active.json")
SERVER_BEFORE=$("$DOCKER" inspect -f '{{.Id}}' "$SERVER")
DATABASE_BEFORE=$("$DOCKER" inspect -f '{{.Id}}' "$DATABASE")
IMAGE=$("$DOCKER" inspect -f '{{.Image}}' "$SERVER")
PG_IMAGE=$("$DOCKER" inspect -f '{{.Image}}' "$DATABASE")
[ "$("$DOCKER" inspect -f '{{.State.Running}}' "$SERVER")" = true ] || exit 1
[ "$("$DOCKER" inspect -f '{{.State.Running}}' "$DATABASE")" = true ] || exit 1

# Verify all candidate files against their immutable manifest, offline.
"$DOCKER" run --rm --network none --entrypoint python \
    --mount "type=bind,src=$ROOT/source-runtime,dst=/app/source-runtime,readonly" \
    "$IMAGE" /app/source-runtime/runner.py info --release "$RELEASE"

TEMP_DIR=''
FIXTURE_ID=''
PG_NAME=''
PY_NAME=''
cleanup() {
    cleanup_failed=0
    for container in "$PY_NAME" "$PG_NAME"; do
        if [ -n "$container" ] && "$DOCKER" inspect "$container" >/dev/null 2>&1; then
            if [ -n "$FIXTURE_ID" ] &&
               [ "$("$DOCKER" inspect -f '{{index .Config.Labels "com.kiwoom.replay-fixture"}}' "$container")" = "$FIXTURE_ID" ]; then
                "$DOCKER" rm -f "$container" >/dev/null 2>&1 || cleanup_failed=1
            else
                echo 'Temporary container identity mismatch; cleanup refused.' >&2
                cleanup_failed=1
            fi
        fi
    done
    case "$TEMP_DIR" in
        "$ROOT"/artifacts/replay-cache-acceptance.*)
            rm -f "$TEMP_DIR/fixture.json" "$TEMP_DIR/postgres-password" || cleanup_failed=1
            rmdir "$TEMP_DIR" 2>/dev/null || cleanup_failed=1 ;;
    esac
    return "$cleanup_failed"
}
trap 'exit_code=$?; trap - EXIT; if ! cleanup; then exit_code=1; fi; exit "$exit_code"' EXIT
trap 'exit 130' HUP INT TERM
umask 077
TEMP_DIR=$(mktemp -d "$ROOT/artifacts/replay-cache-acceptance.XXXXXX")
chmod 700 "$TEMP_DIR"
FIXTURE_ID=$("$DOCKER" run --rm --network none --entrypoint python \
    --mount "type=bind,src=$CANDIDATE,dst=/app/candidate,readonly" \
    --mount "type=bind,src=$TEMP_DIR,dst=/run/replay-fixture" \
    -e PYTHONDONTWRITEBYTECODE=1 "$IMAGE" \
    /app/candidate/scripts/check_replay_cache_baseline.py --make-secrets /run/replay-fixture)
case "$FIXTURE_ID" in ''|*[!a-f0-9]*) echo 'Invalid fixture identity.' >&2; exit 1 ;; esac
[ "${#FIXTURE_ID}" -eq 32 ] || exit 1
PG_NAME="kiwoom-replay-cache-$FIXTURE_ID"
PY_NAME="kiwoom-replay-cache-python-$FIXTURE_ID"
REPORT="$ROOT/artifacts/replay-cache-v2-acceptance-$FIXTURE_ID.log"

echo 'Starting temporary PostgreSQL in RAM, with no external network...'
"$DOCKER" run --detach --rm --name "$PG_NAME" --network none \
    --label "com.kiwoom.replay-fixture=$FIXTURE_ID" \
    --memory 768m --pids-limit 128 --tmpfs /var/lib/postgresql/data:rw,nosuid,size=512m \
    --mount "type=bind,src=$TEMP_DIR,dst=/run/replay-fixture,readonly" \
    -e PGDATA=/var/lib/postgresql/data \
    -e POSTGRES_USER=kiwoom_replay_fixture_admin -e POSTGRES_DB=postgres \
    -e POSTGRES_PASSWORD_FILE=/run/replay-fixture/postgres-password \
    "$PG_IMAGE" postgres -c shared_buffers=16MB -c max_connections=20 -c work_mem=2MB \
    -c "kiwoom.replay_fixture_id=$FIXTURE_ID" >/dev/null
[ "$("$DOCKER" inspect -f '{{.HostConfig.NetworkMode}}' "$PG_NAME")" = none ] || exit 1
ready=false
for attempt in $(seq 1 60); do
    if "$DOCKER" exec "$PG_NAME" pg_isready -U kiwoom_replay_fixture_admin -d postgres >/dev/null 2>&1; then
        ready=true; break
    fi
    sleep 1
done
[ "$ready" = true ] || { echo 'Temporary PostgreSQL did not become ready.' >&2; exit 1; }

echo 'Running v1 preservation and v2 restore, native execution and cleanup gates...'
set +e
"$DOCKER" run --rm --name "$PY_NAME" --network "container:$PG_NAME" \
    --label "com.kiwoom.replay-fixture=$FIXTURE_ID" --memory 512m --pids-limit 128 \
    --entrypoint python \
    --mount "type=bind,src=$CANDIDATE,dst=/app/candidate,readonly" \
    --mount "type=bind,src=$TEMP_DIR,dst=/run/replay-fixture,readonly" \
    -e PYTHONDONTWRITEBYTECODE=1 "$IMAGE" \
    /app/candidate/scripts/check_replay_cache_baseline.py \
    --secrets-file /run/replay-fixture/fixture.json "$@" >"$REPORT" 2>&1
result=$?
set -e
cat "$REPORT"
cleanup
TEMP_DIR=''
[ "$(sha256sum "$ROOT/source-runtime/active.json")" = "$ACTIVE_BEFORE" ] || {
    echo 'Active pointer changed during acceptance.' >&2; exit 1;
}
[ "$("$DOCKER" inspect -f '{{.Id}}' "$SERVER")" = "$SERVER_BEFORE" ] || exit 1
[ "$("$DOCKER" inspect -f '{{.Id}}' "$DATABASE")" = "$DATABASE_BEFORE" ] || exit 1
echo "report=$REPORT active_release_and_both_containers_unchanged=true temporary_cluster_removed=true"
exit "$result"

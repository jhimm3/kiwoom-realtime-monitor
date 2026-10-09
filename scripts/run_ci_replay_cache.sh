#!/bin/sh
# Hosted, disposable replay-cache gate. No production network, credentials or mounts.
set -eu
umask 077
[ "$#" -eq 2 ] || { echo 'Usage: run_ci_replay_cache.sh IMAGE_ID POSTGRES_IMAGE_ID' >&2; exit 2; }
IMAGE=$1
PG_IMAGE=$2
case "$IMAGE:$PG_IMAGE" in
    sha256:????????????????????????????????????????????????????????????????:sha256:????????????????????????????????????????????????????????????????) ;;
    *) echo 'Two pinned local image IDs are required.' >&2; exit 2 ;;
esac
[ "$(docker image inspect -f '{{.Id}}' "$IMAGE")" = "$IMAGE" ] || exit 2
[ "$(docker image inspect -f '{{.Id}}' "$PG_IMAGE")" = "$PG_IMAGE" ] || exit 2
ROOT=$(pwd -P)
mkdir -p "$ROOT/tmp/regression/replay-ci"
TEMP_DIR=''
FIXTURE_ID=''
PG_NAME=''
PY_NAME=''
cleanup() {
    failed=0
    for container in "$PY_NAME" "$PG_NAME"; do
        [ -n "$container" ] || continue
        if docker inspect "$container" >/dev/null 2>&1; then
            label=$(docker inspect -f '{{index .Config.Labels "com.kiwoom.replay-fixture"}}' "$container")
            if [ "$label" = "$FIXTURE_ID" ]; then
                docker rm -f "$container" >/dev/null 2>&1 || failed=1
            else
                echo 'Temporary container identity mismatch; cleanup refused.' >&2
                failed=1
            fi
        fi
    done
    case "$TEMP_DIR" in
        "$ROOT"/tmp/regression/replay-ci/secret.*)
            rm -f "$TEMP_DIR/fixture.json" "$TEMP_DIR/postgres-password" || failed=1
            rmdir "$TEMP_DIR" 2>/dev/null || failed=1 ;;
        '') ;;
        *) echo 'Temporary secret path identity mismatch.' >&2; failed=1 ;;
    esac
    return "$failed"
}
trap 'code=$?; trap - EXIT; if ! cleanup; then code=1; fi; exit "$code"' EXIT
trap 'exit 130' HUP INT TERM
TEMP_DIR=$(mktemp -d "$ROOT/tmp/regression/replay-ci/secret.XXXXXX")
chmod 700 "$TEMP_DIR"
FIXTURE_ID=$(docker run --rm --network none --entrypoint python \
    --mount "type=bind,src=$ROOT,dst=/app/candidate,readonly" \
    --mount "type=bind,src=$TEMP_DIR,dst=/run/replay-fixture" \
    -e PYTHONDONTWRITEBYTECODE=1 "$IMAGE" \
    /app/candidate/scripts/check_replay_cache_baseline.py --make-secrets /run/replay-fixture)
case "$FIXTURE_ID" in ''|*[!a-f0-9]*) echo 'Invalid fixture identity.' >&2; exit 1 ;; esac
[ "${#FIXTURE_ID}" -eq 32 ] || exit 1
PG_NAME="kiwoom-ci-pg-$FIXTURE_ID"
PY_NAME="kiwoom-ci-python-$FIXTURE_ID"
docker run --detach --rm --name "$PG_NAME" --network none \
    --label "com.kiwoom.replay-fixture=$FIXTURE_ID" \
    --memory 768m --pids-limit 128 --tmpfs /var/lib/postgresql/data:rw,nosuid,size=512m \
    --mount "type=bind,src=$TEMP_DIR,dst=/run/replay-fixture,readonly" \
    -e PGDATA=/var/lib/postgresql/data -e POSTGRES_USER=kiwoom_replay_fixture_admin \
    -e POSTGRES_DB=postgres -e POSTGRES_PASSWORD_FILE=/run/replay-fixture/postgres-password \
    "$PG_IMAGE" postgres -c shared_buffers=16MB -c max_connections=20 -c work_mem=2MB \
    -c "kiwoom.replay_fixture_id=$FIXTURE_ID" >/dev/null
[ "$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$PG_NAME")" = none ] || exit 1
[ "$(docker inspect -f '{{index .Config.Labels "com.kiwoom.replay-fixture"}}' "$PG_NAME")" = "$FIXTURE_ID" ] || exit 1
ready=false
for attempt in $(seq 1 60); do
    if docker exec "$PG_NAME" pg_isready -U kiwoom_replay_fixture_admin -d postgres >/dev/null 2>&1; then
        ready=true; break
    fi
    sleep 1
done
[ "$ready" = true ] || { echo 'Temporary PostgreSQL did not become ready.' >&2; exit 1; }

docker run --rm --name "$PY_NAME" --network "container:$PG_NAME" \
    --label "com.kiwoom.replay-fixture=$FIXTURE_ID" \
    --memory 512m --pids-limit 128 --read-only --tmpfs /tmp:rw,nosuid,nodev,size=64m \
    --security-opt no-new-privileges --cap-drop ALL --entrypoint python \
    --mount "type=bind,src=$ROOT,dst=/app/candidate,readonly" \
    --mount "type=bind,src=$TEMP_DIR,dst=/run/replay-fixture,readonly" \
    -e PYTHONDONTWRITEBYTECODE=1 "$IMAGE" \
    /app/candidate/scripts/check_replay_cache_baseline.py \
    --secrets-file /run/replay-fixture/fixture.json --top20-lifecycle
cleanup
TEMP_DIR=''
PG_NAME=''
PY_NAME=''
echo 'Temporary replay cluster and Python container exited and were removed.'

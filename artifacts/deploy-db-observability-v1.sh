#!/bin/sh
set -eu

DOCKER=/usr/local/bin/docker
PROJECT=kiwoom-monitor
ROOT=/volume1/docker/kiwoom-monitor
COMPOSE="$ROOT/deploy/synology/docker-compose.yml"
ROLLBACK="$ROOT/artifacts/rollback-db-observability-v1.yml"
SERVER=kiwoom-monitor-server-1
OLD_TAG=kiwoom-monitor-server:2026.09.26-bar-upsert-wait-correlation-v5
NEW_TAG=kiwoom-monitor-server:2026.09.28-db-observability-v1
OLD_ID=sha256:0a996650e1e8428f204912b73b3c6b1e91e676d272e0dce017754642447f56e2
NEW_ID=sha256:164aa67ac954269d88e2761dc5a2c2df39cbccfd72c2341e0ffe9cfb40690183

fail() { printf '%s\n' "$*" >&2; exit 1; }
docker() { sudo "$DOCKER" "$@"; }

[ -f "$COMPOSE" ] || fail 'Compose file missing'
[ -f "$ROLLBACK" ] || fail 'Rollback override missing'
[ "$(docker inspect --format '{{.Image}}' "$SERVER")" = "$OLD_ID" ] || fail 'Running server image changed; aborting'
[ "$(docker image inspect --format '{{.Id}}' "$OLD_TAG")" = "$OLD_ID" ] || fail 'Old image tag changed; aborting'
[ "$(docker image inspect --format '{{.Id}}' "$NEW_TAG")" = "$NEW_ID" ] || fail 'Candidate image tag changed; aborting'
[ "$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$SERVER")" = "$PROJECT" ] || fail 'Compose project changed; aborting'
grep -q 'image: kiwoom-monitor-server:2026.09.28-db-observability-v1' "$COMPOSE" || fail 'Compose image tag changed; aborting'
grep -q '2026.09.28-db-observability-v1' "$ROOT/src/kiwoom_monitor/central_server/app.py" || fail 'Server source marker changed; aborting'

cd "$ROOT/deploy/synology"
if docker compose version >/dev/null 2>&1; then
    COMPOSE_MODE=plugin
elif [ -x /usr/local/bin/docker-compose ]; then
    COMPOSE_MODE=standalone
else
    fail 'Docker Compose unavailable; no container changed'
fi

compose_server() {
    if [ "$COMPOSE_MODE" = plugin ]; then
        docker compose -p "$PROJECT" -f "$COMPOSE" "$@" up -d --no-build --no-deps --force-recreate server
    else
        sudo /usr/local/bin/docker-compose -p "$PROJECT" -f "$COMPOSE" "$@" up -d --no-build --no-deps --force-recreate server
    fi
}

wait_healthy() {
    count=0
    while [ "$count" -lt 18 ]; do
        state=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$SERVER" 2>/dev/null || true)
        [ "$state" = healthy ] && return 0
        count=$((count + 1))
        sleep 5
    done
    return 1
}

rollback_server() {
    printf '%s\n' 'New server failed verification; restoring the old server image.' >&2
    if compose_server -f "$ROLLBACK" && wait_healthy; then
        printf '%s\n' 'Old server image is healthy again. Source and main Compose still contain the new candidate; investigate before another recreate.' >&2
    else
        printf '%s\n' 'Automatic image rollback failed; inspect Container Manager immediately.' >&2
    fi
}

printf '%s\n' 'Checking candidate app without network or production DB...'
docker run --rm -i --network none --entrypoint python "$NEW_TAG" - <<'PY'
from kiwoom_monitor.central_server.app import SERVER_BUILD, create_app
from kiwoom_monitor.central_server.config import CentralServerSettings

assert SERVER_BUILD == "2026.09.28-db-observability-v1"
app = create_app(CentralServerSettings(
    database_url="sqlite:////tmp/db-observability-preflight.sqlite3",
    access_token="preflight-only",
    autonomous_top20_enabled=False,
    external_market_enabled=False,
))
assert "/api/v1/diagnostics/db-calls" in {route.path for route in app.routes}
print("candidate app and diagnostic route: ok")
PY

DB_BEFORE=$(docker ps --filter "label=com.docker.compose.project=$PROJECT" --filter 'label=com.docker.compose.service=database' --format '{{.ID}}')
[ -n "$DB_BEFORE" ] || fail 'Running database container not found; aborting'
printf '%s\n' 'Recreating only the server service from the prebuilt image...'
if ! compose_server; then
    rollback_server
    fail 'Server recreate failed'
fi
if [ "$(docker inspect --format '{{.Image}}' "$SERVER")" != "$NEW_ID" ] || ! wait_healthy; then
    rollback_server
    fail 'Candidate image or health verification failed'
fi
DB_AFTER=$(docker ps --filter "label=com.docker.compose.project=$PROJECT" --filter 'label=com.docker.compose.service=database' --format '{{.ID}}')
if [ "$DB_AFTER" != "$DB_BEFORE" ]; then
    rollback_server
    fail 'Database container identity changed unexpectedly'
fi

if ! docker exec -i "$SERVER" python - <<'PY'
import json
import os
import time
import urllib.request

port = os.environ.get("KIWOOM_SERVER_PORT", "8787")
base = f"http://127.0.0.1:{port}"
with urllib.request.urlopen(base + "/health", timeout=10) as response:
    health = json.load(response)
assert health["status"] == "ok", health
assert health["server_build"] == "2026.09.28-db-observability-v1", health
end = time.time()
url = f"{base}/api/v1/diagnostics/db-calls?start={end - 60}&end={end}&mode=summary"
request = urllib.request.Request(url, headers={
    "Authorization": "Bearer " + os.environ["MONITOR_SERVER_ACCESS_TOKEN"],
})
with urllib.request.urlopen(request, timeout=10) as response:
    calls = json.load(response)
assert isinstance(calls, dict), type(calls)
print("health build:", health["server_build"])
print("authenticated db-calls: ok; keys:", sorted(calls))
PY
then
    rollback_server
    fail 'Runtime API verification failed'
fi

printf '%s\n' 'Deployment verified: new server image is healthy; database container was not recreated.'

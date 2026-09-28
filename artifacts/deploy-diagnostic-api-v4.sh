#!/bin/sh
set -eu

DOCKER=/usr/local/bin/docker
ROOT=/volume1/docker/kiwoom-monitor
COMPOSE="$ROOT/deploy/synology/docker-compose.yml"
SERVER=kiwoom-monitor-server-1
PROJECT=kiwoom-monitor
OLD_TAG=kiwoom-monitor-server:2026.09.28-db-observability-v2
NEW_TAG=kiwoom-monitor-server:2026.09.29-diagnostic-api-v4
ROLLBACK="$ROOT/artifacts/rollback-diagnostic-api-v4.yml"

fail() { printf '%s\n' "$*" >&2; exit 1; }
docker() { sudo "$DOCKER" "$@"; }

[ -f "$COMPOSE" ] || fail 'Compose file missing'
grep -q "image: $NEW_TAG" "$COMPOSE" || fail 'Compose tag changed'
grep -q '2026.09.29-diagnostic-api-v4' "$ROOT/src/kiwoom_monitor/central_server/app.py" || fail 'Source marker changed'
grep -q '/api/v1/diagnostics/capabilities' "$ROOT/src/kiwoom_monitor/central_server/app.py" || fail 'API route missing'
OLD_ID=$(docker inspect --format '{{.Image}}' "$SERVER")
[ "$(docker image inspect --format '{{.Id}}' "$OLD_TAG")" = "$OLD_ID" ] || fail 'Running server image is not the expected v2 image'
[ "$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$SERVER")" = "$PROJECT" ] || fail 'Unexpected Compose project'
DB_BEFORE=$(docker ps --filter "label=com.docker.compose.project=$PROJECT" --filter 'label=com.docker.compose.service=database' --format '{{.ID}}')
[ -n "$DB_BEFORE" ] || fail 'Database container not found'

cd "$ROOT/deploy/synology"
if docker compose version >/dev/null 2>&1; then
    COMPOSE_MODE=plugin
elif [ -x /usr/local/bin/docker-compose ]; then
    COMPOSE_MODE=standalone
else
    fail 'Docker Compose unavailable'
fi
compose() {
    if [ "$COMPOSE_MODE" = plugin ]; then
        docker compose -p "$PROJECT" -f "$COMPOSE" "$@"
    else
        sudo /usr/local/bin/docker-compose -p "$PROJECT" -f "$COMPOSE" "$@"
    fi
}
compose_rollback() {
    if [ "$COMPOSE_MODE" = plugin ]; then
        docker compose -p "$PROJECT" -f "$COMPOSE" -f "$ROLLBACK" "$@"
    else
        sudo /usr/local/bin/docker-compose -p "$PROJECT" -f "$COMPOSE" -f "$ROLLBACK" "$@"
    fi
}
wait_healthy() {
    count=0
    while [ "$count" -lt 18 ]; do
        status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$SERVER" 2>/dev/null || true)
        [ "$status" = healthy ] && return 0
        count=$((count + 1))
        sleep 5
    done
    return 1
}
rollback() {
    printf '%s\n' 'Candidate failed; restoring v2 server image.' >&2
    if compose_rollback up -d --no-build --no-deps --force-recreate server && wait_healthy; then
        printf '%s\n' 'v2 image restored. Staged source and Compose still select v4; inspect before another recreate.' >&2
    else
        printf '%s\n' 'Automatic rollback failed; inspect Container Manager.' >&2
    fi
}

mkdir -p "$ROOT/artifacts"
[ ! -e "$ROLLBACK" ] || fail 'Rollback override already exists'
printf 'services:\n  server:\n    image: %s\n' "$OLD_TAG" > "$ROLLBACK"
printf '%s\n' 'Building diagnostic API candidate from staged NAS source...'
compose build server
NEW_ID=$(docker image inspect --format '{{.Id}}' "$NEW_TAG")
[ -n "$NEW_ID" ] || fail 'Candidate image not found'

printf '%s\n' 'Checking candidate without network or production DB...'
docker run --rm -i --network none --entrypoint python "$NEW_TAG" - <<'PY'
from kiwoom_monitor.central_server.app import SERVER_BUILD, create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.diagnostic_workloads import diagnostic_run_lock
import subprocess
import sys
import tempfile
from pathlib import Path

assert SERVER_BUILD == '2026.09.29-diagnostic-api-v4'
app = create_app(CentralServerSettings(
    database_url='sqlite:////tmp/diagnostic-api-preflight.sqlite3',
    access_token='preflight-only', autonomous_top20_enabled=False,
    external_market_enabled=False,
))
paths = {route.path for route in app.routes}
for path in ('/api/v1/diagnostics/capabilities', '/api/v1/diagnostics/control',
             '/api/v1/diagnostics/snapshot', '/api/v1/diagnostics/runs',
             '/api/v1/diagnostics/reports', '/api/v1/diagnostics/history'):
    assert path in paths, path
with tempfile.TemporaryDirectory(prefix='diagnostic-flock-') as directory:
    control = Path(directory) / 'control.json'
    locked = """import sys
from pathlib import Path
from kiwoom_monitor.central_server.diagnostic_workloads import diagnostic_run_lock
try:
    with diagnostic_run_lock(Path(sys.argv[1])): pass
except RuntimeError:
    raise SystemExit(0)
raise SystemExit(9)
"""
    available = """import sys
from pathlib import Path
from kiwoom_monitor.central_server.diagnostic_workloads import diagnostic_run_lock
with diagnostic_run_lock(Path(sys.argv[1])): pass
"""
    lock = diagnostic_run_lock(control)
    lock.__enter__()
    try:
        result = subprocess.run([sys.executable, '-c', locked, str(control)])
        assert result.returncode == 0, result.returncode
    finally:
        lock.__exit__(None, None, None)
    result = subprocess.run([sys.executable, '-c', available, str(control)])
    assert result.returncode == 0, result.returncode
print('candidate API routes: ok')
print('candidate Linux flock contention and release: ok')
PY

printf '%s\n' 'Recreating server only; database container stays untouched...'
if ! compose up -d --no-build --no-deps --force-recreate server; then
    rollback
    fail 'Server recreate failed'
fi
if [ "$(docker inspect --format '{{.Image}}' "$SERVER")" != "$NEW_ID" ] || ! wait_healthy; then
    rollback
    fail 'Candidate image or health failed'
fi
DB_AFTER=$(docker ps --filter "label=com.docker.compose.project=$PROJECT" --filter 'label=com.docker.compose.service=database' --format '{{.ID}}')
if [ "$DB_AFTER" != "$DB_BEFORE" ]; then
    rollback
    fail 'Database container identity changed'
fi
if ! docker exec -i "$SERVER" python - <<'PY'
import json
import os
import urllib.request

base = 'http://127.0.0.1:' + os.environ.get('KIWOOM_SERVER_PORT', '8787')
with urllib.request.urlopen(base + '/health', timeout=10) as response:
    health = json.load(response)
assert health['status'] == 'ok' and health['server_build'] == '2026.09.29-diagnostic-api-v4', health
request = urllib.request.Request(base + '/api/v1/diagnostics/capabilities', headers={
    'Authorization': 'Bearer ' + os.environ['MONITOR_SERVER_ACCESS_TOKEN'],
})
with urllib.request.urlopen(request, timeout=10) as response:
    capabilities = json.load(response)
assert capabilities['control_available'] and capabilities['postgres_available'], capabilities
print('healthy build and authenticated diagnostic capabilities: ok')
PY
then
    rollback
    fail 'Runtime API smoke failed'
fi
printf '%s\n' 'Deployment verified: v4 server healthy; database container unchanged.'

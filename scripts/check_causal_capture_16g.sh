#!/bin/sh
# Private 65-minute synthetic-envelope probe; never selects operational limits.
set -eu
ROOT=/volume1/docker/kiwoom-monitor
DOCKER=/usr/local/bin/docker
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
CANDIDATE=$(CDPATH= cd -- "$HERE/.." && pwd -P)
RELEASE=$(basename "$CANDIDATE")
[ "$CANDIDATE" = "$ROOT/source-runtime/releases/$RELEASE" ] || exit 1
ACTIVE=$(sha256sum "$ROOT/source-runtime/active.json")
SERVER=$("$DOCKER" inspect -f '{{.Id}}' kiwoom-monitor-server-1)
DATABASE=$("$DOCKER" inspect -f '{{.Id}}' kiwoom-monitor-database-1)
IMAGE=$("$DOCKER" inspect -f '{{.Image}}' kiwoom-monitor-server-1)
REPORT=$(mktemp "$ROOT/artifacts/causal-capture-16g.XXXXXX.log")
STATUS_FILE=''
verify_unchanged() {
    [ "$(sha256sum "$ROOT/source-runtime/active.json")" = "$ACTIVE" ] || return 1
    [ "$("$DOCKER" inspect -f '{{.Id}}' kiwoom-monitor-server-1)" = "$SERVER" ] || return 1
    [ "$("$DOCKER" inspect -f '{{.Id}}' kiwoom-monitor-database-1)" = "$DATABASE" ] || return 1
}
finish() {
    code=$?
    trap - EXIT
    if [ -n "$STATUS_FILE" ]; then rm -f -- "$STATUS_FILE"; fi
    if verify_unchanged; then unchanged=true; else unchanged=false; code=1; fi
    echo "report=$REPORT active_release_and_both_containers_unchanged=$unchanged"
    exit "$code"
}
trap finish EXIT
echo "report=$REPORT stage=starting private_probe_memory_gib=16 operational_defaults_unchanged=true"
run_logged() {
    STATUS_FILE=$(mktemp "$ROOT/artifacts/.causal-capture-status.XXXXXX")
    (
        set +e
        "$@"
        result=$?
        printf '%s\n' "$result" >"$STATUS_FILE"
        exit 0
    ) 2>&1 | tee -a "$REPORT"
    result=$(cat "$STATUS_FILE")
    rm -f -- "$STATUS_FILE"
    STATUS_FILE=''
    case "$result" in ''|*[!0-9]*) return 1;; esac
    return "$result"
}
run_python() {
    # Charge budget is private; a 20GiB cgroup allows measurement. Keep a 4GiB
    # host and cgroup reserve while sampling actual RSS and MemAvailable.
    "$DOCKER" run --rm --network none --memory 20g --entrypoint python \
        --mount "type=bind,src=$CANDIDATE,dst=/app/candidate,readonly" \
        --tmpfs /tmp:rw,nosuid,size=5g \
        -w /app/candidate -e PYTHONPATH=/app/candidate/src:/app/candidate \
        -e PYTHONDONTWRITEBYTECODE=1 -e TMPDIR=/tmp "$IMAGE" "$@"
}
echo 'stage=verify_candidate_and_production_memory'
run_logged "$DOCKER" run --rm --network none --entrypoint python \
    --mount "type=bind,src=$ROOT/source-runtime,dst=/app/source-runtime,readonly" \
    "$IMAGE" /app/source-runtime/runner.py info --release "$RELEASE"
run_logged "$DOCKER" exec kiwoom-monitor-server-1 python -c \
    'import json,pathlib; s=dict(l.split(":",1) for l in pathlib.Path("/proc/1/status").read_text().splitlines() if ":" in l); m=dict(l.split(":",1) for l in pathlib.Path("/proc/meminfo").read_text().splitlines() if ":" in l); print(json.dumps({"production_baseline":{"pid1_name":s.get("Name","").strip(),"rss_bytes":int(s.get("VmRSS","0 kB").split()[0])*1024,"peak_rss_bytes":int(s.get("VmHWM","0 kB").split()[0])*1024,"host_available_bytes":int(m["MemAvailable"].split()[0])*1024}}))'
echo 'stage=api_and_probe_regression'
run_logged run_python scripts/check_causal_capture_api.py
run_logged run_python -m unittest tests.unit.test_causal_capture_capacity \
    tests.unit.test_rest_request_lane_capacity tests.unit.test_diagnostic_rest_input
echo 'stage=mixed_durable_smoke'
run_logged run_python scripts/check_causal_capture_capacity.py --mode on --messages 20 --rows 2 \
    --mixed-every 10 --catalog-rows 5000 --persist --persist-timeout 60
for round in 1 2; do
    for mode in off on; do
        echo "stage=mixed_latency round=$round mode=$mode"
        run_logged run_python scripts/check_causal_capture_capacity.py --mode "$mode" \
            --messages 200 --rows 20 --mixed-every 30 --catalog-rows 5000 --progress
    done
done
echo 'stage=16g_65m_synthetic_envelope messages=39000 cadence_ms=100 mixed_every=30'
run_logged run_python scripts/check_causal_capture_capacity.py --mode on --messages 39000 --rows 20 \
    --mixed-every 30 --catalog-rows 5000 --memory-gib 16 --event-capacity 5000000 \
    --reserve-gib 4 --progress
echo '{"private_probe_gates":"passed","synthetic_65m_envelope":"completed_without_rejection_or_drop","capacity_acceptance":"not_approved","operational_defaults_changed":false,"active_changed":false,"scheduled_capture_changed":false,"full_capacity_persistence_verified":false}'

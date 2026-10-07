#!/bin/sh
# Inactive correctness and sizing challenge. Never deploys or edits capture reservations.
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
REPORT=$(mktemp "$ROOT/artifacts/causal-capture-sizing.XXXXXX.log")
verify_unchanged() {
    [ "$(sha256sum "$ROOT/source-runtime/active.json")" = "$ACTIVE" ] || return 1
    [ "$("$DOCKER" inspect -f '{{.Id}}' kiwoom-monitor-server-1)" = "$SERVER" ] || return 1
    [ "$("$DOCKER" inspect -f '{{.Id}}' kiwoom-monitor-database-1)" = "$DATABASE" ] || return 1
}
trap 'code=$?; trap - EXIT; if verify_unchanged; then unchanged=true; else unchanged=false; code=1; fi; echo "report=$REPORT active_release_and_both_containers_unchanged=$unchanged"; exit "$code"' EXIT
"$DOCKER" run --rm --network none --entrypoint python \
    --mount "type=bind,src=$ROOT/source-runtime,dst=/app/source-runtime,readonly" \
    "$IMAGE" /app/source-runtime/runner.py info --release "$RELEASE"
run_python() {
    "$DOCKER" run --rm --network none --cpus 1 --memory 12g --entrypoint python \
        --mount "type=bind,src=$CANDIDATE,dst=/app/candidate,readonly" \
        --tmpfs /tmp:rw,nosuid,size=128m \
        -w /app/candidate -e PYTHONPATH=/app/candidate/src:/app/candidate \
        -e PYTHONDONTWRITEBYTECODE=1 -e TMPDIR=/tmp "$IMAGE" "$@"
}
run_python scripts/check_causal_capture_api.py >>"$REPORT" 2>&1
run_python -m unittest \
    tests.unit.test_catalog_capture_profile \
    tests.unit.test_diagnostic_trace_deferred tests.unit.test_diagnostic_trace_batches \
    tests.unit.test_diagnostic_delivery_record \
    tests.unit.test_diagnostic_rest_input tests.unit.test_top20_lifecycle_inputs \
    tests.unit.test_top20_delivery_provenance tests.unit.test_recorded_workload_capture >>"$REPORT" 2>&1
sh "$HERE/check_replay_cache_baseline.sh" --top20-lifecycle >>"$REPORT" 2>&1
# Verify a full-size catalog input through deferred persistence and its bounded reader.
run_python scripts/check_causal_capture_capacity.py --mode on --messages 20 --rows 2 \
    --mixed-every 10 --catalog-rows 5000 --persist --persist-timeout 60 >>"$REPORT" 2>&1
# Sequential private probes. A saturation result is expected evidence, never
# permission to activate a capture that has insufficient capacity.
run_python scripts/check_causal_capture_capacity.py --mode off --messages 1000 --rows 20 >>"$REPORT" 2>&1
run_python scripts/check_causal_capture_capacity.py --mode on --messages 1000 --rows 20 >>"$REPORT" 2>&1
run_python scripts/check_causal_capture_capacity.py --mode on --messages 15000 --rows 20 \
    --mixed-every 30 --catalog-rows 5000 --memory-gib 4 --event-capacity 1000000 \
    --require-limit >>"$REPORT" 2>&1
run_python scripts/check_causal_capture_capacity.py --mode on --messages 25000 --rows 20 \
    --mixed-every 30 --catalog-rows 5000 --memory-gib 8 --event-capacity 5000000 \
    --require-limit >>"$REPORT" 2>&1
cat "$REPORT"
echo '{"correctness_gates":"passed","capacity_acceptance":"not_approved","active_changed":false,"scheduled_capture_changed":false}'

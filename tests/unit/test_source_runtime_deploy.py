"""Execute the deployment shell against a fake Docker boundary, never a NAS."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


FAKE_DOCKER = r'''#!/bin/sh
set -eu
printf '%s\n' "$*" >> "$FIXTURE/log"
case "$1" in
inspect)
    case "$*" in
    *State.Running*) cat "$FIXTURE/running";;
    *Mounts*) printf '%s\n' "$STORE";;
    *Image*) echo image-id;;
    *) echo db-id;;
    esac;;
image) echo image-id;;
stop) echo false > "$FIXTURE/running";;
start)
    echo true > "$FIXTURE/running"
    printf 'started:%s\n' "$(cat "$FIXTURE/pinned")" >> "$FIXTURE/log";;
run)
    mode=''; selected=old
    for arg do
        case "$arg" in info|select|check) mode=$arg;; esac
        selected=$arg
    done
    case "$mode" in
    info)
        [ "$selected" != info ] || selected=$(cat "$FIXTURE/pinned")
        printf '{"release_id": "%s", "server_build": "build-%s", "runtime_image": "test-image"}\n' "$selected" "$selected";;
    select)
        echo "$selected" > "$FIXTURE/pinned"
        if [ "$selected" = new ] && [ "${CASE:-}" = lost_selection ] && [ ! -f "$FIXTURE/lost" ]; then
            touch "$FIXTURE/lost"; exit 1
        fi;;
    check) :;;
    *) exit 9;;
    esac;;
compose)
    target=''; offline=false; take=false
    for arg do
        if [ "$take" = true ]; then target=${arg##*/}; take=false; fi
        [ "$arg" != --target-source ] || take=true
        [ "$arg" != --offline ] || offline=true
    done
    [ -n "$target" ] || exit 9
    printf 'guard:%s:%s\n' "$target" "$offline" >> "$FIXTURE/log"
    if [ "$offline" = true ]; then
        [ "$(cat "$FIXTURE/running")" = false ] || exit 10
        [ "${CASE:-}" != conversion_fault ] || exit 11
        if [ "$target" = old ]; then printf 'converted:old\n' >> "$FIXTURE/log"; fi
    elif [ "${CASE:-}" = refusal ]; then exit 12
    elif [ "$target" = old ]; then exit 3
    fi;;
exec)
    case "$*" in
    *'assert d.get'*)
        if [ "${CASE:-}" = readiness_fault ] && [ "$(cat "$FIXTURE/pinned")" = new ]; then exit 1; fi
        echo '{"status":"ok"}';;
    *) printf 'build-%s\n' "$(cat "$FIXTURE/pinned")";;
    esac;;
*) exit 9;;
esac
'''


class SourceRuntimeDeployTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shell = shutil.which("sh")
        if not cls.shell and os.name == "nt":
            bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/git/usr/bin/sh.exe"
            if bundled.is_file(): cls.shell = str(bundled)
        if not cls.shell: raise unittest.SkipTest("POSIX shell is unavailable")

    def run_fixture(self, case, target="new"):
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp)
            root = fixture / "project"
            deploy = root / "deploy/synology"
            store = root / "source-runtime"
            deploy.mkdir(parents=True)
            store.mkdir()
            for name in ("runtime.json", "runner.py", "active.json"):
                (store / name).write_text("fixture")
            for name in ("old", "new"):
                scripts = store / "releases" / name / "scripts"
                scripts.mkdir(parents=True)
                (scripts / "check_source_database.py").write_text("guard")
            (fixture / "pinned").write_text("old\n")
            (fixture / "running").write_text("true\n")
            docker = fixture / "docker"
            docker.write_text(FAKE_DOCKER, newline="\n")
            # Only the test copy redirects Docker and accelerates its readiness
            # clock. Production still uses its fixed Docker binary and 90s wait.
            script = (Path(__file__).resolve().parents[2] / "deploy/synology/source-runtime.sh").read_text()
            script = script.replace("DOCKER=/usr/local/bin/docker", f"DOCKER='{docker.as_posix()}'")
            script = script.replace("deadline=$(($(date +%s) + 90))", "deadline=1")
            script = script.replace('while [ "$(date +%s)" -lt "$deadline" ]; do', 'for attempt in 1 2; do')
            script = script.replace("sleep 1", ":")
            path = deploy / "source-runtime.sh"
            path.write_text(script, newline="\n")
            # Git's shell resolves C:/... paths to /c/...; ask that same shell
            # for the canonical mount path used by Docker inspect in the fake.
            store_posix = subprocess.check_output([self.shell, "-c", 'cd "$1" && pwd -P', "fixture", store.as_posix()], text=True).strip()
            env = dict(os.environ, PATH=str(Path(self.shell).parent) + os.pathsep + os.environ.get("PATH", ""),
                       FIXTURE=fixture.as_posix(), STORE=store_posix,
                       CASE=case, KIWOOM_SOURCE_RUNTIME_IMAGE="test-image")
            result = subprocess.run([self.shell, path.as_posix(), "deploy", target], env=env, capture_output=True, text=True, timeout=15)
            self.assertTrue((fixture / "log").exists(), result.stderr + result.stdout)
            log = (fixture / "log").read_text().splitlines()
            return result, log, (fixture / "pinned").read_text().strip(), (fixture / "running").read_text().strip()

    def test_unknown_schema_refused_before_stopping_running_server(self):
        result, log, pinned, running = self.run_fixture("refusal")
        self.assertNotEqual(0, result.returncode, result.stderr)
        self.assertFalse(any(line.startswith("stop ") for line in log))
        self.assertEqual(("old", "true"), (pinned, running))

    def test_known_downgrade_converts_only_after_stop_and_before_source_selection(self):
        result, log, pinned, running = self.run_fixture("downgrade", "old")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        stop = next(i for i, line in enumerate(log) if line.startswith("stop "))
        self.assertLess(stop, log.index("converted:old"))
        self.assertLess(log.index("converted:old"), log.index("started:old"))
        self.assertEqual(("old", "true"), (pinned, running))

    def test_failed_conversion_restarts_unchanged_execution_source(self):
        result, log, pinned, running = self.run_fixture("conversion_fault")
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(("old", "true"), (pinned, running))
        self.assertFalse(any("runner.py select" in line for line in log))

    def test_lost_selection_ack_recovers_explicit_previous_pointer_before_start(self):
        result, log, pinned, running = self.run_fixture("lost_selection")
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(("old", "true"), (pinned, running))
        self.assertEqual(["started:old"], [line for line in log if line.startswith("started:")])

    def test_failed_readiness_stops_candidate_then_converts_and_starts_previous(self):
        result, log, pinned, running = self.run_fixture("readiness_fault")
        self.assertNotEqual(0, result.returncode)
        stops = [i for i, line in enumerate(log) if line.startswith("stop ")]
        self.assertEqual(2, len(stops))
        self.assertLess(stops[1], log.index("converted:old"))
        self.assertLess(log.index("converted:old"), log.index("started:old"))
        self.assertEqual(("old", "true"), (pinned, running))


if __name__ == "__main__":
    unittest.main()

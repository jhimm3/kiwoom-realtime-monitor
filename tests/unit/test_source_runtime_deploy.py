"""Execute the deployment shell against a fake Docker boundary, never a NAS."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import tomllib
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
            if os.name == "posix":
                docker.chmod(0o755)
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

    def test_production_server_subset_install_is_constrained_by_copied_lock(self):
        root = Path(__file__).resolve().parents[2]
        recipe = (root / "deploy/synology/server.Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY pyproject.toml README.md requirements.lock.txt ./", recipe.splitlines())
        install = recipe.split("&& pip install ", 1)[1].split("\n\n", 1)[0].replace("\\\n", " ")
        self.assertEqual([
            "--no-cache-dir", "--constraint", "requirements.lock.txt",
            "fastapi>=0.116,<1", "uvicorn[standard]>=0.35,<1", "psycopg[binary]>=3.2,<4",
            "cryptography>=46,<47", "google-auth==2.41.1", "websockets==16.1.1",
            "tzdata==2026.3",
        ], shlex.split(install))
        ci_recipe = (root / "tests/ci_runtime.Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY pyproject.toml README.md requirements.lock.txt ./", ci_recipe.splitlines())
        ci_install = ci_recipe.split("&& pip install ", 1)[1].split("\n\n", 1)[0].replace("\\\n", " ")
        self.assertEqual([
            "--no-cache-dir", "--constraint", "requirements.lock.txt",
            "fastapi>=0.116,<1", "uvicorn[standard]>=0.35,<1", "psycopg[binary]>=3.2,<4",
            "cryptography>=46,<47", "websockets==16.1.1", "tzdata==2026.3",
        ], shlex.split(ci_install))

    def test_role_metadata_keeps_query_and_headless_dependencies_separate(self):
        root = Path(__file__).resolve().parents[2]
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertEqual(["websockets==16.1.1", "tzdata==2026.3"], project["dependencies"])
        extras = project["optional-dependencies"]
        self.assertEqual({"query", "server", "ocr", "drive", "chatgpt", "full"}, set(extras))
        self.assertEqual(["PySide6==6.11.1", "Pillow==12.3.0"], extras["query"])
        self.assertEqual([
            "fastapi>=0.116,<1", "uvicorn[standard]>=0.35,<1", "psycopg[binary]>=3.2,<4",
            "cryptography>=46,<47", "google-auth==2.41.1",
        ], extras["server"])
        self.assertEqual([
            "PySide6==6.11.1", "Pillow==12.3.0", "paddlepaddle==3.2.2", "paddleocr==3.7.0",
        ], extras["ocr"])
        self.assertEqual([
            "google-api-python-client==2.187.0", "google-auth-oauthlib==1.2.3",
        ], extras["drive"])
        self.assertEqual(["google-auth==2.41.1", "cryptography>=46,<47"], extras["chatgpt"])

    def test_role_metadata_preserves_full_features_entrypoints_and_existing_lock_pins(self):
        root = Path(__file__).resolve().parents[2]
        document = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        project = document["project"]
        self.assertEqual("kiwoom-realtime-monitor", project["name"])
        self.assertEqual("2.1.0", project["version"])
        self.assertEqual(">=3.13", project["requires-python"])
        self.assertEqual({
            "kiwoom-monitor": "kiwoom_monitor.bootstrap:main",
            "kiwoom-monitor-server": "kiwoom_monitor.central_server.__main__:main",
            "kiwoom-monitor-check": "kiwoom_monitor.central_server.deployment_check:main",
        }, project["scripts"])
        self.assertEqual({"requires": ["setuptools>=68"], "build-backend": "setuptools.build_meta"},
                         document["build-system"])
        full = project["dependencies"] + project["optional-dependencies"]["full"]
        self.assertEqual({
            "websockets==16.1.1", "tzdata==2026.3", "PySide6==6.11.1", "Pillow==12.3.0",
            "paddlepaddle==3.2.2", "paddleocr==3.7.0", "google-api-python-client==2.187.0",
            "google-auth-oauthlib==1.2.3", "fastapi>=0.116,<1", "uvicorn[standard]>=0.35,<1",
            "psycopg[binary]>=3.2,<4", "cryptography>=46,<47", "google-auth==2.41.1",
        }, set(full))
        lock = dict(line.lower().replace("_", "-").split("==", 1) for line in
                    (root / "requirements.lock.txt").read_text(encoding="utf-8").splitlines()
                    if "==" in line and not line.startswith("#"))
        for requirement in full:
            name = re.split(r"[<>=!\[]", requirement, maxsplit=1)[0].lower().replace("_", "-")
            with self.subTest(requirement=requirement):
                self.assertIn(name, lock)
                if "==" in requirement:
                    self.assertEqual(requirement.split("==", 1)[1], lock[name])

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

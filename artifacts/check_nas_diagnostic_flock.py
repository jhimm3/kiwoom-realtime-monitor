"""Run the exact diagnostic_run_lock function against NAS Linux flock."""
from __future__ import annotations

import ast
import contextlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    source = Path("/volume1/docker/kiwoom-monitor/src/kiwoom_monitor/central_server/diagnostic_workloads.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    function = next(node for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == "diagnostic_run_lock")
    module = ast.Module(body=[ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0), function],
        type_ignores=[])
    namespace = {"contextlib": contextlib, "os": os, "Path": Path}
    exec(compile(ast.fix_missing_locations(module), str(source), "exec"), namespace)
    lock_factory = namespace["diagnostic_run_lock"]
    blocked_child = """import ast, contextlib, os, sys
from pathlib import Path
source=Path('/volume1/docker/kiwoom-monitor/src/kiwoom_monitor/central_server/diagnostic_workloads.py')
tree=ast.parse(source.read_text())
fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='diagnostic_run_lock')
mod=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),fn],type_ignores=[])
ns={'contextlib':contextlib,'os':os,'Path':Path}; exec(compile(ast.fix_missing_locations(mod),str(source),'exec'),ns)
try:
    with ns['diagnostic_run_lock'](Path(sys.argv[1])): pass
except RuntimeError: raise SystemExit(0)
raise SystemExit(9)
"""
    available_child = """import ast, contextlib, os, sys
from pathlib import Path
source=Path('/volume1/docker/kiwoom-monitor/src/kiwoom_monitor/central_server/diagnostic_workloads.py')
tree=ast.parse(source.read_text())
fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='diagnostic_run_lock')
mod=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),fn],type_ignores=[])
ns={'contextlib':contextlib,'os':os,'Path':Path}; exec(compile(ast.fix_missing_locations(mod),str(source),'exec'),ns)
with ns['diagnostic_run_lock'](Path(sys.argv[1])): pass
"""
    bind_dir = Path("/volume1/docker/kiwoom-monitor/deploy/synology/server-data/maintenance")
    with tempfile.TemporaryDirectory(prefix="diag-flock-", dir=bind_dir) as directory:
        control = Path(directory) / "control.json"
        lock = lock_factory(control)
        lock.__enter__()
        try:
            blocked = subprocess.run([sys.executable, "-c", blocked_child, str(control)])
            assert blocked.returncode == 0, blocked.returncode
        finally:
            lock.__exit__(None, None, None)
        released = subprocess.run([sys.executable, "-c", available_child, str(control)])
        assert released.returncode == 0, released.returncode
    print("nas_linux_exact_flock_contention_and_release: ok")


if __name__ == "__main__":
    main()

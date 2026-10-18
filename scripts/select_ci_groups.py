"""Select extra CI environments conservatively from reviewed changed paths."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GROUPS = ("windows_fast", "windows_long", "postgres", "sealed", "operator")


def _module_name(path: str) -> str:
    name = path.replace("\\", "/").removeprefix("src/").removesuffix(".py").replace("/", ".")
    return name.removesuffix(".__init__")


def _dependency_graph(root: Path, previous: dict[str, str] | None = None) -> tuple[dict, dict, dict]:
    """Current and changed-before imports, literal lookups and unresolved dynamic imports."""
    dependencies, dynamic, files = {}, {}, {}
    sources = [(file.relative_to(root).as_posix(), file.read_text(encoding="utf-8-sig"))
               for directory in ("src", "scripts", "tests") for file in (root / directory).rglob("*.py")]
    sources += list((previous or {}).items())
    for relative, text in sources:
        module = _module_name(relative)
        files[module] = relative
        package = module if relative.endswith("/__init__.py") else module.rpartition(".")[0]
        imports = dependencies.setdefault(module, set())
        for node in ast.walk(ast.parse(text, filename=relative)):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    base = importlib.util.resolve_name("." * node.level + base, package)
                imports.add(base)
                imports.update(base + "." + alias.name for alias in node.names)
            elif isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
                if name in {"import_module", "__import__", "loadTestsFromName", "loadTestsFromNames", "loadTestsFromModule", "spec_from_file_location"} or (
                        name == 'exec' and isinstance(node.func, ast.Name)):
                    if name != "exec" and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        imports.add(node.args[0].value)
                    else:
                        dynamic.setdefault(module, set()).add(f"{relative}:{node.lineno}:{name}")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # Literal file fixtures/contracts are dependencies; arbitrary dotted
                # strings in registration/selection tables are not Python imports.
                value = node.value.replace("\\", "/")
                if "\n" not in value and " " not in value and '/' in value and value.endswith(('.py', '.md', '.json')):
                    imports.add(_module_name(value) if value.endswith('.py') else value)
    return dependencies, dynamic, files


def test_impact(paths: list[str], root: Path = ROOT, previous: dict[str, str] | None = None) -> dict:
    """Explain selection; expand uncertain boundaries through existing profiles, never execute."""
    dependencies, dynamic, files = _dependency_graph(root, previous)
    manifest_path = root / "tests/regression_profiles.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    catalog_path = root / "tests/ci_groups.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else {}
    profiles = dict(manifest.get("profiles", {}))
    profiles.update({f"core:{i}": batch["modules"] for i, batch in enumerate(manifest.get("core_batches", []))})
    profiles.update({key: values for key, values in catalog.items()
                     if isinstance(values, list) and any(str(v).startswith("tests.") for v in values)})
    known = {module for values in profiles.values() for module in values}
    tests = {module for module, file in files.items() if file.startswith("tests/") and Path(file).name.startswith("test_")
             and (root / file).is_file()}
    reasons, uncertain, affected, reached = {}, [], set(), set()
    consumers = {}
    for module, imports in dependencies.items():
        for dependency in imports:
            # from x import symbol may name a function/class, not another module.
            target = dependency
            while target not in dependencies and '.' in target and '/' not in target:
                target = target.rpartition('.')[0]
            consumers.setdefault(target, set()).add(module)
    for path in paths:
        seed = _module_name(path) if path.endswith('.py') else path
        chains = {seed: [path]}
        if path.endswith("/__init__.py") or Path(path).name == "conftest.py":
            prefix = str(Path(path).parent).replace("\\", "/") + "/"
            for module in tests:
                if files[module].startswith(prefix):
                    chains[module] = [path, module]
        queue = list(chains)
        for dependency in queue:
            for module in sorted(consumers.get(dependency, [])):
                if module not in chains:
                    chains[module] = [*chains[dependency], module]
                    queue.append(module)
        affected.update(chains)
        for module in tests & chains.keys():
            reached.add(path)
            reasons.setdefault(module, []).append("dependency: " + " -> ".join(chains[module]))
    # Unknown runtime config/contract consumers cannot be proved by an import graph.
    global_paths = [path for path in paths if not path.endswith((".py", ".md"))
                    or Path(path).name in {"API_CONTRACT.md", "DB_SCHEMA.md"}]
    if global_paths:
        uncertain.append({"reason": "shared_config_or_contract", "paths": global_paths,
                          "expansion": "all_registered_environments"})
        for module in tests | known:
            reasons.setdefault(module, []).append("shared configuration/contract: " + ", ".join(global_paths))
    dynamic_candidates = set(affected)
    if any(path.startswith(('src/', 'scripts/')) or (path.startswith('tests/') and
           path.endswith('.py') and not Path(path).name.startswith('test_')) for path in paths):
        # An unresolved dynamic test/runtime importer may consume changed code
        # even without a static edge. Include its existing owner scope explicitly.
        dynamic_candidates.update(module for module in dynamic if module.startswith(('tests.', 'kiwoom_monitor.')))
    for module in sorted(dynamic_candidates):
        if module not in dynamic:
            continue
        owners = [name for name, members in profiles.items() if module in members]
        if not owners and not module.startswith("tests."):
            owners = [name for name, members in profiles.items() if set(members) & reasons.keys()]
        expansion = {test for name in owners for test in profiles[name]}
        if not expansion:
            expansion = tests | known
        uncertain.append({"reason": "unresolved_dynamic_dependency", "locations": sorted(dynamic[module]),
                          "expansion": owners or ["all_registered_environments"]})
        for test in expansion:
            reasons.setdefault(test, []).append("dynamic boundary expansion: " + module)
    for path in paths:
        if path.endswith(".py") and path not in reached:
            uncertain.append({"reason": "no_proven_test_consumer", "paths": [path],
                              "expansion": "all_registered_environments"})
            for module in tests | known:
                reasons.setdefault(module, []).append("unmapped changed Python: " + path)
    external = {module: sorted(name for name, values in profiles.items() if module in values)
                for module in reasons if not module.startswith("tests.unit.") or module in catalog.get("linux_required", [])}
    unregistered = sorted(set(reasons) - known)
    local_registered = {module for values in manifest.get('profiles', {}).values() for module in values}
    local_registered.update(module for batch in manifest.get('core_batches', []) for module in batch['modules'])
    full_required = bool(local_registered) and local_registered <= reasons.keys()
    return {"selected_modules": sorted(reasons), "reasons": reasons, "uncertainties": uncertain,
            "external_modules": external, "unregistered_modules": unregistered,
            "status": "failed" if unregistered else "full_required" if full_required else
                      "expanded" if uncertain else "selected" if reasons else "no_tests"}


def related_test_modules(paths: list[str], root: Path = ROOT) -> list[str]:
    """Legacy CI suggestion API; keep Linux tests separate from Windows callers."""
    module_name = _module_name

    affected = {module_name(path) for path in paths if path.endswith(".py")}
    if any(path.startswith(".github/") or path in {
        "tests/regression_profiles.json", "tests/ci_groups.json", "requirements.lock.txt",
    } for path in paths):
        affected.add("tests.unit.test_run_regression")
    if any(path.startswith("deploy/") or path == "tests/ci_runtime.Dockerfile" for path in paths):
        affected.update({"tests.unit.test_nas_source_runtime", "tests.unit.test_source_runtime_deploy",
                         "tests.unit.test_central_deployment_check"})
    dependencies, _, _ = _dependency_graph(root)
    while True:
        consumers = {module for module, imports in dependencies.items() if imports & affected}
        if consumers <= affected:
            break
        affected.update(consumers)
    catalog_path = root / "tests/ci_groups.json"
    linux = set(json.loads(catalog_path.read_text(encoding="utf-8"))["linux_required"]) if catalog_path.exists() else set()
    return sorted(module for module in affected
                  if module.startswith("tests.unit.test_") and module in dependencies and module not in linux)


def related_modules_since(base: str, root: Path = ROOT) -> list[str]:
    paths = subprocess.check_output(
        ["git", "diff", "--name-only", "--no-ext-diff", base, "HEAD"], cwd=root, text=True,
    ).splitlines()
    return related_test_modules(paths, root)


def select(paths: list[str], mode: str) -> dict[str, object]:
    if mode == "weekly" or mode == "comprehensive":
        chosen = set(GROUPS)
        reason = "scheduled_or_requested_comprehensive"
    elif mode == "nightly":
        chosen = {"windows_fast", "postgres"}
        reason = "daily_extended"
    elif mode == "auto":
        chosen = set()
        reason = "changed_paths"
        for raw in paths:
            path = raw.replace("\\", "/")
            if path.startswith("docs/") or path in {"README.md", "CHANGELOG.md"}:
                continue
            chosen.add("windows_fast")
            if path.startswith("src/kiwoom_monitor/presentation/"):
                continue
            if path.startswith("tests/integration/test_"):
                name = path.removeprefix("tests/integration/").removesuffix(".py")
                if name == "test_nas_operator_linux":
                    chosen.add("operator")
                elif name in {"test_recorded_replay_baseline_postgres",
                              "test_recorded_execution_postgres", "test_replay_cache_baseline_postgres",
                              "test_replay_cache_execution_postgres", "test_top20_replay_runtime_postgres",
                              "test_top20_session_postgres"}:
                    chosen.update(("sealed", "windows_long"))
                elif name.startswith("test_") and name.endswith("_postgres"):
                    chosen.add("postgres")
                else:
                    chosen.update(GROUPS)
                continue
            if path.startswith("src/kiwoom_monitor/central_server/") or path.startswith(
                "src/kiwoom_monitor/application/"
            ):
                chosen.add("postgres")
                if any(term in path for term in (
                    "database", "replay", "top20", "recorded", "observation",
                    "market_event", "diagnostic", "cache", "snapshot",
                )):
                    chosen.update(("sealed", "windows_long"))
                else:
                    chosen.update(GROUPS)
                continue
            # Runner, fixture, unknown source and deployment changes have no proven narrow boundary.
            chosen.update(GROUPS)
        if not paths:
            chosen.update(GROUPS)
            reason = "unknown_change_set"
    else:
        raise ValueError(f"Unknown CI selection mode: {mode}")
    return {"mode": mode, "reason": reason, "changed_paths": paths,
            "groups": {name: name in chosen for name in GROUPS}}


def _changed_paths(base: str) -> tuple[list[str], bool]:
    if not base or set(base) == {"0"}:
        return [], False
    result = subprocess.run(["git", "diff", "--name-only", "--no-ext-diff", base, "HEAD"],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        return [], False
    risky = subprocess.run(["git", "diff", "--name-only", "--diff-filter=DR", base, "HEAD"],
                           cwd=ROOT, capture_output=True, text=True)
    if risky.returncode:
        return [], False
    return result.stdout.splitlines(), not bool(risky.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("auto", "nightly", "weekly", "comprehensive"), required=True)
    parser.add_argument("--base-ref", default="")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    if args.mode == "auto":
        paths, certain = _changed_paths(args.base_ref)
        result = select(paths, "auto" if certain else "comprehensive")
        if not certain:
            result["reason"] = "unavailable_base_or_rename_delete"
    else:
        result = select([], args.mode)
    result["base_ref"] = args.base_ref
    result["head"] = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                             cwd=ROOT, text=True).strip()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    if args.report.exists():
        raise FileExistsError(f"Refusing to overwrite CI selection: {args.report}")
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as stream:
            for name, enabled in result["groups"].items():
                stream.write(f"{name}={'true' if enabled else 'false'}\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

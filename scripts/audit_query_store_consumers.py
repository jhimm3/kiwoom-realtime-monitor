"""Static, read-only inventory of QueryStore consumers and reviewed connections.

This utility parses Python source only. It never imports the application or connects
to SQLite/PostgreSQL. Same-named methods on unknown receivers remain candidates.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

if __package__:
    from .query_store_source import method_sources
else:
    from query_store_source import method_sources


DATABASE_FILE = "src/kiwoom_monitor/central_server/database.py"
APPROVALS_FILE = "docs/db_refactoring/query_store_consumers_approvals.json"
BASELINE_FILE = "docs/db_refactoring/baseline_query_store_consumers.json"
OPTIONAL_METHODS = {
    "analyze_news_job_claim_read_only",
    "explain_news_job_claim_plan",
    "set_news_job_wakeup",
}
OWNED_THREAD_MODULE = "kiwoom_monitor.central_server.diagnostic_replay_runtime"


def _dotted(node: ast.AST) -> str:
    return ast.unparse(node)


def _signature(item: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return json.dumps({
        "kind": "async" if isinstance(item, ast.AsyncFunctionDef) else "sync",
        "arguments": ast.dump(item.args, annotate_fields=True, include_attributes=False),
        "returns": ast.dump(item.returns, annotate_fields=True, include_attributes=False)
        if item.returns else None,
        "decorators": [ast.dump(value, annotate_fields=True, include_attributes=False)
                       for value in item.decorator_list],
        "type_comment": item.type_comment,
        "type_params": [ast.dump(value, annotate_fields=True, include_attributes=False)
                        for value in getattr(item, "type_params", ())],
    }, sort_keys=True)


def _method_sets(root: Path) -> dict[str, dict[str, str]]:
    return {
        owner: {name: _signature(source.node) for name, source in methods.items()
                if name != "__init__"}
        for owner, methods in method_sources(root).items()
    }


def _owners(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> tuple[str, ...]:
    values: list[str] = []
    parent = parents.get(node)
    while parent is not None:
        if isinstance(parent, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            values.append(parent.name)
        parent = parents.get(parent)
    return tuple(reversed(values))


def _route_for_node(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> dict[str, str] | None:
    """Return the nearest actual enclosing route, avoiding same-name scope collisions."""
    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            routes = []
            for decorator in current.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                if decorator.func.attr not in {"get", "post", "put", "patch", "delete", "websocket"}:
                    continue
                if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                    continue
                path = decorator.args[0].value
                if isinstance(path, str):
                    routes.append({"method": decorator.func.attr.upper(), "path": path})
            if routes:
                unique = {json.dumps(value, sort_keys=True): value for value in routes}
                if len(unique) == 1:
                    return next(iter(unique.values()))
                return {"method": "AMBIGUOUS", "path": "|".join(
                    value["path"] for value in unique.values())}
        current = parents.get(current)
    return None


def _binding_for(
    bindings: list[dict[str, Any]], file: str, owner: str, receiver: str,
) -> dict[str, Any] | None:
    matches = []
    for binding in bindings:
        if binding.get("file") != file or binding.get("receiver") != receiver:
            continue
        if binding.get("owner") == owner:
            matches.append(binding)
        elif binding.get("owner_prefix") and (
                owner == binding["owner_prefix"]
                or owner.startswith(binding["owner_prefix"] + ".")):
            matches.append(binding)
    return matches[0] if len(matches) == 1 else None


def _owned_thread_imports(tree: ast.Module, relative: str) -> dict[str, str]:
    imports: dict[str, str] = {}
    central = relative.startswith("src/kiwoom_monitor/central_server/")
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            direct = (module == OWNED_THREAD_MODULE and node.level == 0) or (
                central and node.level == 1 and module == "diagnostic_replay_runtime")
            package = (module == "kiwoom_monitor.central_server" and node.level == 0) or (
                central and node.level == 1 and not module)
            for item in node.names:
                bound = item.asname or item.name
                imports = {key: value for key, value in imports.items() if key.split(".", 1)[0] != bound}
                if direct and item.name == "owned_to_thread":
                    imports[item.asname or item.name] = OWNED_THREAD_MODULE + ".owned_to_thread"
                elif package and item.name == "diagnostic_replay_runtime":
                    imports[(item.asname or item.name) + ".owned_to_thread"] = OWNED_THREAD_MODULE + ".owned_to_thread"
        elif isinstance(node, ast.Import):
            for item in node.names:
                bound = item.asname or item.name.split(".", 1)[0]
                imports = {key: value for key, value in imports.items() if key.split(".", 1)[0] != bound}
                if item.name == OWNED_THREAD_MODULE:
                    imports[(item.asname or item.name) + ".owned_to_thread"] = OWNED_THREAD_MODULE + ".owned_to_thread"
    return imports


def _thread_dispatch(
    call: ast.AST | None, parents: dict[ast.AST, ast.AST], imports: dict[str, str],
    shadowed_names: dict[tuple[str, ...], set[str]],
) -> str:
    if not isinstance(call, ast.Call):
        return ""
    if isinstance(call.func, ast.Attribute) and call.func.attr == "to_thread":
        return "asyncio.to_thread"
    expression = _dotted(call.func)
    dispatch = imports.get(expression, "")
    root_name = expression.split(".", 1)[0]
    owners = _owners(call, parents)
    if any(root_name in shadowed_names.get(owners[:length], set())
           for length in range(len(owners) + 1)):
        return ""
    return dispatch


def _reference_kind(
    node: ast.AST, parent: ast.AST | None, parents: dict[ast.AST, ast.AST],
    imports: dict[str, str], shadowed_names: dict[tuple[str, ...], set[str]],
) -> tuple[str, str]:
    if isinstance(parent, ast.Call) and parent.func is node:
        return "direct_call", _dotted(parent.func)
    if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name) and parent.func.id in {
        "getattr", "hasattr",
    }:
        label = "getattr_reference" if parent.func.id == "getattr" else "hasattr_probe"
        caller = parents.get(parent)
        if isinstance(caller, ast.Call) and caller.func is parent:
            label = "getattr_invocation" if parent.func.id == "getattr" else label
        return label, parent.func.id
    dispatch = _thread_dispatch(parent, parents, imports, shadowed_names)
    if dispatch and isinstance(parent, ast.Call) and parent.args and node is parent.args[0]:
        return "callable_argument", dispatch
    return "bound_reference", ""


def _collect_source(
    path: Path,
    relative: str,
    tree: ast.Module,
    store_methods: set[str],
    bindings: list[dict[str, Any]],
    internal_owners: set[tuple[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    imports = _owned_thread_imports(tree, relative)
    shadowed_names: dict[tuple[str, ...], set[str]] = {}
    for node in ast.walk(tree):
        name = (node.id if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
                else node.arg if isinstance(node, ast.arg)
                else node.name if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                else "")
        if isinstance(node, (ast.Import, ast.ImportFrom)) and parents.get(node) is not tree:
            for item in node.names:
                shadowed_names.setdefault(_owners(node, parents), set()).add(item.asname or item.name.split(".")[0])
        if name:
            shadowed_names.setdefault(_owners(node, parents), set()).add(name)
    sites: list[dict[str, Any]] = []
    forwards: list[dict[str, Any]] = []

    for node in ast.walk(tree):
        owners = _owners(node, parents)
        owner = ".".join(owners) or "<module>"
        if isinstance(node, ast.Attribute) and node.attr in store_methods:
            receiver = _dotted(node.value)
            binding = _binding_for(bindings, relative, owner, receiver)
            if node.attr in {"close", "initialize"} and binding is None:
                continue
            parent = parents.get(node)
            kind, dispatch = _reference_kind(node, parent, parents, imports, shadowed_names)
            site = {
                "file": relative, "owner": owner, "receiver": receiver,
                "method": node.attr, "reference_kind": kind, "dispatch": dispatch,
                "line": node.lineno,
                "route": _route_for_node(node, parents),
                "status": "reviewed_store_binding" if binding else (
                    "store_internal_delegate" if (relative, owner) in internal_owners
                    else "unresolved_candidate"
                ),
            }
            if binding:
                site["binding_id"] = binding["id"]
                site["backend_scope"] = binding["backend_scope"]
            sites.append(site)
        elif (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in {"getattr", "hasattr"} and len(node.args) > 1
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in store_methods
        ):
            receiver = _dotted(node.args[0])
            binding = _binding_for(bindings, relative, owner, receiver)
            if node.args[1].value in {"close", "initialize"} and binding is None:
                continue
            kind = "getattr_reference" if node.func.id == "getattr" else "hasattr_probe"
            parent = parents.get(node)
            if node.func.id == "getattr" and isinstance(parent, ast.Call) and parent.func is node:
                kind = "getattr_invocation"
            site = {
                "file": relative, "owner": owner, "receiver": receiver,
                "method": node.args[1].value, "reference_kind": kind,
                "dispatch": node.func.id, "line": node.lineno,
                "route": _route_for_node(node, parents),
                "status": "reviewed_store_binding" if binding else (
                    "store_internal_delegate" if (relative, owner) in internal_owners
                    else "unresolved_candidate"
                ),
            }
            if binding:
                site["binding_id"] = binding["id"]
                site["backend_scope"] = binding["backend_scope"]
            sites.append(site)

        # Record only explicit local helper calls where a reviewed store receiver is
        # passed as an argument. The helper body is separately inventoried at its own
        # location; this edge does not silently attribute its DB methods to every route.
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and not _thread_dispatch(node, parents, imports, shadowed_names)):
            receiver_args = []
            for index, arg in enumerate(node.args):
                receiver = _dotted(arg)
                binding = _binding_for(bindings, relative, owner, receiver)
                if binding:
                    receiver_args.append((str(index), receiver, binding))
            for keyword in node.keywords:
                receiver = _dotted(keyword.value)
                binding = _binding_for(bindings, relative, owner, receiver)
                if binding:
                    receiver_args.append((f"keyword:{keyword.arg or '**'}", receiver, binding))
            if receiver_args:
                dispatch = _thread_dispatch(parents.get(node), parents, imports, shadowed_names) or "direct_call"
                for index, receiver, binding in receiver_args:
                    forwards.append({
                        "file": relative, "owner": owner, "helper": node.func.id,
                        "receiver": receiver, "argument_index": index,
                        "dispatch": dispatch, "line": node.lineno,
                        "route": _route_for_node(node, parents),
                        "binding_id": binding["id"],
                    })

        # asyncio.to_thread(helper, store, ...) passes the callable separately from
        # its arguments. Record that edge without treating the helper name as a DB call.
        thread_dispatch = _thread_dispatch(node, parents, imports, shadowed_names)
        if (thread_dispatch and isinstance(node, ast.Call) and node.args
                and isinstance(node.args[0], ast.Name)):
            helper = node.args[0].id
            for index, arg in enumerate(node.args[1:], start=1):
                receiver = _dotted(arg)
                binding = _binding_for(bindings, relative, owner, receiver)
                if binding:
                    forwards.append({
                        "file": relative, "owner": owner, "helper": helper,
                        "receiver": receiver, "argument_index": index,
                        "dispatch": thread_dispatch, "line": node.lineno,
                        "route": _route_for_node(node, parents),
                        "binding_id": binding["id"],
                    })
            for keyword in node.keywords:
                receiver = _dotted(keyword.value)
                binding = _binding_for(bindings, relative, owner, receiver)
                if binding:
                    forwards.append({
                        "file": relative, "owner": owner, "helper": helper,
                        "receiver": receiver,
                        "argument_index": f"keyword:{keyword.arg or '**'}",
                        "dispatch": thread_dispatch, "line": node.lineno,
                        "route": _route_for_node(node, parents),
                        "binding_id": binding["id"],
                    })

    return sites, forwards


def _site_identity(site: dict[str, Any]) -> tuple[str, ...]:
    route = site.get("route") or {}
    return (
        site["file"], site["owner"], site["receiver"], site["method"],
        site["reference_kind"], site.get("dispatch", ""),
        str(route.get("method", "")), str(route.get("path", "")),
        site["status"],
    )


def _forward_identity(edge: dict[str, Any]) -> tuple[str, ...]:
    route = edge.get("route") or {}
    return (
        edge["file"], edge["owner"], edge["helper"], edge["receiver"],
        str(edge["argument_index"]), edge["dispatch"],
        str(route.get("method", "")), str(route.get("path", "")),
        edge["binding_id"],
    )


def compare_identities(current: list[tuple[str, ...]], baseline: list[list[str] | tuple[str, ...]]) -> dict[str, Any]:
    actual = Counter(current)
    expected = Counter(tuple(item) for item in baseline)
    added = []
    removed = []
    for key in sorted(actual.keys() | expected.keys()):
        if actual[key] > expected[key]:
            added.append({"identity": list(key), "current_count": actual[key],
                          "baseline_count": expected[key]})
        elif expected[key] > actual[key]:
            removed.append({"identity": list(key), "current_count": actual[key],
                            "baseline_count": expected[key]})
    return {"added": added, "removed": removed}


def inventory(root: Path, approvals_path: Path | None = None,
              baseline_path: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    sources = method_sources(root)
    sets = {
        owner: {name: _signature(source.node) for name, source in methods.items()
                if name != "__init__"}
        for owner, methods in sources.items()
    }
    if not {"QueryStore", "SQLiteQueryStore", "PostgresQueryStore"} <= sets.keys():
        raise ValueError("QueryStore and both backend definitions are required")
    methods = set(sets["QueryStore"]) | OPTIONAL_METHODS
    internal_owners = {
        (source.file, f"{source.class_name}.{source.node.name}")
        for implementations in sources.values() for source in implementations.values()
    }
    try:
        approvals = json.loads((approvals_path or root / APPROVALS_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid consumer approvals manifest: {type(error).__name__}") from error
    bindings = approvals.get("reviewed_bindings", [])

    sites: list[dict[str, Any]] = []
    forwards: list[dict[str, Any]] = []
    parse_errors = []
    hashes: dict[str, str] = {}
    for path in sorted((root / "src").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        try:
            tree = ast.parse(data.decode("utf-8-sig"), filename=relative)
        except (SyntaxError, UnicodeError) as error:
            parse_errors.append({"file": relative, "error_type": type(error).__name__})
            continue
        found_sites, found_forwards = _collect_source(
            path, relative, tree, methods, bindings, internal_owners,
        )
        sites.extend(found_sites)
        forwards.extend(found_forwards)
        if found_sites or found_forwards:
            hashes[relative] = hashlib.sha256(data).hexdigest()

    reviewed = [site for site in sites if site["status"] == "reviewed_store_binding"]
    delegates = [site for site in sites if site["status"] == "store_internal_delegate"]
    unresolved = [site for site in sites if site["status"] == "unresolved_candidate"]
    contracts = {
        name: dict(sorted(values.items()))
        for name, values in sorted(sets.items())
    }
    signatures: dict[str, dict[str, str]] = {}
    try:
        signatures = json.loads((baseline_path or root / BASELINE_FILE).read_text(encoding="utf-8"))[
            "contracts"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        signatures = {}
    baseline_sites: list[list[str]] = []
    baseline_forwards: list[list[str]] = []
    try:
        baseline_data = json.loads((baseline_path or root / BASELINE_FILE).read_text(encoding="utf-8"))
        baseline_sites = baseline_data.get("site_identities", [])
        baseline_forwards = baseline_data.get("forwarding_identities", [])
    except (OSError, json.JSONDecodeError, AttributeError):
        baseline_data = {}

    baseline_reviewed = [identity for identity in baseline_sites
                         if identity and identity[-1] == "reviewed_store_binding"]
    site_delta = compare_identities(
        [_site_identity(site) for site in reviewed], baseline_reviewed,
    ) if baseline_sites else {"added": [], "removed": []}
    candidate_delta = compare_identities(
        [_site_identity(site) for site in sites], baseline_sites,
    ) if baseline_sites else {"added": [], "removed": []}
    forwarding_delta = compare_identities(
        [_forward_identity(edge) for edge in forwards], baseline_forwards,
    ) if baseline_forwards else {"added": [], "removed": []}
    contract_changed = bool(signatures and contracts != signatures)
    stale_bindings = []
    for binding in bindings:
        if not any(site.get("binding_id") == binding.get("id") for site in reviewed) and not any(
            item.get("binding_id") == binding.get("id") for item in forwards
        ):
            stale_bindings.append(binding.get("id", ""))
    status = "pass"
    if (parse_errors or stale_bindings or site_delta["added"] or site_delta["removed"]
            or candidate_delta["added"] or candidate_delta["removed"]
            or forwarding_delta["added"] or forwarding_delta["removed"] or contract_changed):
        status = "review_required"
    if not baseline_sites or not signatures:
        status = "baseline_missing"

    counts_by_kind = Counter(site["reference_kind"] for site in sites)
    return {
        "scope": "static_source_only_no_import_or_database_access",
        "status": status,
        "counts": {
            "protocol_methods": len(sets["QueryStore"]),
            "postgres_methods": len(sets["PostgresQueryStore"]),
            "sqlite_methods": len(sets["SQLiteQueryStore"]),
            "reviewed_store_sites": len(reviewed),
            "store_internal_delegate_sites": len(delegates),
            "unresolved_same_name_candidates": len(unresolved),
            "forwarding_edges": len(forwards),
            "reference_kinds": dict(sorted(counts_by_kind.items())),
            "parse_errors": len(parse_errors),
        },
        "reviewed_store_sites": sorted(reviewed, key=lambda item: _site_identity(item)),
        "store_internal_delegates": sorted(delegates, key=lambda item: _site_identity(item)),
        "unresolved_same_name_candidates": sorted(unresolved, key=lambda item: _site_identity(item)),
        "store_forwarding_edges": sorted(forwards, key=lambda item: (
            item["file"], item["owner"], item["line"], item["helper"],
        )),
        "contract_signatures": contracts,
        "reviewed_site_delta": site_delta,
        "all_candidate_delta": candidate_delta,
        "forwarding_delta": forwarding_delta,
        "contract_signatures_changed": contract_changed,
        "stale_bindings": sorted(stale_bindings),
        "parse_errors": parse_errors,
        "source_sha256": hashes,
        "limits": [
            "A reviewed receiver binding is source evidence, not proof a branch ran or the configured backend was PostgreSQL.",
            "Same-named methods on unreviewed receivers remain candidates and are not counted as store calls.",
            "Forwarding edges are one explicit local call edge; they do not imply transitive runtime execution.",
            "This inventory does not prove transaction behavior, SQL table use, UI result handling, or production runtime coverage.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--approvals", type=Path, help="reviewed receiver binding manifest")
    parser.add_argument("--baseline", type=Path, help="reviewed source identity and contract baseline")
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero for missing baseline, drift, stale binding, or parse error")
    parser.add_argument("--write-baseline", action="store_true",
                        help="write reviewed bindings, unresolved candidates, and contracts as a baseline")
    args = parser.parse_args()
    root = args.root.resolve()
    result = inventory(
        root,
        args.approvals.resolve() if args.approvals else None,
        args.baseline.resolve() if args.baseline else None,
    )
    if args.write_baseline:
        if result["parse_errors"] or result["stale_bindings"]:
            raise SystemExit("refusing baseline: parse errors or stale reviewed bindings require review")
        target = args.baseline.resolve() if args.baseline else root / BASELINE_FILE
        if target.exists():
            raise SystemExit("refusing to overwrite an existing baseline; use --output for an after report")
        target.parent.mkdir(parents=True, exist_ok=True)
        baseline = {
            "schema_version": 1,
            "scope": result["scope"],
            "site_identities": [
                list(_site_identity(site)) for site in (
                    result["reviewed_store_sites"] + result["unresolved_same_name_candidates"]
                    + result["store_internal_delegates"]
                )
            ],
            "forwarding_identities": [
                list(_forward_identity(edge)) for edge in result["store_forwarding_edges"]
            ],
            "contracts": result["contract_signatures"],
        }
        target.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result = inventory(root, args.approvals.resolve() if args.approvals else None, target)
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], **result["counts"],
                      "contract_signatures_changed": result["contract_signatures_changed"],
                      "stale_bindings": result["stale_bindings"],
                      "added_sites": len(result["reviewed_site_delta"]["added"]),
                      "removed_sites": len(result["reviewed_site_delta"]["removed"]),
                      "added_candidates": len(result["all_candidate_delta"]["added"]),
                      "removed_candidates": len(result["all_candidate_delta"]["removed"]),
                      "added_forwarding_edges": len(result["forwarding_delta"]["added"]),
                      "removed_forwarding_edges": len(result["forwarding_delta"]["removed"])}))
    if args.check and result["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
